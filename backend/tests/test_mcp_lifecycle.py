"""Session lifecycle / shutdown regressions.

The MCP SDK's transports are anyio-based: a cancel scope must be exited in the
task that entered it, and a transport whose handshake is still suspended leaves
async generators the loop can never finalize. Both used to surface as stderr
noise ("Attempted to exit cancel scope in a different task",
"aclose(): asynchronous generator is already running") whenever the app quit
during prewarm. These tests pin the fixed behaviour.
"""

from __future__ import annotations

import subprocess
import sys
import time
import textwrap
from pathlib import Path

import pytest

from .conftest import write_server
from coworker.mcp import McpManager
from coworker.mcp_session import McpSessionManager


def add_stdio_server(mgr: McpManager, tmp_path: Path, name: str) -> str:
    server = write_server(tmp_path, name, f"{name}_only")
    entry = mgr.add_server(
        name=name.capitalize(),
        transport="stdio",
        command=sys.executable,
        args=f"{server} {tmp_path / (name + '.marker')}",
    )
    return entry["id"]


def test_shutdown_closes_sessions_and_loop(tmp_path: Path):
    mgr = McpManager(tmp_path / "mcp_servers.json")
    add_stdio_server(mgr, tmp_path, "alpha")

    sessions = McpSessionManager(tmp_path, mgr, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    sessions.ensure_connected(enable_browser_flow=False)
    assert sessions.tool_names(), "the server should be connected before shutdown"

    sessions.shutdown()
    assert sessions._servers == {}
    assert not [t for t in sessions._owner_tasks if not t.done()]
    assert sessions._thread is None or not sessions._thread.is_alive()


def test_shutdown_during_prewarm_is_clean(tmp_path: Path):
    """Quitting mid-handshake must not leave owner tasks behind."""
    mgr = McpManager(tmp_path / "mcp_servers.json")
    for name in ("alpha", "beta"):
        add_stdio_server(mgr, tmp_path, name)

    sessions = McpSessionManager(tmp_path, mgr)
    sessions.start()
    sessions.prewarm()  # fire-and-forget: still connecting
    sessions.shutdown()  # immediate quit

    assert sessions._closing is True
    assert not [t for t in sessions._owner_tasks if not t.done()]


def test_prewarm_after_shutdown_is_a_noop(tmp_path: Path):
    mgr = McpManager(tmp_path / "mcp_servers.json")
    add_stdio_server(mgr, tmp_path, "alpha")

    sessions = McpSessionManager(tmp_path, mgr)
    sessions.start()
    sessions.shutdown()
    sessions.prewarm()  # must not resurrect the loop
    assert not sessions._owner_tasks


def test_double_shutdown_is_safe(tmp_path: Path):
    mgr = McpManager(tmp_path / "mcp_servers.json")
    add_stdio_server(mgr, tmp_path, "alpha")

    sessions = McpSessionManager(tmp_path, mgr, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    sessions.ensure_connected(enable_browser_flow=False)
    sessions.shutdown()
    sessions.shutdown()  # idempotent


def test_wedged_server_does_not_hang_shutdown(tmp_path: Path):
    """A server that opens but never speaks MCP must not wedge quit.

    Cancelling the session owner used to abort the stdio shutdown sequence
    (close stdin -> SIGTERM -> SIGKILL) at its first await, orphaning the child
    process; asyncio's non-daemon waitpid thread then blocked interpreter exit.
    """
    wedged = tmp_path / "wedged_server.py"
    wedged.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")

    mgr = McpManager(tmp_path / "mcp_servers.json")
    mgr.add_server(
        name="Wedged", transport="stdio", command=sys.executable, args=str(wedged)
    )

    sessions = McpSessionManager(tmp_path, mgr, connect_timeout=3.0, call_timeout=3.0)
    sessions.start()
    sessions.prewarm()
    time.sleep(0.5)  # land inside the (never-completing) handshake

    started = time.monotonic()
    sessions.shutdown()
    elapsed = time.monotonic() - started

    assert elapsed < 20, f"shutdown took {elapsed:.1f}s"
    assert not [t for t in sessions._owner_tasks if not t.done()]


def test_handshake_timeout_marks_server_error(tmp_path: Path):
    """The connect must give up instead of pinning the task forever."""
    wedged = tmp_path / "wedged_server.py"
    wedged.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")

    mgr = McpManager(tmp_path / "mcp_servers.json")
    entry = mgr.add_server(
        name="Wedged", transport="stdio", command=sys.executable, args=str(wedged)
    )

    sessions = McpSessionManager(tmp_path, mgr, connect_timeout=3.0, call_timeout=3.0)
    sessions.start()
    try:
        started = time.monotonic()
        sessions.ensure_connected(enable_browser_flow=False)
        elapsed = time.monotonic() - started
        assert elapsed < 20, f"connect blocked for {elapsed:.1f}s"
        assert entry["id"] not in sessions._servers
        assert not sessions.tool_names()
    finally:
        sessions.shutdown()


@pytest.mark.parametrize("mode", ["connected", "prewarm-race", "wedged"])
def test_process_exit_emits_no_teardown_noise(tmp_path: Path, mode: str):
    """A whole interpreter lifecycle must exit without asyncio teardown errors.

    Run out-of-process: the failure mode is asyncgen finalization at interpreter
    shutdown, which only reproduces when the process actually exits. ``wedged``
    is the case that reproduced against the pre-fix code (both
    "cancel scope in a different task" and "athrow(): ... already running").
    """
    if mode == "wedged":
        server = tmp_path / "wedged.py"
        server.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
        args_expr = f"{str(server)!r}"
    else:
        server = write_server(tmp_path, "alpha", "alpha_only")
        args_expr = f'f"{server} {{tmp / \'probe.marker\'}}"'

    script = tmp_path / f"probe_{mode.replace('-', '_')}.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            from pathlib import Path
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from coworker.mcp import McpManager
            from coworker.mcp_session import McpSessionManager

            tmp = Path({str(tmp_path)!r})
            mgr = McpManager(tmp / "probe_{mode.replace('-', '_')}.json")
            mgr.add_server(
                name="Probe", transport="stdio",
                command={sys.executable!r}, args={args_expr},
            )
            s = McpSessionManager(tmp, mgr, connect_timeout=3.0, call_timeout=3.0)
            s.start()
            if {mode!r} == "connected":
                s.ensure_connected(enable_browser_flow=False)
            else:
                s.prewarm()
                time.sleep(0.5)
            s.shutdown()
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    # The FastMCP child logs its own request handling to stderr; only the
    # asyncio/anyio teardown errors are regressions.
    noise = [
        line
        for line in proc.stderr.splitlines()
        if any(
            marker in line
            for marker in (
                "cancel scope",
                "asynchronous generator is already running",
                "Task was destroyed",
                "Traceback",
            )
        )
    ]
    assert not noise, "\n".join(noise)
