# -*- coding: utf-8 -*-

import asyncio
import atexit
import os
import re
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from coworker.agent.runtime import AgentRuntimeRegistry
from coworker.config import load_settings
from coworker.config_controller import AppConfigController
from coworker.events import WorkerEventBus, session_event_bus, worker_event_bus
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID, ProjectStore
from coworker.providers import ProviderManager
from coworker.mcp.mcp import McpManager
from coworker.mcp.mcp_session import McpSessionManager
from coworker.sessions import SessionStore, _now
from coworker.skills.skill_manager import SkillManager
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from coworker.org import (
    AGENT_STATUS_ACTIVE,
    ORG_MODE_MULTI,
    ORG_MODE_SINGLE,
    ORG_MODES,
    Org,
    OrgAgent,
    OrgError,
    OrgStore,
    OrgTeam,
)
from coworker.traces import AGENT_TRACE_FILENAME, MAX_TRACE_LINES
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.workspace_controller import WorkspaceController
from coworker.logger import apply_log_config, current_session_id, get_logger, get_log_settings as _runtime_log_settings, init_logger, is_sensitive_key, redact, set_log_level as _set_log_level, truncate_log as _truncate_log

def _lazy_api(mod_name: str, symbol: str):
    import importlib
    mod = importlib.import_module(f"coworker.api.{mod_name}")
    return getattr(mod, symbol)


try:
    import fcntl
    import pty
    import termios

    _PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - non-POSIX platforms (e.g. Windows)
    pty = None  # type: ignore[assignment]
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    _PTY_AVAILABLE = False
try:
    import ssl

    import certifi

    _orig_create_default_context = ssl.create_default_context

    def _create_default_context_with_certifi(*args, **kwargs):
        kwargs.setdefault("cafile", certifi.where())
        return _orig_create_default_context(*args, **kwargs)

    ssl.create_default_context = _create_default_context_with_certifi
    ssl._create_default_https_context = ssl.create_default_context
except Exception:  # pragma: no cover - certifi is a hard dependency
    pass
_HTTP_LOG_ENABLED = os.getenv("COWORKER_HTTP_LOG", "1").strip().lower() not in {"0", "false", "no", "off"}
_HTTP_LOG_SKIP_PATHS = frozenset({"/health", "/settings/log-file", "/favicon.ico"})
http_logger = get_logger("http")
def _masked_query(query: str) -> str:
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=True)
    except Exception:  # noqa: BLE001 - never let a malformed query break logging
        return query
    parts: list[str] = []
    for key, value in pairs:
        if is_sensitive_key(key):
            parts.append(f"{key}=***")
        else:
            parts.append(f"{key}={value}")
    return "&".join(parts)
def _request_session_id(path: str, query: str) -> str:
    """Best-effort session id for a request, for log correlation.

    Sources, in order: a ``session_id`` query/body param seen in the query string,
    or the ``/sessions/{session_id}[/...]`` path segment. Empty when absent.
    """
    try:
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key == "session_id" and value:
                return value
    except Exception:  # noqa: BLE001
        pass
    match = re.match(r"^/sessions/([^/]+)", path)
    if match:
        return match.group(1)
    return ""
def set_http_log(enabled: bool) -> None:
    """Flip request logging on/off at runtime (no restart required)."""
    global _HTTP_LOG_ENABLED
    _HTTP_LOG_ENABLED = bool(enabled)
class HTTPRequestLogMiddleware:
    """Pure-ASGI middleware: one INFO record per HTTP request.

    Emits ``coworker.http`` lines with method, path (query masked), status,
    duration_ms and a per-request request_id. A logging hiccup must never break
    request handling, so the whole path is best-effort.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _HTTP_LOG_ENABLED:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "?")
        if path in _HTTP_LOG_SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        method = scope.get("method", "?")
        raw_query = scope.get("query_string", b"")
        if isinstance(raw_query, (bytes, bytearray)):
            raw_query = raw_query.decode("latin-1", errors="replace")
        masked_query = _masked_query(str(raw_query))
        session_id = _request_session_id(path, str(raw_query))
        # Correlate every app.log record emitted by this request with the session.
        token = current_session_id.set(session_id) if session_id else None
        started = time.perf_counter()
        status: Any = "?"

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status", status)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            try:
                suffix = f"?{masked_query}" if masked_query else ""
                sid = f" session_id={session_id}" if session_id else ""
                http_logger.info(
                    "%s %s%s -> %s %.1fms request_id=%s%s",
                    method, path, suffix, status, duration_ms, request_id, sid,
                )
            except Exception:  # noqa: BLE001 - logging must never break a request
                pass
            finally:
                if token is not None:
                    try:
                        current_session_id.reset(token)
                    except ValueError:
                        # A nested handler bound a newer session id without
                        # resetting (e.g. body-provided in /chat/stream); the
                        # per-request task context is discarded anyway.
                        pass
settings = load_settings()
logger = get_logger(__name__)
app = FastAPI()
app.add_middleware(HTTPRequestLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
log_path = init_logger(settings.data_dir, settings.log_level)
logger.info("Unified logger initialized: level=%s json=%d file=%s", settings.log_level, settings.json_log, log_path)
session_store = SessionStore(settings.data_dir / "sessions")
provider_manager = ProviderManager(settings.data_dir / "providers.json", settings.data_dir)
config_controller = AppConfigController(settings, provider_manager)
mcp_manager = McpManager(settings.data_dir / "mcp_servers.json")
mcp_sessions = McpSessionManager(settings.data_dir, mcp_manager)
skill_manager = SkillManager(settings.data_dir, settings.workspace_dir)
project_store = ProjectStore(settings.data_dir / "projects.json")
tool_audit_path = settings.data_dir / TOOL_AUDIT_FILENAME
command_approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
worker_event_bus.configure(settings.data_dir)
memory_manager = MemoryManager(
    settings.data_dir,
    memory_dir=settings.memory_dir,
    config=MemoryConfig(
        enabled=settings.memory_enabled,
        inject_char_limit=settings.memory_char_limit or MemoryConfig().inject_char_limit,
        auto_extract=settings.memory_auto_extract,
        nudge_interval=settings.memory_nudge_interval,
        extract_model=settings.memory_extract_model,
    ),
)
def _memory_project_name(memory_dir: str) -> str:
    """Resolve a project memory_dir to its real display name."""
    try:
        return next((p.name for p in project_store.list_projects() if p.memory_dir == memory_dir), "")
    except Exception:  # noqa: BLE001 - display-only, never break the scan
        return ""
memory_manager.scanner.project_name_resolver = _memory_project_name
org_store = OrgStore(memory_manager.root)
memory_manager.org_store = org_store
def _memory_agent_name(project_dir: str, agent_id: str) -> str:
    """Resolve an agent id to its display name from the project's org manifest."""
    try:
        org = org_store.load(project_dir)
        for member in org_store.members_for(org):
            if member["id"] == agent_id:
                return member["name"] or agent_id
    except Exception:  # noqa: BLE001 - display-only, never break the scan
        pass
    return agent_id
memory_manager.scanner.agent_name_resolver = _memory_agent_name
workspace_controller = WorkspaceController(
    project_store,
    session_store,
    settings.workspace_dir,
    settings.data_dir,
    org_store=org_store,
    chat_workspace_path=settings.data_dir / "chat",
)
agent_registry = AgentRuntimeRegistry(settings, session_store, mcp_session_manager=mcp_sessions, skill_manager=skill_manager, memory_manager=memory_manager, project_store=project_store, provider_manager=provider_manager)
mcp_sessions.start()
mcp_sessions.prewarm()
atexit.register(mcp_sessions.shutdown)
command_approval_store.prune()
trim_jsonl_file(tool_audit_path, MAX_TOOL_AUDIT_LINES)
trim_jsonl_file(settings.data_dir / AGENT_TRACE_FILENAME, MAX_TRACE_LINES)
worker_event_bus.prune_disk()
_legacy_orphan_log = settings.data_dir / "frontend_stream.log"
if _legacy_orphan_log.exists():
    try:
        _legacy_orphan_log.unlink()
    except OSError:
        pass
_checkpoint_sweep_task: asyncio.Task | None = None
_snapshot_gc_task: asyncio.Task | None = None
async def _checkpoint_sweep_loop() -> None:
    while True:
        await asyncio.sleep(settings.checkpoint_sweep_interval_seconds)
        try:
            stats = await asyncio.to_thread(agent_registry.checkpoint_manager.sweep)
            logger.info("checkpoint sweep: %s", stats)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("checkpoint sweep failed: %s", exc)
        try:
            prune_stats = await asyncio.to_thread(worker_event_bus.prune_disk)
            if prune_stats.get("removed"):
                logger.info("worker event disk prune: %s", prune_stats)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("worker event disk prune failed: %s", exc)
async def _snapshot_gc_loop() -> None:
    while True:
        await asyncio.sleep(settings.checkpoint_sweep_interval_seconds)
        try:
            stats = await asyncio.to_thread(agent_registry.snapshot_manager.gc)
            logger.info("snapshot gc: %s", stats)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("snapshot gc failed: %s", exc)
@app.on_event("startup")
async def _startup_checkpoint_maintenance() -> None:
    global _checkpoint_sweep_task
    # 系统保留「聊天」项目自愈：记录 + 沙箱文件夹 + memory scaffold 三者缺失
    # 都重建，保证应用启动后聊天项目始终存在（用户手动删除也无效）。
    try:
        _lazy_api("memory_org", "_ensure_chat_project")()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("chat project ensure failed: %s", exc)
    # Apply persisted logging overrides (level/rotation/json/http) now that the
    # whole module is loaded — init_logger() ran at import time with env defaults.
    try:
        _lazy_api("settings", "apply_stored_log_settings")()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("stored log settings apply failed: %s", exc)
    try:
        stats = await asyncio.to_thread(agent_registry.checkpoint_manager.sweep)
        logger.info("checkpoint sweep (startup): %s", stats)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("checkpoint sweep (startup) failed: %s", exc)
    _checkpoint_sweep_task = asyncio.create_task(_checkpoint_sweep_loop())
    global _snapshot_gc_task
    _snapshot_gc_task = asyncio.create_task(_snapshot_gc_loop())
    # Best-effort: tag already-installed skills (from before the provenance
    # feature) so their market cards show as "already installed". Retries until
    # the upstream market is reachable, then stops.
    asyncio.create_task(_lazy_api("skills", "_backfill_market_provenance_loop")())
@app.on_event("shutdown")
async def _stop_checkpoint_maintenance() -> None:
    global _checkpoint_sweep_task
    task = _checkpoint_sweep_task
    _checkpoint_sweep_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    global _snapshot_gc_task
    snap_task = _snapshot_gc_task
    _snapshot_gc_task = None
    if snap_task is not None:
        snap_task.cancel()
        try:
            await snap_task
        except (asyncio.CancelledError, Exception):
            pass
_stream_tasks: dict[str, asyncio.Task] = {}
def _invalidate_cached_runtimes(provider_id: str | None = None, *, default_runtimes: bool = False) -> None:
    """Best-effort eviction of cached session runtimes after provider writes.

    Provider edits aren't part of the runtime-cache key, so without this an
    existing session would keep chatting against the old provider snapshot
    (base_url / api_key / model / max tokens). Eviction only drops the cached
    compiled-graph entries; they rebuild from session history on the next turn.
    """
    try:
        dropped = 0
        if provider_id:
            dropped += agent_registry.invalidate_runtimes_for_provider(provider_id)
        if default_runtimes:
            dropped += agent_registry.invalidate_default_runtimes()
        if dropped:
            logger.info("provider config changed: invalidated %d cached runtime(s)", dropped)
    except Exception:  # noqa: BLE001 - never fail a config write over cache cleanup
        logger.warning("failed to invalidate cached runtimes after provider config change", exc_info=True)
