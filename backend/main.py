import shlex
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from coworker.agents import AgentMode, AgentRuntimeRegistry, Language, format_user_message, normalize_access_mode, normalize_work_mode
from coworker.config import load_settings
from coworker.config_controller import AppConfigController
from coworker.projects import ProjectStore
from coworker.providers import ProviderManager
from coworker.sessions import SessionStore
from coworker.workspace import COMMAND_APPROVAL_FILENAME, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events
from coworker.workspace_controller import WorkspaceController

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
settings = load_settings()
agent_registry = AgentRuntimeRegistry(settings)
provider_manager = ProviderManager(settings.data_dir / "providers.json")
config_controller = AppConfigController(settings, provider_manager)
session_store = SessionStore(settings.data_dir / "sessions")
project_store = ProjectStore(settings.data_dir / "projects.json")
tool_audit_path = settings.data_dir / TOOL_AUDIT_FILENAME
command_approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
workspace_controller = WorkspaceController(project_store, session_store, settings.workspace_dir, settings.data_dir)

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: AgentMode = "single"
    language: Language = "zh"
    work_mode: Optional[str] = None
    access_mode: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[str] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)

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

class CommandApprovalAction(BaseModel):
    approval_id: str

class ApprovalDecisionPayload(BaseModel):
    type: str
    message: str = ""

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
    access_mode = normalize_access_mode(request.access_mode)
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(
            session_id=session_id,
            project_id=request.project_id,
        )
        if not session_id:
            created_session = session_store.new_session(request.message, project_id=request.project_id or "")
            session_id = created_session.id
        runtime = agent_registry.get_runtime(request.mode, request.provider_id, request.model, resolved_workspace)
        reply = runtime.run(format_user_message(request.message, request.attachments), session_id, request.language, work_mode, access_mode)
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
                attachments=request.attachments,
            )
            session_store.append_message(
                session_id,
                role="assistant",
                content=reply.content,
                mode=reply.mode,
                provider=reply.provider,
                model=request.model or "",
            )
        else:
            session = created_session or session_store.require(session_id)
            if created_session:
                session_store.save(session)
            session_store.append_message(session.id, role="user", content=request.message, mode=request.mode, attachments=request.attachments)
            session_store.append_message(
                session.id,
                role="assistant",
                content=reply.content,
                mode=reply.mode,
                provider=reply.provider,
                model=request.model or "",
            )
            session_id = session.id
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

@app.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest):
    if not request.session_id and not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required to start a new chat")
    work_mode = normalize_work_mode(request.work_mode)
    access_mode = normalize_access_mode(request.access_mode)
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(
            session_id=request.session_id or None,
            project_id=request.project_id,
        )
        runtime = agent_registry.get_stream_runtime(request.mode, request.provider_id, request.model, resolved_workspace)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = request.session_id
    history = []
    if request.session_id:
        try:
            session = session_store.require(request.session_id)
            history = [
                {"role": m.role, "content": format_user_message(m.content, m.attachments) if m.role == "user" else m.content}
                for m in session.messages
                if m.role in {"user", "assistant"} and m.content
            ]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        session = session_store.create(request.message, project_id=request.project_id or "")
        session_id = session.id

    user_message = {"role": "user", "content": format_user_message(request.message, request.attachments)}
    session_store.append_message(session_id, role="user", content=request.message, mode=request.mode, attachments=request.attachments)
    if runtime.owns_runtime_messages and agent_registry.has_runtime_checkpoint(session_id):
        messages = [user_message]
    else:
        messages = history + [user_message]

    async def event_stream():
        try:
            async for event in runtime.stream(messages, session_id, request.language, work_mode, access_mode):
                event["session_id"] = session_id
                if event.get("type") == "done":
                    try:
                        session_store.append_message(
                            session_id,
                            role="assistant",
                            content=event["content"],
                            mode=event.get("mode") or request.mode,
                            provider=event.get("provider") or "",
                            model=event.get("model") or request.model or "",
                        )
                    except KeyError:
                        pass
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'type': 'error', 'session_id': session_id, 'error': str(exc)[:400]}, ensure_ascii=False)}\n\n"

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
    return {"status": "ok"}

@app.post("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: SessionRenameRequest):
    try:
        session = session_store.rename(session_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.public()}

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
        kind = str(context.get("kind") or "command")
        decision_type = request.decision.type

        if decision_type == "always":
            command = approval.get("command") if isinstance(approval.get("command"), list) else []
            cwd = str(approval.get("cwd") or "")
            if command:
                from coworker.workspace import Workspace

                command_approval_store.always_allow(Workspace.command_digest(command, cwd))
            decision = {"type": "approve"}
            status = "approved"
        elif decision_type == "approve":
            decision = {"type": "approve"}
            status = "approved"
        elif decision_type == "respond":
            decision = {"type": "respond", "message": request.decision.message}
            status = "answered"
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
        events: list[dict[str, Any]] = []
        resolved_statuses = {"approved", "denied", "answered"}
        if context.get("source") == "agent_langgraph_hitl" and interrupt_id:
            siblings = [
                item
                for item in command_approval_store.list()
                if isinstance(item.get("context"), dict)
                and str(item.get("context", {}).get("interrupt_id") or "") == interrupt_id
            ]
            all_decided = all(item.get("decision") is not None for item in siblings)
            if all_decided:
                ordered = sorted(
                    siblings,
                    key=lambda item: int(item.get("context", {}).get("action_index") or 0),
                )
                decisions = [item.get("decision") for item in ordered if item.get("decision")]
                if decisions:
                    events = await agent_registry.resume_interrupt(approval, decisions)
                    done = next((event for event in reversed(events) if event.get("type") == "done"), None)
                    session_id = str(context.get("session_id") or "")
                    if done and session_id:
                        try:
                            session_store.append_message(
                                session_id,
                                role="assistant",
                                content=str(done.get("content") or ""),
                                mode="single",
                                provider=str(done.get("provider") or ""),
                                model=str(done.get("model") or ""),
                            )
                        except KeyError:
                            pass
                    for item in ordered:
                        command_approval_store.mark_consumed(item.get("id", ""))
            else:
                events = []
        return {"status": "ok", "approval": approval, "events": events, "resumed": bool(events)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9527)
