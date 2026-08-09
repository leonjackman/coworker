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

# Providers reject tool names outside ``^[a-zA-Z0-9_-]{1,128}$``; namespaced
# names must stay inside that budget.
MAX_TOOL_NAME_LEN = 128

# How long a ``trusted`` flag read from disk stays valid. The flag can change
# without a reconnect (the UI toggles it), so the approval path re-reads it,
# but not on every single call.
_TRUST_CACHE_TTL = 2.0


def _slug(value: str) -> str:
    """Sanitize a server id into a tool-name-safe prefix."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(value or ""))
    cleaned = cleaned.strip("_-")
    return cleaned or "mcp"


def _annotation_flags(mcp_tool: Any) -> dict[str, Any]:
    """Extract MCP ``ToolAnnotations`` into plain JSON-safe flags.

    ``None`` means "the server did not declare it". Callers apply the spec
    defaults themselves (``readOnlyHint=false`` / ``destructiveHint=true``) so
    an undeclared tool is treated as potentially dangerous.
    """
    annotations = getattr(mcp_tool, "annotations", None)

    def flag(attr: str) -> bool | None:
        if annotations is None:
            return None
        value = getattr(annotations, attr, None)
        return bool(value) if value is not None else None

    title = getattr(annotations, "title", None) if annotations is not None else None
    return {
        "read_only": flag("readOnlyHint"),
        "destructive": flag("destructiveHint"),
        "idempotent": flag("idempotentHint"),
        "open_world": flag("openWorldHint"),
        "title": str(title) if title else None,
    }


def tool_is_read_only(flags: dict[str, Any] | None) -> bool:
    """Spec-default-aware read-only test (undeclared == not read-only)."""
    if not flags:
        return False
    return flags.get("read_only") is True


def mcp_approval_digest(server_id: str, remote_name: str) -> str:
    """Stable allowlist key for "always allow this MCP tool"."""
    return f"mcp:{server_id}:{remote_name}"


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


async def _close_quietly(closable: Any) -> None:
    """Best-effort ``await closable.close()`` that never propagates."""
    try:
        await closable.close()
    except Exception:  # noqa: BLE001 - teardown is best-effort
        logger.debug("Quiet close raised for %r", closable)


class _ServerRuntime:
    """One persistent session + its dispatch tools."""

    __slots__ = (
        "server_id",
        "server_name",
        "session",
        "connection",
        "tools",
        "raw_tools",
        "loopback",
        "_stop",
        "_owner",
    )

    def __init__(
        self,
        server_id: str,
        session: Any,
        connection: dict[str, Any],
        stop: asyncio.Event,
        owner: asyncio.Task,
    ) -> None:
        self.server_id = server_id
        self.server_name = ""
        self.session = session
        self.connection = connection
        # The transport/session context is owned by ``_owner`` and must be
        # exited in that same task -- anyio cancel scopes are task-bound, and
        # closing from another task raises "Attempted to exit cancel scope in a
        # different task". ``close()`` therefore only signals and awaits.
        self._stop = stop
        self._owner = owner
        # ``raw_tools`` are the MCP tool descriptors as advertised by the
        # server (post user-disable filter). ``tools`` are the LangChain
        # dispatch tools built from them, with cross-server name conflicts
        # already resolved -- see :meth:`McpSessionManager._rebuild_tools`.
        self.raw_tools: list[Any] = []
        self.tools: list[Any] = []
        self.loopback: LoopbackCallbackServer | None = None

    async def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        owner = self._owner
        if owner is not None and not owner.done():
            try:
                await asyncio.wait_for(asyncio.shield(owner), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                logger.debug("MCP session %s did not close in time; cancelling", self.server_id)
                owner.cancel()
                try:
                    await asyncio.wait([owner], timeout=2.0)
                except Exception:  # noqa: BLE001 - teardown is best-effort
                    pass
            except Exception:  # noqa: BLE001 - teardown is best-effort
                logger.debug("MCP session teardown raised for %s", self.server_id)
        if self.loopback is not None:
            await self.loopback.close()
            self.loopback = None
        self.session = None
        self.tools = []


def _make_dispatch_tool(
    server_id: str,
    exposed_name: str,
    remote_name: str,
    mcp_tool: Any,
    manager: "McpSessionManager",
    flags: dict[str, Any] | None = None,
) -> StructuredTool:
    """Build a session-agnostic dispatch tool for one MCP tool.

    Provides both a sync and an async entrypoint (``func`` + ``coroutine``) so
    the tool works in the non-streaming ``graph.invoke`` path and the streaming
    ``astream`` path alike. Reuses the adapter's result conversion and error
    formatting so the model sees exactly the same content blocks / error
    ToolMessages as the stock ``langchain-mcp-adapters`` integration.

    ``exposed_name`` is what the model sees (possibly namespaced to avoid a
    cross-server collision); ``remote_name`` is what is sent over the wire.
    """
    from langchain_mcp_adapters.tools import _convert_call_tool_result, _handle_mcp_tool_error

    def _run_dispatch(**kwargs: Any) -> Any:
        result = manager.call_tool_sync(server_id, remote_name, kwargs)
        return _convert_call_tool_result(result)

    async def _arun_dispatch(**kwargs: Any) -> Any:
        result = await manager.call_tool(server_id, remote_name, kwargs)
        return _convert_call_tool_result(result)

    description = getattr(mcp_tool, "description", None) or ""
    if exposed_name != remote_name:
        # Keep the original identity discoverable by the model.
        description = f"[{remote_name}] {description}".strip()

    return StructuredTool(
        name=exposed_name,
        description=description,
        args_schema=mcp_tool.inputSchema,
        func=_run_dispatch,
        coroutine=_arun_dispatch,
        response_format="content_and_artifact",
        metadata={
            "coworker_server": server_id,
            "coworker_mcp": True,
            "coworker_remote_name": remote_name,
            "coworker_annotations": dict(flags or {}),
        },
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
        # exposed tool name -> policy record (see :meth:`tool_policy`)
        self._policies: dict[str, dict[str, Any]] = {}
        # bare tool name -> server ids that all advertise it
        self._conflicts: dict[str, list[str]] = {}
        self._trust_cache: dict[str, tuple[float, bool]] = {}
        # Servers whose exposed tool names changed and whose persisted status
        # still has to be rewritten (drained by ``_refresh_statuses``).
        self._pending_status_refresh: set[str] = set()
        # Background prewarm task, tracked so shutdown can cancel it instead of
        # racing an in-flight `stdio_client` handshake (which leaves suspended
        # async generators the loop can no longer finalize).
        self._prewarm_task: asyncio.Task | None = None
        self._closing = False
        # Every live session-owner task (see ``_own_session``). Tracked at the
        # manager level because a connect that is itself being cancelled cannot
        # reliably await its own cleanup -- shutdown drains this set instead.
        self._owner_tasks: set[asyncio.Task] = set()

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
                # Bounded: a transport whose teardown is wedged must not keep
                # the loop thread (and therefore app exit) alive forever.
                loop.run_until_complete(
                    asyncio.wait_for(loop.shutdown_asyncgens(), timeout=5.0)
                )
            except BaseException:  # noqa: BLE001 - teardown is best-effort
                pass
            loop.close()
            self._loop = None

    def shutdown(self) -> None:
        """Close every session and stop the loop thread."""
        if self._loop is None:
            return
        self._closing = True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
            # Budget: drain connects (5s) + drain owners (8s + 3s) + runtime
            # closes, each individually bounded.
            fut.result(timeout=30)
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
        self._closing = True
        # Let an in-flight prewarm/connect unwind first. Closing a runtime whose
        # `stdio_client` handshake is still suspended leaves an async generator
        # the loop can never finalize ("aclose(): already running" at exit).
        await self._drain_pending_connects()
        runtimes = list(self._servers.values())
        self._servers.clear()
        self._policies.clear()
        self._conflicts.clear()
        self._trust_cache.clear()
        await asyncio.gather(*(rt.close() for rt in runtimes), return_exceptions=True)
        # Sessions abandoned mid-handshake never reached a `_ServerRuntime`;
        # collect them here so no transport generator survives the loop.
        await self._drain_owner_tasks()

    async def _drain_owner_tasks(self, timeout: float = 8.0) -> None:
        """Let every session owner unwind, cancelling only as a last resort."""
        tasks = [t for t in list(self._owner_tasks) if not t.done()]
        if not tasks:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=timeout)
        stragglers = [t for t in tasks if not t.done()]
        if not stragglers:
            return
        logger.debug("Cancelling %d MCP session owner(s) that did not stop", len(stragglers))
        for task in stragglers:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait(stragglers, timeout=3.0)

    async def _drain_pending_connects(self, timeout: float = 5.0) -> None:
        """Wait for (or cancel) connects that are still in flight."""
        pending = [fut for fut in self._connecting.values() if not fut.done()]
        task = self._prewarm_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            pending.append(task)
        if not pending:
            return
        try:
            await asyncio.wait(pending, timeout=timeout)
        except Exception:  # noqa: BLE001 - teardown is best-effort
            logger.debug("MCP connect drain raised during shutdown")

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
        if self._loop is None or self._closing:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._prewarm_async(), self._loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to schedule MCP prewarm: %s", exc)

    async def _prewarm_async(self) -> None:
        await asyncio.sleep(0)
        if self._closing:
            return
        # Tracked so `_shutdown_async` can cancel an in-flight handshake before
        # tearing sessions down (quitting during prewarm otherwise leaves the
        # transport's async generators suspended).
        self._prewarm_task = asyncio.current_task()
        try:
            await self._ensure_connected_async(enable_browser_flow=False)
        except asyncio.CancelledError:
            logger.debug("MCP prewarm cancelled by shutdown")
        except BaseException as exc:  # noqa: BLE001
            logger.warning("MCP prewarm failed: %s", _friendly_error(exc))
        finally:
            self._prewarm_task = None

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
        if self._closing:
            return
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
        await self._refresh_statuses({server_id})
        return rt

    async def _refresh_statuses(self, server_ids: set[str]) -> None:
        """Persist the *exposed* tool list for the given connected servers.

        Namespacing can rename another server's tools, so the stored status has
        to be refreshed for every server whose exposed names changed -- not
        only the one that just connected.
        """
        targets = set(server_ids) | self._pending_status_refresh
        self._pending_status_refresh.clear()
        for server_id in sorted(targets):
            rt = self._servers.get(server_id)
            if rt is None:
                continue
            await asyncio.to_thread(
                self.mcp_manager.update_server_status,
                server_id,
                STATUS_CONNECTED,
                "",
                len(rt.tools),
                [
                    {"name": getattr(t, "name", ""), "description": getattr(t, "description", "") or ""}
                    for t in rt.tools
                ],
            )

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
        if wired is not None:
            connection, loopback = wired
        elif forced_connection is None:
            connection, loopback = await self._wire_auth(
                server_id, server, connection, enable_browser_flow
            )
        else:
            loopback = None

        # The transport context is opened and closed inside a single dedicated
        # task. anyio (used by the MCP SDK's http/sse transports) pins cancel
        # scopes to the task that entered them, so tearing the stack down from
        # the shutdown task raises "Attempted to exit cancel scope in a
        # different task" and leaks the connection.
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        owner = asyncio.ensure_future(
            self._own_session(server_id, connection, ready, stop)
        )
        self._owner_tasks.add(owner)
        owner.add_done_callback(self._owner_tasks.discard)

        def abandon() -> None:
            """Release the half-built session without awaiting it.

            Only *signals* the owner: cancelling it would abort the transport's
            shutdown sequence mid-await and orphan the child process. This runs
            on paths where the current task may itself be under cancellation, so
            it must stay synchronous; ``_drain_owner_tasks`` collects the owner
            during shutdown.
            """
            stop.set()
            if loopback is not None:
                asyncio.ensure_future(_close_quietly(loopback))

        try:
            # A server that opens but never speaks MCP would otherwise pin this
            # connect (and its child process) forever.
            session = await asyncio.wait_for(ready, timeout=self._connect_timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            abandon()
            raise TimeoutError(
                f"MCP handshake timed out after {self._connect_timeout:.0f}s"
            ) from exc
        except BaseException as exc:
            abandon()
            raise exc

        try:
            init = await session.initialize()
            server_info = getattr(init, "serverInfo", None)
            server_name = (
                getattr(server_info, "name", "") or server.get("name", "") or server_id
            )
            listed = await session.list_tools()
            raw_tools = self._apply_policy(server, list(listed.tools or []))
        except BaseException as exc:
            abandon()
            raise exc

        rt = _ServerRuntime(server_id, session, connection, stop, owner)
        rt.server_name = server_name
        rt.raw_tools = raw_tools
        rt.loopback = loopback
        self._servers[server_id] = rt
        # Cross-server name conflicts can only be resolved globally, so the
        # exposed tool list is (re)built for every connected server here.
        self._rebuild_tools()
        return rt

    async def _own_session(
        self,
        server_id: str,
        connection: dict[str, Any],
        ready: asyncio.Future,
        stop: asyncio.Event,
    ) -> None:
        """Hold one MCP session open for its whole lifetime, in one task.

        Resolves ``ready`` with the live session (or the connect error) and then
        parks on ``stop`` so the ``async with`` unwinds in this same task.
        """
        import anyio

        stack = contextlib.AsyncExitStack()
        try:
            session = await stack.enter_async_context(create_session(connection))
            if not ready.done():
                ready.set_result(session)
            await stop.wait()
        except BaseException as exc:  # noqa: BLE001 - surfaced through `ready`
            if not ready.done():
                ready.set_exception(exc)
            else:
                logger.debug("MCP session %s ended: %s", server_id, exc)
        finally:
            # Shielded: the stdio transport's shutdown sequence (close stdin ->
            # wait -> SIGTERM -> SIGKILL) is itself a sequence of awaits. If the
            # task is being cancelled, an unshielded teardown aborts at the
            # first await, orphaning the child process -- which then blocks
            # interpreter exit on asyncio's non-daemon waitpid thread.
            with anyio.CancelScope(shield=True):
                try:
                    await stack.aclose()
                except BaseException:  # noqa: BLE001 - teardown is best-effort
                    logger.debug("MCP session %s teardown raised", server_id)

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

    # ── tool exposure (namespacing + policy) ─────────────────────────────

    def _rebuild_tools(self) -> set[str]:
        """Rebuild every connected server's exposed tool list.

        Two servers advertising the same bare tool name (``search``, ``query``
        ...) used to silently shadow each other: whichever loaded first won and
        the rest vanished with only a debug log. Here every colliding name is
        namespaced as ``<server-slug>__<tool>`` for *all* participants, so the
        outcome does not depend on connection order and no tool is ever lost.

        Returns the ids of servers whose exposed tool names changed.
        """
        runtimes = list(self._servers.values())

        counts: dict[str, int] = {}
        for rt in runtimes:
            for mcp_tool in rt.raw_tools:
                bare = str(getattr(mcp_tool, "name", "") or "")
                if bare:
                    counts[bare] = counts.get(bare, 0) + 1

        conflicts: dict[str, list[str]] = {}
        policies: dict[str, dict[str, Any]] = {}
        changed: set[str] = set()
        used: set[str] = set()

        for rt in runtimes:
            before = tuple(getattr(t, "name", "") for t in rt.tools)
            built: list[Any] = []
            for mcp_tool in rt.raw_tools:
                bare = str(getattr(mcp_tool, "name", "") or "")
                if not bare:
                    continue
                if counts.get(bare, 0) > 1:
                    conflicts.setdefault(bare, []).append(rt.server_id)
                    exposed = self._namespaced(rt.server_id, bare)
                else:
                    exposed = bare
                exposed = self._deduplicate(exposed, used)
                used.add(exposed)

                flags = _annotation_flags(mcp_tool)
                built.append(
                    _make_dispatch_tool(rt.server_id, exposed, bare, mcp_tool, self, flags)
                )
                policies[exposed] = {
                    "tool": exposed,
                    "remote_name": bare,
                    "server_id": rt.server_id,
                    "server_name": rt.server_name or rt.server_id,
                    "annotations": flags,
                    "read_only": tool_is_read_only(flags),
                    "digest": mcp_approval_digest(rt.server_id, bare),
                }
            rt.tools = built
            if tuple(getattr(t, "name", "") for t in built) != before:
                changed.add(rt.server_id)

        self._policies = policies
        self._conflicts = conflicts
        self._pending_status_refresh |= changed
        if conflicts:
            logger.info(
                "MCP tool name conflicts namespaced: %s",
                ", ".join(f"{name} ({len(ids)} servers)" for name, ids in conflicts.items()),
            )
        return changed

    @staticmethod
    def _namespaced(server_id: str, bare: str) -> str:
        prefix = _slug(server_id)
        budget = MAX_TOOL_NAME_LEN - len(bare) - 2
        if budget < 1:
            # Pathologically long tool name: truncate the tool part instead.
            return f"{prefix[:16]}__{bare}"[:MAX_TOOL_NAME_LEN]
        return f"{prefix[:budget]}__{bare}"

    @staticmethod
    def _deduplicate(name: str, used: set[str]) -> str:
        """Guarantee uniqueness even if namespacing itself collides."""
        if name not in used:
            return name
        for index in range(2, 100):
            candidate = f"{name}_{index}"[:MAX_TOOL_NAME_LEN]
            if candidate not in used:
                return candidate
        return f"{name}_{len(used)}"[:MAX_TOOL_NAME_LEN]

    def list_conflicts(self) -> dict[str, list[str]]:
        """Bare tool names advertised by more than one connected server."""
        return {name: list(ids) for name, ids in self._conflicts.items()}

    def tool_policy(self, name: str) -> dict[str, Any] | None:
        """Approval-relevant metadata for an exposed MCP tool name.

        Returns ``None`` for non-MCP (builtin) tools, which lets callers use it
        as an "is this an MCP tool?" test as well.
        """
        base = self._policies.get(str(name or ""))
        if base is None:
            return None
        return {**base, "trusted": self._server_trusted(base["server_id"])}

    def _server_trusted(self, server_id: str) -> bool:
        """Read the ``trusted`` flag with a short TTL cache.

        The flag is toggled from the UI without a reconnect, so it cannot be
        snapshotted at connect time; the cache just keeps the approval path
        from re-reading the config file on every tool call.
        """
        now = time.monotonic()
        cached = self._trust_cache.get(server_id)
        if cached is not None and now - cached[0] < _TRUST_CACHE_TTL:
            return cached[1]
        trusted = False
        try:
            trusted = bool(self.mcp_manager.get_server(server_id).get("trusted"))
        except Exception:  # noqa: BLE001 - a missing/broken config is "not trusted"
            trusted = False
        self._trust_cache[server_id] = (now, trusted)
        return trusted

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

        await self._refresh_statuses({server_id})
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
        self._trust_cache.pop(server_id, None)
        if rt is not None:
            await rt.close()
        # Removing a server can un-conflict names that were namespaced.
        changed = self._rebuild_tools()
        await self._refresh_statuses(changed)

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _apply_policy(server: dict[str, Any], tools: list[Any]) -> list[Any]:
        """Drop tools the user explicitly disabled.

        ``trusted`` deliberately does **not** override this. Trust means "I do
        not want an approval prompt for this server" (the industry semantic);
        it must never silently re-enable a tool the user switched off.
        """
        raw = server.get("disabled_tools") or []
        disabled = {str(item).strip() for item in raw if str(item).strip()}
        if not disabled:
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
