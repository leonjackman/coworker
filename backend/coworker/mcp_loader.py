"""MCP client loader and shared connection helpers for the Coworker runtime.

This module owns the single source of truth for turning a stored MCP server
record into a ``langchain-mcp-adapters`` connection dict. Every transport has a
different accepted keyword set, so the connection must be built per transport --
passing ``command``/``args`` to a Streamable HTTP session raises ``TypeError``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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


def build_mcp_config(servers_config: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the ``{server_key: connection}`` map, skipping invalid entries."""
    mcp_config: dict[str, dict[str, Any]] = {}
    for server in servers_config:
        server_id = str(server.get("id") or server.get("name") or "").strip()
        if not server_id:
            continue
        try:
            mcp_config[server_id] = build_connection(server)
        except ValueError as exc:
            logger.warning("Skipping MCP server %s: %s", server_id, exc)
    return mcp_config


# Backwards-compatible alias (older call sites used the private name).
_build_mcp_config = build_mcp_config


def run_blocking(coro_factory, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """Run an async factory on a private event loop in a dedicated thread.

    The MCP SDK spawns subprocesses and long-lived task groups. Running it on
    the caller's loop (or patching the global loop policy with ``nest_asyncio``)
    breaks uvicorn. A throwaway thread keeps everything isolated and lets us
    enforce a hard timeout.
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


_tools_cache: dict[str, list[Any]] = {}
_tools_cache_lock = threading.Lock()


def _cache_key(mcp_config: dict[str, dict[str, Any]]) -> str:
    blob = json.dumps(mcp_config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def invalidate_tools_cache() -> None:
    """Drop cached MCP tools so the next agent build reloads them."""
    with _tools_cache_lock:
        _tools_cache.clear()


def load_mcp_tools_sync(
    servers_config: list[dict[str, Any]],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
) -> list[Any]:
    """Synchronously load LangChain tools from the given MCP servers.

    Tools are loaded with a per-server name prefix so they can be attributed
    back to their server; tools listed in a server's ``disabled_tools`` are
    dropped here (so they never reach the agent), and the prefix is stripped
    again so the model still sees the bare tool name.

    Never raises: a broken server must not take down agent construction.
    """
    if not servers_config:
        return []

    mcp_config = build_mcp_config(servers_config)
    if not mcp_config:
        return []

    key = _cache_key(mcp_config)
    if use_cache:
        with _tools_cache_lock:
            cached = _tools_cache.get(key)
        if cached is not None:
            return cached

    disabled_by_server: dict[str, set[str]] = {}
    for server in servers_config:
        server_id = str(server.get("id") or server.get("name") or "").strip()
        if not server_id or server_id not in mcp_config:
            continue
        raw = server.get("disabled_tools")
        if isinstance(raw, list):
            disabled_by_server[server_id] = {str(item).strip() for item in raw if str(item).strip()}

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(mcp_config, tool_name_prefix=True)
        tools = run_blocking(client.get_tools, timeout=timeout) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load MCP tools: %s", exc)
        return []

    # Attribute each tool to its server via the name prefix, drop disabled
    # tools, and restore the bare tool name for the model. The server id is
    # also recorded in tool metadata so the middleware can tell the model which
    # MCP service each tool belongs to.
    filtered: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str):
            filtered.append(tool)
            continue
        server_id, separator, bare = name.partition("_")
        if separator and server_id in disabled_by_server and bare in disabled_by_server[server_id]:
            continue
        if separator and server_id in mcp_config and bare:
            try:
                tool.name = bare
            except Exception:  # noqa: BLE001 - cosmetic rename; keep prefixed name
                pass
            try:
                tool.metadata = {
                    **(getattr(tool, "metadata", None) or {}),
                    "coworker_server": server_id,
                }
            except Exception:  # noqa: BLE001 - attribution is best-effort
                pass
        filtered.append(tool)

    if use_cache:
        with _tools_cache_lock:
            _tools_cache[key] = filtered
    return filtered


def list_mcp_tools_sync(
    servers_config: list[dict[str, Any]],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """List discovered MCP tools as plain metadata dicts for the UI."""
    tools = load_mcp_tools_sync(servers_config, timeout=timeout, use_cache=False)
    return [
        {
            "name": getattr(tool, "name", "unknown"),
            "description": getattr(tool, "description", "") or "",
        }
        for tool in tools
    ]
