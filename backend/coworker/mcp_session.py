"""Persistent MCP session manager.

This is the industry-standard MCP client model used by desktop agents
(Claude Desktop / Cursor / Cline): every enabled server keeps a **long-lived
session** open on a dedicated background asyncio loop. Sessions survive graph
rebuilds, tool calls are dispatched across event loops, and a failed server
never takes the others down.

Design notes
------------
* Sessions run on a private loop thread (:attr:`McpSessionManager._loop`) so a
  hung MCP server (a stalled SSE stream, a stuck subprocess) cannot block the
  uvicorn event loop that runs the agent graph.
* Cross-loop tool calls use ``asyncio.run_coroutine_threadsafe`` +
  ``asyncio.wrap_future``.
* Tools are *dispatch tools*: lightweight ``StructuredTool`` objects that carry
  no session reference and route every invocation to the manager by
  ``server_id`` + tool name. This keeps them valid across reconnects and avoids
  async-object/loop binding problems.
* ``ensure_connected`` never opens the OAuth browser flow; that only happens in
  the explicit ``reauthorize`` path. Servers that need auth are reported as
  ``needs_auth`` and stay out of the tool set until the user re-authorizes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from langchain_core.tools import StructuredTool

from langchain_mcp_adapters.sessions import create_session

from .mcp import McpManager, STATUS_CONNECTED, STATUS_ERROR, STATUS_NEEDS_AUTH
from .mcp_loader import build_connection
from .mcp_oauth import (
    FileTokenStorage,
    LoopbackCallbackServer,
    build_oauth_provider,
)

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_CALL_TIMEOUT = 60.0

_REMOTE_TRANSPORTS = {"sse", "streamable_http"}


class McpSessionUnavailable(RuntimeError):
    """No live session for the requested server."""


class McpSessionClosedError(RuntimeError):
    """The session died mid-call; the caller may reconnect and retry once."""


def _is_transport_error(exc: BaseException) -> bool:
    """Best-effort detection of a dead transport (vs. an MCP tool error).

    A false positive is harmless: the reconnect+retry path is idempotent.
    """
    for leaf in _flatten_exceptions(exc):
        if isinstance(leaf, (ConnectionError, BrokenPipeError, EOFError, TimeoutError)):
            return True
        text = str(leaf).lower()
        markers = (
            "session not initialized",
            "client not initialized",
            "connection closed",
            "connection reset",
            "broken pipe",
            "stream closed",
            "the client has been closed",
            "loop is closed",
            "server terminated",
            "subprocess",
            "not connected",
            "read connection lost",
        )
        if any(m in text for m in markers):
            return True
    return False


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Unwrap ``ExceptionGroup``/``BaseExceptionGroup`` into leaf exceptions.

    The MCP SDK and anyio wrap transport errors (e.g. ``httpx.HTTPStatusError``
    for a 401) inside exception groups, so classifying a failure by
    ``str(exc)`` only sees the group summary. Recursing into the groups lets us
    inspect the real leaf error.
    """
    leaves: list[BaseException] = []

    def _walk(e: BaseException) -> None:
        for leaf in getattr(e, "exceptions", ()) or ():
            if getattr(leaf, "exceptions", None):
                _walk(leaf)
            else:
                leaves.append(leaf)
        if not getattr(e, "exceptions", ()):
            leaves.append(e)

    _walk(exc)
    return leaves or [exc]


def _classify_auth_error(exc: BaseException) -> bool:
    for leaf in _flatten_exceptions(exc):
        text = str(leaf).lower()
        if any(m in text for m in ("401", "unauthorized", "authorization", "oauth", "www-authenticate", "403")):
            return True
    return False


class _ServerRuntime:
    """One persistent session + its dispatch tools."""

    __slots__ = (
        "server_id",
        "server_name",
        "session",
        "stack",
        "connection",
        "tools",
        "loopback",
    )

    def __init__(self, server_id: str, session: Any, stack: contextlib.AsyncExitStack, connection: dict[str, Any]) -> None:
        self.server_id = server_id
        self.server_name = ""
        self.session = session
        self.stack = stack
        self.connection = connection
        self.tools: list[Any] = []
        self.loopback: LoopbackCallbackServer | None = None

    async def close(self) -> None:
        try:
            await self.stack.aclose()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            logger.debug("MCP session teardown raised for %s", self.server_id)
        if self.loopback is not None:
            await self.loopback.close()
            self.loopback = None
        self.session = None
        self.tools = []


def _make_dispatch_tool(
    server_id: str,
    name: str,
    mcp_tool: Any,
    manager: "McpSessionManager",
) -> StructuredTool:
    """Build a session-agnostic dispatch tool for one MCP tool.

    Provides both a sync and an async entrypoint (``func`` + ``coroutine``) so
    the tool works in the non-streaming ``graph.invoke`` path and the streaming
    ``astream`` path alike. Reuses the adapter's result conversion and error
    formatting so the model sees exactly the same content blocks / error
    ToolMessages as the stock ``langchain-mcp-adapters`` integration.
    """
    from langchain_mcp_adapters.tools import _convert_call_tool_result, _handle_mcp_tool_error

    def _run_dispatch(**kwargs: Any) -> Any:
        result = manager.call_tool_sync(server_id, name, kwargs)
        return _convert_call_tool_result(result)

    async def _arun_dispatch(**kwargs: Any) -> Any:
        result = await manager.call_tool(server_id, name, kwargs)
        return _convert_call_tool_result(result)

    return StructuredTool(
        name=name,
        description=getattr(mcp_tool, "description", None) or "",
        args_schema=mcp_tool.inputSchema,
        func=_run_dispatch,
        coroutine=_arun_dispatch,
        response_format="content_and_artifact",
        metadata={"coworker_server": server_id},
        handle_tool_error=_handle_mcp_tool_error,  # type: ignore[arg-type]
    )


class McpSessionManager:
    """Owns persistent MCP sessions for every enabled server."""

    def __init__(
        self,
        data_dir: Path,
        mcp_manager: McpManager,
        open_browser: Any | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.mcp_manager = mcp_manager
        self._oauth_dir = self.data_dir / "mcp_oauth"
        self._oauth_dir.mkdir(parents=True, exist_ok=True)
        self.open_browser = open_browser
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._servers: dict[str, _ServerRuntime] = {}
        self._connecting: dict[str, asyncio.Future] = {}

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background event loop thread."""
        if self._loop is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="mcp-sessions", daemon=True)
        self._thread.start()
        while self._loop is None:
            time.sleep(0.01)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            self._loop = None

    def shutdown(self) -> None:
        """Close every session and stop the loop thread."""
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
            fut.result(timeout=15)
        except Exception:  # noqa: BLE001
            logger.warning("MCP session shutdown had errors")
        thread, loop = self._thread, self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)

    async def _shutdown_async(self) -> None:
        runtimes = list(self._servers.values())
        self._servers.clear()
        await asyncio.gather(*(rt.close() for rt in runtimes), return_exceptions=True)

    # ── bridge helpers ───────────────────────────────────────────────────

    def run_sync(self, coro_factory: Any, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> Any:
        """Run ``coro_factory()`` on the manager loop and block for the result."""
        if self._loop is None:
            raise RuntimeError("McpSessionManager is not started")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return fut.result(timeout=timeout)

    async def _bridge_async(self, coro_factory: Any) -> Any:
        """Run ``coro_factory()`` on the manager loop from another loop."""
        if self._loop is None:
            raise RuntimeError("McpSessionManager is not started")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return await asyncio.wrap_future(fut)

    # ── connection ───────────────────────────────────────────────────────

    def prewarm(self) -> None:
        """Fire-and-forget connect of every enabled server (no blocking)."""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._prewarm_async(), self._loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to schedule MCP prewarm: %s", exc)

    async def _prewarm_async(self) -> None:
        await asyncio.sleep(0)
        try:
            await self._ensure_connected_async(enable_browser_flow=False)
        except BaseException as exc:  # noqa: BLE001
            logger.warning("MCP prewarm failed: %s", _friendly_error(exc))

    def ensure_connected(self, enable_browser_flow: bool = False) -> None:
        """Synchronous bridge: connect missing enabled servers (bounded)."""
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._ensure_connected_async(enable_browser_flow=enable_browser_flow), self._loop
            )
            fut.result(timeout=self._connect_timeout + 10)
        except TimeoutError:
            logger.warning("MCP connect did not finish within the timeout")
        except BaseException as exc:  # noqa: BLE001 - a broken server must never break chat
            logger.warning("MCP connect failed: %s", _friendly_error(exc))

    async def _ensure_connected_async(self, enable_browser_flow: bool = False) -> None:
        try:
            servers = self.mcp_manager.list_runtime_configs(enabled_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read MCP config: %s", exc)
            return
        if not servers:
            return

        pending = [s for s in servers if s["id"] not in self._servers]
        if not pending:
            return

        async def connect_one(server: dict[str, Any]) -> None:
            server_id = server["id"]
            in_flight = self._connecting.get(server_id)
            if in_flight is not None:
                try:
                    await in_flight
                except BaseException:  # noqa: BLE001
                    pass
                return
            task = asyncio.ensure_future(
                self._connect_safely(server, enable_browser_flow=enable_browser_flow)
            )
            self._connecting[server_id] = task
            try:
                await task
            finally:
                self._connecting.pop(server_id, None)

        await asyncio.gather(*(connect_one(s) for s in pending), return_exceptions=True)

    async def _connect_safely(
        self,
        server: dict[str, Any],
        enable_browser_flow: bool = False,
        forced_connection: dict[str, Any] | None = None,
    ) -> _ServerRuntime | None:
        server_id = server["id"]

        # The MCP SDK swallows a 401 behind a broken teardown ("cancel scope in
        # a different task"), so auth failures never surface as a clean error.
        # Probe the endpoint directly to detect authentication needs reliably.
        # The probe reuses the same OAuth wiring as the real connect so a stored
        # token is attached (an authorized server must not read as needs_auth).
        wired: tuple[dict[str, Any], LoopbackCallbackServer | None] | None = None
        if not enable_browser_flow:
            try:
                probe_conn = forced_connection if forced_connection is not None else build_connection(server)
                probe_conn, loopback = await self._wire_auth(
                    server_id, server, probe_conn, enable_browser_flow=False
                )
                wired = (probe_conn, loopback)
                probe = await self._probe_remote_auth(server, probe_conn)
                if probe:
                    if loopback is not None:
                        await loopback.close()
                    await asyncio.to_thread(
                        self.mcp_manager.update_server_status,
                        server_id,
                        probe,
                        "Authentication required",
                        0,
                        [],
                    )
                    return None
            except BaseException:  # noqa: BLE001 - probe is best-effort
                wired = None

        try:
            rt = await self._connect_one(
                server, enable_browser_flow, forced_connection, wired=wired
            )
        except BaseException as exc:  # noqa: BLE001 - one bad server must not affect others
            if _classify_auth_error(exc):
                status, error = STATUS_NEEDS_AUTH, _friendly_error(exc, server.get("transport", ""))
            else:
                status, error = STATUS_ERROR, _friendly_error(exc, server.get("transport", ""))
            logger.info("MCP server %s not available: %s", server_id, error)
            await asyncio.to_thread(
                self.mcp_manager.update_server_status,
                server_id,
                status,
                error,
                0,
                [],
            )
            return None
        await asyncio.to_thread(
            self.mcp_manager.update_server_status,
            server_id,
            STATUS_CONNECTED,
            "",
            len(rt.tools),
            [{"name": getattr(t, "name", ""), "description": getattr(t, "description", "") or ""} for t in rt.tools],
        )
        return rt

    async def _probe_remote_auth(
        self, server: dict[str, Any], connection: dict[str, Any]
    ) -> str | None:
        """Detect ``401``/``403`` for a remote endpoint before connecting.

        Returns ``STATUS_NEEDS_AUTH`` when the server rejects the request for
        authentication, otherwise ``None`` (reachable or transport error --
        let the real connect classify those).
        """
        transport = connection.get("transport")
        if transport not in _REMOTE_TRANSPORTS:
            return None
        url = connection.get("url")
        if not url:
            return None

        headers = dict(connection.get("headers") or {})
        auth = connection.get("auth")
        timeout = httpx.Timeout(self._connect_timeout, read=8)
        status = 0
        try:
            if transport == "sse":
                async with httpx.AsyncClient(headers=headers, auth=auth, timeout=timeout) as client:
                    async with client.stream("GET", url) as resp:
                        status = resp.status_code
            else:
                headers.setdefault("Accept", "application/json, text/event-stream")
                headers.setdefault("Content-Type", "application/json")
                headers.setdefault("MCP-Protocol-Version", "2025-06-18")
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "coworker", "version": "1.0"},
                        },
                    }
                )
                async with httpx.AsyncClient(headers=headers, auth=auth, timeout=timeout) as client:
                    resp = await client.post(url, content=body)
                    status = resp.status_code
        except Exception:  # noqa: BLE001 - probe is best-effort
            return None
        if status in (401, 403):
            return STATUS_NEEDS_AUTH
        return None

    async def _connect_one(
        self,
        server: dict[str, Any],
        enable_browser_flow: bool = False,
        forced_connection: dict[str, Any] | None = None,
        wired: tuple[dict[str, Any], LoopbackCallbackServer | None] | None = None,
    ) -> _ServerRuntime:
        server_id = server["id"]
        existing = self._servers.pop(server_id, None)
        if existing is not None:
            await existing.close()

        connection = forced_connection if forced_connection is not None else build_connection(server)
        stack = contextlib.AsyncExitStack()
        try:
            if wired is not None:
                connection, loopback = wired
            elif forced_connection is None:
                connection, loopback = await self._wire_auth(
                    server_id, server, connection, enable_browser_flow
                )
            else:
                loopback = None
            session = await stack.enter_async_context(create_session(connection))
            init = await session.initialize()
            server_info = getattr(init, "serverInfo", None)
            server_name = (
                getattr(server_info, "name", "") or server.get("name", "") or server_id
            )

            listed = await session.list_tools()
            tools: list[Any] = []
            for mcp_tool in listed.tools or []:
                bare = mcp_tool.name
                tools.append(_make_dispatch_tool(server_id, bare, mcp_tool, self))
            tools = self._apply_policy(server, tools)

            rt = _ServerRuntime(server_id, session, stack, connection)
            rt.server_name = server_name
            rt.tools = tools
            rt.loopback = loopback
            self._servers[server_id] = rt
            return rt
        except BaseException as exc:
            # Best-effort teardown. The MCP SDK can raise a spurious "cancel
            # scope in a different task" error while unwinding a failed
            # session; that must never mask the real connect error.
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("MCP teardown noise after failed connect to %s", server_id)
            raise exc

    async def _wire_auth(
        self,
        server_id: str,
        server: dict[str, Any],
        connection: dict[str, Any],
        enable_browser_flow: bool,
    ) -> tuple[dict[str, Any], LoopbackCallbackServer | None]:
        """Attach an OAuth ``httpx.Auth`` for remote servers when appropriate."""
        if connection.get("transport") not in _REMOTE_TRANSPORTS:
            return connection, None
        headers = connection.get("headers") or {}
        if any(str(k).lower() == "authorization" for k in headers):
            return connection, None

        token_path = self._oauth_dir / f"{server_id}.json"
        if not enable_browser_flow and not token_path.exists():
            return connection, None

        loopback = LoopbackCallbackServer()
        await loopback.start()
        storage = FileTokenStorage(token_path)
        provider = build_oauth_provider(
            server["url"],
            storage,
            loopback,
            self.open_browser,
            enable_browser_flow=enable_browser_flow,
        )
        connection["auth"] = provider
        return connection, loopback

    # ── tool access ──────────────────────────────────────────────────────

    def all_tools(self) -> list[Any]:
        tools: list[Any] = []
        for rt in list(self._servers.values()):
            tools.extend(rt.tools)
        return tools

    def tool_names(self) -> set[str]:
        names: set[str] = set()
        for rt in list(self._servers.values()):
            for tool in rt.tools:
                name = getattr(tool, "name", None)
                if name:
                    names.add(str(name))
        return names

    # ── tool invocation ──────────────────────────────────────────────────

    async def call_tool(self, server_id: str, name: str, args: dict[str, Any]) -> Any:
        """Invoke an MCP tool from an async context (agent's event loop)."""
        return await self._bridge_async(lambda: self._call_with_retry(server_id, name, args))

    def call_tool_sync(self, server_id: str, name: str, args: dict[str, Any]) -> Any:
        """Invoke an MCP tool from a sync context (non-streaming agent path)."""
        return self.run_sync(
            lambda: self._call_with_retry(server_id, name, args),
            timeout=self._call_timeout + self._connect_timeout,
        )

    async def _call_with_retry(self, server_id: str, name: str, args: dict[str, Any]) -> Any:
        try:
            return await self._call_once(server_id, name, args)
        except McpSessionClosedError:
            logger.info("MCP session for %s closed; reconnecting and retrying %r", server_id, name)
            try:
                await self._reconnect_async(server_id, enable_browser_flow=False)
            except Exception:  # noqa: BLE001 - reconnect failure surfaces on retry
                logger.warning("MCP reconnect failed for %s", server_id)
            return await self._call_once(server_id, name, args)

    async def _call_once(self, server_id: str, name: str, args: dict[str, Any]) -> Any:
        rt = self._servers.get(server_id)
        if rt is None or rt.session is None:
            raise McpSessionClosedError(f"No session for {server_id}")
        try:
            return await rt.session.call_tool(
                name,
                args,
                read_timeout_seconds=timedelta(seconds=self._call_timeout),
            )
        except Exception as exc:  # noqa: BLE001
            if _is_transport_error(exc):
                raise McpSessionClosedError(f"Session for {server_id} closed") from exc
            raise

    def reconnect(self, server_id: str, enable_browser_flow: bool = False) -> None:
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._reconnect_async(server_id, enable_browser_flow), self._loop
            )
            fut.result(timeout=self._connect_timeout + 10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP reconnect failed for %s: %s", server_id, exc)

    async def _reconnect_async(self, server_id: str, enable_browser_flow: bool) -> None:
        try:
            server = self.mcp_manager.get_runtime_config(server_id)
        except Exception as exc:  # noqa: BLE001
            raise McpSessionUnavailable(server_id) from exc
        await self._connect_safely(server, enable_browser_flow=enable_browser_flow)

    # ── reauthorize (OAuth) ──────────────────────────────────────────────

    def reauthorize(self, server_id: str, timeout: float = 360.0) -> dict[str, Any]:
        """Run the OAuth browser flow for a server and (re)connect it.

        Returns a flat ``{ok, error, needs_auth, server}`` dict.
        """
        if self._loop is None:
            raise RuntimeError("McpSessionManager is not started")
        fut = asyncio.run_coroutine_threadsafe(self._reauthorize_async(server_id), self._loop)
        return fut.result(timeout=timeout)

    async def _reauthorize_async(self, server_id: str) -> dict[str, Any]:
        try:
            server = self.mcp_manager.get_runtime_config(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "needs_auth": False, "server": None}

        existing = self._servers.pop(server_id, None)
        if existing is not None:
            await existing.close()

        connection = build_connection(server)
        loopback = LoopbackCallbackServer()
        await loopback.start()
        try:
            storage = FileTokenStorage(self._oauth_dir / f"{server_id}.json")
            connection["auth"] = build_oauth_provider(
                server["url"], storage, loopback, self.open_browser, enable_browser_flow=True
            )
            rt = await self._connect_one(server, enable_browser_flow=True, forced_connection=connection)
        except Exception as exc:  # noqa: BLE001
            error = _friendly_error(exc, server.get("transport", ""))
            needs_auth = _classify_auth_error(exc)
            await asyncio.to_thread(
                self.mcp_manager.update_server_status, server_id, STATUS_NEEDS_AUTH if needs_auth else STATUS_ERROR, error, 0, []
            )
            return {
                "ok": False,
                "error": error,
                "needs_auth": needs_auth,
                "server": await asyncio.to_thread(self.mcp_manager.get_server, server_id),
            }
        finally:
            await loopback.close()

        await asyncio.to_thread(
            self.mcp_manager.update_server_status,
            server_id,
            STATUS_CONNECTED,
            "",
            len(rt.tools),
            [{"name": getattr(t, "name", ""), "description": getattr(t, "description", "") or ""} for t in rt.tools],
        )
        return {
            "ok": True,
            "error": "",
            "needs_auth": False,
            "server": await asyncio.to_thread(self.mcp_manager.get_server, server_id),
        }

    # ── teardown of a single server ──────────────────────────────────────

    def close_server(self, server_id: str) -> None:
        """Drop the session for one server (disable/delete/update)."""
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._close_server_async(server_id), self._loop)
            fut.result(timeout=10)
        except Exception:  # noqa: BLE001
            logger.debug("close_server(%s) failed", server_id)

    async def _close_server_async(self, server_id: str) -> None:
        rt = self._servers.pop(server_id, None)
        if rt is not None:
            await rt.close()

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _apply_policy(server: dict[str, Any], tools: list[Any]) -> list[Any]:
        trusted = bool(server.get("trusted"))
        raw = server.get("disabled_tools") or []
        disabled = {str(item).strip() for item in raw if str(item).strip()}
        if trusted or not disabled:
            return tools
        return [t for t in tools if getattr(t, "name", None) not in disabled]


def _friendly_error(exc: BaseException, transport: str = "") -> str:
    """Turn raw adapter/SDK exceptions into something a user can act on."""
    leaves = _flatten_exceptions(exc)
    if any(isinstance(leaf, TimeoutError) for leaf in leaves):
        return "Connection timed out"
    # Prefer the most actionable leaf: a nested HTTPStatusError (e.g. 401/403).
    for leaf in leaves:
        if isinstance(leaf, FileNotFoundError):
            return f"Command not found: {leaf}"
        status_error = getattr(leaf, "response", None)
        if status_error is not None and getattr(status_error, "status_code", None):
            return f"Authentication required (401)" if status_error.status_code == 401 else f"HTTP {status_error.status_code}: {leaf}"
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "no such file or directory" in lowered:
        return f"Command not found: {text}"
    if "unauthorized" in lowered or "401" in lowered:
        return f"Authentication required (401): {text}"
    if "403" in lowered:
        return f"Access denied (403): {text}"
    if "404" in lowered:
        return f"Endpoint not found (404) -- check the URL: {text}"
    if transport == "sse" and "text/event-stream" in lowered:
        return f"Server did not return an SSE stream -- try HTTP transport: {text}"
    if len(text) > 300:
        text = text[:297] + "..."
    return text
