import asyncio
import atexit
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

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

from coworker.agents import AgentMode, AgentRuntimeRegistry, Language, format_user_message, normalize_autonomy, normalize_work_mode
from coworker.config import load_settings
from coworker.config_controller import AppConfigController
from coworker.projects import ProjectStore
from coworker.providers import ProviderManager
from backend.coworker.mcp.mcp import McpManager
from backend.coworker.mcp.mcp_session import McpSessionManager
from coworker.sessions import SessionStore
from coworker.traces import AGENT_TRACE_FILENAME, MAX_TRACE_LINES
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.workspace_controller import WorkspaceController

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
settings = load_settings()
session_store = SessionStore(settings.data_dir / "sessions")
provider_manager = ProviderManager(settings.data_dir / "providers.json")
config_controller = AppConfigController(settings, provider_manager)
mcp_manager = McpManager(settings.data_dir / "mcp_servers.json")
mcp_sessions = McpSessionManager(settings.data_dir, mcp_manager)
project_store = ProjectStore(settings.data_dir / "projects.json")
tool_audit_path = settings.data_dir / TOOL_AUDIT_FILENAME
command_approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
workspace_controller = WorkspaceController(project_store, session_store, settings.workspace_dir, settings.data_dir)
agent_registry = AgentRuntimeRegistry(settings, session_store, mcp_session_manager=mcp_sessions)

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


class ApprovalEventBus:
    """In-memory pub/sub that streams resume progress events to SSE subscribers.

    A small ring buffer per resume_id ensures subscribers that attach after the
    background resume task started still receive the events already published.
    """

    def __init__(self, buffer_size: int = 64) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._buffer_size = buffer_size

    def subscribe(self, resume_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
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

    async def publish(self, resume_id: str, event: dict[str, Any]) -> None:
        buffer = self._buffer[resume_id]
        buffer.append(event)
        if len(buffer) > self._buffer_size:
            del buffer[: len(buffer) - self._buffer_size]
        queues = list(self._subscribers.get(resume_id, []))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                await queue.put(event)

    def close(self, resume_id: str) -> None:
        queues = self._subscribers.pop(resume_id, [])
        for queue in queues:
            queue.put_nowait({"type": "stream_end"})


approval_event_bus = ApprovalEventBus()


# Per-session locks to prevent concurrent goal_resume calls for the same session.
_goal_locks: dict[str, asyncio.Lock] = {}
# Per-session cancel events for goal streaming termination.
_goal_cancel_events: dict[str, asyncio.Event] = {}
# Track active goal stream coroutines by stream_id.
_goal_active_streams: dict[str, str] = {}  # stream_id -> session_id

SSE_TIMEOUT = int(os.environ.get("COWORKER_SSE_TIMEOUT", str(30 * 60)))

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: AgentMode = "single"
    language: Language = "zh"
    work_mode: Optional[str] = None
    access_mode: Optional[str] = None
    autonomy: Optional[str] = None
    goal_mode: Optional[bool] = None
    goal_text: Optional[str] = None
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
    """Effective autonomy for a chat request.

    Prefers the explicit ``autonomy`` field and falls back to mapping the
    legacy ``access_mode`` switch (default->guarded, full->autonomous).
    """
    if getattr(request, "autonomy", None):
        return normalize_autonomy(request.autonomy)
    from coworker.agents import autonomy_from_access

    return autonomy_from_access(getattr(request, "access_mode", None))

class ChatResponse(BaseModel):
    response: str
    session_id: str
    mode: AgentMode
    provider: str

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

class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None

class DefaultProviderPayload(BaseModel):
    provider_id: str
    model: str

class ProviderTestPayload(BaseModel):
    base_url: str
    api_key: str = ""
    model: str

class ProviderFetchModelsPayload(BaseModel):
    base_url: str
    api_key: str = ""
    provider_type: str = "custom"

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


class CommandApprovalAction(BaseModel):
    approval_id: str

class ApprovalDecisionPayload(BaseModel):
    type: str
    message: str = ""
    autonomy: Optional[str] = None

class CommandApprovalResolve(BaseModel):
    approval_id: str
    decision: ApprovalDecisionPayload

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
async def runtime_config():
    return RuntimeConfigResponse(**config_controller.runtime_config())

@app.patch("/config", response_model=RuntimeConfigResponse)
async def update_runtime_config(request: RuntimeConfigUpdate):
    try:
        return RuntimeConfigResponse(**config_controller.update_runtime_config(request.model_dump()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.session_id and not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required to start a new chat")
    created_session = None
    session_id = request.session_id
    work_mode = normalize_work_mode(request.work_mode)
    autonomy = resolve_request_autonomy(request)
    references = _resolve_references(request.referenced_sessions)
    referenced_ids = {ref["id"] for ref in references}
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(
            session_id=session_id,
            project_id=request.project_id,
        )
        if not session_id:
            created_session = session_store.new_session("", project_id=request.project_id or "")
            session_id = created_session.id
        runtime = agent_registry.get_runtime(request.mode, request.provider_id, request.model, resolved_workspace, referenced_sessions=referenced_ids)
        reply = runtime.run(format_user_message(request.message, request.attachments, references), session_id, request.language, work_mode, autonomy)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        if request.session_id:
            session_store.append_message(
                session_id,
                role="user",
                content=request.message,
                mode=request.mode,
                work_mode=work_mode,
                autonomy=autonomy,
                attachments=request.attachments,
                references=references,
            )
            session_store.append_message(
                session_id,
                role="assistant",
                content=reply.content,
                mode=reply.mode,
                provider=reply.provider,
                model=request.model or "",
                work_mode=work_mode,
                autonomy=autonomy,
                parts=reply.parts or [],
            )
        else:
            session = created_session or session_store.require(session_id)
            if created_session:
                session_store.save(session)
            session_store.append_message(session.id, role="user", content=request.message, mode=request.mode, work_mode=work_mode, autonomy=autonomy, attachments=request.attachments, references=references)
            session_store.append_message(
                session.id,
                role="assistant",
                content=reply.content,
                mode=reply.mode,
                provider=reply.provider,
                model=request.model or "",
                work_mode=work_mode,
                autonomy=autonomy,
                parts=reply.parts or [],
            )
            session_id = session.id
        try:
            session_store.update_modes(session_id, work_mode, autonomy)
        except KeyError:
            pass
    except KeyError:
        pass

    return ChatResponse(
        response=reply.content,
        session_id=session_id,
        mode=reply.mode,
        provider=reply.provider,
    )

from fastapi.responses import StreamingResponse
import json as _json

class ChatStreamRequest(ChatRequest):
    session_id: str = ""
    # 前端乐观渲染时生成的消息 id，回传以统一前后端 id（修复按 id 回退/重生成时 404）
    user_message_id: str = ""
    assistant_message_id: str = ""

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
        runtime = agent_registry.get_stream_runtime(request.mode, request.provider_id, request.model, resolved_workspace, referenced_sessions=referenced_ids)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = request.session_id
    history = []
    if request.session_id:
        try:
            session = session_store.require(request.session_id)
            history = [
                {"role": m.role, "content": format_user_message(m.content, m.attachments, m.references) if m.role == "user" else m.content}
                for m in session.messages
                if m.role in {"user", "assistant"} and m.content
            ]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        session = session_store.create("", project_id=request.project_id or "")
        session_id = session.id

    user_message = {"role": "user", "content": format_user_message(request.message, request.attachments, references)}
    session_store.append_message(session_id, role="user", content=request.message, mode=request.mode, work_mode=work_mode, autonomy=autonomy, attachments=request.attachments, references=references, message_id=request.user_message_id or None)
    if runtime.owns_runtime_messages and agent_registry.has_runtime_checkpoint(session_id):
        messages = [user_message]
    else:
        messages = history + [user_message]

    async def event_stream():
        terminal_sent = False
        accumulated_content = ""
        goal_parts_accum: list[dict[str, Any]] = []
        stream_iter: Any
        in_goal_flag = bool(request.goal_mode)
        cancel_event: asyncio.Event | None = None

        def _persist_assistant(content, mode, provider, model, parts):
            try:
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
                    message_id=request.assistant_message_id or None,
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except KeyError:
                pass

        def _handle_event(event):
            nonlocal terminal_sent, accumulated_content
            etype = event.get("type", "")
            if etype == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if etype == "todos":
                try:
                    session_store.update_goal(session_id, goal_todos=event.get("todos") or [])
                except KeyError:
                    pass
            if etype == "done":
                if in_goal_flag:
                    for part in (event.get("parts") or []):
                        goal_parts_accum.append(part)
                else:
                    _persist_assistant(
                        event.get("content", ""),
                        event.get("mode"),
                        event.get("provider"),
                        event.get("model"),
                        event.get("parts"),
                    )
                    try:
                        session_store.update_modes(session_id, work_mode, autonomy)
                    except KeyError:
                        pass
                    terminal_sent = True
            elif etype == "goal_done":
                _persist_assistant(
                    event.get("content", ""),
                    request.mode,
                    "",
                    request.model or "",
                    _merge_goal_parts(goal_parts_accum),
                )
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except KeyError:
                    pass
                terminal_sent = True
            elif etype == "goal_paused":
                # Persist the work done up to the pause point (tool calls,
                # plans, progress) so refreshing the page does not lose the
                # goal's trajectory.
                try:
                    _persist_assistant(
                        accumulated_content or "",
                        request.mode,
                        "",
                        request.model or "",
                        _merge_goal_parts(goal_parts_accum),
                    )
                except Exception:
                    pass
                terminal_sent = True
            elif etype == "error":
                terminal_sent = True

        if in_goal_flag:
            goal_text = str(request.goal_text or request.message or "")
            try:
                existing = session_store.require(session_id)
                new_stream_id = existing.goal_stream_id or str(uuid.uuid4())
                session_store.update_goal(
                    session_id, goal_text=goal_text, goal_done=False, goal_paused=False,
                    goal_todos=[], goal_stopped=False, goal_interrupted=False,
                    goal_force_count=0, goal_just_edited=False, goal_stream_id=new_stream_id,
                    goal_max_rounds=read_user_goal_max_rounds(),
                )
            except KeyError:
                pass
            cancel_event = asyncio.Event()
            _goal_cancel_events[session_id] = cancel_event
            runtime = agent_registry.get_stream_runtime(
                request.mode, request.provider_id, request.model,
                resolved_workspace, referenced_sessions=referenced_ids,
            )
            try:
                stream_id = session_store.require(session_id).goal_stream_id or ""
            except KeyError:
                stream_id = ""
            stream_iter = runtime.goal_stream(
                messages, session_id, request.language, work_mode, autonomy,
                goal_text=goal_text, goal_continue_first=False,
                _cancel_event=cancel_event, goal_stream_id=stream_id,
            )
        else:
            runtime = agent_registry.get_stream_runtime(
                request.mode, request.provider_id, request.model,
                resolved_workspace, referenced_sessions=referenced_ids,
            )
            stream_iter = runtime.stream(
                messages, session_id, request.language, work_mode, autonomy,
            )

        # Serialize concurrent goal loops for the same session. Without this,
        # a second /goal (or a /goal/resume racing this stream) starts a
        # parallel loop that writes the same LangGraph checkpoint and corrupts
        # state. Non-goal streams are passed through untouched.
        goal_lock = _goal_locks.get(session_id) if in_goal_flag else None
        if goal_lock is None and in_goal_flag:
            goal_lock = asyncio.Lock()
            _goal_locks[session_id] = goal_lock

        _raw_stream_iter = stream_iter

        async def _locked_stream_iterator(it, lock):
            if lock is None:
                async for _ev in it:
                    yield _ev
            else:
                async with lock:
                    async for _ev in it:
                        yield _ev

        stream_iter = _locked_stream_iterator(_raw_stream_iter, goal_lock)

        try:
            async for event in stream_iter:
                event["session_id"] = session_id
                _handle_event(event)
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except BaseException as exc:
            # Catch GeneratorExit / asyncio.CancelledError on client disconnect.
            if accumulated_content:
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
            if cancel_event is not None:
                _goal_cancel_events.pop(session_id, None)
                try:
                    session_store.update_goal(session_id, goal_interrupted=True)
                except KeyError:
                    pass
            try:
                session_store.update_modes(session_id, work_mode, autonomy)
            except KeyError:
                pass
            terminal_sent = True
            yield f"data: {_json.dumps({'type': 'error', 'session_id': session_id, 'error': str(exc)[:400]}, ensure_ascii=False)}\n\n"
        else:
            if not terminal_sent:
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except KeyError:
                    pass
                yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'content': accumulated_content, 'stream_end': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

class GoalPauseRequest(BaseModel):
    session_id: str


class GoalResumeRequest(BaseModel):
    session_id: str
    language: Language = "zh"


class GoalStopRequest(BaseModel):
    session_id: str


class SettingsUpdate(BaseModel):
    goal_max_rounds: int = 50


SETTING_FILE = os.path.join(os.path.dirname(__file__), '..', '.coworker_settings.json')


def read_user_goal_max_rounds() -> int:
    """Read the user-level goal step cap from .coworker_settings.json.

    Falls back to 50 (the product default) when the file is missing or the
    key is absent. This is the bridge between the Settings page and the actual
    /goal loop, which otherwise only sees the per-session default.
    """
    try:
        data = json.loads(Path(SETTING_FILE).read_text() or "{}")
        if "goal_max_rounds" in data:
            return int(data["goal_max_rounds"])
    except Exception:
        pass
    return 50


@app.get("/settings")
async def get_settings():
    """Get user-level settings for the goal feature."""
    try:
        settings_path = Path(SETTING_FILE)
        data = json.loads(settings_path.read_text() or "{}")
        if "goal_max_rounds" in data:
            return {"goal_max_rounds": int(data["goal_max_rounds"])}
    except Exception:
        pass
    return {"goal_max_rounds": 50}


@app.post("/settings")
async def set_settings(request: SettingsUpdate):
    """Update user-level settings for the goal feature."""
    max_rounds = request.goal_max_rounds
    if max_rounds < 0 or max_rounds > 1000:
        max_rounds = max(0, min(1000, max_rounds))
    try:
        path = Path(SETTING_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"goal_max_rounds": max_rounds}))
    except Exception as exc:
        print(f"[settings] failed to persist goal_max_rounds={max_rounds}: {exc!r}", file=sys.stderr)
        return {"status": "error", "goal_max_rounds": max_rounds, "detail": str(exc)}
    return {"status": "ok", "goal_max_rounds": max_rounds}


@app.post("/goal/stop")
async def goal_stop(request: GoalStopRequest):
    """Stop an active goal loop: set flags and trigger cancel event for immediate exit."""
    try:
        session_store.update_goal(request.session_id, goal_stopped=True, goal_interrupted=True)
        cancel = _goal_cancel_events.pop(request.session_id, None)
        if cancel:
            cancel.set()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "stopped", "session_id": request.session_id}

class GoalEditRequest(BaseModel):
    session_id: str
    goal: str


class GoalStartRequest(BaseModel):
    session_id: str
    goal: str
    language: Language = "zh"


@app.get("/goal/status")
async def goal_status(session_id: str):
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Map session state to a single terminal state the frontend can branch on.
    # Previously this always returned "ok", so the frontend's done/paused
    # reconciliation never fired.
    if session.goal_done:
        status = "done"
    elif session.goal_paused or session.goal_interrupted:
        status = "paused"
    elif session.goal_text:
        status = "active"
    else:
        status = "inactive"
    return {
        "status": status,
        "session_id": session_id,
        "goal": {
            "goal_text": session.goal_text,
            "goal_done": session.goal_done,
            "goal_paused": session.goal_paused,
            "goal_todos": session.goal_todos,
            "goal_max_rounds": session.goal_max_rounds,
            "goal_force_count": session.goal_force_count,
            "goal_stopped": session.goal_stopped,
            "goal_interrupted": session.goal_interrupted,
        },
    }

@app.post("/goal/pause")
async def goal_pause(request: GoalPauseRequest):
    try:
        # Keep the cancel handle registered so a subsequent stop can still
        # interrupt the loop — only clear it once the loop has fully ended.
        session_store.update_goal(request.session_id, goal_paused=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session_id": request.session_id, "goal_paused": True}

@app.post("/goal/edit")
async def goal_edit(request: GoalEditRequest):
    try:
        session_store.update_goal(request.session_id, goal_text=request.goal, goal_just_edited=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session_id": request.session_id, "goal_text": request.goal}


@app.post("/goal/start")
async def goal_start(request: GoalStartRequest):
    """Start a goal loop: set goal_text and begin autonomous run."""
    lock = _goal_locks.get(request.session_id)
    if lock is None:
        lock = asyncio.Lock()
        _goal_locks[request.session_id] = lock

    async with lock:
        try:
            session = session_store.require(request.session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        goal_text = request.goal or str(session.goal_text or "")
        work_mode = normalize_work_mode(session.work_mode)
        autonomy = normalize_autonomy(session.autonomy)
        language = request.language

        # Reset goal state
        session_store.update_goal(
            request.session_id,
            goal_text=goal_text,
            goal_done=False,
            goal_paused=False,
            goal_todos=[],
            goal_stopped=False,
            goal_interrupted=False,
            goal_force_count=0,
            goal_just_edited=False,
            goal_stream_id=str(uuid.uuid4()),
            goal_max_rounds=read_user_goal_max_rounds(),
        )

        return { "status": "ok", "goal_text": goal_text }

@app.post("/goal/delete")
async def goal_delete(request: GoalPauseRequest):
    try:
        # Actually stop the running loop: flag it and trigger the cancel event
        # so the in-flight round aborts and the loop ends at the next boundary.
        # Not "interrupted" — a delete is a clean removal (goal text is cleared),
        # so it must not surface as a resumable/paused goal.
        session_store.update_goal(
            request.session_id,
            goal_text="", goal_done=False, goal_paused=False, goal_todos=[],
            goal_stopped=True, goal_interrupted=False, goal_force_count=0,
            goal_stream_id="",
        )
        cancel = _goal_cancel_events.pop(request.session_id, None)
        if cancel:
            cancel.set()
        for sid, vlist in list(_goal_active_streams.items()):
            if vlist == request.session_id:
                _goal_active_streams.pop(sid, None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session_id": request.session_id}

@app.post("/goal/resume")
async def goal_resume(request: GoalResumeRequest):
    """Resume a paused goal: clear the pause flag and continue the autonomous
    goal loop from the checkpoint (round 2+), streaming progress via SSE."""
    stream_id = str(uuid.uuid4())
    _goal_active_streams[stream_id] = request.session_id
    lock = _goal_locks.get(request.session_id)
    if lock is None:
        lock = asyncio.Lock()
        _goal_locks[request.session_id] = lock

    try:
        session = session_store.require(request.session_id)
    except KeyError as exc:
        _goal_active_streams.pop(stream_id, None)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session_store.update_goal(request.session_id, goal_paused=False, goal_stopped=False, goal_interrupted=False, goal_stream_id=stream_id)
    goal_text = session.goal_text
    work_mode = normalize_work_mode(session.work_mode)
    autonomy = normalize_autonomy(session.autonomy)
    language = request.language
    references = []
    referenced_ids: set[str] = set()
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(session_id=request.session_id, project_id=session.project_id or None)
        runtime = agent_registry.get_stream_runtime("single", None, None, resolved_workspace, referenced_sessions=referenced_ids)
    except (KeyError, ValueError, RuntimeError) as exc:
        _goal_active_streams.pop(stream_id, None)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cancel_event = asyncio.Event()
    _goal_cancel_events[request.session_id] = cancel_event

    async def event_stream():
        terminal_sent = False
        goal_parts_accum: list[dict[str, Any]] = []
        # Hold the per-session goal lock for the ENTIRE stream. The previous
        # version returned StreamingResponse inside `async with lock`, which
        # released the lock before the generator was even driven — so two
        # /goal/resume (or a resume racing a /chat/stream goal) could run in
        # parallel and corrupt the shared LangGraph checkpoint.
        async with lock:
            try:
                async with asyncio.timeout(SSE_TIMEOUT):
                    async for event in runtime.goal_stream(
                        [], request.session_id, language, work_mode, autonomy,
                        goal_text=goal_text, goal_continue_first=True,
                        _cancel_event=cancel_event, goal_stream_id=stream_id,
                    ):
                        event["session_id"] = request.session_id
                        etype = event.get("type")
                        if etype == "done":
                            for part in (event.get("parts") or []):
                                goal_parts_accum.append(part)
                        elif etype == "goal_done":
                            try:
                                session_store.update_goal(request.session_id, goal_done=True)
                                session_store.append_message(
                                    request.session_id,
                                    role="assistant",
                                    content=str(event.get("content") or ""),
                                    mode="single",
                                    work_mode=work_mode,
                                    autonomy=autonomy,
                                    parts=_merge_goal_parts(goal_parts_accum),
                                )
                            except KeyError:
                                pass
                            terminal_sent = True
                        elif etype == "goal_paused":
                            terminal_sent = True
                        yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                try:
                    session_store.update_goal(request.session_id, goal_interrupted=True)
                except KeyError:
                    pass
                yield f"data: {_json.dumps({'type': 'goal_done', 'session_id': request.session_id, 'content': '', 'reason': 'timeout'}, ensure_ascii=False)}\n\n"
                return
            except BaseException as exc:
                _goal_cancel_events.pop(request.session_id, None)
                if isinstance(exc, (asyncio.CancelledError, GeneratorExit, asyncio.TimeoutError)):
                    try:
                        session_store.update_goal(request.session_id, goal_interrupted=True)
                    except KeyError:
                        pass
                _goal_cancel_events.pop(request.session_id, None)
                yield f"data: {_json.dumps({'type': 'error', 'session_id': request.session_id, 'error': str(exc)[:400]}, ensure_ascii=False)}\n\n"
                return
            finally:
                _goal_cancel_events.pop(request.session_id, None)
                _goal_active_streams.pop(stream_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

class SessionCreateRequest(BaseModel):
    title: str = ""
    project_id: str = ""

class SessionRenameRequest(BaseModel):
    title: str

class SessionMessageIn(BaseModel):
    role: str
    content: str
    mode: str = ""
    provider: str = ""
    model: str = ""

class ProjectCreateRequest(BaseModel):
    name: str
    workspace_path: str

class ProjectRenameRequest(BaseModel):
    name: str

@app.get("/sessions")
async def list_sessions(project_id: str | None = None):
    return {"status": "ok", "sessions": session_store.list_sessions(project_id)}

@app.post("/sessions")
async def create_session(request: SessionCreateRequest):
    if not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    try:
        project_store.require(request.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = session_store.create(request.title, project_id=request.project_id)
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
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    agent_registry.forget_runtime_checkpoint(session_id)
    agent_registry.change_store.delete_session(session_id)
    return {"status": "ok"}

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
    from coworker.agents import generate_title
    new_title = generate_title(user_message, assistant_message or request.assistant_response or "")
    final_title = new_title or session.title
    session_store.rename(session_id, final_title)
    return {"status": "ok", "title": final_title}


class EditMessageRequest(BaseModel):
    content: str
    work_mode: Optional[str] = None
    access_mode: Optional[str] = None
    autonomy: Optional[str] = None


class RollbackRequest(BaseModel):
    with_code: bool = False


def request_language_for_session(session) -> str:
    return "en" if getattr(session, "_language", "zh") == "en" else "zh"


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


@app.post("/sessions/{session_id}/messages/{message_id}/rollback")
async def rollback_message(session_id: str, message_id: str, request: RollbackRequest | None = None):
    """Revert the conversation to the state right before the given message.
    The target message and everything after it are truncated; the agent
    checkpoint is reset so the next turn continues from that point.

    When ``with_code`` is true, the code changes made by the truncated
    assistant messages are reverted first (safe inverse edits with conflict
    detection — files that were changed by another session or the user are
    reported as conflicts and left untouched).
    """
    with_code = bool(request and request.with_code)
    try:
        session = session_store.require(session_id)
        target_index = session_store.find_message_index(session_id, message_id)
        dropped_messages = session.messages[target_index:]
        dropped_assistant_ids = [m.id for m in dropped_messages if m.role == "assistant"]

        revert_summary: dict[str, Any] = {"reverted": [], "conflicts": [], "reverted_count": 0, "conflict_count": 0, "total": 0}
        if with_code and dropped_assistant_ids:
            workspace = workspace_controller.workspace_for_session(session_id)
            revert_summary = agent_registry.change_store.revert_changes(session_id, dropped_assistant_ids, workspace)
            changed_ids = [c.get("id") for c in revert_summary.get("reverted", []) if c.get("id")]
            if changed_ids:
                agent_registry.change_store.delete_records(session_id, changed_ids)

        session_store.truncate_before(session_id, message_id)
        agent_registry.forget_runtime_checkpoint(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = session_store.require(session_id)
    return {
        "status": "ok",
        "messages": [m.__dict__ for m in session.messages],
        "revert": revert_summary,
    }


@app.get("/sessions/{session_id}/messages/{message_id}/revert-preview")
async def revert_preview(session_id: str, message_id: str):
    """Return the code changes that a rollback to (before) the given message
    would revert, so the UI can show a diff preview before confirming."""
    try:
        session = session_store.require(session_id)
        target_index = session_store.find_message_index(session_id, message_id)
        dropped_messages = session.messages[target_index:]
        dropped_assistant_ids = [m.id for m in dropped_messages if m.role == "assistant"]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    changes = agent_registry.change_store.changes_for_message_ids(session_id, dropped_assistant_ids)
    return {"status": "ok", "changes": changes, "count": len(changes)}


@app.post("/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_message(session_id: str, message_id: str):
    """Re-run the assistant reply for the user message that precedes the given
    assistant message (or, if given a user message, for that user message).
    Truncates after that user message and streams a fresh reply."""
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
        session_store.truncate_from(session_id, user_message.id)
        agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_name, model = _session_provider_context(session)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(session.work_mode)
    autonomy = normalize_autonomy(session.autonomy)
    language = request_language_for_session(session)
    provider_id = _provider_id_for_model(provider_name, model)

    async def event_stream():
        try:
            async for event in agent_registry.rerun_stream(
                history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids,
            ):
                event["session_id"] = session_id
                if event.get("type") == "done":
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
                            parts=event.get("parts") or [],
                        )
                        last = session.messages[-1] if session.messages else None
                        if last is not None:
                            agent_registry.change_store.assign_message(session_id, last.id)
                    except KeyError:
                        pass
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'type': 'error', 'session_id': session_id, 'error': str(exc)[:400]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/sessions/{session_id}/messages/{message_id}/edit")
async def edit_message(session_id: str, message_id: str, request: EditMessageRequest):
    """Edit a user message and re-run the conversation from that point."""
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        if session.messages[index].role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
        session_store.update_message_content(session_id, message_id, request.content)
        session_store.truncate_from(session_id, message_id)
        agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_name, model = _session_provider_context(session)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(request.work_mode or session.work_mode)
    if request.autonomy:
        autonomy = normalize_autonomy(request.autonomy)
    elif request.access_mode:
        from coworker.agents import autonomy_from_access

        autonomy = autonomy_from_access(request.access_mode)
    else:
        autonomy = normalize_autonomy(session.autonomy)
    if request.work_mode or request.autonomy or request.access_mode:
        try:
            session_store.update_modes(session_id, work_mode, autonomy)
        except KeyError:
            pass
    language = request_language_for_session(session)
    provider_id = _provider_id_for_model(provider_name, model)

    async def event_stream():
        try:
            async for event in agent_registry.rerun_stream(
                history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids,
            ):
                event["session_id"] = session_id
                if event.get("type") == "done":
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
                            parts=event.get("parts") or [],
                        )
                        last = session.messages[-1] if session.messages else None
                        if last is not None:
                            agent_registry.change_store.assign_message(session_id, last.id)
                    except KeyError:
                        pass
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'type': 'error', 'session_id': session_id, 'error': str(exc)[:400]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/sessions/{session_id}/messages/{message_id}/rollback")
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
        workspace_path = workspace_controller.validate_workspace_path(request.workspace_path)
        project = project_store.create(request.name, workspace_path)
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
    if not project_store.delete(project_id):
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    for session in session_store.list_sessions(project_id):
        agent_registry.forget_runtime_checkpoint(session["id"])
        agent_registry.change_store.delete_session(session["id"])
    session_store.delete_by_project(project_id)
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

@app.get("/traces/agent")
async def agent_traces(limit: int = 100):
    return {"status": "ok", "events": agent_registry.list_agent_traces(limit)}

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
            from coworker.agents import normalize_autonomy

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

        approval = command_approval_store.set_decision(request.approval_id, status, decision)

        interrupt_id = str(context.get("interrupt_id") or "")
        resume_id = interrupt_id or approval.get("id", "")
        if context.get("source") == "agent_langgraph_hitl" and interrupt_id:
            siblings = [
                item
                for item in command_approval_store.list()
                if isinstance(item.get("context"), dict)
                and str(item.get("context", {}).get("interrupt_id") or "") == interrupt_id
            ]
            all_decided = all(item.get("decision") is not None for item in siblings)
            if all_decided:
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


def _merge_goal_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge tool/reasoning/plan parts accumulated across goal rounds into one
    final part list (tools deduped by id, newest wins)."""
    return _merge_message_parts([], parts)


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
    context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
    session_id = str(context.get("session_id") or "")
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
    except Exception as exc:
        await approval_event_bus.publish(
            resume_id,
            {"type": "error", "session_id": session_id, "error": str(exc)[:400], "resume_id": resume_id},
        )
    finally:
        approval_event_bus.close(resume_id)


@app.get("/command-approvals/events/{resume_id}")
async def stream_approval_events(resume_id: str):
    """SSE stream of resume progress events for a given resume_id."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        queue = approval_event_bus.subscribe(resume_id)
        try:
            while True:
                event = await queue.get()
                if event.get("type") == "stream_end":
                    break
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            approval_event_bus.unsubscribe(resume_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/providers")
async def list_providers():
    return provider_manager.public_config()

@app.post("/providers")
async def create_provider(request: ProviderCreate):
    try:
        provider = provider_manager.add_provider(
            name=request.name,
            provider_type=request.provider_type,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "provider": provider}

@app.put("/providers/default")
async def set_default_provider(request: DefaultProviderPayload):
    try:
        config = config_controller.update_runtime_config({
            "selected_provider_id": request.provider_id,
            "selected_model": request.model,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "config": config}

@app.put("/providers/{provider_id}")
async def update_provider(provider_id: str, request: ProviderUpdate):
    try:
        provider = provider_manager.update_provider(
            provider_id,
            name=request.name,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            enabled=request.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "provider": provider}

@app.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str):
    try:
        provider_manager.delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}

@app.post("/providers/test")
async def test_provider(request: ProviderTestPayload):
    result = provider_manager.test_provider_connection(request.base_url, request.api_key, request.model)
    return {"status": "ok", "result": result}

@app.post("/providers/fetch-models")
async def fetch_provider_models(request: ProviderFetchModelsPayload):
    try:
        models = provider_manager.fetch_models(request.base_url, request.api_key, request.provider_type)
    except Exception as exc:
        return {"status": "error", "models": [], "error": str(exc)[:300]}
    return {"status": "ok", "models": models}


# ─────────────────────────── MCP ──────────────────────────

from backend.coworker.mcp.mcp import SECRET_PLACEHOLDER, STATUS_CONNECTED, STATUS_ERROR, STATUS_NEEDS_AUTH
from backend.coworker.mcp.mcp_discover import TEMPLATES
from backend.coworker.mcp.mcp_test import test_mcp_connection_sync

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
    await websocket.accept()

    project_id = websocket.query_params.get("project_id")

    if not _PTY_AVAILABLE:
        await websocket.send_text(json.dumps({"type": "error", "message": "Interactive terminal is not supported on this platform."}))
        await websocket.close()
        return

    try:
        if project_id:
            workspace = workspace_controller.workspace_for_project(project_id)
        else:
            workspace = workspace_controller.default()
        cwd = str(workspace.root)
    except Exception:
        cwd = os.path.expanduser("~")

    shell = os.environ.get("SHELL") or "/bin/bash"
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9527)
