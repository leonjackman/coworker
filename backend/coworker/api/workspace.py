# -*- coding: utf-8 -*-

import shlex
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID, ProjectStore
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
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.api.memory_org import (
    _ensure_org,
    _unique_memory_dir
)
from coworker.api.state import (
    agent_registry,
    app,
    command_approval_store,
    logger,
    mcp_manager,
    memory_manager,
    org_store,
    project_store,
    session_store,
    settings,
    skill_manager,
    workspace_controller
)
from coworker.api.streaming import (
    _cleanup_session_screenshots
)

from fastapi import APIRouter

router = APIRouter()


class WorkspaceCommandRequest(BaseModel):
    command: str
    cwd: str = ""
    timeout_seconds: int = 20
    project_id: str = ""
class ProjectCreateRequest(BaseModel):
    name: str
    workspace_path: str
    mode: str = ORG_MODE_SINGLE
class ProjectRenameRequest(BaseModel):
    name: str
@router.get("/projects")
async def list_projects():
    projects = []
    for project in project_store.list_projects():
        count = len(session_store.list_sessions(project.id))
        projects.append(workspace_controller.public_project(project.id, count))
    return {"status": "ok", "projects": projects}
@router.post("/projects")
async def create_project(request: ProjectCreateRequest):
    try:
        if request.mode not in ORG_MODES:
            raise ValueError(f"mode must be one of {list(ORG_MODES)}")
        workspace_path = workspace_controller.validate_workspace_path(request.workspace_path)
        # 系统保留的聊天沙箱目录不可被用户项目占用。
        if workspace_path == str((settings.data_dir / "chat").resolve()):
            raise ValueError("该文件夹为系统保留的聊天项目工作区，不可创建新项目")
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
            memory_manager.registry.ensure_project(project.memory_dir, workspace_root=workspace_path)
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
@router.post("/projects/{project_id}/rename")
async def rename_project(project_id: str, request: ProjectRenameRequest):
    if project_id == CHAT_PROJECT_ID:
        raise HTTPException(status_code=400, detail="聊天项目为系统保留项目，不可重命名")
    try:
        project = project_store.rename(project_id, request.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    count = len(session_store.list_sessions(project.id))
    return {"status": "ok", "project": workspace_controller.public_project(project.id, count)}
@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if project_id == CHAT_PROJECT_ID:
        raise HTTPException(status_code=400, detail="聊天项目为系统保留项目，不可删除")
    try:
        memory_dir = project_store.memory_dir_for(project_id)
    except (KeyError, ValueError):
        memory_dir = ""
    if not project_store.delete(project_id):
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    for session in session_store.list_sessions(project_id):
        await agent_registry.forget_runtime_checkpoint(session["id"])
        agent_registry.evict_runtime(session["id"])
        agent_registry.change_store.delete_session(session["id"])
        agent_registry.snapshot_manager.delete_session(session["id"])
        command_approval_store.purge_session(session["id"])
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
@router.get("/workspace/tree")
async def workspace_tree(path: str = "", project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "root": str(workspace.root), "tree": workspace.build_tree(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/workspace/dir")
async def workspace_dir(path: str = "", project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "path": path or ".", "entries": workspace.list_dir(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/workspace/file")
async def workspace_file(path: str, project_id: str = ""):
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "path": path, "file": workspace.read_preview(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/workspace/file/preview")
async def workspace_file_preview(path: str, project_id: str = ""):
    """Rich dashboard file preview: text content, or base64 image/PDF/audio/video
    payload, or a non-previewable classification (office archives etc.)."""
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        return {"status": "ok", "path": path, "preview": workspace.read_preview_payload(path)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/diffs/current")
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
@router.get("/workspace/branch")
async def workspace_branch(project_id: str = ""):
    """Current git branch for the project workspace (read-only status)."""
    try:
        workspace = workspace_controller.workspace_for_project(project_id) if project_id else workspace_controller.default()
        result = workspace_git_branch(workspace.root)
        return {"status": "ok", **result}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/projects/{project_id}/dashboard")
async def project_dashboard(project_id: str):
    """Aggregate read-only dashboard bundle for one project.

    Phase 0 of the business-agent roadmap: makes the project's files, agents,
    tools and capabilities visible so they can later become configurable.
    """
    from coworker.dashboard import build_dashboard_data

    try:
        return build_dashboard_data(
            project_id=project_id,
            workspace_controller=workspace_controller,
            session_store=session_store,
            org_store=org_store,
            mcp_manager=mcp_manager,
            skill_manager=skill_manager,
            settings=settings,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.post("/workspace/command")
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
