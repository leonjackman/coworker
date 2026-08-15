import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import atomic_write_text

CONFIG_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    updated_at: str
    workspace_path: str = ""
    memory_dir: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Project":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
            workspace_path=str(payload.get("workspace_path", "")),
            memory_dir=str(payload.get("memory_dir", "")),
        )


@dataclass
class ProjectConfig:
    version: int = CONFIG_VERSION
    projects: list[Project] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectConfig":
        return cls(
            version=int(payload.get("version", CONFIG_VERSION)),
            projects=[Project.from_dict(item) for item in payload.get("projects", [])],
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def find(self, project_id: str) -> Project | None:
        for project in self.projects:
            if project.id == project_id:
                return project
        return None

    def find_by_workspace_path(self, workspace_path: str) -> Project | None:
        for project in self.projects:
            if project.workspace_path == workspace_path:
                return project
        return None


class ProjectStore:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProjectConfig:
        if not self.config_path.exists():
            config = ProjectConfig()
            self.save(config)
            return config
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        return ProjectConfig.from_dict(payload)

    def save(self, config: ProjectConfig) -> None:
        config.updated_at = _now()
        atomic_write_text(
            self.config_path,
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def list_projects(self) -> list[Project]:
        return self.load().projects

    def require(self, project_id: str) -> Project:
        project = self.load().find(project_id)
        if not project:
            raise KeyError(f"project {project_id} not found")
        return project

    def memory_dir_for(self, project_id: str) -> str:
        """Return the project's memory_dir, generating + persisting it lazily.

        Legacy projects created before the memory_dir field existed get one
        derived from their ``created_at`` on first use, so the memory library
        stays stable even without a stored value.
        """
        from coworker.memory.layout import memory_dir_from_created_at

        config = self.load()
        project = config.find(project_id)
        if not project:
            raise KeyError(f"project {project_id} not found")
        if project.memory_dir:
            return project.memory_dir
        project.memory_dir = memory_dir_from_created_at(project.created_at)
        project.updated_at = _now()
        self.save(config)
        return project.memory_dir

    def create(self, name: str, workspace_path: str, memory_dir: str = "") -> Project:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("project name is required")
        cleaned_workspace = workspace_path.strip()
        if not cleaned_workspace:
            raise ValueError("workspace path is required")
        config = self.load()
        existing = config.find_by_workspace_path(cleaned_workspace)
        if existing:
            if memory_dir and not existing.memory_dir:
                existing.memory_dir = memory_dir
                self.save(config)
            return existing
        now = _now()
        project = Project(
            id=str(uuid.uuid4()),
            name=cleaned[:60],
            created_at=now,
            updated_at=now,
            workspace_path=cleaned_workspace,
            memory_dir=memory_dir,
        )
        config.projects.append(project)
        self.save(config)
        return project

    def rename(self, project_id: str, name: str) -> Project:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("project name is required")
        config = self.load()
        project = config.find(project_id)
        if not project:
            raise KeyError(f"project {project_id} not found")
        project.name = cleaned[:60]
        # memory_dir is intentionally NOT recomputed: renaming a project must
        # never invalidate its memory library directory.
        project.updated_at = _now()
        self.save(config)
        return project

    def delete(self, project_id: str) -> bool:
        config = self.load()
        for index, project in enumerate(config.projects):
            if project.id != project_id:
                continue
            config.projects.pop(index)
            self.save(config)
            return True
        return False
