# -*- coding: utf-8 -*-

from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID, ProjectStore
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
from coworker.api.settings import (
    save_user_memory_settings
)
from coworker.api.state import (
    agent_registry,
    app,
    logger,
    memory_manager,
    org_store,
    project_store,
    provider_manager,
    session_store,
    settings
)
from coworker.api.streaming import (
    _cleanup_session_screenshots
)

from fastapi import APIRouter

router = APIRouter()


CHAT_PROJECT_NAME = "聊天"
def _ensure_chat_project() -> str:
    """Idempotent startup self-healing for the reserved 聊天 project.

    Ensures all three artifacts exist: the project record (projects.json), the
    system-designated sandbox workspace folder, and the memory scaffold. Any of
    them can be lost (manual folder deletion, projects.json edit) — this
    recreates them so the 聊天 project is always present and reachable. The
    display name is localized by the frontend via the ``is_chat`` flag.
    """
    chat_workspace = settings.data_dir / "chat"
    chat_workspace.mkdir(parents=True, exist_ok=True)
    project = project_store.ensure_system_project(CHAT_PROJECT_NAME, str(chat_workspace), CHAT_MEMORY_DIR)
    try:
        memory_manager.registry.ensure_project(project.memory_dir, workspace_root=str(chat_workspace))
        _ensure_org(project.memory_dir, ORG_MODE_SINGLE)
        memory_manager.registry.ensure_agent(memory_manager.root / project.memory_dir, DEFAULT_AGENT)
        _seed_chat_memory(project.memory_dir, chat_workspace)
    except Exception as exc:  # noqa: BLE001 - scaffold must not block startup
        logger.warning("chat project memory scaffold failed: %s", exc)
    return project.id
_CHAT_SOUL_MD = (
    "# SOUL\n"
    "\n"
    "你是「聊天」项目里的 CoWorker「懒懒男孩」（Lazzzy Boy），来自相信「懒是美德、"
    "技术本该让生活更轻松」的 Lazzzy Boy 工作室。\n"
    "\n"
    "- 语气自然口语化，像一位靠谱又有点懒散的同事，而不是客服或说明书。\n"
    "- 先给结论，再给原因；能一句话说清，就绝不说三句。\n"
    "- 严肃的问题也可以用轻松的口吻来答。\n"
)
_CHAT_AGENT_MD = (
    "# AGENT\n"
    "\n"
    "这是系统内置的「聊天」项目（Lazzzy Boy 的懒哲学：少做事、做对事）。\n"
    "\n"
    "- 这里是对话场景，不是代码开发任务。除非用户明确要求，否则不读取/创建/修改\n"
    "  任何文件、不运行命令、不调用搜索或 MCP 等工具。\n"
    "- 可以直接凭知识回答，不必刻意调用工具。\n"
    "- 用户提到「项目 / 代码 / 写文件」时，先确认是想在聊天里讨论，还是想真正开发\n"
    "  （后者建议用户去创建一个项目）。\n"
)
_CHAT_BASE_MD = (
    "# 聊天项目说明\n"
    "\n"
    "本目录是系统为「聊天」项目分配的沙箱工作区，用于隔离简单对话。\n"
    "\n"
    "- 日常对话直接回答即可，不要修改这里的任何文件。\n"
    "- 只有用户明确要求时，才在本目录内创建/保存内容。\n"
)
def _chat_context_md(workspace: Path) -> str:
    return (
        "# 项目背景与约束\n"
        "\n"
        "（由系统生成与维护 — 记录项目的高层级背景、约束与上下文）\n"
        "\n"
        "## 项目信息\n"
        "\n"
        f"- **项目名**: 聊天（系统内置）\n"
        f"- **项目根路径**: `{workspace}`（沙箱工作区）\n"
        "\n"
        "## 聊天模式约束\n"
        "\n"
        "这是系统内置的「聊天」项目，用于和用户轻松对话、答疑与讨论，不是代码开发项目。"
        "除非用户明确要求，否则不要修改本目录的文件、不要运行命令。\n"
    )
def _seed_chat_memory(memory_dir: str, workspace: Path) -> None:
    """Seed the chat project's memory with the Lazzzy Boy persona + chat rules.

    Idempotent: each file is written only when it is still the default skeleton
    (or missing), so user edits survive every startup/self-heal.
    """
    from coworker.memory.layout import AGENT_SKELETON

    project_dir = memory_manager.root / memory_dir
    agent_base = project_dir / DEFAULT_AGENT / "BASE"
    project_base = project_dir / "BASE"

    def _write_if_unchanged(path: Path, expected: str | None, content: str) -> None:
        try:
            if path.exists():
                current = path.read_text(encoding="utf-8", errors="replace").strip()
                if expected is not None and current != expected.strip():
                    return  # 用户已编辑，绝不覆盖
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - best-effort
            logger.warning("chat memory seed failed for %s: %s", path, exc)

    _write_if_unchanged(agent_base / "SOUL.md", AGENT_SKELETON.get("SOUL.md"), _CHAT_SOUL_MD)
    _write_if_unchanged(agent_base / "AGENT.md", AGENT_SKELETON.get("AGENT.md"), _CHAT_AGENT_MD)
    _write_if_unchanged(project_base / "CHAT.md", None, _CHAT_BASE_MD)

    # CONTEXT.md：仅在仍是自动种子（build_project_context_md 输出）时替换为聊天背景。
    from coworker.agent.system_prompt import build_project_context_md

    auto_seed = build_project_context_md(workspace)
    _write_if_unchanged(project_base / "PROJECT" / "CONTEXT.md", auto_seed, _chat_context_md(workspace))
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
    # 系统保留的聊天项目 memory_dir 永远不可被普通项目占用。
    taken.add(CHAT_MEMORY_DIR)
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
memory_manager.configure_extractor(
    llm_factory=_memory_extract_llm,
    transcript_provider=_memory_transcript,
)
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
@router.get("/api/memory/discover")
async def memory_discover(project_id: str = "", agent: str = DEFAULT_AGENT, scope: str = "all"):
    """Memory library tree: system files + project views (BASE/PROJECT/agents).

    ``scope="project"`` restricts the result to the given project's own memory
    (matched by ``memory_dir``), excluding system files and every other project
    — used by the dashboard's project memory tab.
    """
    project_dir = _project_memory_dir(project_id)
    if project_dir:
        _ensure_agent_skeleton(project_dir, agent)
        try:
            _ensure_org(project_dir)
        except Exception:  # noqa: BLE001 - org scaffold must not break discovery
            pass
    if scope == "project" and not project_dir:
        raise HTTPException(status_code=404, detail=f"unknown project {project_id!r}")
    library = memory_manager.scanner.scan(include_missing=True)
    projects = []
    for view in library.projects:
        if scope == "project" and view.name != project_dir:
            continue
        mode = ORG_MODE_MULTI
        if org_store.exists(view.name):
            mode = org_store.load(view.name).mode
        if mode != ORG_MODE_MULTI:
            view = _scoped_single_project_view(view)
        projects.append(view)
    return {
        "root": str(library.root),
        "system": [] if scope == "project" else [n.to_dict() for n in library.system],
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
@router.get("/api/memory/file")
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
@router.get("/api/memory/resolve")
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
@router.post("/api/memory/file")
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
@router.post("/api/memory/delete")
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
@router.post("/api/memory/move")
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
@router.get("/api/memory/search")
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
@router.post("/api/memory/export")
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
@router.post("/api/memory/import/preview")
async def preview_import_api(request: MemoryImportPreviewRequest):
    """Unpack a zip into a staging dir and report entries with conflict flags."""
    if not request.path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        return preview_import(memory_manager.root, memory_manager.data_dir, request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.post("/api/memory/import/apply")
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
@router.post("/api/memory/write")
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
@router.post("/api/memory/register-project")
async def memory_register_project(request: MemoryWriteRequest):
    """Materialize the project memory skeleton (BASE/ + BASE/PROJECT/)."""
    project_dir = _project_memory_dir(request.project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    memory_manager.registry.ensure_project(project_dir)
    return {"status": "ok", "project_dir": project_dir}
@router.post("/api/memory/register-agent")
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
@router.get("/api/org")
async def org_get(project_id: str = ""):
    """Return the org manifest for one project (agents + teams + config + roster)."""
    project_dir = _project_memory_dir(project_id)
    if not project_dir:
        raise HTTPException(status_code=400, detail="project memory is unavailable")
    _ensure_org(project_dir)
    org = org_store.load(project_dir)
    return _org_public(org)
@router.post("/api/org/agent")
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
@router.patch("/api/org/agent")
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
@router.delete("/api/org/agent")
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
        agent_registry.evict_runtime(session["id"])
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
@router.post("/api/org/team")
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
@router.patch("/api/org/team")
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
@router.delete("/api/org/team")
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
@router.patch("/api/org/config")
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
@router.get("/api/memory/status")
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
@router.get("/api/memory/settings")
async def get_memory_settings():
    """Runtime memory settings (the Settings page surface)."""
    return {
        "enabled": memory_manager.enabled,
        "auto_extract": memory_manager.auto_extract,
    }
@router.post("/api/memory/settings")
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
