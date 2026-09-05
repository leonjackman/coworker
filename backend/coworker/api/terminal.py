# -*- coding: utf-8 -*-

import asyncio
import json
import os
import signal
import struct
import subprocess
from urllib.parse import parse_qsl, urlparse
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from coworker.platform import default_shell as _platform_default_shell
from coworker.api.state import (
    _PTY_AVAILABLE,
    app,
    fcntl,
    pty,
    termios,
    workspace_controller
)

from fastapi import APIRouter

router = APIRouter()


@router.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket):
    """Stream a real interactive shell (PTY) to the browser bottom-panel terminal.

    Protocol (JSON text frames):
      client -> server: {"type": "input", "data": "<raw keystrokes>"}
                        {"type": "resize", "cols": N, "rows": M}
      server -> client: raw terminal bytes (text)
                       {"type": "error", "message": "..."}  (on spawn failure)
    The cwd is the project's workspace when project_id is given, otherwise the
    default workspace.
    """
    # Origin gate: WebSocket is not subject to CORS, so a malicious web page
    # could otherwise drive this PTY shell directly. Only allow the local dev
    # origins the app itself uses (plus Electron's file:// origin). Hostnames
    # are matched exactly — a prefix match (e.g. http://localhost.evil.com) and
    # the opaque "null" origin (spawned by sandboxed iframes) must be rejected.
    origin = websocket.headers.get("origin", "")
    if origin:
        parsed = urlparse(origin)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if scheme == "file":
            pass  # Electron loads the bundled app from file://
        elif host in {"localhost", "127.0.0.1", "::1"} and scheme in {"http", "https"}:
            pass
        else:
            await websocket.close(code=1008)
            return

    await websocket.accept()

    project_id = websocket.query_params.get("project_id")

    try:
        if project_id:
            workspace = workspace_controller.workspace_for_project(project_id)
        else:
            workspace = workspace_controller.default()
        cwd = str(workspace.root)
    except Exception:
        cwd = os.path.expanduser("~")

    if not _PTY_AVAILABLE:
        # Windows: no POSIX pty. Fall back to a pipe-based interactive shell
        # that keeps the same WebSocket protocol.
        await _pipe_terminal(websocket, cwd)
        return

    shell = _platform_default_shell()
    master_fd: int | None = None
    proc: subprocess.Popen[bytes] | None = None

    try:
        master_fd, slave_fd = pty.openpty()
        try:
            winsize = struct.pack("HHHH", 24, 80, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

        env = dict(os.environ)
        env["TERM"] = "xterm-256color"

        proc = subprocess.Popen(
            [shell],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
    except Exception as exc:
        if master_fd is not None:
            os.close(master_fd)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": f"Failed to start shell: {exc}"}))
        except Exception:
            pass
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    write_queue: asyncio.Queue[str] = asyncio.Queue()
    eof = asyncio.Event()

    def on_master_readable() -> None:
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            data = b""
        if not data:
            loop.call_soon_threadsafe(eof.set)
            return
        try:
            write_queue.put_nowait(data.decode("utf-8", errors="replace"))
        except asyncio.QueueFull:
            pass

    loop.add_reader(master_fd, on_master_readable)

    async def pump() -> None:
        while True:
            try:
                chunk = await write_queue.get()
            except asyncio.CancelledError:
                return
            try:
                await websocket.send_text(chunk)
            except Exception:
                return

    pump_task = asyncio.ensure_future(pump())

    cols, rows = 80, 24

    def set_winsize(next_cols: int, next_rows: int) -> None:
        nonlocal cols, rows
        cols, rows = max(1, int(next_cols)), max(1, int(next_rows))
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    try:
        while True:
            if eof.is_set():
                break
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                os.write(master_fd, message.encode("utf-8", errors="replace"))
                continue
            msg_type = payload.get("type")
            if msg_type == "resize":
                set_winsize(payload.get("cols", cols), payload.get("rows", rows))
            elif msg_type == "input":
                os.write(master_fd, str(payload.get("data", "")).encode("utf-8", errors="replace"))
    except WebSocketDisconnect:
        pass
    finally:
        loop.remove_reader(master_fd)
        pump_task.cancel()
        if proc is not None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
async def _pipe_terminal(websocket: WebSocket, cwd: str) -> None:
    """Non-PTY interactive terminal fallback for platforms without ``pty`` (Windows).

    Runs ``powershell.exe`` (or ``cmd.exe``) over anonymous pipes, keeping the
    same WebSocket protocol as the POSIX PTY terminal: the client sends
    ``{"type":"input","data":...}`` frames (``resize`` is accepted but ignored)
    and receives raw stdout bytes as text frames. A one-line banner notes the
    reduced mode (no resize, no raw TTY control codes).
    """
    import threading

    import subprocess as _subprocess

    shell = _platform_default_shell()
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    creationflags = 0
    try:
        from subprocess import CREATE_NEW_PROCESS_GROUP

        creationflags = CREATE_NEW_PROCESS_GROUP
    except ImportError:  # pragma: no cover - non-Windows
        pass

    proc: _subprocess.Popen[bytes] | None = None
    try:
        proc = _subprocess.Popen(
            [shell],
            stdin=_subprocess.PIPE,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.STDOUT,
            cwd=cwd or os.path.expanduser("~"),
            env=env,
            bufsize=0,
            creationflags=creationflags,
        )
    except Exception as exc:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": f"Failed to start shell: {exc}"}))
        except Exception:  # noqa: BLE001
            pass
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    write_queue: asyncio.Queue[str] = asyncio.Queue()
    eof = asyncio.Event()

    def _enqueue(text: str) -> None:
        try:
            write_queue.put_nowait(text)
        except asyncio.QueueFull:
            pass

    def _reader() -> None:
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                data = proc.stdout.read(65536)
                if not data:
                    break
                loop.call_soon_threadsafe(_enqueue, data.decode("utf-8", errors="replace"))
        finally:
            loop.call_soon_threadsafe(eof.set)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    async def pump() -> None:
        while True:
            try:
                chunk = await write_queue.get()
            except asyncio.CancelledError:
                return
            try:
                await websocket.send_text(chunk)
            except Exception:  # noqa: BLE001
                return

    pump_task = asyncio.ensure_future(pump())

    def _write(data: str) -> None:
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(data.encode("utf-8", errors="replace"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    try:
        await websocket.send_text(
            "\r\n\x1b[33mNon-PTY terminal mode (Windows). Resize is not supported; ANSI-only.\x1b[0m\r\n"
        )
        while True:
            if eof.is_set():
                break
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                _write(message)
                continue
            msg_type = payload.get("type")
            if msg_type == "input":
                _write(str(payload.get("data", "")))
            # "resize" is accepted and ignored on non-PTY platforms.
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
