"""Shared MCP connection helpers for the Coworker runtime.

This module owns the single source of truth for turning a stored MCP server
record into a ``langchain-mcp-adapters`` connection dict. Every transport has a
different accepted keyword set, so the connection must be built per transport --
passing ``command``/``args`` to a Streamable HTTP session raises ``TypeError``.

Live sessions are owned by :class:`coworker.mcp_session.McpSessionManager`;
this module deliberately contains no caching or process-spawning logic.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import threading
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0

# Canonical transport names accepted by langchain_mcp_adapters.sessions.create_session
TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "streamable_http"
TRANSPORT_SSE = "sse"
TRANSPORT_WEBSOCKET = "websocket"

REMOTE_TRANSPORTS = {TRANSPORT_HTTP, TRANSPORT_SSE, TRANSPORT_WEBSOCKET}

_TRANSPORT_ALIASES: dict[str, str] = {
    "stdio": TRANSPORT_STDIO,
    "local": TRANSPORT_STDIO,
    "http": TRANSPORT_HTTP,
    "https": TRANSPORT_HTTP,
    "streamable_http": TRANSPORT_HTTP,
    "streamable-http": TRANSPORT_HTTP,
    "streamablehttp": TRANSPORT_HTTP,
    "sse": TRANSPORT_SSE,
    "websocket": TRANSPORT_WEBSOCKET,
    "ws": TRANSPORT_WEBSOCKET,
    "wss": TRANSPORT_WEBSOCKET,
}


def normalize_transport(value: str | None) -> str:
    """Map a stored/user-supplied transport string to its canonical name."""
    key = (value or "").strip().lower()
    return _TRANSPORT_ALIASES.get(key, TRANSPORT_STDIO)


def is_remote_transport(value: str | None) -> bool:
    return normalize_transport(value) in REMOTE_TRANSPORTS


def split_args(raw: str | None) -> list[str]:
    """Split a command-line argument string, honouring quotes.

    ``str.split()`` breaks on every space which corrupts quoted paths such as
    ``--dir "/Users/my folder/data"``. ``shlex`` handles that correctly and we
    fall back to naive splitting only when the input has unbalanced quotes.
    """
    text = (raw or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return [chunk for chunk in text.split() if chunk]


def build_connection(server: dict[str, Any]) -> dict[str, Any]:
    """Build a transport-specific connection dict.

    Raises:
        ValueError: when a required field for the chosen transport is missing.
    """
    transport = normalize_transport(server.get("transport"))

    if transport == TRANSPORT_STDIO:
        command = (server.get("command") or "").strip()
        if not command:
            raise ValueError("command is required for stdio transport")
        conn: dict[str, Any] = {
            "transport": TRANSPORT_STDIO,
            "command": command,
            "args": split_args(server.get("args")),
        }
        env = {
            str(key): str(value)
            for key, value in (server.get("env") or {}).items()
            if str(key).strip()
        }
        if env:
            conn["env"] = env
        cwd = (server.get("cwd") or "").strip()
        if cwd:
            conn["cwd"] = cwd
        return conn

    url = (server.get("url") or "").strip()
    if not url:
        raise ValueError(f"url is required for {transport} transport")

    conn = {"transport": transport, "url": url}

    if transport == TRANSPORT_WEBSOCKET:
        return conn

    headers = {
        str(key): str(value)
        for key, value in (server.get("headers") or {}).items()
        if str(key).strip()
    }
    if headers:
        conn["headers"] = headers
    timeout = server.get("timeout")
    if timeout:
        try:
            conn["timeout"] = float(timeout)
        except (TypeError, ValueError):
            pass
    return conn


def run_blocking(coro_factory, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """Run an async factory on a private event loop in a dedicated thread.

    The MCP SDK spawns subprocesses and long-lived task groups. Running it on
    the caller's loop (or patching the global loop policy with ``nest_asyncio``)
    breaks uvicorn. A throwaway thread keeps everything isolated and lets us
    enforce a hard timeout. Used only for one-shot test/check operations; live
    sessions live on the ``McpSessionManager`` loop instead.
    """
    box: dict[str, Any] = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(
                asyncio.wait_for(coro_factory(), timeout=timeout)
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            box["error"] = exc
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - best effort cleanup
                pass
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=_worker, name="mcp-runtime", daemon=True)
    thread.start()
    thread.join(timeout + 5)

    if thread.is_alive():
        raise TimeoutError(f"MCP operation did not finish within {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")
