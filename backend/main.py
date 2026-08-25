import asyncio
import atexit
import json
import os
import time
from pathlib import Path
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlparse
from dataclasses import replace

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

from coworker.agent.core import (
    AgentMode,
    Language,
    context_budget_chars,
    context_budget_tokens,
    format_user_message,
    normalize_autonomy,
    normalize_work_mode,
    _runtime_context_budget,
)
from coworker.agent.runtime import AgentRuntimeRegistry
from coworker.platform import default_shell as _platform_default_shell
from coworker.config import load_settings
from coworker.config_controller import AppConfigController
from coworker.events import WorkerEventBus, session_event_bus, worker_event_bus
from coworker.projects import ProjectStore
from coworker.providers import ProviderManager
from coworker.mcp.mcp import McpManager
from coworker.mcp.mcp_session import McpSessionManager
from coworker.sessions import SessionStore
from coworker.goal_prompts import (
    is_degenerate_text,
    render_budget_limit,
    render_goal_continuation,
    render_objective_updated,
)
from coworker.steer import SteerEntry, steer_inbox
from coworker.skills.skill_manager import SkillManager
from coworker.skills.skills import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, MAX_SKILL_FILE_BYTES
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from coworker.memory.layout import AGENT_CORE_FILES, BASE_DIR, SYSTEM_FILES
from coworker.memory.transfer import apply_import, export_memory, preview_import
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
from coworker.web import (
    delete_tavily_key,
    get_tavily_key,
    read_web_block,
    set_tavily_key,
    tavily_key_configured,
    tavily_search,
    write_web_block,
)
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.workspace_controller import WorkspaceController
from coworker.logger import get_logger, get_log_level, init_logger, set_log_level as _set_log_level, truncate_log as _truncate_log

# ---------------------------------------------------------------------------
# HTTPS trust store fix for PyInstaller bundles.
#
# In a packaged app the embedded CPython's default CA paths point at the CI
# build machine (e.g. /Library/Frameworks/Python.framework/.../cert.pem),
# which does not exist on the user's machine. `ssl.create_default_context()`
# then yields an empty trust store and EVERY https:// request fails with
# CERTIFICATE_VERIFY_FAILED — breaking SkillHub/ClawHub (aiohttp) and all
# https LLM providers (urllib) while plain-http still works.
#
# certifi's cacert.pem IS bundled with the app, so point the default context
# at it. `setdefault` keeps any explicit `cafile`/`cadata` from callers.
# `_create_default_https_context` is a separate module-level alias used by
# http.client/urllib (bound at ssl import time to the original function), so
# it must be re-pointed too — otherwise aiohttp works but urllib still fails.
# ---------------------------------------------------------------------------
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

settings = load_settings()
logger = get_logger(__name__)
app = FastAPI()
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

# Initialize the unified logger subsystem BEFORE any other coworker module
# accesses its logger. This must happen after load_settings() so we have
# the data_dir, but before we instantiate any runtime that creates loggers.
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
# Persist worker sub-agent streams under <data_dir>/worker_events/ so a
# completed run can be replayed after the fact (and across restarts).
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
workspace_controller = WorkspaceController(project_store, session_store, settings.workspace_dir, settings.data_dir, org_store=org_store)
# The runtime checkpoint DB is a disposable per-turn scratch (single-writer
# model): every /chat/stream deletes the session's thread and rebuilds from the
# session history, so there is no "dirty checkpoint from an aborted turn" to
# guard against — see _guard_session_not_streaming / forget_runtime_checkpoint.
agent_registry = AgentRuntimeRegistry(settings, session_store, mcp_session_manager=mcp_sessions, skill_manager=skill_manager, memory_manager=memory_manager, project_store=project_store)

# Persistent MCP sessions: start the background loop, pre-warm connections so
# the first chat is instant, and tear sessions down on exit.
mcp_sessions.start()
mcp_sessions.prewarm()
atexit.register(mcp_sessions.shutdown)

# Run-observation retention: shrink pre-policy stores to the current caps so
# old approvals and append-only logs don't accumulate forever.
command_approval_store.prune()
trim_jsonl_file(tool_audit_path, MAX_TOOL_AUDIT_LINES)
trim_jsonl_file(settings.data_dir / AGENT_TRACE_FILENAME, MAX_TRACE_LINES)

# ---- Checkpoint lifecycle maintenance ----------------------------------- #
# The LangGraph runtime checkpoint DB grows unboundedly; a background sweep
# (startup + periodic) keeps it bounded via orphan cleanup, per-thread caps and
# incremental vacuum. Threads with an in-flight stream are skipped.

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


async def _snapshot_gc_loop() -> None:
    while True:
        await asyncio.sleep(settings.checkpoint_sweep_interval_seconds)
        try:
            stats = await asyncio.to_thread(agent_registry.snapshot_manager.gc)
            logger.info("snapshot gc: %s", stats)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("snapshot gc failed: %s", exc)


async def _tracked_stream(stream_iter: Any, session_id: str):
    """Mark a session active while its stream is consumed so the periodic
    sweep never trims a thread that is currently being written to."""
    agent_registry.checkpoint_manager.mark_active(session_id)
    # Register the task consuming this stream (the _sse_events producer) so a
    # hard stop — e.g. deleting the session mid-generation — can cancel it.
    _stream_tasks[session_id] = asyncio.current_task()
    try:
        async for event in stream_iter:
            yield event
    finally:
        agent_registry.checkpoint_manager.mark_idle(session_id)
        if _stream_tasks.get(session_id) is asyncio.current_task():
            _stream_tasks.pop(session_id, None)
        # Force-close the wrapped stream so a cancelled turn tears the provider
        # HTTP request down (see _sse_events producer's finally). Without this,
        # closing this generator alone leaves the inner graph.astream suspended
        # and generating to a dead socket.
        try:
            await stream_iter.aclose()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass


@app.on_event("startup")
async def _startup_checkpoint_maintenance() -> None:
    global _checkpoint_sweep_task
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
    asyncio.create_task(_backfill_market_provenance_loop())


# ---------------------------------------------------------------------------
# Market-provenance backfill
# ---------------------------------------------------------------------------
# Skills installed *before* the install-provenance feature exist on disk but
# carry no provenance record, so the market UI cannot tell they are installed
# (their market slug often differs from the local frontmatter name). This
# one-shot backfill searches the market for each installed skill and records the
# provenance when a confident (normalised) match is found. It is conservative:
# only exact normalised slug/name matches are accepted, to avoid false positives.


def _norm_market_key(value: str | None) -> str:
    return "".join(c for c in (value or "").lower() if c.isalnum())


async def _backfill_market_provenance_once() -> None:
    installed = list_skills()["skills"]
    provenance = skill_market_manager._load_provenance()
    for s in installed:
        name = s.get("name")
        if not name or name in provenance:
            continue
        target = _norm_market_key(name)
        if not target:
            continue
        hit: tuple[str, str | None, str | None] | None = None
        for source in ("skillhub", "clawhub"):
            try:
                page = await skill_market_manager.search(source, name, limit=5)
            except Exception:
                continue
            for sk in page.skills:
                if _norm_market_key(sk.get("slug")) == target or _norm_market_key(sk.get("name")) == target:
                    hit = (source, sk.get("slug"), sk.get("owner"))
                    break
            if hit:
                break
        if hit:
            skill_market_manager.record_install(hit[0], hit[1], hit[2], name)


async def _backfill_market_provenance_loop() -> None:
    while True:
        try:
            await _backfill_market_provenance_once()
            return
        except Exception as exc:  # network / upstream transient failures
            logger.warning("market provenance backfill deferred: %s", exc)
            await asyncio.sleep(120)



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


class ApprovalEventBus:
    """In-memory pub/sub that streams resume progress events to SSE subscribers.

    A small ring buffer per resume_id ensures subscribers that attach after the
    background resume task started still receive the events already published.
    """

    def __init__(self, buffer_size: int = 64) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._closed: set[str] = set()
        self._buffer_size = buffer_size

    def subscribe(self, resume_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        if resume_id in self._closed:
            # The resume already finished before this subscriber attached: hand
            # it an immediate stream_end so the SSE connection terminates instead
            # of hanging on heartbeats forever.
            queue.put_nowait({"type": "stream_end"})
            return queue
        self._subscribers[resume_id].append(queue)
        for event in list(self._buffer.get(resume_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        return queue

    def unsubscribe(self, resume_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(resume_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(resume_id, None)
        # Drain the queue to release queued events and prevent memory leak.
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def publish(self, resume_id: str, event: dict[str, Any]) -> None:
        """Publish an event to all subscribers for a given resume_id.

        Non-blocking: if any subscriber queue is full the OLDEST queued event
        is dropped to make room (latest-wins — the newest event, e.g. a
        terminal ``stream_end`` or ``approval_resolved``, must not be lost).
        This prevents a single slow subscriber from back-pressuring and
        stalling the entire HITL resume pipeline.
        """
        buffer = self._buffer[resume_id]
        buffer.append(event)
        if len(buffer) > self._buffer_size:
            del buffer[: len(buffer) - self._buffer_size]
        queues = list(self._subscribers.get(resume_id, []))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest queued event to make room for the newest
                # (latest-wins). If it is still full (e.g. a concurrent
                # drainer took the slot), drop the incoming event itself
                # rather than ever blocking the pipeline.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def close(self, resume_id: str) -> None:
        queues = self._subscribers.pop(resume_id, [])
        for queue in queues:
            queue.put_nowait({"type": "stream_end"})
        # Drop the ring buffer and mark the resume finished so late subscribers
        # terminate immediately (see subscribe) instead of hanging.
        self._buffer.pop(resume_id, None)
        self._closed.add(resume_id)
        # The closed set is small (one entry per HITL resume); bound it so it
        # cannot grow unboundedly for a long-running process.
        if len(self._closed) > 512:
            self._closed = set(list(self._closed)[-256:])


approval_event_bus = ApprovalEventBus()


# Task consuming each session's SSE stream, so a hard stop (session delete)
# can cancel the run mid-generation instead of waiting for it to finish.
_stream_tasks: dict[str, asyncio.Task] = {}

SSE_TIMEOUT = int(os.environ.get("COWORKER_SSE_TIMEOUT", str(30 * 60)))

# Keep-alive cadence for SSE streams. The agent may sit in "thinking" / tool /
# LLM-wait phases with no events for a long time; a periodic comment line
# (`: ping`) tells proxies and the client that the stream is alive, so a long
# thinking phase never looks like a dead connection (and the frontend's idle
# timeout never fires for a genuinely-running task).
SSE_HEARTBEAT_SECONDS = float(os.environ.get("COWORKER_SSE_HEARTBEAT_SECONDS", "15.0"))


async def _sse_events(
    stream_iter: Any,
    on_event: Any = None,
    on_end: Any = None,
    on_error: Any = None,
):
    """Consume ``stream_iter`` and yield ``(kind, payload)`` tuples.

    kind is one of:
      * ``"event"``    — payload is a real event dict (``on_event`` already ran)
      * ``"heartbeat"``— no event for ``SSE_HEARTBEAT_SECONDS``; caller should
                         emit an SSE comment line to keep the connection alive
      * ``"error"``    — payload is the error event dict (stream raised, incl.
                         client-disconnect cancellation)
      * ``"end"``      — payload ``None``; the underlying stream finished

    ``on_event(event)`` runs for each real event (accumulate/persist side
    effects). ``on_end()`` runs once on normal completion and may return a final
    dict to emit (e.g. a synthetic ``done``) or ``None``. ``on_error(exc)`` runs
    when the stream raises and must return the error event dict.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()
    _IDLE_WARN_INTERVAL = 240.0  # re-warn every 4 min of provider silence
    _last_activity_ts = _get_monotonic()  # start from stream init, not 0
    _last_idle_warning_ts: float | None = None

    async def _producer():
        nonlocal _last_activity_ts
        try:
            async for event in stream_iter:
                _last_activity_ts = _get_monotonic()
                if on_event is not None:
                    on_event(event)
                await queue.put(("event", event))
            final = on_end() if on_end is not None else None
            if final is not None:
                await queue.put(("event", final))
        except BaseException as exc:  # incl. GeneratorExit / CancelledError
            # The terminal event must ALWAYS reach the client: without it the
            # frontend sees a clean EOF with neither `done` nor `error` and
            # renders a permanently "interrupted" (yellow) bubble. If the
            # caller's on_error handler itself fails, fall back to a plain
            # error event instead of letting the exception propagate (which
            # would leave only the "end" sentinel in the queue).
            try:
                err = on_error(exc) if on_error is not None else {"type": "error", "error": str(exc)[:400]}
            except BaseException:  # noqa: BLE001 - never lose the terminal event
                logger.exception("on_error handler failed for stream: %r", str(exc)[:200])
                err = {"type": "error", "error": "internal stream failure"}
            await queue.put(("error", err))
        finally:
            # CRITICAL teardown: ``async for`` does NOT close an async iterator
            # when the loop is interrupted by an exception. A task.cancel()
            # (user Stop / client disconnect) that lands between chunks leaves
            # the provider stream (graph.astream → langchain → httpx → vLLM
            # socket) running and generating to a dead socket — the "vLLM is
            # still running after Stop" bug. Force-close the whole chain here so
            # the upstream HTTP request is aborted promptly.
            try:
                await stream_iter.aclose()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
            await queue.put(("end", None))

    task = asyncio.ensure_future(_producer())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Emit SSE keep-alive comment every second of idle. Idle
                # accounting starts at stream init (`_last_activity_ts` is
                # monotonic from the start), so no 0-sentinel guard needed.
                elapsed = _get_monotonic() - _last_activity_ts
                # Repeatedly push idle-warning so the frontend's idle watchdog
                # keeps being reset while the provider is legitimately slow
                # (long thinking / large prefill). Without recurrence the client
                # would abort before a long per-provider stream timeout fires.
                if elapsed >= _IDLE_WARN_INTERVAL and (
                    _last_idle_warning_ts is None
                    or _get_monotonic() - _last_idle_warning_ts >= _IDLE_WARN_INTERVAL
                ):
                    _last_idle_warning_ts = _get_monotonic()
                    yield "event", {"type": "idle_warning", "seconds_idle": int(elapsed)}
                yield "heartbeat", None
                continue
            yield kind, payload
            if kind == "end":
                break
    finally:
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


def _get_monotonic() -> float:
    """Monotonic clock (never goes backwards) for tracking idle time."""
    return time.monotonic()


async def _publish_turn(
    session_id: str,
    stream_iter: Any,
    on_event: Any = None,
    on_end: Any = None,
    on_error: Any = None,
) -> None:
    """Background task: consume a runtime stream and publish its events to the
    session event bus (live, independent of the generator being blocked on a
    long-running tool). Preserves the terminal semantics of ``_sse_events``:
    the synthetic ``done``/error event is published, then the bus is closed so
    the subscribing endpoint unblocks.

    Invariant: this task ALWAYS closes the bus — on normal end, on error, and
    even when the task itself is cancelled (client disconnect). Otherwise the
    subscriber would hang waiting for a terminal ``worker_stream_end``.
    """
    try:
        published = 0
        async for kind, payload in _sse_events(
            _tracked_stream(stream_iter, session_id),
            on_event=on_event,
            on_end=on_end,
            on_error=on_error,
        ):
            if kind == "event":
                session_event_bus.publish(session_id, payload)
            elif kind == "error":
                session_event_bus.publish(session_id, payload)
                session_event_bus.close(session_id)
            elif kind == "end":
                session_event_bus.close(session_id)
            # Model bursts can drain hundreds of deltas in one synchronous run
            # (queue.get() on a non-empty queue never yields to the loop), which
            # would starve the SSE subscriber task. Yield periodically so the
            # subscriber keeps pace and the bus buffer never needs eviction.
            published += 1
            if published % 32 == 0:
                await asyncio.sleep(0)
    except BaseException:  # noqa: BLE001 - incl. cancellation; must close the bus
        session_event_bus.close(session_id)
        raise


def _emit_goal_updated(session_id: str, goal) -> dict | None:
    """Broadcast a ``goal_updated`` event on the session bus (streaming channel)
    and return the goal payload for the HTTP response (idle channel).

    Both channels land on the same frontend ``goal`` state — no second truth.
    """
    if goal is None:
        return None
    try:
        payload = goal.to_dict()
    except Exception:  # noqa: BLE001 - never break on a serialization hiccup
        return None
    try:
        session_event_bus.publish(
            session_id,
            {"type": "goal_updated", "session_id": session_id, "goal": payload},
        )
    except Exception:  # noqa: BLE001 - never break on a publish hiccup
        pass
    return payload


def _emit_goal_cleared(session_id: str) -> None:
    try:
        session_event_bus.publish(
            session_id,
            {"type": "goal_cleared", "session_id": session_id},
        )
    except Exception:  # noqa: BLE001 - never break on a publish hiccup
        pass


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    agent: Optional[str] = None
    mode: AgentMode = "single"
    language: Language = "zh"
    work_mode: Optional[str] = None
    autonomy: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[str] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    referenced_sessions: list[str] = Field(
        default_factory=list,
        description="Session ids the user explicitly referenced in this message; the agent may read them via the read_session tool.",
    )


def _resolve_references(referenced_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve pasted session ids to {id, title} entries, dropping unknown ids."""
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_id in referenced_ids:
        session_id = str(raw_id or "").strip()
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        try:
            session = session_store.load(session_id)
        except Exception:
            session = None
        if session is not None:
            resolved.append({"id": session.id, "title": session.title})
    return resolved


def resolve_request_autonomy(request) -> str:
    """Effective autonomy for a chat request."""
    if getattr(request, "autonomy", None):
        return normalize_autonomy(request.autonomy)
    return "guarded"

class RuntimeConfigResponse(BaseModel):
    workspace: str
    data_dir: str
    default_mode: AgentMode
    agent_provider: str
    available_modes: list[AgentMode]
    selected_provider_id: str = ""
    selected_model: str = ""

class RuntimeConfigUpdate(BaseModel):
    selected_provider_id: str = ""
    selected_model: str = ""

class ProviderCreate(BaseModel):
    name: str
    provider_type: str = "custom"
    base_url: str
    api_key: str = ""
    model: str = ""
    context_window: int = 0
    max_output_tokens: int = 0
    vision: bool = False
    temperature: float = 0

class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    vision: Optional[bool] = None
    temperature: Optional[float] = None

class DefaultProviderPayload(BaseModel):
    provider_id: str
    model: str

class ProviderTestPayload(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    provider_id: str = ""

class ProviderFetchModelsPayload(BaseModel):
    base_url: str
    api_key: str = ""
    provider_type: str = "custom"
    provider_id: str = ""

class WorkspaceCommandRequest(BaseModel):
    command: str
    cwd: str = ""
    timeout_seconds: int = 20
    project_id: str = ""


class McpServerCreatePayload(BaseModel):
    name: str
    transport: str  # "stdio" | "http" | "sse" | "websocket"
    command: str = ""
    args: str = ""
    cwd: str = ""
    timeout: float | None = None
    url: str = ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}


class McpServerUpdatePayload(BaseModel):
    name: str | None = None
    transport: str | None = None
    enabled: bool | None = None
    command: str | None = None
    args: str | None = None
    cwd: str | None = None
    timeout: float | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    trusted: bool | None = None
    disabled_tools: list[str] | None = None


class McpTestPayload(BaseModel):
    transport: str
    command: str = ""
    args: str = ""
    cwd: str = ""
    timeout: float | None = None
    url: str = ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    server_id: str = ""


class SkillUpdatePayload(BaseModel):
    enabled: bool | None = None
    permission: str | None = None


class SkillValidatePayload(BaseModel):
    path: str = ""
    name: str = ""


class ApprovalDecisionPayload(BaseModel):
    # The resolve endpoint keys off the TOP-LEVEL `request.approval_id`;
    # this nested copy is legacy/optional and must not fail validation when the
    # frontend sends a decision without duplicating the id (fixes dead buttons
    # on question/approval/plan cards → HTTP 422).
    approval_id: str = ""
    type: str = ""
    decision: str = ""
    reason: str = ""
    provider_id: str = ""
    model: str = ""
    workspace: str = ""
    message: str = ""
    session_id: str = ""
    always_allow: bool = False
    respond_text: str = ""
    plan_text: str = ""
    # The plan card's "approve with autonomy" buttons send the autonomy they
    # should approve under; must be declared or pydantic silently drops it and
    # main.py:2416 reads .autonomy -> AttributeError -> 500.
    autonomy: str = ""


class CommandApprovalResolve(BaseModel):
    approval_id: str
    decision: ApprovalDecisionPayload


# --------------------------------------------------------------------------- #
# Long-term memory helpers (read injection; Phase 2 auto-extract dispatch)
# --------------------------------------------------------------------------- #

def _project_memory_dir(project_id: str) -> str:
    """Resolve a project's memory_dir (auto-generating for legacy projects)."""
    if not project_id:
        return ""
    try:
        return project_store.memory_dir_for(project_id)
    except (KeyError, ValueError):
        return ""


def _unique_memory_dir(created_at: str, mode: str) -> str:
    """Build a unique project memory dir name: ``{timestamp}_{mode}``.

    The mode suffix keeps the single- and multi-agent projects of one folder
    distinct even when created within the same second. Two different folders
    creating a same-mode project in the same second would still collide, so a
    ``_2/_3`` suffix is appended while the candidate exists either in the
    project store or on disk.
    """
    from coworker.memory.layout import memory_dir_from_created_at

    base = f"{memory_dir_from_created_at(created_at)}_{mode}"
    taken = {p.memory_dir for p in project_store.list_projects() if p.memory_dir}
    candidate = base
    index = 2
    while candidate in taken or (memory_manager.root / candidate).exists():
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _ensure_agent_skeleton(project_dir: str, agent: str = DEFAULT_AGENT) -> str:
    """Materialize the agent skeleton for a project; returns the agent rel dir."""
    project_path = memory_manager.registry.ensure_project(project_dir)
    agent_path = memory_manager.registry.ensure_agent(project_path, agent)
    try:
        return agent_path.relative_to(memory_manager.root).as_posix()
    except ValueError:
        return f"{project_dir}/{agent}"


def _ensure_org(memory_dir: str, mode: str | None = None) -> str:
    """Materialize (or migrate) the org manifest for a project memory dir.

    ``mode`` only applies when the org does not exist yet (mode is immutable
    after creation); an existing org keeps its stored mode regardless of the
    argument. Returns the ``memory_dir`` on success; raises
    ``HTTPException(400)`` when the project's memory is unavailable.
    """
    if not memory_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    if not org_store.exists(memory_dir):
        org = org_store.load(memory_dir)
        if mode is not None:
            org.mode = mode
        # Migration: back-fill existing agent directories discovered on disk.
        library = memory_manager.scanner.scan()
        view = next((p for p in library.projects if p.name == memory_dir), None)
        if view:
            for aview in view.agents:
                if not any(a.id == aview.id for a in org.agents):
                    org.agents.append(
                        OrgAgent(
                            id=aview.id,
                            name=aview.name,
                            role="",
                            description="",
                            parent="",
                            team_id="",
                            status=AGENT_STATUS_ACTIVE,
                        )
                    )
        if not any(a.id == DEFAULT_AGENT for a in org.agents):
            org.agents.append(
                OrgAgent(
                    id=DEFAULT_AGENT,
                    name=DEFAULT_AGENT,
                    role="team lead" if org.mode == ORG_MODE_MULTI else "",
                    description="",
                    parent="",
                    team_id="",
                    status=AGENT_STATUS_ACTIVE,
                )
            )
        org_store.save(memory_dir, org)
    return memory_dir


def _load_org(project_dir: str) -> Org:
    """Load a project's org manifest, materializing it first if needed."""
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _ensure_org(project_dir)
    return org_store.load(project_dir)


def _require_multi(project_dir: str) -> Org:
    """Load a project's org and reject team features in single mode.

    Returns the org when ``mode == multi``; otherwise raises ``HTTPException``.
    """
    org = _load_org(project_dir)
    if org.mode != ORG_MODE_MULTI:
        raise HTTPException(
            status_code=400,
            detail="project is in single mode; team features disabled",
        )
    return org


def _is_agent_core_rel(rel: str) -> bool:
    """True when ``rel`` points at an agent identity/core file (``…/BASE/SOUL|AGENT|MEMORY.md``)."""
    parts = rel.split("/")
    return len(parts) >= 2 and parts[-1] in AGENT_CORE_FILES and parts[-2] == BASE_DIR


def _is_protected_memory_file(rel: str) -> bool:
    """True when ``rel`` is a system root file or an agent core file (immovable)."""
    parts = rel.split("/")
    if len(parts) == 1 and parts[0] in SYSTEM_FILES:
        return True
    return _is_agent_core_rel(rel)


def _memory_extract_llm() -> Any | None:
    from coworker.memory.auto_extract import build_extract_llm

    select = memory_manager.config.extract_model  # "" = default, else a provider id
    provider = None
    model_override = ""
    if select:
        try:
            provider = provider_manager.load().find_enabled(select)
        except Exception:
            provider = None
        if provider is None:
            # Legacy value: a bare model string (pre-dropdown config) — treat as
            # a model override against the default provider's endpoint.
            provider = provider_manager.default_provider()
            model_override = select
    if provider is None:
        provider = provider_manager.default_provider()
    if provider is None:
        return None
    return build_extract_llm(provider, model_override)


def _memory_transcript(session_id: str) -> list[dict[str, Any]]:
    """Return recent role/content pairs for a session (auto-extract input)."""
    session = session_store.load(session_id)
    if session is None:
        return []
    return [
        {"role": msg.role, "content": msg.content}
        for msg in session.messages
        if msg.content
    ]


# Wire the Phase 2 auto-extract dependencies (extractor LLM + transcript
# provider). Without this, after_turn's extraction task short-circuits
# and auto-extract silently never runs.
memory_manager.configure_extractor(
    llm_factory=_memory_extract_llm,
    transcript_provider=_memory_transcript,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "workspace": str(settings.workspace_dir),
        "data_dir": str(settings.data_dir),
        "agent_provider": settings.agent_provider,
        "available_modes": ["single"],
    }

@app.get("/config", response_model=RuntimeConfigResponse)
def runtime_config():
    return RuntimeConfigResponse(**config_controller.runtime_config())

@app.patch("/config", response_model=RuntimeConfigResponse)
def update_runtime_config(request: RuntimeConfigUpdate):
    try:
        return RuntimeConfigResponse(**config_controller.update_runtime_config(request.model_dump()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Long-term memory API (library tree + settings)
# --------------------------------------------------------------------------- #

class MemoryWriteRequest(BaseModel):
    action: str = "add"        # add | replace | remove
    content: str = ""
    target: str = ""
    project_id: str = ""
    agent: str = DEFAULT_AGENT


class MemoryFileRequest(BaseModel):
    rel: str = ""
    content: str = ""


class MemoryMoveRequest(BaseModel):
    rel: str = ""
    new_rel: str = ""


class MemoryExportRequest(BaseModel):
    scope: str = "all"  # all | system | projects
    project_dirs: list[str] = []


class MemoryImportPreviewRequest(BaseModel):
    path: str = ""


class MemoryImportApplyRequest(BaseModel):
    token: str = ""
    decisions: dict[str, str] = {}


class MemorySettingsUpdate(BaseModel):
    enabled: bool | None = None
    auto_extract: bool | None = None


@app.get("/api/memory/discover")
async def memory_discover(project_id: str = "", agent: str = DEFAULT_AGENT):
    """Memory library tree: system files + project views (BASE/PROJECT/agents)."""
    project_dir = _project_memory_dir(project_id)
    if project_dir:
        _ensure_agent_skeleton(project_dir, agent)
        try:
            _ensure_org(project_dir)
        except Exception:  # noqa: BLE001 - org scaffold must not break discovery
            pass
    library = memory_manager.scanner.scan(include_missing=True)
    projects = []
    for view in library.projects:
        mode = ORG_MODE_MULTI
        if org_store.exists(view.name):
            mode = org_store.load(view.name).mode
        if mode != ORG_MODE_MULTI:
            view = _scoped_single_project_view(view)
        projects.append(view)
    return {
        "root": str(library.root),
        "system": [n.to_dict() for n in library.system],
        "projects": [p.to_dict() for p in projects],
    }


def _scoped_single_project_view(view):
    """Strip team containers and extra agents from a single-mode project view.

    A single-mode project exposes exactly one agent (``default_agent``) and no
    team structure; anything else found on disk is legacy residue and must not
    surface in the memory tree.
    """
    return view.__class__(
        name=view.name,
        rel=view.rel,
        project_name=view.project_name,
        base=view.base,
        project=view.project,
        agents=[a for a in view.agents if a.id == DEFAULT_AGENT],
        folders=view.folders,
        teams=[],
    )


@app.get("/api/memory/file")
async def get_memory_file(rel: str = ""):
    """Read one memory file by memory-root-relative path."""
    if not rel:
        raise HTTPException(status_code=400, detail="rel is required")
    try:
        memory = memory_manager.store.read_file(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": str(memory.path),
        "rel": rel,
        "content": memory.content,
        "mtime": memory.mtime,
        "blocks": list(memory.blocks),
    }


@app.get("/api/memory/resolve")
async def resolve_memory_path(rel: str = ""):
    """Resolve a memory-root-relative ``rel`` to its absolute filesystem path.

    Used by the UI's right-click "jump to system directory" (reveal in the OS
    file manager). Both files and directories are resolvable; the path is
    validated to stay inside the memory root.
    """
    if not rel:
        raise HTTPException(status_code=400, detail="rel is required")
    try:
        path = memory_manager.store._resolve(rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"memory path not found: {rel!r}")
    return {"rel": rel, "path": str(path)}


@app.post("/api/memory/file")
async def save_memory_file(request: MemoryFileRequest):
    """Replace one memory file's full content (raw Markdown)."""
    if not memory_manager.enabled:
        raise HTTPException(status_code=400, detail="memory is disabled")
    if not request.rel:
        raise HTTPException(status_code=400, detail="rel is required")
    try:
        memory = memory_manager.store.write_file(request.rel, request.content)
    except MemoryError as exc:
        if len(exc.args) > 0 and "file changed externally" in str(exc.args[0]):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rel": request.rel, "content": memory.content}


@app.post("/api/memory/delete")
async def delete_memory(request: MemoryFileRequest):
    """Delete a file (moved to the OS trash) or empty directory under the root."""
    if not request.rel:
        raise HTTPException(status_code=400, detail="rel is required")
    try:
        deleted = memory_manager.store.remove_file(request.rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="file not found")
    return {"status": "ok", "rel": request.rel, "trashed": True}


@app.post("/api/memory/move")
async def move_memory(request: MemoryMoveRequest):
    """Move/rename a memory file. System and agent-core files are immovable."""
    if not request.rel or not request.new_rel:
        raise HTTPException(status_code=400, detail="rel and new_rel are required")
    if _is_protected_memory_file(request.rel):
        raise HTTPException(status_code=400, detail="system and agent-core files cannot be moved")
    if _is_agent_core_rel(request.new_rel):
        raise HTTPException(status_code=400, detail="cannot move a file into an agent core location")
    try:
        new_rel = memory_manager.store.move_file(request.rel, request.new_rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "rel": request.rel, "new_rel": new_rel}


@app.get("/api/memory/search")
async def search_memory(q: str = "", limit: int = 50):
    """Full-text search across the memory library (case-insensitive substring)."""
    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q is required")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="query too long")
    limit = max(1, min(int(limit or 50), 100))
    results = memory_manager.scanner.search(query, limit=limit)
    return {"query": query, "results": results}


@app.post("/api/memory/export")
async def export_memory_api(request: MemoryExportRequest):
    """Export a memory subset as a zip archive on the backend."""
    if request.scope not in ("all", "system", "projects"):
        raise HTTPException(status_code=400, detail="scope must be all|system|projects")
    project_dirs = request.project_dirs if request.scope == "projects" else []
    try:
        return export_memory(
            memory_manager.root,
            memory_manager.data_dir,
            scope=request.scope,
            project_dirs=project_dirs,
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/memory/import/preview")
async def preview_import_api(request: MemoryImportPreviewRequest):
    """Unpack a zip into a staging dir and report entries with conflict flags."""
    if not request.path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        return preview_import(memory_manager.root, memory_manager.data_dir, request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/memory/import/apply")
async def apply_import_api(request: MemoryImportApplyRequest):
    """Apply a previewed import with a per-file skip/overwrite decision map."""
    if not request.token:
        raise HTTPException(status_code=400, detail="token is required")
    try:
        return apply_import(
            memory_manager.root,
            memory_manager.data_dir,
            request.token,
            request.decisions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/memory/write")
async def memory_write(request: MemoryWriteRequest):
    """Backend-direct memory write to the current agent's memory file.

    ``action`` add/replace/remove; ``target`` only meaningful for replace/remove.
    """
    if not memory_manager.enabled:
        raise HTTPException(status_code=400, detail="memory is disabled")
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    agent_rel = _ensure_agent_skeleton(project_dir, request.agent) + "/BASE/MEMORY.md"
    store = memory_manager.store
    try:
        if request.action == "replace":
            if not request.content:
                raise ValueError("content is required for replace")
            blocks = store.replace_block(agent_rel, request.target, request.content)
        elif request.action == "remove":
            if not request.target:
                raise ValueError("target is required for remove")
            blocks = store.remove_block(agent_rel, request.target)
        elif request.action == "add":
            if not request.content:
                raise ValueError("content is required for add")
            blocks = store.add_block(agent_rel, request.content)
        else:
            raise ValueError(f"unsupported memory action: {request.action}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rel": agent_rel, "blocks": blocks}


@app.post("/api/memory/register-project")
async def memory_register_project(request: MemoryWriteRequest):
    """Materialize the project memory skeleton (BASE/ + BASE/PROJECT/)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    memory_manager.registry.ensure_project(project_dir)
    return {"status": "ok", "project_dir": project_dir}


@app.post("/api/memory/register-agent")
async def memory_register_agent(request: MemoryWriteRequest):
    """Materialize an agent skeleton under a project and register it in the org.

    Only available in multi mode (single-mode projects have exactly one agent).
    """
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    agent = request.agent or DEFAULT_AGENT
    org = org_store.load(project_dir)
    if not any(a.id == agent for a in org.agents):
        org.agents.append(
            OrgAgent(id=agent, name=agent, role="", description="", parent="", team_id="", status=AGENT_STATUS_ACTIVE)
        )
        org_store.save(project_dir, org)
    agent_dir = _ensure_agent_skeleton(project_dir, agent)
    return {"status": "ok", "agent_dir": agent_dir}


# --------------------------------------------------------------------------- #
# Org API (project = organization container: agents + teams)
# --------------------------------------------------------------------------- #

class OrgAgentCreateRequest(BaseModel):
    project_id: str = ""
    name: str = ""
    role: str = ""
    description: str = ""
    parent: str = ""
    team_id: str = ""


class OrgAgentUpdateRequest(BaseModel):
    project_id: str = ""
    id: str = ""
    name: str | None = None
    role: str | None = None
    description: str | None = None
    parent: str | None = None
    team_id: str | None = None
    status: str | None = None


class OrgAgentDeleteRequest(BaseModel):
    project_id: str = ""
    id: str = ""


class OrgTeamCreateRequest(BaseModel):
    project_id: str = ""
    id: str = ""
    name: str = ""
    lead: str = ""
    parent_team_id: str = ""


class OrgTeamUpdateRequest(BaseModel):
    project_id: str = ""
    id: str = ""
    name: str | None = None
    lead: str | None = None
    parent_team_id: str | None = None
    status: str | None = None


class OrgTeamDeleteRequest(BaseModel):
    project_id: str = ""
    id: str = ""


class OrgConfigUpdateRequest(BaseModel):
    project_id: str = ""
    mode: str | None = None
    max_depth: int | None = None
    max_concurrent: int | None = None
    allow_agent_creation: bool | None = None


def _org_public(org) -> dict:
    return {
        "agents": [a.__dict__ for a in org.agents],
        "teams": [t.__dict__ for t in org.teams],
        "config": {
            "mode": org.mode,
            "max_depth": org.max_depth,
            "max_concurrent": org.max_concurrent,
            "allow_agent_creation": org.allow_agent_creation,
        },
        "roster": org_store.roster(org),
    }


@app.get("/api/org")
async def org_get(project_id: str = ""):
    """Return the org manifest for one project (agents + teams + config + roster)."""
    project_dir = _project_memory_dir(project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _ensure_org(project_dir)
    org = org_store.load(project_dir)
    return _org_public(org)


@app.post("/api/org/agent")
async def org_create_agent(request: OrgAgentCreateRequest):
    """Create an agent: register in the org manifest + materialize the skeleton."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    org = org_store.load(project_dir)
    if any(a.id == request.name for a in org.agents):
        raise HTTPException(status_code=400, detail=f"agent {request.name!r} already exists")
    try:
        org_store.upsert_agent(
            project_dir,
            OrgAgent(
                id=request.name,
                name=request.name,
                role=request.role,
                description=request.description,
                parent=request.parent,
                team_id=request.team_id,
                status=AGENT_STATUS_ACTIVE,
            ),
        )
        _ensure_agent_skeleton(project_dir, request.name)
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _org_public(org_store.load(project_dir))


@app.patch("/api/org/agent")
async def org_update_agent(request: OrgAgentUpdateRequest):
    """Edit an agent (role/description/superior/team/status)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    org = org_store.load(project_dir)
    agent = next((a for a in org.agents if a.id == request.id), None)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent {request.id!r} not found")
    if request.name is not None:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent name must not be empty")
        agent.name = name
    if request.role is not None:
        agent.role = request.role
    if request.description is not None:
        agent.description = request.description
    if request.parent is not None:
        agent.parent = request.parent
    if request.team_id is not None:
        agent.team_id = request.team_id
    if request.status is not None:
        agent.status = request.status
    try:
        org_store.save(project_dir, org)
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _org_public(org_store.load(project_dir))


@app.delete("/api/org/agent")
async def org_delete_agent(request: OrgAgentDeleteRequest):
    """Delete an agent (org entry + bound sessions + memory dir to trash).

    ``default_agent`` is protected. Agents that are another member's superior or
    a team lead are hard-blocked (reassign those first) so deleting a member can
    never break the org hierarchy.
    """
    if request.id == DEFAULT_AGENT:
        raise HTTPException(status_code=400, detail="default_agent cannot be deleted")
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    try:
        org_store.remove_agent(project_dir, request.id)
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Cascade: forget runtimes + clean change store + delete sessions bound to this agent.
    for session in session_store.list_sessions(request.project_id):
        if session.get("agent_id") != request.id:
            continue
        await agent_registry.forget_runtime_checkpoint(session["id"])
        agent_registry.change_store.delete_session(session["id"])
        agent_registry.snapshot_manager.delete_session(session["id"])
        _cleanup_session_screenshots(session["id"])
    session_store.delete_by_agent(request.project_id, request.id)
    # Trash the agent's memory directory (recoverable), never blocking the delete.
    from coworker.memory.memory_store import MemoryStore

    store = MemoryStore(memory_manager.root)
    try:
        store.remove_file(f"{project_dir}/{request.id}")
    except Exception:  # noqa: BLE001 - trash failure must not fail the org update
        logger.warning("could not trash agent dir %s/%s", project_dir, request.id, exc_info=True)
    return _org_public(org_store.load(project_dir))


@app.post("/api/org/team")
async def org_create_team(request: OrgTeamCreateRequest):
    """Create a team (department)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    org = org_store.load(project_dir)
    if any(t.id == request.id for t in org.teams):
        raise HTTPException(status_code=400, detail=f"team {request.id!r} already exists")
    try:
        org_store.upsert_team(
            project_dir,
            OrgTeam(
                id=request.id,
                name=request.name,
                lead=request.lead,
                parent_team_id=request.parent_team_id,
                status=AGENT_STATUS_ACTIVE,
            ),
        )
        memory_manager.registry.ensure_project(project_dir)
        team_dir = memory_manager.root / project_dir / "teams" / request.id
        team_dir.mkdir(parents=True, exist_ok=True)
        for name in ("GOALS.md", "CONTEXT.md", "MEMORY.md"):
            path = team_dir / name
            if not path.exists():
                path.write_text(f"# {name}\n\n（{request.name} 部门记忆）\n", encoding="utf-8")
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _org_public(org_store.load(project_dir))


@app.patch("/api/org/team")
async def org_update_team(request: OrgTeamUpdateRequest):
    """Edit a team (name/lead/parent/status)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    org = org_store.load(project_dir)
    team = next((t for t in org.teams if t.id == request.id), None)
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {request.id!r} not found")
    if request.name is not None:
        team.name = request.name
    if request.lead is not None:
        team.lead = request.lead
    if request.parent_team_id is not None:
        team.parent_team_id = request.parent_team_id
    if request.status is not None:
        team.status = request.status
    try:
        org_store.save(project_dir, org)
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _org_public(org_store.load(project_dir))


@app.delete("/api/org/team")
async def org_delete_team(request: OrgTeamDeleteRequest):
    """Delete a team (only empty teams may be removed)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _require_multi(project_dir)
    try:
        org_store.remove_team(project_dir, request.id)
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    team_path = memory_manager.root / project_dir / "teams" / request.id
    if team_path.is_dir():
        from coworker.memory.memory_store import MemoryStore

        store = MemoryStore(memory_manager.root)
        try:
            store.remove_file(f"{project_dir}/teams/{request.id}")
        except Exception:  # noqa: BLE001
            logger.warning("could not trash team dir %s", team_path, exc_info=True)
    return _org_public(org_store.load(project_dir))


@app.patch("/api/org/config")
async def org_update_config(request: OrgConfigUpdateRequest):
    """Update org config (mode/max_depth/max_concurrent/allow_agent_creation).

    ``mode`` is immutable after project creation: a mode sent in the request is
    ignored (it is retained from the existing org manifest).
    """
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _ensure_org(project_dir)
    try:
        org_store.update_config(
            project_dir,
            max_depth=request.max_depth,
            max_concurrent=request.max_concurrent,
            allow_agent_creation=request.allow_agent_creation,
        )
    except OrgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _org_public(org_store.load(project_dir))


@app.get("/api/memory/status")
async def memory_status(project_id: str = ""):
    """Full memory overview: file counts, sizes, budget, enabled flags."""
    project_dir = _project_memory_dir(project_id)
    library = memory_manager.scanner.scan(include_missing=True)
    nodes = library.injected(project_dir=project_dir or None, agent=DEFAULT_AGENT)
    char_total = sum(len(n.content) for n in nodes)
    return {
        "enabled": memory_manager.enabled,
        "auto_extract": memory_manager.auto_extract,
        "root": str(library.root),
        "file_count": len(nodes),
        "char_count": char_total,
        "over_budget": char_total > memory_manager.char_limit,
    }


@app.get("/api/memory/settings")
async def get_memory_settings():
    """Runtime memory settings (the Settings page surface)."""
    return {
        "enabled": memory_manager.enabled,
        "auto_extract": memory_manager.auto_extract,
    }


@app.post("/api/memory/settings")
async def save_memory_settings(request: MemorySettingsUpdate):
    """Persist memory settings to .coworker_settings.json and apply at runtime."""
    current = memory_manager.config
    updated = MemoryConfig(
        enabled=request.enabled if request.enabled is not None else current.enabled,
        inject_char_limit=current.inject_char_limit,
        auto_extract=request.auto_extract if request.auto_extract is not None else current.auto_extract,
        nudge_interval=current.nudge_interval,
        extract_model=current.extract_model,
        max_prior_loss=current.max_prior_loss,
        dream_idle_seconds=current.dream_idle_seconds,
    )
    memory_manager.config = updated
    try:
        save_user_memory_settings(
            {
                "enabled": updated.enabled,
                "auto_extract": updated.auto_extract,
            }
        )
    except OSError as exc:  # noqa: BLE001 - settings persistence must not fail the request
        logger.warning("Failed to persist memory settings: %s", exc)
    return {
        "enabled": updated.enabled,
        "auto_extract": updated.auto_extract,
    }

from fastapi.responses import StreamingResponse

class ChatStreamRequest(ChatRequest):
    session_id: str = ""
    # 前端乐观渲染时生成的消息 id，回传以统一前后端 id（修复按 id 回退/重生成时 404）
    user_message_id: str = ""
    assistant_message_id: str = ""
    # 前端「文件体积上限」设置换算成的字节数；后端按此上限如实处理附件
    # （超过的附件不内联、在提示词中说明未转发）。None 时后端用默认 25MB。
    max_attachment_bytes: Optional[int] = None
    # 插話 (interject) 自动续跑：user 消息已由 /chat/interject 持久化（它就是
    # history 的最后一条 user 消息），这里不再 append，直接以 history 作为模型输入，
    # 避免重复写库与重复送上下文。
    skip_user_append: bool = False

def _cached_provider_unreachable(provider_id: str | None, model: str | None) -> str | None:
    """Best-effort fast-fail guard: return the cached "LLM 服务不可达" message
    when the requested LOCAL provider's context discovery recently failed, else
    ``None``. Pure cache read — never probes, never blocks the event loop.

    Only LOCAL providers fast-fail here: a failed discovery probe (e.g. a 401/404
    from a cloud/gateway endpoint that omits ``/v1/models``) does not mean chat
    completions are down, and the UI's ``context_error`` warning is only surfaced
    for local providers too — so the two must agree. See ``_resolve_context_window_full``.
    """
    try:
        if not provider_id:
            return None
        provider = provider_manager.load().find_enabled(provider_id)
        if not provider:
            return None
        if model and model != provider.model:
            provider = replace(provider, model=model)
        if not ProviderManager._is_local(provider):
            return None
        return ProviderManager.cached_context_error(provider)
    except Exception:  # noqa: BLE001 - a guard failure must never break a turn
        return None


async def _build_stream_runtime(
    mode: str,
    provider_id: str | None,
    model: str | None,
    workspace: Any,
    referenced_ids: set[str],
    agent: str,
    project_id: str | None,
) -> Any:
    """Build a stream runtime WITHOUT blocking the event loop.

    Runtime construction resolves the provider's context window, which performs
    a synchronous network probe whenever the discovery cache is cold (up to the
    3s probe timeout). That must never freeze the event loop that also serves
    other sessions' SSE streams/heartbeats, so the whole init runs on a thread.
    """
    return await asyncio.to_thread(
        agent_registry.get_stream_runtime,
        mode,
        provider_id,
        model,
        workspace,
        referenced_sessions=referenced_ids,
        agent=agent,
        project_id=project_id,
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest):
    if not request.session_id and not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required to start a new chat")
    work_mode = normalize_work_mode(request.work_mode)
    autonomy = resolve_request_autonomy(request)
    references = _resolve_references(request.referenced_sessions)
    referenced_ids = {ref["id"] for ref in references}
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(
            session_id=request.session_id or None,
            project_id=request.project_id,
        )
        agent = request.agent or DEFAULT_AGENT
        if request.session_id:
            try:
                existing = session_store.require(request.session_id)
                if request.agent:
                    existing.agent_id = request.agent
                    session_store.save(existing)
                elif existing.agent_id:
                    agent = existing.agent_id
            except KeyError:
                pass
        # Ensure org manifest so the agent runtime can use delegation & team tools.
        project_dir = _project_memory_dir(request.project_id or "")
        if project_dir:
            _ensure_agent_skeleton(project_dir, agent)
            _ensure_org(project_dir)
        runtime = await _build_stream_runtime(request.mode, request.provider_id, request.model, resolved_workspace, referenced_ids, agent, request.project_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = request.session_id
    memory_manager.note_turn_active(session_id) if session_id else None
    max_attachment_bytes = request.max_attachment_bytes
    history = []
    if request.session_id:
        try:
            session = session_store.require(request.session_id)
            history = [
                {"role": m.role, "content": format_user_message(m.content, m.attachments, m.references, max_attachment_bytes=max_attachment_bytes) if m.role == "user" else m.content}
                for m in session.messages
                if m.role in {"user", "assistant"} and m.content
            ]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        session = session_store.create("", project_id=request.project_id or "", agent_id=agent)
        session_id = session.id

    # A session may only have ONE in-flight stream writing its checkpoint; a
    # second concurrent /chat/stream (e.g. a misbehaving client or a double
    # submit) would race the graph's SQLite writes on the same thread_id.
    await _guard_session_not_streaming(session_id)

    # The checkpoint DB is a DISPOSABLE per-turn scratch (single-writer model,
    # cf. codex/opencode): every turn rebuilds from the session history instead
    # of resuming a runtime checkpoint. Delete the thread so this run starts
    # fresh from `history + [user_message]` — this also covers crash leftovers
    # and aborted turns (Stop).
    await agent_registry.forget_runtime_checkpoint(session_id)

    if request.skip_user_append:
        # 插話自动续跑：steer 已由 /chat/interject 持久化为最后一条 user 消息。
        # 直接复用 history（不 append、不重复送模型）。
        user_message = None
        messages = history
    else:
        user_message = {"role": "user", "content": format_user_message(request.message, request.attachments, references, max_attachment_bytes=max_attachment_bytes)}
        session_store.append_message(session_id, role="user", content=request.message, mode=request.mode, work_mode=work_mode, autonomy=autonomy, attachments=request.attachments, references=references, message_id=request.user_message_id or None)
        messages = history + [user_message]
    if request.user_message_id:
        snapshot_user_message_id = request.user_message_id
    else:
        session_messages = session_store.require(session_id).messages
        snapshot_user_message_id = session_messages[-1].id if session_messages else ""

    async def event_stream():
        terminal_sent = False
        error_emitted = False
        interrupt_emitted = False
        accumulated_content = ""
        # Last full `context_usage` frame from the run, persisted on `done` so the
        # session-open preview shows the true request size (system + tools + …).
        last_context_usage: dict[str, Any] | None = None
        # Per-round state (goal 多轮续跑）: the assistant message id the CURRENT
        # round persists to. Round 0 reuses the client-supplied id (frontend
        # bubble reconciliation); continuation rounds get a freshly generated id
        # (never adopt as a successful client commit) and surface it via
        # `done.message_id` so the frontend creates a new bubble per round.
        current_round_assistant_id: str | None = request.assistant_message_id or None
        round_index = 0
        budget_wrapup_done = False
        last_seen_objective: str | None = None
        stream_iter: Any

        # Fast-fail: when the requested provider was recently discovered as
        # unreachable (cached probe failure), refuse to start the agent and
        # surface the reason immediately instead of letting the client wait on
        # a connect timeout. Pure cache read — never probes, never blocks.
        cached_block = _cached_provider_unreachable(request.provider_id, request.model)
        if cached_block:
            yield f"data: {json.dumps({'type': 'error', 'session_id': session_id, 'error': cached_block}, ensure_ascii=False)}\n\n"
            return

        _USE_CLIENT_MESSAGE_ID = object()

        def _persist_assistant(content, mode, provider, model, parts, message_id=_USE_CLIENT_MESSAGE_ID):
            # By default the CURRENT ROUND's assistant id is used so a completed
            # turn's persisted message reconciles 1:1 with the frontend bubble
            # (round 0 = the client-supplied id; continuation rounds = the
            # per-round generated id). Error / disconnect partials pass
            # `message_id=None` so they get a freshly generated id: they must
            # NEVER be adopted as a successful commit by the frontend's
            # stream-settle reconciliation (which matches on the exact id).
            try:
                resolved_id = (
                    current_round_assistant_id if message_id is _USE_CLIENT_MESSAGE_ID else message_id
                )
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=content,
                    mode=mode or request.mode,
                    provider=provider or "",
                    model=model or request.model or "",
                    work_mode=work_mode,
                    autonomy=autonomy,
                    parts=parts or [],
                    message_id=resolved_id,
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence is a side effect; a
                # write failure must NEVER corrupt the SSE stream contract (the
                # client still needs its terminal done/error event), otherwise a
                # transient fs/json/sqlite hiccup turns a reply into an endless
                # "interrupted" (yellow) bubble with no terminal event at all.
                logger.exception("Failed to persist assistant message for session %s", session_id)

        def _handle_event(event):
            nonlocal terminal_sent, accumulated_content, error_emitted, interrupt_emitted, last_context_usage, current_round_assistant_id
            etype = event.get("type", "")
            if etype == "context_usage":
                # Remember the latest full measurement; persisted on `done`.
                last_context_usage = event
            if etype in ("approval_required", "question_required"):
                # The agent interrupted to ask the user. The turn is NOT complete:
                # the stream must end without a synthetic `done` so the frontend
                # keeps the message in the "waiting" state instead of showing a
                # truncated reply as a finished bubble (fixes "回答後流式回覆提前結束").
                interrupt_emitted = True
            if etype == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if etype == "todos":
                try:
                    session_store.update_todos(session_id, event.get("todos") or [])
                except Exception:  # noqa: BLE001 - a todo persist hiccup must not kill the stream
                    logger.warning("update_todos(todos) failed for session %s", session_id, exc_info=True)
            if etype == "done":
                _persist_assistant(
                    event.get("content", ""),
                    event.get("mode"),
                    event.get("provider"),
                    event.get("model"),
                    event.get("parts"),
                )
                # 回带该轮 assistant 消息 id：前端据此识别是首轮（等于请求里的
                # assistant_message_id）还是续跑轮（新建气泡），并对账。
                event["message_id"] = current_round_assistant_id
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.warning("update_modes failed on done for session %s", session_id, exc_info=True)
                # Persist the last full context-usage measurement so the preview
                # reflects the real request size (system prompt + tool schemas +
                # messages + overhead), not a message-only undercount.
                if last_context_usage is not None:
                    try:
                        session_store.update_context_usage(
                            session_id,
                            used_tokens=int(last_context_usage.get("used_tokens", 0) or 0),
                            used_tokens_calibrated=int(last_context_usage.get("used_tokens_calibrated", 0) or 0),
                            used_chars=int(last_context_usage.get("used_chars", 0) or 0),
                            calibration_factor=float(last_context_usage.get("calibration_factor", 0.0) or 0.0),
                        )
                    except Exception:  # noqa: BLE001 - telemetry must never break the stream
                        logger.debug("update_context_usage failed for session %s", session_id, exc_info=True)
                terminal_sent = True
            elif etype == "error":
                # The runtime yields an explicit error event BEFORE re-raising so
                # the SSE layer can turn it into a proper terminal `error` event.
                # Do NOT mark terminal_sent here: no assistant message has been
                # persisted for this turn, and _on_error must still persist the
                # partial reply (or the "interrupted" marker) when the generator
                # raises — otherwise a stalled/errored run leaves NO assistant
                # message in the session (looks like the agent never answered).
                # error_emitted only suppresses _on_end's synthetic `done`.
                error_emitted = True

        runtime = await _build_stream_runtime(
            request.mode, request.provider_id, request.model,
            resolved_workspace, referenced_ids, agent, request.project_id,
        )

        def _current_history() -> list[dict[str, Any]]:
            """Full user/assistant history from the session (post-round persist),
            so round N sees rounds 1..N-1 output (codex rollout semantics)."""
            try:
                session = session_store.require(session_id)
            except KeyError:
                return []
            return [
                {"role": m.role, "content": format_user_message(m.content, m.attachments, m.references, max_attachment_bytes=max_attachment_bytes) if m.role == "user" else m.content}
                for m in session.messages
                if m.role in {"user", "assistant"} and m.content
            ]

        def _goal_injection(goal) -> str | None:
            """该续跑轮要注入的 system 首位内容（内部指令，不落库），或 None。"""
            nonlocal last_seen_objective
            if goal.status == "budget_limited":
                return render_budget_limit(goal)
            if last_seen_objective is not None and goal.objective != last_seen_objective:
                return render_objective_updated(goal)
            return render_goal_continuation(goal)

        async def _goal_rounds_iter():
            """单一生成器内层多轮循环（已拍板落地方式）。

            每轮调用一次 ``runtime.stream`` 依序 yield；``_publish_turn`` /
            SSE 订阅 / 会话事件总线只建一次，整个 goal 运行期保持单条 SSE 连接。
            每轮起点重置终态旗标，每轮独立 snapshot，每轮结束记账并决定是否续跑。
            """
            nonlocal terminal_sent, error_emitted, interrupt_emitted, accumulated_content, last_context_usage, current_round_assistant_id, round_index, budget_wrapup_done, last_seen_objective
            goal_stream_active = session_store.get_goal(session_id) is not None
            inflight_anchor: str | None = None
            inflight_pre: str | None = None
            # 退化回复计数（同一回复内大量重复，qwen3 模式）：累计 ≥2 轮即 blocked。
            degenerate_rounds = 0

            def _begin_round(anchor: str) -> str | None:
                return agent_registry.snapshot_manager.begin_turn(session_id, anchor, resolved_workspace)

            def _end_round(anchor: str | None, pre: str | None) -> None:
                if pre is not None and anchor is not None:
                    agent_registry.snapshot_manager.end_turn(session_id, anchor, resolved_workspace)

            try:
                while True:
                    # ---- per-round reset（否则第 2 轮起 _on_error/_on_end 短路错乱）----
                    terminal_sent = False
                    error_emitted = False
                    interrupt_emitted = False
                    accumulated_content = ""
                    last_context_usage = None

                    if round_index == 0:
                        if request.skip_user_append and session_store.get_goal(session_id) is not None:
                            # 会话恢复 / 空闲启动：round 0 本身就是续跑轮，注入 goal 上下文。
                            # 该轮的 assistant 消息仍复用客户端传入的 id（前端已预建气泡），
                            # 只有真正的续跑轮（round >= 1）才用后端生成的新 id。
                            goal = session_store.get_goal(session_id)
                            injection = _goal_injection(goal)
                            round_messages = ([{"role": "system", "content": injection}] if injection else []) + history
                            current_round_assistant_id = request.assistant_message_id or None
                            round_anchor = current_round_assistant_id or snapshot_user_message_id
                            last_seen_objective = goal.objective
                        else:
                            # 普通首轮（用户消息 / interject skip_user_append 无 goal）。
                            round_messages = messages
                            current_round_assistant_id = request.assistant_message_id or None
                            round_anchor = snapshot_user_message_id
                    else:
                        goal = session_store.get_goal(session_id)
                        if goal is None:
                            break
                        if goal.status != "active" and not (goal.status == "budget_limited" and not budget_wrapup_done):
                            break
                        if goal.status == "budget_limited":
                            # 预算兜底轮：注入 budget_limit，仅一轮后停。
                            budget_wrapup_done = True
                            injection = render_budget_limit(goal)
                        else:
                            injection = _goal_injection(goal)
                        round_history = _current_history()
                        if not round_history:
                            break
                        round_messages = ([{"role": "system", "content": injection}] if injection else []) + round_history
                        current_round_assistant_id = f"goal-round-{uuid.uuid4().hex[:12]}"
                        round_anchor = current_round_assistant_id
                        last_seen_objective = goal.objective

                    # 通知前端本轮（续跑轮）已开始：提前给出该轮 assistant 消息 id，
                    # 让前端以 running 态建泡并流式渲染 delta（done 前不折叠进组）。
                    if round_index > 0:
                        yield {
                            "type": "goal_round_start",
                            "session_id": session_id,
                            "round": round_index,
                            "message_id": current_round_assistant_id or "",
                        }

                    # ---- per-round snapshot（回滚按轮隔离）----
                    inflight_anchor = round_anchor
                    inflight_pre = await asyncio.to_thread(_begin_round, round_anchor)
                    try:
                        round_started = time.monotonic()
                        round_iter = runtime.stream(round_messages, session_id, request.language, work_mode, autonomy)
                        async for ev in round_iter:
                            yield ev
                        round_elapsed = time.monotonic() - round_started
                    finally:
                        try:
                            await asyncio.to_thread(_end_round, inflight_anchor, inflight_pre)
                        except Exception:  # noqa: BLE001 - best-effort
                            logger.warning("round-end snapshot failed for %s", session_id, exc_info=True)
                        inflight_pre = None
                        inflight_anchor = None

                    # ---- accounting（仅 session 有 goal 时）----
                    if session_store.get_goal(session_id) is not None:
                        token_delta = int((last_context_usage or {}).get("used_tokens", 0) or 0)
                        try:
                            session_store.account_goal_usage(session_id, token_delta, round_elapsed)
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("account_goal_usage failed for %s", session_id, exc_info=True)
                        # 记录已完成的回合数（供 update_goal(blocked) 做引擎侧 ≥3 轮审计）。
                        try:
                            session_store.update_goal_round(session_id, round_index)
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("update_goal_round failed for %s", session_id, exc_info=True)

                    # HITL：保留 interrupt checkpoint，goal 保持 active，等前端 resume。
                    if interrupt_emitted:
                        break

                    # ---- 退化回复检测（「一直重複說話」）----
                    # 同一回复内大量重复（qwen3 模式）。硬停只拦一轮，循环继续会再退化；
                    # 累计 ≥2 轮退化 → 目标 blocked，避免跨轮持续重复。
                    if session_store.get_goal(session_id) is not None:
                        try:
                            session = session_store.require(session_id)
                            if session.messages and session.messages[-1].role == "assistant":
                                if is_degenerate_text(session.messages[-1].content):
                                    degenerate_rounds += 1
                                if degenerate_rounds >= 2:
                                    goal = session_store.get_goal(session_id)
                                    if goal is not None and goal.status == "active":
                                        blocked = session_store.update_goal_status(session_id, "blocked")
                                        if blocked is not None:
                                            _emit_goal_updated(session_id, blocked)
                                    break
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("degenerate check failed for %s", session_id, exc_info=True)

                    # ---- continue decision ----
                    goal = session_store.get_goal(session_id)
                    if goal is None:
                        break
                    if goal.status == "active":
                        round_index += 1
                        continue
                    if goal.status == "budget_limited" and not budget_wrapup_done:
                        # 预算刚超限：再跑一轮兜底（注入 budget_limit）后停。
                        round_index += 1
                        continue
                    break

                # 自然收口（goal 达成 / 暂停 / 清除 / 预算兜底完成）：通知前端整条
                # 续跑链结束，供其做最终 settle/收尾。HITL / error 不发（前者保持
                # waiting，后者 error 帧即终态）。
                if goal_stream_active and not interrupt_emitted and not error_emitted:
                    yield {"type": "goal_stream_end", "session_id": session_id}
            finally:
                # 生成器被取消（客户端断开 / Stop）：当前轮 snapshot 尚未 end 则兜底关闭。
                if inflight_pre is not None and inflight_anchor is not None:
                    try:
                        await asyncio.to_thread(_end_round, inflight_anchor, inflight_pre)
                    except Exception:  # noqa: BLE001 - best-effort
                        pass

        stream_iter = _goal_rounds_iter()

        _raw_stream_iter = stream_iter

        async def _locked_stream_iterator(it, lock):
            if lock is None:
                async for _ev in it:
                    yield _ev
            else:
                async with lock:
                    async for _ev in it:
                        yield _ev

        stream_iter = _locked_stream_iterator(_raw_stream_iter, None)

        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)

        def _on_event(event):
            event["session_id"] = session_id
            _handle_event(event)

        def _on_end():
            if not terminal_sent and not error_emitted and not interrupt_emitted:
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.warning("update_modes failed in _on_end for session %s", session_id, exc_info=True)
                return {"type": "done", "session_id": session_id, "content": accumulated_content, "stream_end": True}
            if interrupt_emitted and not terminal_sent and not error_emitted:
                # Interrupted for an approval/question: persist the partial reply
                # so a refresh keeps the question context, but do NOT emit a done
                # frame — the frontend is waiting for the user's decision and must
                # keep the message in `waiting` until the resume stream settles it.
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
            return None

        def _on_error(exc):
            # Catch GeneratorExit / asyncio.CancelledError on client disconnect.
            nonlocal terminal_sent
            # The in-flight turn was interrupted (client abort / Stop). The turn's
            # checkpoint thread is deleted by the stream's finally (turn-end
            # cleanup), and the next /chat/stream deletes it again before starting
            # fresh from session history — no dirty state survives.
            if accumulated_content and not terminal_sent:
                # Persist the partial reply with a GENERATED id (not the
                # client-supplied one) so the frontend's stream-settle
                # reconciliation can never adopt a half reply as a successful
                # commit. The persist still binds this turn's tool changes to a
                # message for rollback (see assign_message in _persist_assistant).
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [], message_id=None)
            elif not terminal_sent:
                # The stream was cut before any text was emitted (client
                # disconnected mid-tool / mid-thought). Persist a short
                # interrupted marker so the session does not look like the
                # assistant never answered — otherwise a half-finished turn
                # (e.g. a team member creation that already took effect) is
                # invisible to the user after refresh. _persist_assistant is
                # fully guarded and can never raise, so this path cannot drop
                # the terminal error event either. Generated id: never adopt as
                # a successful commit.
                _persist_assistant("（会话流被中断，回复未完成）", request.mode, "", request.model or "", [], message_id=None)
            try:
                session_store.update_modes(session_id, work_mode, autonomy)
            except Exception:  # noqa: BLE001 - never break the terminal event
                logger.warning("update_modes failed in _on_error for session %s", session_id, exc_info=True)
            # turn 报错（非客户端取消 / Stop）且 goal active → blocked（对标 codex
            # TurnError→Blocked 停循环）。Stop / 客户端断开不改 goal 状态（选项 A）。
            if not isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
                try:
                    goal = session_store.get_goal(session_id)
                    if goal is not None and goal.status == "active":
                        blocked = session_store.update_goal_status(session_id, "blocked")
                        if blocked is not None:
                            _emit_goal_updated(session_id, blocked)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.debug("error→blocked goal update failed for %s", session_id, exc_info=True)
            terminal_sent = True
            return {
                "type": "error",
                "session_id": session_id,
                "error": str(exc)[:400],
                "provider": getattr(runtime, "provider_name", "") or "",
                "model": getattr(runtime, "model_name", "") or "",
                "base_url": getattr(getattr(runtime, "llm", None), "base_url", "") or "",
            }

        # The turn runs as a background task that publishes to the session bus;
        # this endpoint subscribes (replay + live). Decoupling delivery from the
        # runtime generator means tool/worker status transitions reach the client
        # the moment they happen — even while the graph is blocked awaiting a
        # long-running tool (opencode-style session event bus). Snapshots are
        # taken per round inside ``_goal_rounds_iter`` (round 0 anchors on the
        # user message id; continuation rounds anchor on their own assistant id).
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    # SSE comment line: keep the connection (and the client's idle
                    # watchdog) alive while the agent is busy thinking/working.
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    event["session_id"] = session_id
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # Lifecycle binding: the connection is over (normal end or client
            # disconnect) ⟹ cancel the turn task. Its _publish_turn closes the
            # bus even under cancellation, so a late subscribe can never hang.
            turn_task.cancel()
            # Safety net: the client is gone, so force-terminate the underlying
            # stream consumer and release the session. Graceful cancellation can
            # stall behind a checkpoint DB lock (or a provider that ignores
            # cancellation), which would otherwise leave the session "active" and
            # reject the next edit/regenerate with 409 "session is still
            # generating". Both calls are idempotent. (The interrupted marker for
            # a client abort is set by _on_error in the producer's cancellation
            # handler — NOT here — so a normally-completed turn is never mistaken
            # for an interrupted one.)
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # The turn is over and the session released. Delete the disposable
            # checkpoint thread UNLESS the turn paused for an approval/question
            # (interrupt_emitted): that interrupt checkpoint must survive so the
            # resume (HITL) can continue the graph. Shielded so the delete still
            # completes even when the client disconnects right after `done`
            # (Starlette then cancels this generator, which would otherwise abort
            # the await mid-delete). The next /chat/stream also deletes the
            # thread, so a failed delete here is harmless.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class InterjectRequest(BaseModel):
    session_id: str = ""
    message: str = ""
    # 前端乐观渲染时生成的 user 消息 id，与 /chat/interject 持久化的 id 一致
    # （late-steer 自动续跑时以同一 id + skip_user_append 复用 history）。
    user_message_id: str = ""
    # 前端生成的 steer id：steer_injected 事件会原样带回该 id，前端据此匹配并
    # 从 pending 列表移除，避免「已注入当前轮」的插话又被误判为 late-steer 而二次执行。
    steer_id: str = ""
    attachments: list[dict[str, Any]] = []
    referenced_sessions: list[str] = []
    max_attachment_bytes: int = 25 * 1024 * 1024


@app.post("/chat/interject")
async def interject(request: InterjectRequest):
    """把一条排队消息插入正在进行的流式任务，引导 LLM 后续输出与思考方向。

    与「排队」的区别：插话不会暂停/终止当前流式任务，而是把消息送入
    per-session 的 steer 收件箱，由图内的 ``SteerInjectionMiddleware`` 在下一
    次模型呼叫边界注入为 HumanMessage。当前在飞的 ``llm.stream`` 不被中止。

    仅当会话正处于流式（有在飞的 /chat/stream 任务）时才可插话；否则 409，
    前端回退为普通发送。
    """
    session_id = request.session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 仅当会话正处于流式（有在飞的 /chat/stream 任务）时才可插话；否则 409，
    # 前端回退为普通发送。
    task = _stream_tasks.get(session_id)
    if task is None or task.done():
        raise HTTPException(
            status_code=409,
            detail="当前没有正在进行的任务，无法插话（消息已退回队列）。",
        )

    references = _resolve_references(request.referenced_sessions)
    steer_id = request.steer_id or str(uuid.uuid4())
    entry = SteerEntry(
        id=steer_id,
        content=request.message,
        ts=int(time.time() * 1000),
        user_message_id=request.user_message_id,
        attachments=request.attachments or [],
        references=references,
        max_attachment_bytes=request.max_attachment_bytes,
    )
    # 持久化为真实 user 消息：会话历史可回溯；late-steer 自动续跑时以
    # skip_user_append 直接复用 history（该 steer 已是最后一条 user 消息）。
    # interject=True 让前端不把它渲染为独立用户泡泡（内容由「收到插話」card 展示）。
    try:
        session_store.append_message(
            session_id,
            role="user",
            content=request.message,
            attachments=request.attachments or [],
            references=references,
            message_id=request.user_message_id or None,
            interject=True,
        )
    except Exception:  # noqa: BLE001 - persistence failure must not break the interject
        logger.exception("interject persist failed for session %s", session_id)

    steer_inbox.push(session_id, entry)
    try:
        session_event_bus.publish(
            session_id,
            {
                "type": "steer_admitted",
                "session_id": session_id,
                "steer_id": steer_id,
                "content": request.message,
            },
        )
    except Exception:  # noqa: BLE001 - a publish hiccup must not break the interject
        pass
    return {"status": "ok", "steer_id": steer_id, "session_id": session_id}

class SettingsUpdate(BaseModel):
    max_attachment_mb: int = 25
    revert_code: Optional[bool] = None


class LogSettingsUpdate(BaseModel):
    log_level: str = "INFO"


SETTING_FILE = str(settings.data_dir / ".coworker_settings.json")

DEFAULT_MAX_ATTACHMENT_MB = 25
MIN_MAX_ATTACHMENT_MB = 1
MAX_MAX_ATTACHMENT_MB = 1024


def read_user_max_attachment_mb() -> int:
    """Read the user-level attachment size cap (MB) from .coworker_settings.json.

    Falls back to 25 (the product default) when the file is missing or the key
    is absent. Clamped to the supported 1–1024 MB range.
    """
    try:
        data = json.loads(Path(SETTING_FILE).read_text() or "{}")
        if "max_attachment_mb" in data:
            return max(MIN_MAX_ATTACHMENT_MB, min(MAX_MAX_ATTACHMENT_MB, int(data["max_attachment_mb"])))
    except Exception:
        pass
    return DEFAULT_MAX_ATTACHMENT_MB


def read_user_revert_code() -> bool:
    """Read the user-level "edit message reverts code changes" toggle.

    Defaults to True (align with opencode/Codex: editing a message starts from
    a clean file state). Absent/legacy settings fall back to True.
    """
    try:
        data = json.loads(Path(SETTING_FILE).read_text() or "{}")
        return bool(data.get("revert_code", True))
    except Exception:
        return True


def _load_user_settings_file() -> dict:
    try:
        return json.loads(Path(SETTING_FILE).read_text() or "{}")
    except Exception:
        return {}


def read_user_memory_settings() -> dict:
    """Read the user-level memory settings from .coworker_settings.json.

    Returns only the overrides a user has saved (absent keys fall back to the
    env-var-driven MemoryConfig defaults).
    """
    data = _load_user_settings_file()
    stored = data.get("memory")
    if not isinstance(stored, dict):
        return {}
    known = {"enabled", "auto_extract"}
    return {k: v for k, v in stored.items() if k in known}


def _save_user_settings_file(payload: dict) -> None:
    """Persist .coworker_settings.json atomically (never a truncated JSON)."""
    from coworker.atomicio import atomic_write_text

    path = Path(SETTING_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))


def save_user_memory_settings(settings: dict) -> None:
    """Merge memory settings into .coworker_settings.json without clobbering others."""
    existing = _load_user_settings_file()
    existing["memory"] = {k: v for k, v in settings.items() if v is not None}
    _save_user_settings_file(existing)


def read_user_retention_settings() -> dict:
    """Read user-level data-retention overrides (trace/audit line caps)."""
    data = _load_user_settings_file()
    stored = data.get("retention")
    if not isinstance(stored, dict):
        return {}
    known = {"trace_lines", "audit_lines"}
    return {k: v for k, v in stored.items() if k in known and isinstance(v, int)}


def save_user_retention_settings(settings: dict) -> None:
    """Merge retention settings into .coworker_settings.json and apply at runtime."""
    from coworker.traces import set_trace_retention
    from coworker.workspace import set_tool_audit_retention

    path = Path(SETTING_FILE)
    existing = _load_user_settings_file()
    merged = {**read_user_retention_settings(), **{k: v for k, v in settings.items() if v is not None}}
    existing["retention"] = merged
    _save_user_settings_file(existing)
    # Apply immediately so the running process trims at the new cap.
    set_trace_retention(merged.get("trace_lines", 0))
    set_tool_audit_retention(merged.get("audit_lines", 0))


def apply_stored_retention_settings() -> None:
    """Apply persisted retention overrides at startup."""
    from coworker.traces import set_trace_retention
    from coworker.workspace import set_tool_audit_retention

    stored = read_user_retention_settings()
    if stored:
        set_trace_retention(stored.get("trace_lines", 0))
        set_tool_audit_retention(stored.get("audit_lines", 0))


def apply_stored_memory_settings() -> None:
    """Overlay user-saved memory settings onto the runtime MemoryConfig."""
    overrides = read_user_memory_settings()
    if not overrides:
        return
    current = memory_manager.config
    memory_manager.config = MemoryConfig(
        enabled=overrides.get("enabled", current.enabled),
        inject_char_limit=current.inject_char_limit,
        auto_extract=overrides.get("auto_extract", current.auto_extract),
        nudge_interval=current.nudge_interval,
        extract_model=current.extract_model,
        max_prior_loss=current.max_prior_loss,
        dream_idle_seconds=current.dream_idle_seconds,
    )


apply_stored_memory_settings()
apply_stored_retention_settings()


@app.get("/settings")
async def get_settings():
    """Get user-level settings (attachment size cap + edit revert)."""
    return {
        "max_attachment_mb": read_user_max_attachment_mb(),
        "revert_code": read_user_revert_code(),
    }


@app.post("/settings")
async def set_settings(request: SettingsUpdate):
    """Update user-level settings (attachment size cap + edit revert)."""
    max_attachment_mb = max(MIN_MAX_ATTACHMENT_MB, min(MAX_MAX_ATTACHMENT_MB, request.max_attachment_mb))
    try:
        # Merge so the two keys don't clobber each other across saves.
        existing: dict = _load_user_settings_file()
        existing.update({"max_attachment_mb": max_attachment_mb})
        if request.revert_code is not None:
            existing["revert_code"] = bool(request.revert_code)
        _save_user_settings_file(existing)
    except Exception as exc:
        return {
            "status": "error",
            "max_attachment_mb": max_attachment_mb,
            "revert_code": read_user_revert_code(),
            "detail": str(exc),
        }
    return {
        "status": "ok",
        "max_attachment_mb": max_attachment_mb,
        "revert_code": read_user_revert_code(),
    }


class WebConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    max_results: Optional[int] = None
    search_depth: Optional[str] = None
    fetch_enabled: Optional[bool] = None


class TavilyKeyUpdate(BaseModel):
    api_key: str


class WebTestRequest(BaseModel):
    query: str = "opencode web search"
    max_results: Optional[int] = None
    api_key: Optional[str] = None


@app.get("/api/web/config")
async def get_web_config():
    """Non-secret web capability settings + whether a search key is configured."""
    block = read_web_block(settings.data_dir)
    return {
        "enabled": bool(block.get("enabled")),
        "provider": str(block.get("provider") or "tavily"),
        "max_results": int(block.get("max_results") or 8),
        "search_depth": str(block.get("search_depth") or "basic"),
        "fetch_enabled": bool(block.get("fetch_enabled")),
        "api_key_configured": tavily_key_configured(settings.data_dir),
    }


@app.post("/api/web/config")
async def save_web_config(request: WebConfigUpdate):
    """Persist non-secret web settings to .coworker_settings.json (merge)."""
    patch = {
        k: getattr(request, k)
        for k in ("enabled", "provider", "max_results", "search_depth", "fetch_enabled")
        if getattr(request, k) is not None
    }
    if not patch:
        return await get_web_config()
    try:
        block = write_web_block(settings.data_dir, patch)
    except OSError as exc:  # noqa: BLE001 - settings persistence must not fail the request
        logger.warning("Failed to persist web settings: %s", exc)
        block = read_web_block(settings.data_dir)
    return {
        "enabled": bool(block.get("enabled")),
        "provider": str(block.get("provider") or "tavily"),
        "max_results": int(block.get("max_results") or 8),
        "search_depth": str(block.get("search_depth") or "basic"),
        "fetch_enabled": bool(block.get("fetch_enabled")),
        "api_key_configured": tavily_key_configured(settings.data_dir),
    }


@app.post("/api/web/tavily/key")
async def set_web_tavily_key(request: TavilyKeyUpdate):
    """Store the Tavily API key in the OS secret store (never returned)."""
    api_key = (request.api_key or "").strip()
    if not api_key:
        return {"status": "error", "detail": "API key is empty"}
    try:
        set_tavily_key(settings.data_dir, api_key)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "api_key_configured": True}


@app.delete("/api/web/tavily/key")
async def clear_web_tavily_key():
    """Remove the stored Tavily API key."""
    delete_tavily_key(settings.data_dir)
    return {"status": "ok", "api_key_configured": False}


@app.post("/api/web/test")
async def test_web_search(request: WebTestRequest):
    """Run a single search to verify a key works (pending key preferred)."""
    api_key = (request.api_key or "").strip() or get_tavily_key(settings.data_dir)
    if not api_key:
        return {"ok": False, "message": "Tavily API key is not configured", "results_count": 0}
    block = read_web_block(settings.data_dir)
    result = tavily_search(
        request.query,
        api_key,
        max_results=request.max_results or int(block.get("max_results") or 8),
        search_depth=str(block.get("search_depth") or "basic"),
    )
    if result.get("error"):
        return {"ok": False, "message": result["error"], "results_count": 0}
    return {"ok": True, "message": "Search succeeded", "results_count": len(result.get("results") or [])}


class BrowserBridgeUpdate(BaseModel):
    port: int
    token: str


@app.get("/api/browser/bridge")
async def get_browser_bridge():
    """Bridge info Electron registered for the embedded browser (may be absent)."""
    from coworker.browser.bridge_client import read_browser_bridge

    info = read_browser_bridge(settings.data_dir)
    if info is None:
        return {"registered": False}
    return {"registered": True, "port": info.port, "token": info.token}


@app.post("/api/browser/bridge")
async def register_browser_bridge(request: BrowserBridgeUpdate):
    """Electron main registers its loopback bridge here at startup.

    Only the desktop app writes this; the bridge client only reads it back.
    """
    from coworker.browser.bridge_client import write_browser_bridge

    write_browser_bridge(settings.data_dir, request.port, request.token)
    return {"ok": True}


class SessionCreateRequest(BaseModel):
    title: str = ""
    project_id: str = ""
    agent_id: str = ""

class SessionRenameRequest(BaseModel):
    title: str

class ProjectCreateRequest(BaseModel):
    name: str
    workspace_path: str
    mode: str = ORG_MODE_SINGLE

class ProjectRenameRequest(BaseModel):
    name: str

@app.get("/sessions")
async def list_sessions(project_id: str | None = None):
    return {"status": "ok", "sessions": session_store.list_sessions(project_id)}


@app.get("/sessions/active")
async def list_active_sessions():
    """Return session ids that currently have an in-flight stream (running/active)."""
    return {"status": "ok", "session_ids": sorted(agent_registry.checkpoint_manager.active_sessions())}

@app.post("/sessions")
async def create_session(request: SessionCreateRequest):
    if not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    try:
        project_store.require(request.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = session_store.create(request.title, project_id=request.project_id, agent_id=request.agent_id)
    return {"status": "ok", "session": session.public()}

@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.full()}

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    # Hard-terminate the session's in-flight stream/task first, so a delete
    # always succeeds even while the session is mid-generation. The streaming
    # guard below then returns promptly once the teardown marks the session idle.
    _hard_stop_session_stream(session_id)
    await _guard_session_not_streaming(session_id)
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    # Checkpoint teardown just unlinks a per-session JSON file; still run it in
    # the background so a huge file never pushes the delete past the caller's
    # timeout (the session record is already gone, so cleanup is safe).
    asyncio.create_task(agent_registry.forget_runtime_checkpoint(session_id))
    agent_registry.change_store.delete_session(session_id)
    agent_registry.snapshot_manager.delete_session(session_id)
    _cleanup_session_screenshots(session_id)
    return {"status": "ok"}


def _cleanup_session_screenshots(session_id: str) -> None:
    """Remove the session's externalized screenshots (best-effort)."""
    try:
        from coworker.browser.bridge_client import screenshots_dir_for

        target = screenshots_dir_for(settings.data_dir, session_id)
        if target is not None and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except Exception:  # noqa: BLE001 - cleanup must never break a delete
        logger.debug("screenshot cleanup failed for %s", session_id, exc_info=True)

@app.post("/sessions/{session_id}/stop")
async def stop_session_stream(session_id: str):
    """Explicitly stop the session's in-flight generation (user pressed Stop).

    A client abort alone is not enough to free the session: uvicorn/Starlette
    can fail to propagate the disconnect promptly, so the stream keeps running
    and the session stays marked "active" — which makes the next edit or
    regenerate fail with ``409 session is still generating``. Force-cancelling
    the stream task (and marking the session idle) makes Stop deterministic and
    idempotent: stopping an idle session is a no-op.
    """
    _force_stop_session_stream(session_id)
    return {"status": "ok", "session_id": session_id}

@app.post("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: SessionRenameRequest):
    try:
        session = session_store.rename(session_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.public()}


class GenerateTitleRequest(BaseModel):
    first_user_message: str = ""
    assistant_response: str = ""
    language: Language = "zh"


@app.post("/sessions/{session_id}/generateTitle")
async def generate_title_endpoint(session_id: str, request: GenerateTitleRequest):
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not session.title_auto:
        return {"status": "ok", "title": session.title}
    user_message = ""
    assistant_message = ""
    for message in session.messages:
        if message.role == "user" and not user_message:
            user_message = message.content
        elif message.role == "assistant" and not assistant_message:
            assistant_message = message.content
        if user_message and assistant_message:
            break
    if not user_message:
        return {"status": "ok", "title": session.title}
    from coworker.agent.core import generate_title
    new_title = await asyncio.to_thread(
        generate_title,
        user_message,
        assistant_message or request.assistant_response or "",
        request.language,
    )
    final_title = new_title or session.title
    session_store.rename(session_id, final_title)
    return {"status": "ok", "title": final_title}


class GoalSetRequest(BaseModel):
    session_id: str
    objective: str
    token_budget: int | None = None
    # 前端乐观渲染的 /goal user 泡泡元数据：持久化进 session 保证重载/重进会话不消失，
    # 且用 user_message_id 保证前后端消息 id 一致（可编辑/回退）。
    user_message_id: str | None = None
    provider: str = ""
    model: str = ""
    work_mode: str = ""
    autonomy: str = ""


class GoalControlRequest(BaseModel):
    session_id: str


class GoalEditRequest(BaseModel):
    session_id: str
    objective: str


def _require_goal(session_id: str):
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session_id


@app.post("/goal/set")
async def goal_set(request: GoalSetRequest):
    """设定并激活目标：置 active、计数清零、广播 ``goal_updated``。

    空闲会话下前端拿到返回的 ``active`` 后必须立即发起一次
    ``skip_user_append=True`` 的 /chat/stream 触发续跑（见设计文档 §3.3.2）。
    """
    _require_goal(request.session_id)
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")
    goal = session_store.set_goal(request.session_id, objective, request.token_budget)
    # 持久化目标设定 user 消息：前端乐观渲染的 /goal 泡泡在重载/重进会话后不消失，
    # 且沿用前端传入的 id（编辑/回退时按 id 检索不会 404）。
    try:
        session = session_store.require(request.session_id)
        session_store.append_message(
            request.session_id,
            role="user",
            content=objective,
            mode="single",
            provider=request.provider or "",
            model=request.model or "",
            work_mode=request.work_mode or session.work_mode,
            autonomy=request.autonomy or session.autonomy,
            message_id=request.user_message_id or None,
        )
    except Exception:  # noqa: BLE001 - 持久化失败不影响 goal 设定
        logger.warning("goal/set persist user message failed for %s", request.session_id, exc_info=True)
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}


@app.post("/goal/pause")
async def goal_pause(request: GoalControlRequest):
    """暂停目标作用（不中止进行中的流）：仅 active/budget_limited 可暂停。"""
    _require_goal(request.session_id)
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status not in {"active", "budget_limited"}:
        raise HTTPException(status_code=409, detail=f"cannot pause a {current.status} goal")
    goal = session_store.update_goal_status(request.session_id, "paused")
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}


@app.post("/goal/resume")
async def goal_resume(request: GoalControlRequest):
    """恢复目标作用：仅 paused 可恢复。"""
    _require_goal(request.session_id)
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status != "paused":
        raise HTTPException(status_code=409, detail="goal is not paused")
    goal = session_store.update_goal_status(request.session_id, "active")
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}


@app.post("/goal/edit")
async def goal_edit(request: GoalEditRequest):
    """修改 objective（仅 active 可编辑，状态保持 active）。"""
    _require_goal(request.session_id)
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status != "active":
        raise HTTPException(status_code=409, detail=f"cannot edit a {current.status} goal")
    goal = session_store.update_goal_objective(request.session_id, objective)
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}


@app.post("/goal/clear")
async def goal_clear(request: GoalControlRequest):
    """清除目标：终止一切续跑并清状态条。"""
    _require_goal(request.session_id)
    cleared = session_store.clear_goal(request.session_id)
    if cleared:
        _emit_goal_cleared(request.session_id)
    return {"status": "ok", "cleared": cleared}


@app.get("/goal")
async def goal_get(session_id: str):
    _require_goal(session_id)
    goal = session_store.get_goal(session_id)
    return {"status": "ok", "goal": goal.to_dict() if goal is not None else None}


class EditMessageRequest(BaseModel):
    content: str
    work_mode: Optional[str] = None
    autonomy: Optional[str] = None
    language: Language = "zh"
    revert_code: bool = True
    assistant_message_id: str = ""
    # Provider/model for the re-run. When empty, falls back to the session's
    # last-run provider/model (legacy clients).
    provider_id: str = ""
    model: str = ""


class RegenerateRequest(BaseModel):
    language: Language = "zh"
    assistant_message_id: str = ""
    # Provider/model for the re-run. When empty, falls back to the session's
    # last-run provider/model (legacy clients).
    provider_id: str = ""
    model: str = ""


class EditBeginRequest(BaseModel):
    revert_code: bool = True


def _hard_stop_session_stream(session_id: str) -> None:
    """Hard-terminate any in-flight stream/task for a session.

    Called by session delete so a session can be removed even while it is
    mid-generation (regular stream, regenerate/edit). Cancels the task
    consuming the session's SSE stream; ``_tracked_stream``'s ``finally``
    marks the session idle so the caller's streaming guard returns promptly.
    """
    task = _stream_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()


def _force_stop_session_stream(session_id: str) -> None:
    """Force-stop a session's in-flight generation and release it for new work.

    A client abort (Stop button) is only observable to the backend as a socket
    disconnect, which uvicorn/Starlette can fail to propagate promptly — the
    SSE generator keeps running (heartbeats into a dead socket) and the runtime
    graph can stall behind a checkpoint DB lock, so ``mark_idle`` (inside the
    stream consumer's ``finally``) may never run. The session then stays marked
    "active" forever and every later edit/regenerate for it is rejected with
    ``409 session is still generating``.

    Cancelling the stream-consuming task directly (``_hard_stop_session_stream``)
    makes Stop deterministic: the task's cancellation closes the tracked stream,
    which runs ``mark_idle``, and the explicit ``mark_idle`` below is an
    idempotent safety net for the case where the graph never unwinds. The turn's
    checkpoint thread is deleted by the stream's finally, and the next run
    rebuilds fresh from session history anyway.
    """
    _hard_stop_session_stream(session_id)
    agent_registry.checkpoint_manager.mark_idle(session_id)


async def _guard_session_not_streaming(session_id: str) -> None:
    """Reject destructive session mutations while a stream is in flight.

    Truncating/deleting a session (or its checkpoint) mid-stream can silently
    drop the reply the running agent is still producing, so refuse with 409
    instead of racing it.

    A short grace period covers the teardown window after the client aborts a
    stream: the abort races the reconnect (e.g. a rapid edit), so instantly
    returning 409 would reject an edit the user just issued.
    """
    for _ in range(30):
        if session_id not in agent_registry.checkpoint_manager.active_sessions():
            return
        await asyncio.sleep(0.1)
    raise HTTPException(
        status_code=409,
        detail="session is still generating; wait for the current response to finish",
    )


def _provider_id_for_model(provider_name: str, model: str) -> str:
    if provider_name:
        try:
            config = provider_manager.load()
            for provider in config.providers:
                if provider.name == provider_name or provider.model == model:
                    return provider.id
        except Exception:
            pass
    return ""


def _provider_name_for_id(provider_id: str) -> str:
    """Provider display name for a provider id ('' when unknown/disabled)."""
    if not provider_id:
        return ""
    try:
        provider = provider_manager.load().find_enabled(provider_id)
        return provider.name if provider is not None else ""
    except Exception:  # noqa: BLE001 - best-effort
        return ""


def _resolve_run_provider(session, provider_id: str, model: str) -> tuple[str, str]:
    """Resolve ``(provider_id, model)`` for regenerate/edit re-runs.

    Prefers the caller's explicit provider/model (the current UI selection — the
    same source /chat/stream and the context-usage preview use), so every run
    path and the topbar meter agree on the window. Falls back to the session's
    last-run provider/model, then to the default enabled provider, so invalid
    selections and legacy clients keep working.
    """
    try:
        config = provider_manager.load()
    except Exception:  # noqa: BLE001 - a broken config must never block a run
        return "", ""
    requested = config.find_enabled(provider_id) if provider_id else None
    if requested is not None:
        return requested.id, (model or requested.model)
    provider_name, stored_model = _session_provider_context(session)
    if provider_name:
        sid = _provider_id_for_model(provider_name, stored_model)
        if sid:
            return sid, stored_model or model
    first = next((p for p in config.providers if p.enabled), None)
    if first is not None:
        return first.id, (model or first.model)
    return "", ""


def _session_message_history(session) -> list[dict[str, Any]]:
    """Build the message history (role/content) that should be replayed when
    re-running the agent from a truncated point."""
    history = []
    for message in session.messages:
        if message.role not in {"user", "assistant"} or not message.content:
            continue
        if message.role == "user":
            history.append({"role": "user", "content": format_user_message(message.content, message.attachments, message.references)})
        else:
            history.append({"role": "assistant", "content": message.content})
    return history


def _session_referenced_ids(session) -> set[str]:
    """Collect every session id the user referenced across the session's user
    messages (used to restore the read_session allowlist on rerun paths)."""
    ids: set[str] = set()
    for message in session.messages:
        if message.role != "user":
            continue
        for ref in message.references or []:
            ref_id = str(ref.get("id") or "").strip()
            if ref_id:
                ids.add(ref_id)
    return ids


def _session_provider_context(session) -> tuple[str, str]:
    for message in reversed(session.messages):
        if message.role == "assistant" and message.provider:
            return message.provider, message.model
    return "", ""


def _history_content_chars(content) -> int:
    """Char length of a stored message's TEXT blocks only (media skipped).

    Mirrors the runtime meter (``_msg_chars`` → ``_message_text``), which counts
    images at a per-item vision cost, never as text. Counting ``str(content)``
    on a multimodal list would charge the full base64 data URL as ~400x too many
    tokens, making every image-bearing session preview as "full".
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += len(str(part.get("text") or ""))
        return total
    return 0


def _history_content_tokens(content) -> int:
    """Token estimate of a stored message's content, matching the runtime meter.

    Text blocks use the CJK/base64-aware estimator; media blocks (image/audio/
    video/file) are charged the per-item vision cost — the same accounting as
    ``coworker.context.message_tokens``.
    """
    from coworker.context import TOKENS_PER_IMAGE_DEFAULT, estimate_text_tokens

    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                total += estimate_text_tokens(str(part.get("text") or ""))
            elif ptype in ("image", "image_url", "audio", "video", "file"):
                total += TOKENS_PER_IMAGE_DEFAULT
            else:
                total += estimate_text_tokens(str(part.get("text") or part.get("input") or ""))
        return total
    return 0


def _session_context_usage_snapshot(session, provider_id: str, model: str) -> dict | None:
    """Lightweight context-usage preview for a session, resolved from the
    CURRENT provider config (so older sessions immediately reflect model-window
    changes instead of a stale last-run value). Field shape matches the
    streaming ``context_usage`` event so the client reuses the same mapping.
    A subsequent run supersedes this with a fresh calibrated figure."""
    pm = provider_manager.load()
    provider = pm.find_enabled(provider_id) if provider_id else None
    if provider is None:
        enabled = [p for p in pm.providers if p.enabled]
        provider = enabled[0] if enabled else None
    if provider is None:
        return None
    budget_chars, window_tokens, source, warning, max_output = _runtime_context_budget(provider, model or None)
    from coworker.context import CalibrationStore, effective_input_limit, get_calibration_store

    budget_tokens = context_budget_tokens(window_tokens, max_output)
    effective = effective_input_limit(window_tokens, max_output)
    # Prefer the last FULL measurement persisted from a real run (system prompt +
    # tool schemas + messages + overhead, calibrated) — that is the true request
    # size. Fall back to a message-only estimate only for sessions that have
    # never run (nothing to measure yet).
    stored_cal = int(session.context_used_tokens_calibrated or 0)
    if stored_cal > 0:
        used_tokens = int(session.context_used_tokens or 0)
        used_tokens_calibrated = stored_cal
        used_chars = int(session.context_used_chars or 0)
        calibration_factor = float(session.context_calibration_factor or 0.0) or 1.0
    else:
        history = _session_message_history(session)
        used_chars = sum(_history_content_chars(m.get("content")) for m in history)
        used_tokens = sum(_history_content_tokens(m.get("content")) for m in history)
        # Reuse the same closed-loop calibration factor a real run would apply for
        # this (provider, model), so the preview does not jump on the next run.
        calibration_factor = 1.0
        if provider is not None:
            resolved_model = (model or None) or provider.model
            store = get_calibration_store(settings.data_dir)
            calibration_factor = store.get(CalibrationStore.key_for(provider.id, resolved_model or ""))
        used_tokens_calibrated = int(round(used_tokens * calibration_factor))
    return {
        "type": "context_usage",
        "used_chars": used_chars,
        "budget_chars": budget_chars,
        "used_tokens": used_tokens,
        "used_tokens_calibrated": used_tokens_calibrated,
        "calibration_factor": round(calibration_factor, 3),
        "budget_tokens": budget_tokens,
        "active_budget_tokens": budget_tokens,
        "window_tokens": window_tokens,
        "effective_window_tokens": effective,
        "max_output_tokens": max_output,
        # Over the effective input ceiling (window − max_output), which is what
        # the model actually receives — consistent with the live event.
        "compressed": used_tokens > effective,
        "compacted": False,
        "compact_count": 0,
        "window_source": source,
        "window_warning": warning,
    }


@app.get("/sessions/{session_id}/context-usage")
async def session_context_usage(session_id: str, provider_id: str = "", model: str = ""):
    # Surface the CURRENT context-window usage for a session without running it,
    # so opening an older session shows the live window (e.g. 192k after a
    # context_window change) rather than the stale last-run figure (e.g. 252k).
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        snap = _session_context_usage_snapshot(session, provider_id, model)
    except Exception:  # noqa: BLE001 - never let a preview crash the open flow
        snap = None
    if snap is None:
        raise HTTPException(status_code=404, detail="no enabled provider")
    return {"status": "ok", "context_usage": snap}


async def _revert_turn_changes(
    session_id: str,
    message_id: str,
    dropped_assistant_ids: list[str],
    *,
    mark: bool = True,
) -> dict[str, Any]:
    """Two-layer revert of the code changes a turn produced.

    Layer 1 (record-level, authoritative): ``revert_changes`` restores every
    ``active`` record via safe inverse edits. Layer 2 (git content carrier):
    ``revert_turn`` restores records whose content was too large for the change
    store and, when the workspace is exclusive, any shell-driven changes that
    left no record. Reverts that succeed are marked ``reverted`` so a later
    "redo" can re-apply them. Returns the merged ``revert_summary``.
    """
    revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0, "reverted_paths": []}
    if not dropped_assistant_ids:
        return revert_summary
    try:
        workspace = workspace_controller.workspace_for_session(session_id)
        # ① Record-level revert (authoritative, session-scoped).
        summary = agent_registry.change_store.revert_changes(session_id, dropped_assistant_ids, workspace)
        reverted_ids = [c.get("id") for c in summary.get("reverted", []) if c.get("id")]
        conflict_ids = [c.get("id") for c in summary.get("conflicts", []) if c.get("id")]
        # ② Git content carrier: records whose full content was too large for
        # the change store (and shell-driven changes, when exclusive) are
        # restored from the snapshot trees (session-scoped + current==post gated).
        dropped_records = agent_registry.change_store.changes_for_message_ids(session_id, dropped_assistant_ids)
        too_large_paths = {str(c.get("file_path") or "") for c in dropped_records if c.get("too_large")}
        record_paths = {str(c.get("file_path") or "") for c in dropped_records}
        git_summary = await asyncio.to_thread(
            agent_registry.snapshot_manager.revert_turn, session_id, message_id, workspace, too_large_paths=too_large_paths, record_paths=record_paths,
        )
        git_reverted = git_summary.get("reverted") or []
        git_conflicts = git_summary.get("conflicts") or []
        if mark:
            if reverted_ids:
                agent_registry.change_store.mark_reverted(session_id, reverted_ids, message_id)
            if conflict_ids:
                agent_registry.change_store.mark_abandoned(session_id, conflict_ids)
        revert_summary = {
            "reverted_count": summary.get("reverted_count", 0) + len(git_reverted),
            "conflict_count": summary.get("conflict_count", 0) + len(git_conflicts),
            "total": summary.get("total", 0) + len(git_reverted) + len(git_conflicts),
            "reverted_paths": [c.get("path") for c in summary.get("reverted", []) if c.get("path")] + [c.get("path") for c in git_reverted if c.get("path")],
        }
    except (ValueError, KeyError) as exc:
        # Workspace unavailable or session missing: skip file revert but keep the
        # caller's flow (message edit still allowed).
        logger.warning("_revert_turn_changes: code revert skipped for %s: %s", session_id, exc)
    except Exception as exc:  # noqa: BLE001 - revert must never break the edit/regenerate request
        logger.warning("_revert_turn_changes: code revert failed for %s: %s", session_id, exc, exc_info=True)
    return revert_summary


@app.post("/sessions/{session_id}/messages/{message_id}/redo")
async def redo_message(session_id: str, message_id: str, keep_state: bool = False):
    """Re-apply the file changes that editing the given user message reverted
    (undo-the-undo). Each ``reverted`` change record bound to that edit is
    restored to its recorded ``after`` content, with conflict detection: a
    record whose file no longer matches the recorded ``before`` state (e.g. the
    user hand-edited it) is reported as a conflict and left untouched. Records
    restored successfully are removed from the change store.

    With ``keep_state=true`` (cancelling a pending edit) the records and the
    snapshot pair return to ``active`` instead of being discarded, so the same
    message can be reverted again by a later edit.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session_store.require(session_id)
        workspace = workspace_controller.workspace_for_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records = agent_registry.change_store.reverted_records(session_id, message_id)
    restored: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for record in records:
        result = workspace.redo_change(record)
        if result.get("status") == "restored":
            restored.append(result)
        else:
            conflicts.append(result)
    restored_ids = [r.get("id") for r in restored if r.get("id")]
    if restored_ids:
        if keep_state:
            agent_registry.change_store.mark_active(session_id, restored_ids)
        else:
            agent_registry.change_store.delete_records(session_id, restored_ids)
    # Git-backed redo for the records whose content was too large for the
    # change store (and any shell-driven changes reverted during the edit).
    try:
        git_redo = await asyncio.to_thread(
            agent_registry.snapshot_manager.redo_turn, session_id, message_id, workspace, keep_state=keep_state,
        )
    except Exception:  # noqa: BLE001 - snapshot redo must never break the request
        logger.warning("redo_message: snapshot redo skipped for %s: %s", session_id, exc_info=True)
        git_redo = {}
    restored += git_redo.get("restored") or []
    conflicts += git_redo.get("conflicts") or []
    return {
        "status": "ok",
        "session_id": session_id,
        "message_id": message_id,
        "restored": restored,
        "conflicts": conflicts,
        "restored_count": len(restored),
        "conflict_count": len(conflicts),
    }


@app.post("/sessions/{session_id}/messages/{message_id}/edit-begin")
async def edit_message_begin(session_id: str, message_id: str, request: EditBeginRequest):
    """Enter edit mode for a user message: revert its downstream file changes
    immediately (two-layer revert) so the user sees a clean file state while
    editing. No messages are truncated or re-run yet. If ``revert_code`` is off
    this is a no-op revert (returns zeros). A later ``edit-cancel`` restores the
    files and returns the records to ``active``.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        if session.messages[index].role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="message not found") from exc

    dropped_assistant_ids = [m.id for m in session.messages[index + 1 :] if m.role == "assistant"]
    if request.revert_code and dropped_assistant_ids:
        revert_summary = await _revert_turn_changes(session_id, message_id, dropped_assistant_ids, mark=True)
    else:
        revert_summary = {"reverted_count": 0, "conflict_count": 0, "total": 0, "reverted_paths": []}
    return {"status": "ok", "session_id": session_id, "message_id": message_id, **revert_summary}


@app.post("/sessions/{session_id}/messages/{message_id}/edit-cancel")
async def edit_message_cancel(session_id: str, message_id: str):
    """Cancel a pending edit: restore the files the ``edit-begin`` reverted and
    return the change records and snapshot pair to ``active`` so the message can
    be reverted again. Conflicts (files changed in the meantime) are reported
    and left untouched.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session_store.require(session_id)
        workspace = workspace_controller.workspace_for_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records = agent_registry.change_store.reverted_records(session_id, message_id)
    restored: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for record in records:
        result = workspace.redo_change(record)
        if result.get("status") == "restored":
            restored.append(result)
        else:
            conflicts.append(result)
    restored_ids = [r.get("id") for r in restored if r.get("id")]
    if restored_ids:
        agent_registry.change_store.mark_active(session_id, restored_ids)
    try:
        git_redo = await asyncio.to_thread(
            agent_registry.snapshot_manager.redo_turn, session_id, message_id, workspace, keep_state=True,
        )
    except Exception:  # noqa: BLE001 - snapshot redo must never break the request
        logger.warning("edit_message_cancel: snapshot restore skipped for %s: %s", session_id, exc_info=True)
        git_redo = {}
    restored += git_redo.get("restored") or []
    conflicts += git_redo.get("conflicts") or []
    return {
        "status": "ok",
        "session_id": session_id,
        "message_id": message_id,
        "restored": restored,
        "conflicts": conflicts,
        "restored_count": len(restored),
        "conflict_count": len(conflicts),
    }


@app.post("/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_message(session_id: str, message_id: str, request: RegenerateRequest):
    """Re-run the assistant reply for the user message that precedes the given
    assistant message (or, if given a user message, for that user message).
    Truncates after that user message and streams a fresh reply."""
    await _guard_session_not_streaming(session_id)
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        target_message = session.messages[index]
        # Walk back to the triggering user message.
        user_index = index
        if target_message.role == "assistant":
            user_index = index - 1
        while user_index >= 0 and session.messages[user_index].role != "user":
            user_index -= 1
        if user_index < 0:
            raise HTTPException(status_code=400, detail="No user message to regenerate from")
        user_message = session.messages[user_index]
        # Regeneration drops the assistant messages after this user message and
        # reverts their file changes (two-layer revert, same as edit) so the
        # re-run starts from a clean file state. Reverted records stay redo-able;
        # conflicted ones are hidden.
        dropped_assistant_ids = [m.id for m in session.messages[user_index:] if m.role == "assistant"]
        revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0}
        if dropped_assistant_ids:
            revert_summary = await _revert_turn_changes(session_id, user_message.id, dropped_assistant_ids, mark=True)
        session_store.truncate_from(session_id, user_message.id)
        # Checkpoint delete is routed through the single shared checkpointer
        # (single-writer model) — no thread needed, never contends.
        await agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_id, model = _resolve_run_provider(session, request.provider_id, request.model)
        provider_name = _provider_name_for_id(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(session.work_mode)
    autonomy = normalize_autonomy(session.autonomy)
    language = request.language
    try:
        workspace_path = workspace_controller.workspace_for_session(session_id).root
    except ValueError as exc:
        # The session's project workspace is missing/invalid. Refuse instead of
        # silently re-running in the DEFAULT workspace (which would write files
        # in the wrong place under the project's session).
        raise HTTPException(status_code=400, detail=f"workspace unavailable: {exc}") from exc
    except Exception:  # noqa: BLE001 - genuinely workspace-less sessions use the default
        workspace_path = None

    async def event_stream():
        accumulated_content = ""
        terminal_sent = False
        interrupt_emitted = False

        # Surface the file-revert result to the UI as the first event, so the
        # client can show "reverted N file changes / restore" right away.
        if revert_summary.get("reverted_count", 0) > 0 or revert_summary.get("conflict_count", 0) > 0:
            yield f"data: {json.dumps({'type': 'revert_summary', 'session_id': session_id, **revert_summary}, ensure_ascii=False)}\n\n"

        def _persist_partial():
            nonlocal accumulated_content, terminal_sent
            if not accumulated_content or terminal_sent:
                return
            terminal_sent = True
            try:
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=accumulated_content,
                    mode="single",
                    provider=provider_name,
                    model=model,
                    work_mode=work_mode,
                    autonomy=autonomy,
                    # Generated id (not the client-supplied one): a partial reply
                    # persisted on error must never be adopted as a successful
                    # commit by the frontend's stream-settle reconciliation.
                    message_id=None,
                    parts=[],
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                logger.exception("Failed to persist partial rerun for session %s", session_id)

        def _on_event(event):
            nonlocal accumulated_content, terminal_sent, interrupt_emitted
            event["session_id"] = session_id
            if event.get("type") in ("approval_required", "question_required"):
                # Agent interrupted to ask the user: the turn is NOT complete. No
                # synthetic done will follow, so persist the partial reply on end
                # (message_id=None) to keep the question context after a refresh.
                interrupt_emitted = True
            if event.get("type") == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if event.get("type") == "done":
                terminal_sent = True
                try:
                    session = session_store.append_message(
                        session_id,
                        role="assistant",
                        content=event["content"],
                        mode="single",
                        provider=event.get("provider") or provider_name,
                        model=event.get("model") or model,
                        work_mode=work_mode,
                        autonomy=autonomy,
                        message_id=request.assistant_message_id or None,
                        parts=event.get("parts") or [],
                    )
                    last = session.messages[-1] if session.messages else None
                    if last is not None:
                        agent_registry.change_store.assign_message(session_id, last.id)
                except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                    logger.exception("Failed to persist rerun assistant message for session %s", session_id)

        def _on_end():
            # Normal end after an interrupt (question/approval asked): no done
            # frame was emitted — persist the partial so the turn survives refresh.
            if interrupt_emitted and not terminal_sent:
                _persist_partial()
            return None

        def _on_error(exc):
            # Persist whatever was produced before the failure so tool-change
            # records from this stream stay bound to a message (rollback needs
            # the binding).
            _persist_partial()
            return {"type": "error", "session_id": session_id, "error": str(exc)[:400]}

        snapshot_workspace = None
        try:
            snapshot_workspace = workspace_controller.workspace_for_session(session_id)
        except (ValueError, KeyError, Exception):  # noqa: BLE001 - default/workspace-less sessions skip snapshot
            snapshot_workspace = None
        snapshot_pre = None
        if snapshot_workspace is not None:
            snapshot_pre = await asyncio.to_thread(
                agent_registry.snapshot_manager.begin_turn, session_id, user_message.id, snapshot_workspace,
            )
        # Same bus-fed turn as /chat/stream: live delivery even while the graph
        # is blocked on a long-running tool / worker.
        stream_iter = agent_registry.rerun_stream(
            history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids, workspace_path=workspace_path, agent=session.agent_id or DEFAULT_AGENT, project_id=session.project_id or None,
        )
        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            turn_task.cancel()
            # Safety net (see /chat/stream): the client is gone — force-stop the
            # underlying stream consumer and release the session so the next
            # edit/regenerate isn't rejected with 409 "session is still generating".
            # Idempotent; the interrupted marker is set by _on_error, not here.
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # Turn over: delete the disposable checkpoint thread unless it paused
            # for an approval/question (the interrupt checkpoint must survive resume).
            # Shielded so it completes even if the client disconnects after done.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)
            if snapshot_pre is not None and snapshot_workspace is not None:
                await asyncio.to_thread(
                    agent_registry.snapshot_manager.end_turn, session_id, user_message.id, snapshot_workspace,
                )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/sessions/{session_id}/messages/{message_id}/edit")
async def edit_message(session_id: str, message_id: str, request: EditMessageRequest):
    """Edit a user message and re-run the conversation from that point."""
    await _guard_session_not_streaming(session_id)
    revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0}
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        if session.messages[index].role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
        # Editing re-runs from this user message: the assistant messages after
        # it are dropped. When ``revert_code`` is on (default), their file
        # changes are reverted first (safe inverse edits, conflict-detected),
        # mirroring opencode/Codex "edit = redo from a clean file state". Only
        # ``active`` records are reverted: if ``edit-begin`` already reverted
        # this message's turn, the records are now ``reverted`` and this step is
        # idempotently skipped.
        dropped_assistant_ids = [m.id for m in session.messages[index + 1 :] if m.role == "assistant"]
        if request.revert_code and dropped_assistant_ids:
            revert_summary = await _revert_turn_changes(session_id, message_id, dropped_assistant_ids, mark=True)
        session_store.update_message_content(session_id, message_id, request.content)
        session_store.truncate_from(session_id, message_id)
        # Checkpoint delete is routed through the single shared checkpointer
        # (single-writer model) — no thread needed, never contends.
        await agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_id, model = _resolve_run_provider(session, request.provider_id, request.model)
        provider_name = _provider_name_for_id(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(request.work_mode or session.work_mode)
    if request.autonomy:
        autonomy = normalize_autonomy(request.autonomy)
    else:
        autonomy = normalize_autonomy(session.autonomy)
    if request.work_mode or request.autonomy:
        try:
            session_store.update_modes(session_id, work_mode, autonomy)
        except KeyError:
            pass
    language = request.language
    try:
        workspace_path = workspace_controller.workspace_for_session(session_id).root
    except ValueError as exc:
        # The session's project workspace is missing/invalid. Refuse instead of
        # silently re-running in the DEFAULT workspace (which would write files
        # in the wrong place under the project's session).
        raise HTTPException(status_code=400, detail=f"workspace unavailable: {exc}") from exc
    except Exception:  # noqa: BLE001 - genuinely workspace-less sessions use the default
        workspace_path = None

    async def event_stream():
        accumulated_content = ""
        terminal_sent = False
        interrupt_emitted = False

        # Surface the file-revert result to the UI as the first event, so the
        # client can show "reverted N file changes / restore" right away.
        if revert_summary.get("reverted_count", 0) > 0 or revert_summary.get("conflict_count", 0) > 0:
            yield f"data: {json.dumps({'type': 'revert_summary', 'session_id': session_id, **revert_summary}, ensure_ascii=False)}\n\n"

        def _persist_partial():
            nonlocal accumulated_content, terminal_sent
            if not accumulated_content or terminal_sent:
                return
            terminal_sent = True
            try:
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=accumulated_content,
                    mode="single",
                    provider=provider_name,
                    model=model,
                    work_mode=work_mode,
                    autonomy=autonomy,
                    # Generated id (not the client-supplied one): a partial reply
                    # persisted on error must never be adopted as a successful
                    # commit by the frontend's stream-settle reconciliation.
                    message_id=None,
                    parts=[],
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                logger.exception("Failed to persist partial rerun for session %s", session_id)

        def _on_event(event):
            nonlocal accumulated_content, terminal_sent, interrupt_emitted
            event["session_id"] = session_id
            if event.get("type") in ("approval_required", "question_required"):
                # Agent interrupted to ask the user: the turn is NOT complete. No
                # synthetic done will follow, so persist the partial reply on end
                # (message_id=None) to keep the question context after a refresh.
                interrupt_emitted = True
            if event.get("type") == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if event.get("type") == "done":
                terminal_sent = True
                try:
                    session = session_store.append_message(
                        session_id,
                        role="assistant",
                        content=event["content"],
                        mode="single",
                        provider=event.get("provider") or provider_name,
                        model=event.get("model") or model,
                        work_mode=work_mode,
                        autonomy=autonomy,
                        message_id=request.assistant_message_id or None,
                        parts=event.get("parts") or [],
                    )
                    last = session.messages[-1] if session.messages else None
                    if last is not None:
                        agent_registry.change_store.assign_message(session_id, last.id)
                except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                    logger.exception("Failed to persist rerun assistant message for session %s", session_id)

        def _on_end():
            # Normal end after an interrupt (question/approval asked): no done
            # frame was emitted — persist the partial so the turn survives refresh.
            if interrupt_emitted and not terminal_sent:
                _persist_partial()
            return None

        def _on_error(exc):
            # Persist whatever was produced before the failure so tool-change
            # records from this stream stay bound to a message (rollback needs
            # the binding).
            _persist_partial()
            return {"type": "error", "session_id": session_id, "error": str(exc)[:400]}

        snapshot_workspace = None
        try:
            snapshot_workspace = workspace_controller.workspace_for_session(session_id)
        except (ValueError, KeyError, Exception):  # noqa: BLE001 - default/workspace-less sessions skip snapshot
            snapshot_workspace = None
        snapshot_pre = None
        if snapshot_workspace is not None:
            snapshot_pre = await asyncio.to_thread(
                agent_registry.snapshot_manager.begin_turn, session_id, message_id, snapshot_workspace,
            )
        # Same bus-fed turn as /chat/stream: live delivery even while the graph
        # is blocked on a long-running tool / worker.
        stream_iter = agent_registry.rerun_stream(
            history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids, workspace_path=workspace_path, agent=session.agent_id or DEFAULT_AGENT, project_id=session.project_id or None,
        )
        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            turn_task.cancel()
            # Safety net (see /chat/stream): the client is gone — force-stop the
            # underlying stream consumer and release the session so the next
            # edit/regenerate isn't rejected with 409 "session is still generating".
            # Idempotent; the interrupted marker is set by _on_error, not here.
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # Turn over: delete the disposable checkpoint thread unless it paused
            # for an approval/question (the interrupt checkpoint must survive resume).
            # Shielded so it completes even if the client disconnects after done.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)
            if snapshot_pre is not None and snapshot_workspace is not None:
                await asyncio.to_thread(
                    agent_registry.snapshot_manager.end_turn, session_id, message_id, snapshot_workspace,
                )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/projects")
async def list_projects():
    projects = []
    for project in project_store.list_projects():
        count = len(session_store.list_sessions(project.id))
        projects.append(workspace_controller.public_project(project.id, count))
    return {"status": "ok", "projects": projects}

@app.post("/projects")
async def create_project(request: ProjectCreateRequest):
    try:
        if request.mode not in ORG_MODES:
            raise ValueError(f"mode must be one of {list(ORG_MODES)}")
        workspace_path = workspace_controller.validate_workspace_path(request.workspace_path)
        # A folder hosts at most two projects (one single + one multi); reject
        # a second project with the same mode on the same workspace path.
        for existing in project_store.list_by_workspace_path(workspace_path):
            existing_mode = ORG_MODE_SINGLE
            if existing.memory_dir and org_store.exists(existing.memory_dir):
                existing_mode = org_store.load(existing.memory_dir).mode
            if existing_mode == request.mode:
                label = "single" if request.mode == ORG_MODE_SINGLE else "multi"
                raise ValueError(
                    f"该文件夹已存在 {label} 模式的项目，请删除后重建或选择另一模式"
                )
        from datetime import datetime, timezone

        memory_dir = _unique_memory_dir(datetime.now(timezone.utc).isoformat(), request.mode)
        project = project_store.create(request.name, workspace_path, memory_dir=memory_dir)
        try:
            memory_manager.registry.ensure_project(project.memory_dir)
            _ensure_org(project.memory_dir, request.mode)
            memory_manager.registry.ensure_agent(memory_manager.root / project.memory_dir, DEFAULT_AGENT)
        except Exception:  # noqa: BLE001 - memory scaffold must not block project creation
            pass
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    count = len(session_store.list_sessions(project.id))
    return {"status": "ok", "project": workspace_controller.public_project(project.id, count)}

@app.post("/projects/{project_id}/rename")
async def rename_project(project_id: str, request: ProjectRenameRequest):
    try:
        project = project_store.rename(project_id, request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    count = len(session_store.list_sessions(project.id))
    return {"status": "ok", "project": workspace_controller.public_project(project.id, count)}

@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        memory_dir = project_store.memory_dir_for(project_id)
    except (KeyError, ValueError):
        memory_dir = ""
    if not project_store.delete(project_id):
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    for session in session_store.list_sessions(project_id):
        await agent_registry.forget_runtime_checkpoint(session["id"])
        agent_registry.change_store.delete_session(session["id"])
        agent_registry.snapshot_manager.delete_session(session["id"])
        _cleanup_session_screenshots(session["id"])
    session_store.delete_by_project(project_id)
    if memory_dir:
        project_memory = memory_manager.root / memory_dir
        if project_memory.exists():
            try:
                from coworker.memory.trash import send_to_os_trash, send_to_trash

                dest = send_to_os_trash(project_memory)
                if dest is None:
                    dest = send_to_trash(project_memory, memory_manager.root / ".trash")
            except Exception:  # noqa: BLE001 - trash failure must not fail the delete
                logger.warning("could not trash project memory dir %s", memory_dir, exc_info=True)
    return {"status": "ok"}

@app.get("/workspace/tree")
async def workspace_tree(path: str = "", project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "root": str(workspace.root), "tree": workspace.build_tree(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/workspace/dir")
async def workspace_dir(path: str = "", project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "path": path or ".", "entries": workspace.list_dir(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/workspace/file")
async def workspace_file(path: str, project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "path": path, "file": workspace.read_preview(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/sessions/{session_id}/changes")
async def session_changes(session_id: str):
    """All file changes made by the agent in this session, grouped by turn."""
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    turns = agent_registry.change_store.changes_by_turn(session_id)
    return {"status": "ok", "session_id": session_id, "turns": turns, "count": sum(len(item["changes"]) for item in turns)}

@app.get("/diffs/current")
async def diffs_current(project_id: str = "", session_id: str = ""):
    """Current working-tree diff for the workspace. Falls back to session
    aggregate when the workspace is not a git repository."""
    try:
        workspace = workspace_controller.workspace_for_chat(session_id=session_id or None, project_id=project_id or None)
        result = workspace_git_diff(workspace.root)
        result["workspace"] = str(workspace.root)
        return {"status": "ok", **result}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/workspace/branch")
async def workspace_branch(project_id: str = ""):
    """Current git branch for the project workspace (read-only status)."""
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        result = workspace_git_branch(workspace.root)
        return {"status": "ok", **result}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/workspace/command")
async def workspace_command(request: WorkspaceCommandRequest):
    try:
        workspace = workspace_controller.workspace_for_project(request.project_id) if request.project_id else workspace_controller.default()
        result = workspace.run_command(
            shlex.split(request.command),
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            audit_context={"source": "bottom_panel_terminal"},
            approval_store=command_approval_store,
            require_approval=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "result": result}

@app.get("/audit/tool")
async def tool_audit(limit: int = 100):
    return {"status": "ok", "events": list_tool_audit_events(tool_audit_path, limit)}


@app.get("/audit/tool/export")
async def export_tool_audit():
    """Export the full tool-audit JSONL as a text download."""
    from fastapi.responses import PlainTextResponse

    text = ""
    try:
        if tool_audit_path.exists():
            text = tool_audit_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return PlainTextResponse(text, media_type="text/plain")


@app.post("/audit/tool/clear")
async def clear_tool_audit():
    """Empty the tool-audit log (retention trim keeps it bounded afterwards)."""
    from coworker.atomicio import atomic_write_text

    try:
        if tool_audit_path.exists():
            atomic_write_text(tool_audit_path, "")
    except OSError:
        pass
    return {"status": "ok"}


@app.get("/traces/agent")
async def agent_traces(limit: int = 100):
    return {"status": "ok", "events": agent_registry.list_agent_traces(limit)}


@app.get("/traces/agent/export")
async def export_agent_traces():
    """Export the full agent-trace JSONL as a text download."""
    from fastapi.responses import PlainTextResponse

    trace_path = settings.data_dir / AGENT_TRACE_FILENAME
    text = ""
    try:
        if trace_path.exists():
            text = trace_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return PlainTextResponse(text, media_type="text/plain")


@app.post("/traces/agent/clear")
async def clear_agent_traces():
    """Empty the agent-trace log (retention trim keeps it bounded afterwards)."""
    from coworker.atomicio import atomic_write_text

    trace_path = settings.data_dir / AGENT_TRACE_FILENAME
    try:
        if trace_path.exists():
            atomic_write_text(trace_path, "")
    except OSError:
        pass
    return {"status": "ok"}


@app.get("/checkpoints/export")
async def export_checkpoints():
    """Download the per-session checkpoint files as a zip (best-effort copy)."""
    from starlette.background import BackgroundTask
    from fastapi import BackgroundTasks
    from fastapi.responses import FileResponse
    import shutil

    ck_dir = agent_registry.checkpoints_dir
    if not ck_dir.is_dir() or not any(ck_dir.glob("*.json")):
        return {"status": "ok", "size": 0, "note": "no checkpoints yet"}
    tmp = ck_dir.with_name(f"checkpoints.export.{uuid.uuid4().hex[:8]}.zip")
    try:
        import zipfile
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(ck_dir.glob("*.json")):
                zf.write(f, arcname=f.name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to snapshot checkpoints: {exc}") from exc

    def _cleanup() -> None:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    return FileResponse(
        str(tmp),
        media_type="application/zip",
        filename="coworker-checkpoints.zip",
        background=BackgroundTasks([BackgroundTask(_cleanup)]),
    )


@app.post("/checkpoints/clear")
async def clear_checkpoints():
    """Delete all runtime checkpoint threads (active streams are skipped)."""
    stats = await asyncio.to_thread(agent_registry.checkpoint_manager.clear_all)
    return {"status": "ok", "stats": stats}


@app.get("/settings/retention")
async def get_retention_settings():
    """Current data-retention caps (trace/audit line limits)."""
    from coworker.traces import ACTIVE_TRACE_RETENTION
    from coworker.workspace import ACTIVE_TOOL_AUDIT_RETENTION

    return {
        "trace_lines": ACTIVE_TRACE_RETENTION,
        "audit_lines": ACTIVE_TOOL_AUDIT_RETENTION,
    }


class RetentionUpdate(BaseModel):
    trace_lines: int | None = None
    audit_lines: int | None = None


@app.post("/settings/retention")
async def save_retention_settings(request: RetentionUpdate):
    """Save and immediately apply retention caps for trace/audit logs."""
    if request.trace_lines is not None or request.audit_lines is not None:
        save_user_retention_settings(
            {
                "trace_lines": request.trace_lines,
                "audit_lines": request.audit_lines,
            }
        )
    from coworker.traces import ACTIVE_TRACE_RETENTION
    from coworker.workspace import ACTIVE_TOOL_AUDIT_RETENTION

    return {"status": "ok", "trace_lines": ACTIVE_TRACE_RETENTION, "audit_lines": ACTIVE_TOOL_AUDIT_RETENTION}


# ==========================================================================
# Logging subsystem endpoints
# ==========================================================================

@app.get("/settings/log")
async def get_log_settings():
    """Current logging configuration."""
    return {
        "log_level": get_log_level(),
        "log_file": str(log_path),
        "log_max_bytes": settings.log_max_bytes,
        "log_backup_count": settings.log_backup_count,
        "json_log": settings.json_log,
    }


@app.post("/settings/log-level")
async def set_log_level(request: LogSettingsUpdate):
    """Change the log level at runtime. Returns current level after the change."""
    level = request.log_level.strip().upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"Invalid log level: {level}. Must be one of {', '.join(valid_levels)}")
    result = _set_log_level(level)
    if result != "ok":
        raise HTTPException(status_code=400, detail=result)
    return {"status": "ok", "log_level": level}


class TruncateLogRequest(BaseModel):
    max_bytes: int | None = None


@app.post("/settings/truncate-log")
async def truncate_log_settings(request: TruncateLogRequest):
    """Truncate the app log file, keeping the last ``max_bytes`` bytes.

    ``max_bytes`` is read from the JSON body (matches the Electron and HTTP
    frontend clients). ``max_bytes <= 0`` clears the file completely.
    """
    mb = request.max_bytes if request.max_bytes is not None else settings.log_max_bytes
    result = _truncate_log(mb)
    return result

@app.get("/settings/log-file")
async def read_log_file(start: int = 0, count: int = 100):
    """Read log lines from the tail of the app log file.

    ``start`` is the number of newest lines to skip (0 = newest lines);
    ``count`` is how many lines to return. Together they page backwards
    from the end of the file: ``lines[-start-count:-start]``.
    ``truncated`` is True when older lines exist before this page.
    """
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total = len(lines)
        if count <= 0 or start < 0 or total == 0:
            return {"total_lines": total, "lines": [], "truncated": total > 0}
        end = max(0, total - start)
        begin = max(0, end - count)
        page = lines[begin:end]
        return {
            "total_lines": total,
            "lines": page,
            "truncated": begin > 0,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/command-approvals")
async def list_command_approvals():
    return {"status": "ok", "approvals": command_approval_store.list()}

@app.post("/command-approvals/resolve")
async def resolve_command_approval(request: CommandApprovalResolve):
    try:
        approval = command_approval_store.require(request.approval_id)
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        decision_type = request.decision.type

        if decision_type == "always":
            # MCP approvals carry their own allowlist key (server + remote tool
            # name); workspace commands are keyed by argv + cwd.
            mcp = context.get("mcp") if isinstance(context.get("mcp"), dict) else {}
            mcp_digest = str(mcp.get("digest") or "")
            if mcp_digest:
                command_approval_store.always_allow(mcp_digest)
            else:
                command = approval.get("command") if isinstance(approval.get("command"), list) else []
                cwd = str(approval.get("cwd") or "")
                if command:
                    from coworker.workspace import Workspace

                    command_approval_store.always_allow(Workspace.command_digest(command, cwd))
            decision = {"type": "approve"}
            status = "approved"
        elif decision_type == "approve":
            # For plan approvals the decision also carries the chosen execution
            # autonomy (supervised / guarded / autonomous) that routes the
            # follow-up execution posture.
            from coworker.agent.core import normalize_autonomy

            autonomy = normalize_autonomy(request.decision.autonomy) if request.decision.autonomy else None
            decision = {"type": "approve", **({"autonomy": autonomy} if autonomy else {})}
            status = "approved"
        elif decision_type == "continue_discuss":
            decision = {"type": "continue_discuss"}
            status = "answered"
        elif decision_type == "respond":
            decision = {"type": "respond", "message": request.decision.message}
            status = "answered"
        elif decision_type == "regenerate":
            # Legacy regenerate semantics: keep planning (stay in discuss phase).
            decision = {"type": "continue_discuss"}
            status = "denied"
        else:
            decision = {
                "type": "reject",
                "message": request.decision.message or (
                    "The user rejected this command. Do not run it or retry it unless the user explicitly asks."
                ),
            }
            status = "denied"

        interrupt_id = str(context.get("interrupt_id") or "")
        resume_id = interrupt_id or request.approval_id
        is_hitl_resumable = context.get("source") == "agent_langgraph_hitl" and bool(interrupt_id)
        # 关键：必须在 set_decision 之前查询 siblings。set_decision 会把当前审批
        # 翻成 terminal 状态，而保留策略可能在同一此 save 里将其逐出存储；若在
        # 之后查询就会漏掉当前审批（siblings 为空 → all([])=True → 以空决策调度
        # resume → _resume_in_background 直接关闭事件流，agent 不恢复，前端橙条）。
        siblings = (
            [
                item
                for item in command_approval_store.list()
                if isinstance(item.get("context"), dict)
                and str(item.get("context", {}).get("interrupt_id") or "") == interrupt_id
            ]
            if is_hitl_resumable
            else []
        )
        approval = command_approval_store.set_decision(request.approval_id, status, decision)
        if is_hitl_resumable:
            # 把当前审批（含刚写入的 decision）合并进 siblings，并兜底确保它在列，
            # 避免存储剪枝或历史裁剪导致该兄弟组决策不完整。
            siblings = [approval if item.get("id") == approval.get("id") else item for item in siblings]
            if not any(item.get("id") == approval.get("id") for item in siblings):
                siblings.append(approval)
            if all(item.get("decision") is not None for item in siblings):
                asyncio.create_task(
                    _resume_in_background(resume_id, approval, siblings)
                )
                return {
                    "status": "ok",
                    "approval": approval,
                    "events": [],
                    "resumed": True,
                    "resume_id": resume_id,
                }
            return {"status": "ok", "approval": approval, "events": [], "resumed": False}
        return {"status": "ok", "approval": approval, "events": [], "resumed": False}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _merge_message_parts(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge newly emitted resume parts into the assistant message's accumulated parts.

    Tools are merged by id (the newest entry wins), reasoning/plan blocks are
    coalesced so repeated resumes of the same turn do not duplicate blocks.
    """
    merged = list(existing)
    for part in incoming:
        ptype = part.get("type")
        if ptype == "tool":
            tool_id = part.get("id")
            index = next((i for i, p in enumerate(merged) if p.get("type") == "tool" and p.get("id") == tool_id), None)
            if index is not None:
                prev = merged[index]
                merged[index] = {
                    **prev,
                    **part,
                    # 不应用空值覆盖已有内容，避免后续 resume 的空字段抹掉工具名/参数
                    "name": part.get("name") or prev.get("name") or "",
                    "input": part.get("input") or prev.get("input") or "",
                }
            else:
                merged.append(dict(part))
        elif ptype in ("plan", "reasoning"):
            index = next((i for i, p in enumerate(merged) if p.get("type") == ptype), None)
            if index is not None:
                merged[index] = {**merged[index], **part}
            else:
                merged.append(dict(part))
        elif ptype == "text":
            # 各次 resume 会重放同一轮执行（工具按 id 去重），文本同样按内容去重，
            # 避免重放时 text part 重复叠加；多轮文本内容各不相同，天然追加。
            text_content = str(part.get("content") or "")
            if not text_content:
                continue
            exists = any(p.get("type") == "text" and p.get("content") == text_content for p in merged)
            if not exists:
                merged.append(dict(part))
        else:
            merged.append(dict(part))
    return merged


async def _resume_in_background(resume_id: str, approval: dict[str, Any], siblings: list[dict[str, Any]]) -> None:
    """Run the agent resume in the background and stream events via the event bus."""
    ordered = sorted(
        siblings,
        key=lambda item: int(item.get("context", {}).get("action_index") or 0),
    )
    decisions = [item.get("decision") for item in ordered if item.get("decision")]
    # If any question is rejected, inject a stop signal so the resume
    # path does NOT re-enter the agent graph — the turn ends.
    _has_question_reject = any(
        item.get("decision", {}).get("type") == "reject"
        and item.get("context", {}).get("kind") == "question"
        for item in ordered
    )
    if _has_question_reject:
        decisions.insert(0, {"type": "_stop_turn"})
    context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
    session_id = str(context.get("session_id") or "")
    memory_manager.note_turn_active(session_id) if session_id else None
    # A resume re-enters the graph and writes checkpoints on the session's
    # thread_id. Mark the session active for the resume's duration so the sweep
    # never trims the interrupt checkpoint mid-resume and the streaming guard
    # correctly sees the session as busy.
    if session_id:
        agent_registry.checkpoint_manager.mark_active(session_id)
    try:
        if not decisions:
            approval_event_bus.close(resume_id)
            return
        done: dict[str, Any] | None = None
        # resume_interrupt now yields events in real time, so each one is pushed
        # to the SSE bus as soon as it is produced instead of buffering the whole
        # resume. This gives the frontend live progress during the agent's resume
        # and surfaces newly hit approvals immediately.
        async for event in agent_registry.resume_interrupt(approval, decisions):
            if event.get("type") == "done":
                done = event
            event["session_id"] = session_id
            event["resume_id"] = resume_id
            await approval_event_bus.publish(resume_id, event)
        if done and session_id:
            try:
                session = session_store.require(session_id)
                last = session.messages[-1] if session.messages else None
                if last is not None and last.role == "assistant":
                    # 同一次 agent turn 的多次 resume 共享同一条 assistant 消息：
                    # 把本次 resume 的 parts 合并进最后一条消息，而不是每次 append 新消息，
                    # 避免切换会话重载后同一次回复被拆成多个气泡。
                    last.parts = _merge_message_parts(last.parts, done.get("parts") or [])
                    last.content = str(done.get("content") or last.content or "")
                    last.provider = str(done.get("provider") or last.provider or "")
                    last.model = str(done.get("model") or last.model or "")
                    session_store.save(session)
                    done["content"] = last.content
                    done["parts"] = last.parts
                else:
                    session_store.append_message(
                        session_id,
                        role="assistant",
                        content=str(done.get("content") or ""),
                        mode="single",
                        provider=str(done.get("provider") or ""),
                        model=str(done.get("model") or ""),
                        parts=done.get("parts") or [],
                    )
            except KeyError:
                pass
        for item in ordered:
            command_approval_store.mark_consumed(item.get("id", ""))
        # Resume reached a terminal `done`: the turn is over, so the disposable
        # checkpoint thread is no longer needed — delete it (fresh-start handles
        # it next turn too, but cleaning here keeps the DB empty). Shielded so
        # the delete still completes if this background task is cancelled.
        if done and session_id:
            try:
                await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning("resume checkpoint delete failed for %s", session_id, exc_info=True)
    except Exception as exc:
        # Surface the failure on the bus AND record it in the trace log so a
        # silently-dead resume (answer sent but agent never continues) is
        # diagnosable from agent_trace.jsonl instead of vanishing.
        try:
            agent_registry.trace_store.record(
                "agent_activity", "error",
                {
                    "session_id": session_id,
                    "provider": str(context.get("provider") or ""),
                    "provider_id": str(context.get("provider_id") or ""),
                    "model": str(context.get("model") or ""),
                    "language": str(context.get("language") or "zh"),
                    "work_mode": str(context.get("work_mode") or "build"),
                    "autonomy": str(context.get("autonomy") or "guarded"),
                    "streaming": True,
                },
                {"error": str(exc)[:400], "resume_id": resume_id, "approval_id": approval.get("id", "")},
            )
        except Exception:  # noqa: BLE001 - a trace hiccup must never mask the resume error
            logger.warning("failed to record resume error trace", exc_info=True)
        await approval_event_bus.publish(
            resume_id,
            {"type": "error", "session_id": session_id, "error": str(exc)[:400], "resume_id": resume_id},
        )
    finally:
        approval_event_bus.close(resume_id)
        # The resume is over: release the session so the next turn can start.
        if session_id:
            agent_registry.checkpoint_manager.mark_idle(session_id)


@app.get("/command-approvals/events/{resume_id}")
async def stream_approval_events(resume_id: str):
    """SSE stream of resume progress events for a given resume_id."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        queue = approval_event_bus.subscribe(resume_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.get("type") == "stream_end":
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            approval_event_bus.unsubscribe(resume_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/worker-events/{worker_run_id}")
async def stream_worker_events(worker_run_id: str):
    """SSE stream of a worker sub-agent's internal run events.

    Replays the persisted run history first, then follows live events, and
    terminates with ``worker_stream_end`` once the run finishes. The main agent
    stream only carries the ``delegate_*`` summary; the worker's detailed
    delta/tool/reasoning stream lives here and is consumed on demand when the
    user opens the worker block.
    """
    from fastapi.responses import StreamingResponse

    async def event_stream():
        try:
            async for event in worker_event_bus.stream(worker_run_id):
                if event is None:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a broken worker stream must never hang the client
            logger.warning("worker event stream error for %s", worker_run_id, exc_info=True)
            yield f"data: {json.dumps({'type': 'worker_stream_end'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/providers")
def list_providers():
    return provider_manager.public_config()

@app.post("/providers")
def create_provider(request: ProviderCreate):
    try:
        provider = provider_manager.add_provider(
            name=request.name,
            provider_type=request.provider_type,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            context_window=request.context_window,
            max_output_tokens=request.max_output_tokens,
            vision=request.vision,
            temperature=request.temperature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "provider": provider}

@app.put("/providers/default")
def set_default_provider(request: DefaultProviderPayload):
    try:
        config = config_controller.update_runtime_config({
            "selected_provider_id": request.provider_id,
            "selected_model": request.model,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "config": config}

@app.put("/providers/{provider_id}")
def update_provider(provider_id: str, request: ProviderUpdate):
    try:
        provider = provider_manager.update_provider(
            provider_id,
            name=request.name,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            enabled=request.enabled,
            context_window=request.context_window,
            max_output_tokens=request.max_output_tokens,
            vision=request.vision,
            temperature=request.temperature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "provider": provider}

@app.post("/providers/{provider_id}/discover-context")
def discover_provider_context(provider_id: str):
    """Probe the provider's local server for its actual context window (tokens).

    For cloud providers the known-model table already covers most cases, so a
    failed probe simply returns 0 and the caller falls back to table/default.
    """
    try:
        config = provider_manager.load()
        provider = provider_manager.require_provider(config, provider_id)
        provider_manager._resolve_secret(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    window, error = provider_manager._fetch_context_window_full(provider)
    if not window or window <= 0:
        raise HTTPException(status_code=404, detail=error or "could not discover context window from this provider")
    try:
        provider_manager.update_provider(provider_id, context_window=window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "provider": provider_manager.public_provider(provider_manager.require_provider(provider_manager.load(), provider_id))}

@app.delete("/providers/{provider_id}")
def delete_provider(provider_id: str):
    try:
        provider_manager.delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}

@app.post("/providers/test")
def test_provider(request: ProviderTestPayload):
    api_key = _resolve_provider_secret(request) if not request.api_key and request.provider_id else request.api_key
    try:
        result = provider_manager.test_provider_connection(request.base_url, api_key, request.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "result": result}

@app.post("/providers/fetch-models")
def fetch_provider_models(request: ProviderFetchModelsPayload):
    api_key = _resolve_provider_secret(request) if not request.api_key and request.provider_id else request.api_key
    try:
        models = provider_manager.fetch_models(request.base_url, api_key, request.provider_type)
    except Exception as exc:
        return {"status": "error", "models": [], "error": str(exc)[:300]}
    return {"status": "ok", "models": models}


def _resolve_provider_secret(request: BaseModel) -> str:
    """Fill an empty test/fetch api_key from the Keychain-stored secret of the
    provider being edited (key_in_secrets providers keep the JSON blank)."""
    try:
        config = provider_manager.load()
        provider = provider_manager.require_provider(config, request.provider_id)
        provider_manager._resolve_secret(provider)
        return provider.api_key
    except Exception:
        return ""


# ─────────────────────────── MCP ──────────────────────────

from coworker.mcp.mcp import SECRET_PLACEHOLDER, STATUS_CONNECTED, STATUS_ERROR, STATUS_NEEDS_AUTH
from coworker.mcp.mcp_discover import TEMPLATES
from coworker.mcp.mcp_test import test_mcp_connection_sync

MCP_CHECK_TIMEOUT_SECONDS = 25.0


def _mcp_not_found(exc: ValueError) -> HTTPException:
    detail = str(exc)
    code = 404 if "not found" in detail.lower() else 400
    return HTTPException(status_code=code, detail=detail)


def _resolve_secret_map(
    incoming: dict[str, str] | None, stored: dict[str, str] | None
) -> dict[str, str]:
    """Swap placeholder values for the real stored secret (test path only)."""
    if not incoming:
        return {}
    stored = stored or {}
    return {
        key: (stored.get(key, "") if value == SECRET_PLACEHOLDER else value)
        for key, value in incoming.items()
    }


def _check_server(server_id: str) -> dict[str, Any]:
    """Run a live connection check and persist the resulting status."""
    runtime = mcp_manager.get_runtime_config(server_id)
    result = test_mcp_connection_sync(
        transport=runtime["transport"],
        command=runtime.get("command", ""),
        args=runtime.get("args", ""),
        cwd=runtime.get("cwd", ""),
        url=runtime.get("url", ""),
        env=runtime.get("env") or {},
        headers=runtime.get("headers") or {},
        timeout=runtime.get("timeout") or MCP_CHECK_TIMEOUT_SECONDS,
    )

    if result["ok"]:
        status = STATUS_CONNECTED
    elif "auth" in (result.get("error") or "").lower() or "401" in (result.get("error") or ""):
        status = STATUS_NEEDS_AUTH
    else:
        status = STATUS_ERROR

    server = mcp_manager.update_server_status(
        server_id,
        status=status,
        error_message=result.get("error", ""),
        tool_count=result.get("tool_count", 0),
        tools=result.get("tools", []),
    )
    # Drop any live session so the next graph build reconnects with the check result.
    mcp_sessions.close_server(server_id)
    return server


@app.get("/mcp/servers")
def list_mcp_servers():
    return {"status": "ok", "servers": mcp_manager.list_servers()}


@app.post("/mcp/servers")
def create_mcp_server(request: McpServerCreatePayload):
    try:
        result = mcp_manager.add_server(
            name=request.name,
            transport=request.transport,
            command=request.command or "",
            args=request.args or "",
            cwd=request.cwd or "",
            timeout=request.timeout,
            url=request.url or "",
            env=request.env or {},
            headers=request.headers or {},
        )
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    return {"status": "ok", "server": result}


@app.patch("/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, request: McpServerUpdatePayload):
    kwargs: dict[str, Any] = {"server_id": server_id}
    for key in ("name", "transport", "enabled", "command", "args", "cwd", "timeout", "url", "env", "headers", "trusted", "disabled_tools"):
        value = getattr(request, key)
        if value is not None:
            kwargs[key] = value
    try:
        result = mcp_manager.update_server(**kwargs)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    # A connection-relevant field may have changed: drop the live session so the
    # next graph build reconnects with the new config.
    mcp_sessions.close_server(server_id)
    return {"status": "ok", "server": result}


@app.delete("/mcp/servers/{server_id}")
def delete_mcp_server(server_id: str):
    try:
        mcp_manager.delete_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    mcp_sessions.close_server(server_id)
    return {"status": "ok"}


@app.get("/mcp/discover")
def discover_mcp_templates():
    return {"status": "ok", "servers": TEMPLATES}


@app.post("/mcp/servers/{server_id}/check")
def check_mcp_server(server_id: str):
    try:
        server = _check_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    return {"status": "ok", "server": server}


@app.post("/mcp/check-all")
def check_all_mcp_servers():
    servers = mcp_manager.list_servers(enabled_only=True)
    ids = [entry["id"] for entry in servers]
    if ids:
        with ThreadPoolExecutor(max_workers=min(len(ids), 4)) as pool:
            results = list(pool.map(_check_server, ids))
    return {"status": "ok", "servers": mcp_manager.list_servers()}


@app.post("/mcp/test")
def test_mcp(request: McpTestPayload):
    env = request.env or {}
    headers = request.headers or {}

    # When testing an existing server, placeholder secrets resolve to the real ones.
    if request.server_id:
        try:
            stored = mcp_manager.get_runtime_config(request.server_id)
        except ValueError:
            stored = {}
        env = _resolve_secret_map(env, stored.get("env"))
        headers = _resolve_secret_map(headers, stored.get("headers"))

    result = test_mcp_connection_sync(
        transport=request.transport,
        command=request.command or "",
        args=request.args or "",
        cwd=request.cwd or "",
        url=request.url or "",
        env=env,
        headers=headers,
        timeout=request.timeout or MCP_CHECK_TIMEOUT_SECONDS,
    )
    return {"status": "ok", "result": result}


@app.post("/mcp/servers/{server_id}/reauthorize")
def reauthorize_mcp_server(server_id: str):
    """Run the OAuth 2.1+PKCE browser flow for a remote server and reconnect it."""
    try:
        mcp_manager.get_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    result = mcp_sessions.reauthorize(server_id)
    return {"status": "ok", **result}


@app.websocket("/ws/terminal")
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


# ---------------------------------------------------------------------------
# Skills API
# ---------------------------------------------------------------------------


@app.get("/skills")
def list_skills(enabled_only: bool = False):
    """List discovered skills (catalog) with scan diagnostics."""
    result = skill_manager.refresh()
    skills = [s.to_dict() for s in result.skills if not enabled_only or s.enabled]
    return {
        "status": "ok",
        "skills": skills,
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "count": len(skills),
    }


# ---------------------------------------------------------------------------
# Skill Market API
# ---------------------------------------------------------------------------

from coworker.skills.skill_market import SkillMarketManager

# Initialize market manager (same user home as skill_manager)
skill_market_manager = SkillMarketManager(Path.home())


class MarketInstallRequest(BaseModel):
    """Request body for skill installation from market."""
    source: str  # "skillhub" | "clawhub"
    slug: str    # skill identifier
    owner: str | None = None  # disambiguates colliding slugs (ClawHub)


class SkillInstallRequest(BaseModel):
    """Request body for installing a skill from raw SKILL.md content.

    Used by chat-driven installs (agent ``install_skill`` tool) and any external
    caller that already has the skill's SKILL.md text. ``commands`` optionally
    declares sub-commands whose instruction bodies are written to
    ``commands/<name>.md`` and listed in the root SKILL.md frontmatter.
    """
    name: str  # skill slug/identifier
    content: str  # full SKILL.md content including YAML frontmatter
    commands: list[dict[str, str]] | None = None  # [{name, description, body}]


@app.get("/skills/market")
def list_market_sources():
    """List available skill market sources."""
    return {
        "status": "ok",
        "sources": [
            {"id": "skillhub", "name": "腾讯 SkillHub", "description": "中文技能市场，国内 CDN 加速"},
            {"id": "clawhub", "name": "ClawHub", "description": "全球最大技能市场"},
        ],
    }


@app.get("/skills/market/categories")
async def list_market_categories(source: str):
    """Describe the filter dimension a market source can slice on.

    ``kind`` says which query parameter the tabs drive — ``category`` for
    SkillHub, ``sort`` for ClawHub (which has no category vocabulary upstream).
    The legacy ``categories`` key is kept so older clients keep working.
    """
    try:
        facet = await skill_market_manager.list_facets(source)
        items = facet.get("items", [])
        return {
            "status": "ok",
            "kind": facet.get("kind"),
            "default": facet.get("default"),
            "categories": items,
            "count": len(items),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mark_market_installed(
    result: dict, installed_names: set, installed_ids: set
) -> dict:
    """Annotate each market skill dict with ``installed``.

    Matching is exact-first: a skill is marked installed when its ``(source,
    slug, owner)`` matches a record persisted at install time (see
    ``SkillMarketManager.record_install``). Owner is part of the identity because
    ClawHub slugs are not unique across owners.

    A name/slug fallback is kept so skills installed *before* this feature also
    get flagged when their frontmatter ``name`` happens to match.
    """
    skills = result.get("skills") if isinstance(result, dict) else None
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            slug = skill.get("slug") or ""
            name = skill.get("name") or ""
            src = skill.get("source") or ""
            owner = skill.get("owner") or ""
            skill["installed"] = (
                (src, slug, owner) in installed_ids
                or slug in installed_names
                or name in installed_names
            )
    return result


@app.get("/skills/market/search")
async def search_market_skills(
    source: str,
    q: str,
    limit: int = 20,
    offset: int = 0,
    category: str | None = None,
):
    """Search skills in a market source."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="Search query 'q' is required")
    try:
        page = await skill_market_manager.search(
            source, q.strip(), limit, offset, category
        )
        result = page.to_dict()
        installed_names = {s["name"] for s in list_skills()["skills"]}
        installed_ids = skill_market_manager.installed_identifiers()
        _mark_market_installed(result, installed_names, installed_ids)
        return {"status": "error" if page.error else "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/skills/market/hot")
async def list_hot_market_skills(
    source: str,
    limit: int = 20,
    offset: int = 0,
    cursor: str | None = None,
    category: str | None = None,
    sort: str | None = None,
):
    """List hot/popular skills in a market source."""
    try:
        page = await skill_market_manager.list_hot(
            source, limit, offset, cursor, category, sort
        )
        result = page.to_dict()
        installed_names = {s["name"] for s in list_skills()["skills"]}
        installed_ids = skill_market_manager.installed_identifiers()
        _mark_market_installed(result, installed_names, installed_ids)
        return {"status": "error" if page.error else "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/skills/market/install")
async def install_market_skill(request: MarketInstallRequest):
    """Install a skill from a market source."""
    try:
        result = await skill_market_manager.install(
            request.source, request.slug, request.owner
        )
        if result.get("status") == "ok":
            # Auto-trigger scan to pick up the newly installed skill
            skill_manager.refresh()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/skills/install")
def install_skill_from_content(request: SkillInstallRequest):
    """Install a skill from raw SKILL.md content (chat-driven / agent installs)."""
    try:
        result = skill_market_manager.install_from_content(
            request.name, request.content, commands=request.commands
        )
        if result.get("status") == "ok":
            # Auto-trigger scan to pick up the newly installed skill
            skill_manager.refresh()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/skills/{skill_name}")
def get_skill(skill_name: str, command: str | None = None):
    """Return one skill's catalog entry plus its body (progressive disclosure).

    When ``command`` is provided, returns that sub-command's instructions
    (read from the package's ``commands/<name>.md``) instead of the whole
    skill body — this powers the ``/<command>`` chat menu entries.
    """
    skill = skill_manager.get(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    payload = skill.to_dict()
    if command:
        cmd_body = skill_manager.read_command_body(skill_name, command)
        if cmd_body is None:
            raise HTTPException(
                status_code=404, detail=f"Command '{command}' not found in skill '{skill_name}'"
            )
        payload["body"] = cmd_body[0]
        payload["base_dir"] = cmd_body[1]
        payload["command"] = command
    else:
        body = skill_manager.read_body(skill_name)
        if body is not None:
            payload["body"] = body[0]
            payload["base_dir"] = body[1]
    return {"status": "ok", "skill": payload}


@app.patch("/skills/{skill_name}")
def update_skill(skill_name: str, request: SkillUpdatePayload):
    """Toggle a skill's enabled state or permission override."""
    skill = skill_manager.get(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    try:
        if request.enabled is not None:
            skill = skill_manager.set_enabled(skill_name, request.enabled)
        if request.permission is not None:
            skill = skill_manager.set_permission(skill_name, request.permission)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "skill": skill.to_dict() if skill else None}


@app.delete("/skills/{skill_name}")
def delete_skill_route(skill_name: str):
    """Uninstall a skill: remove its directory from disk and refresh the catalog."""
    try:
        removed = skill_manager.delete_skill(skill_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    # Drop the market provenance record so the card becomes installable again.
    skill_market_manager.forget_install(skill_name)
    return {"status": "ok", "name": skill_name, "removed": True}


@app.post("/skills/scan")
def scan_skills():
    """Force a re-scan of all skill roots."""
    result = skill_manager.refresh()
    return {
        "status": "ok",
        "skills": [s.to_dict() for s in result.skills],
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "count": len(result.skills),
    }


@app.post("/skills/validate")
def validate_skill(request: SkillValidatePayload):
    """Validate a single skill directory/file without loading it into the catalog."""
    from coworker.skills.skill_discovery import SKILL_FILE, SkillScanner
    from coworker.skills.skills import load_skill_from_file

    target = request.path.strip()
    if not target:
        raise HTTPException(status_code=400, detail="path is required")
    candidate = Path(target).expanduser().resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {target}")
    # Restrict validation to the skill roots this app actually scans, so the
    # endpoint cannot be used to probe arbitrary files on the machine.
    allowed_roots: list[Path] = []
    try:
        allowed_roots = [Path(root).resolve() for root, _label in skill_manager.scanner.roots()]
    except Exception:  # noqa: BLE001 - never let scanner errors disable the guard
        pass
    if not any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside the skill directories")
    if candidate.is_dir():
        candidate = candidate / SKILL_FILE
        if not candidate.exists():
            raise HTTPException(status_code=404, detail=f"No {SKILL_FILE} in {target}")
    entry, diagnostics = load_skill_from_file(candidate, "validate")
    return {
        "status": "ok",
        "valid": entry is not None,
        "skill": entry.to_dict() if entry else None,
        "diagnostics": [d.to_dict() for d in diagnostics],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9527)