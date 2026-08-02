import shlex
from typing import Any, Optional
from uuid import uuid4

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
from coworker.workspace import COMMAND_APPROVAL_FILENAME, TOOL_AUDIT_FILENAME, CommandApprovalStore, Workspace, list_tool_audit_events

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
workspace = Workspace(settings.workspace_dir, tool_audit_path)
command_approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)

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

class CommandApprovalAction(BaseModel):
    approval_id: str

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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid4())
    work_mode = normalize_work_mode(request.work_mode)
    access_mode = normalize_access_mode(request.access_mode)
    try:
        runtime = agent_registry.get_runtime(request.mode, request.provider_id, request.model)
        reply = runtime.run(format_user_message(request.message, request.attachments), session_id, request.language, work_mode, access_mode)
    except Exception as exc:
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
            session = session_store.create(request.message, project_id=request.project_id or "")
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
    work_mode = normalize_work_mode(request.work_mode)
    access_mode = normalize_access_mode(request.access_mode)
    try:
        runtime = agent_registry.get_stream_runtime(request.mode, request.provider_id, request.model)
    except RuntimeError as exc:
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
        except KeyError:
            session_id = None
    else:
        session = session_store.create(request.message, project_id=request.project_id or "")
        session_id = session.id

    session_store.append_message(session_id, role="user", content=request.message, mode=request.mode, attachments=request.attachments)
    messages = history + [{"role": "user", "content": format_user_message(request.message, request.attachments)}]

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

class SessionMoveRequest(BaseModel):
    project_id: str = ""

class SessionMessageIn(BaseModel):
    role: str
    content: str
    mode: str = ""
    provider: str = ""
    model: str = ""

class ProjectCreateRequest(BaseModel):
    name: str

class ProjectRenameRequest(BaseModel):
    name: str

@app.get("/sessions")
async def list_sessions(project_id: str | None = None):
    return {"status": "ok", "sessions": session_store.list_sessions(project_id)}

@app.post("/sessions")
async def create_session(request: SessionCreateRequest):
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
    return {"status": "ok"}

@app.post("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: SessionRenameRequest):
    try:
        session = session_store.rename(session_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.public()}

@app.post("/sessions/{session_id}/move")
async def move_session(session_id: str, request: SessionMoveRequest):
    try:
        session = session_store.set_project(session_id, request.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.public()}

@app.get("/projects")
async def list_projects():
    projects = []
    for project in project_store.list_projects():
        count = len(session_store.list_sessions(project.id))
        projects.append({"id": project.id, "name": project.name, "created_at": project.created_at, "updated_at": project.updated_at, "session_count": count})
    return {"status": "ok", "projects": projects}

@app.post("/projects")
async def create_project(request: ProjectCreateRequest):
    try:
        project = project_store.create(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "project": {"id": project.id, "name": project.name, "created_at": project.created_at, "updated_at": project.updated_at, "session_count": 0}}

@app.post("/projects/{project_id}/rename")
async def rename_project(project_id: str, request: ProjectRenameRequest):
    try:
        project = project_store.rename(project_id, request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    count = len(session_store.list_sessions(project.id))
    return {"status": "ok", "project": {"id": project.id, "name": project.name, "created_at": project.created_at, "updated_at": project.updated_at, "session_count": count}}

@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if not project_store.delete(project_id):
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    session_store.delete_by_project(project_id)
    return {"status": "ok"}

@app.get("/workspace/tree")
async def workspace_tree(path: str = ""):
    try:
        return {"status": "ok", "root": str(settings.workspace_dir), "tree": workspace.build_tree(path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/workspace/dir")
async def workspace_dir(path: str = ""):
    try:
        return {"status": "ok", "path": path or ".", "entries": workspace.list_dir(path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/workspace/file")
async def workspace_file(path: str):
    try:
        return {"status": "ok", "path": path, "file": workspace.read_preview(path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/workspace/command")
async def workspace_command(request: WorkspaceCommandRequest):
    try:
        result = workspace.run_command(
            shlex.split(request.command),
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            audit_context={"source": "bottom_panel_terminal"},
            approval_store=command_approval_store,
            require_approval=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "result": result}

@app.get("/audit/tool")
async def tool_audit(limit: int = 100):
    return {"status": "ok", "events": list_tool_audit_events(tool_audit_path, limit)}

@app.get("/command-approvals")
async def list_command_approvals():
    return {"status": "ok", "approvals": command_approval_store.list()}

@app.post("/command-approvals/approve")
async def approve_command(request: CommandApprovalAction):
    try:
        approval = command_approval_store.approve(request.approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "approval": approval}

@app.post("/command-approvals/deny")
async def deny_command(request: CommandApprovalAction):
    try:
        approval = command_approval_store.deny(request.approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "approval": approval}

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
