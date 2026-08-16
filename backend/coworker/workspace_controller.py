from pathlib import Path
from typing import Any
from uuid import uuid4

from .projects import ProjectStore
from .sessions import SessionStore
from .workspace import TOOL_AUDIT_FILENAME, Workspace, fingerprint_path_for


class WorkspaceController:
    def __init__(self, project_store: ProjectStore, session_store: SessionStore, default_workspace: Path, data_dir: Path, org_store=None):
        self.project_store = project_store
        self.session_store = session_store
        self.default_workspace = default_workspace
        self.audit_path = data_dir / TOOL_AUDIT_FILENAME
        self.data_dir = data_dir
        self.org_store = org_store

    def validate_workspace_path(self, workspace_path: str) -> str:
        cleaned = workspace_path.strip()
        if not cleaned:
            raise ValueError("workspace path is required")
        path = Path(cleaned).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"workspace path does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"workspace path is not a directory: {path}")
        probe = path / f".coworker-write-test-{uuid4().hex}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError(f"workspace path is not writable: {path}") from exc
        return str(path)

    def create_workspace(self, workspace_path: str) -> Workspace:
        return Workspace(
            Path(workspace_path),
            self.audit_path,
            fingerprint_path_for(self.data_dir, Path(workspace_path)),
        )

    def default(self) -> Workspace:
        return self.create_workspace(str(self.default_workspace))

    def workspace_for_project(self, project_id: str) -> Workspace:
        project = self.project_store.require(project_id)
        if not project.workspace_path:
            raise ValueError(f"project {project_id} has no workspace path")
        workspace_path = self.validate_workspace_path(project.workspace_path)
        return self.create_workspace(workspace_path)

    def workspace_for_session(self, session_id: str) -> Workspace:
        session = self.session_store.require(session_id)
        if not session.project_id:
            return self.default()
        return self.workspace_for_project(session.project_id)

    def workspace_for_chat(self, *, session_id: str | None = None, project_id: str | None = None) -> Workspace:
        if session_id:
            return self.workspace_for_session(session_id)
        if project_id:
            return self.workspace_for_project(project_id)
        return self.default()

    def public_project(self, project_id: str, session_count: int) -> dict[str, Any]:
        project = self.project_store.require(project_id)
        if not project.memory_dir:
            project.memory_dir = self.project_store.memory_dir_for(project_id)
        roster: list[dict[str, Any]] = []
        if self.org_store is not None and project.memory_dir:
            org = self.org_store.load(project.memory_dir)
            roster = self.org_store.members_for(org)
        return {
            "id": project.id,
            "name": project.name,
            "workspace_path": project.workspace_path,
            "workspace_available": bool(project.workspace_path and Path(project.workspace_path).expanduser().exists()),
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "session_count": session_count,
            "memory_dir": project.memory_dir,
            "roster": roster,
        }
