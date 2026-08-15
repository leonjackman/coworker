"""Memory library discovery: scan the directory tree in injection order.

Discovers system files, project trees (BASE + BASE/PROJECT) and agent trees
(core files + SESSIONS). Nodes are returned in injection precedence order:

    system → BASE/* (user) → BASE/PROJECT/* (system) → SOUL → AGENT → MEMORY
    → SESSIONS/*

Each ``MemoryNode`` carries a ``rel`` path (relative to the memory root) used
by the store and the API, so callers never need absolute paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .layout import AGENT_CORE_FILES, BASE_DIR, PROJECT_SUBDIR, SESSIONS_DIR, SYSTEM_FILES
from .memory_file import MemoryFile, load_file

logger = logging.getLogger(__name__)

_KINDS = ("system", "base_file", "project_file", "agent_file", "session_file")


@dataclass(frozen=True)
class MemoryNode:
    kind: str            # one of _KINDS
    name: str            # file name (stem for display convenience) or label
    rel: str             # path relative to the memory root ("/"-separated)
    path: Path           # absolute path
    content: str = ""
    mtime: float = 0.0
    blocks: tuple[str, ...] = ()

    @classmethod
    def from_file(cls, kind: str, rel: str, file: MemoryFile) -> "MemoryNode":
        return cls(
            kind=kind,
            name=file.path.name,
            rel=rel,
            path=file.path,
            content=file.content,
            mtime=file.mtime,
            blocks=file.blocks,
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "rel": self.rel,
            "mtime": self.mtime,
            "content": self.content,
            "blocks": list(self.blocks),
            "char_count": len(self.content),
        }


@dataclass(frozen=True)
class AgentView:
    name: str
    rel: str
    soul: MemoryNode | None = None
    agent: MemoryNode | None = None
    memory: MemoryNode | None = None
    sessions: list[MemoryNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rel": self.rel,
            "soul": self.soul.to_dict() if self.soul else None,
            "agent": self.agent.to_dict() if self.agent else None,
            "memory": self.memory.to_dict() if self.memory else None,
            "sessions": [s.to_dict() for s in self.sessions],
        }


@dataclass(frozen=True)
class ProjectView:
    name: str            # directory name = memory_dir
    rel: str
    base: list[MemoryNode] = field(default_factory=list)
    project: list[MemoryNode] = field(default_factory=list)
    agents: list[AgentView] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rel": self.rel,
            "base": [b.to_dict() for b in self.base],
            "project": [p.to_dict() for p in self.project],
            "agents": [a.to_dict() for a in self.agents],
        }


@dataclass(frozen=True)
class MemoryLibrary:
    root: Path
    system: list[MemoryNode] = field(default_factory=list)
    projects: list[ProjectView] = field(default_factory=list)

    def injected(self, *, project_dir: str | None = None, agent: str | None = None) -> list[MemoryNode]:
        """Return nodes for injection: system + one project (+ agent) scoped."""
        nodes: list[MemoryNode] = list(self.system)
        if project_dir:
            view = next((p for p in self.projects if p.name == project_dir), None)
            if view:
                nodes.extend(view.base)
                nodes.extend(view.project)
                if agent:
                    aview = next((a for a in view.agents if a.name == agent), None)
                    if aview:
                        for core in (aview.soul, aview.agent, aview.memory):
                            if core:
                                nodes.append(core)
                        nodes.extend(aview.sessions)
        return nodes


class MemoryScanner:
    """Scan the memory library directory tree."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def scan(self, *, include_missing: bool = False) -> MemoryLibrary:
        """Discover system files, project dirs and agent dirs.

        ``include_missing`` synthesizes empty nodes for expected skeleton files
        so the frontend can show a well-formed tree even before first use.
        """
        system: list[MemoryNode] = []
        for name in SYSTEM_FILES:
            node = self._read_node("system", name)
            if node is None and include_missing:
                node = self._empty_node("system", name)
            if node is not None:
                system.append(node)

        projects: list[ProjectView] = []
        if self.root.is_dir():
            for entry in sorted(self.root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                view = self._scan_project(entry, include_missing)
                if view is not None:
                    projects.append(view)

        return MemoryLibrary(root=self.root, system=system, projects=projects)

    # -- helpers ------------------------------------------------------------

    def _scan_project(self, project_dir: Path, include_missing: bool) -> ProjectView | None:
        base_dir = project_dir / BASE_DIR
        base: list[MemoryNode] = []
        project: list[MemoryNode] = []
        if base_dir.is_dir():
            for entry in sorted(base_dir.iterdir()):
                if not entry.is_file() or entry.name.endswith(".lock"):
                    continue
                rel = _rel(self.root, entry)
                base.append(self._read_node("base_file", rel) or self._empty_node("base_file", rel))
            proj_sub = base_dir / PROJECT_SUBDIR
            if proj_sub.is_dir():
                for entry in sorted(proj_sub.iterdir()):
                    if not entry.is_file() or entry.name.endswith(".lock"):
                        continue
                    rel = _rel(self.root, entry)
                    project.append(self._read_node("project_file", rel) or self._empty_node("project_file", rel))
        elif include_missing:
            base_dir.mkdir(parents=True, exist_ok=True)
            (base_dir / PROJECT_SUBDIR).mkdir(parents=True, exist_ok=True)

        agents: list[AgentView] = []
        for entry in sorted(project_dir.iterdir()):
            if entry.name == BASE_DIR or not entry.is_dir() or entry.name.startswith("."):
                continue
            agents.append(self._scan_agent(project_dir, entry, include_missing))

        return ProjectView(
            name=project_dir.name,
            rel=_rel(self.root, project_dir),
            base=base,
            project=project,
            agents=agents,
        )

    def _scan_agent(self, project_dir: Path, agent_dir: Path, include_missing: bool) -> AgentView:
        core: dict[str, MemoryNode | None] = {k: None for k in AGENT_CORE_FILES}
        sessions: list[MemoryNode] = []
        for name in AGENT_CORE_FILES:
            rel = _rel(self.root, agent_dir / name)
            node = self._read_node("agent_file", rel)
            if node is None and include_missing:
                node = self._empty_node("agent_file", rel)
            core[name] = node
        sessions_dir = agent_dir / SESSIONS_DIR
        if sessions_dir.is_dir():
            for entry in sorted(sessions_dir.iterdir()):
                if not entry.is_file() or entry.name.endswith(".lock"):
                    continue
                rel = _rel(self.root, entry)
                node = self._read_node("session_file", rel) or self._empty_node("session_file", rel)
                sessions.append(node)
        return AgentView(
            name=agent_dir.name,
            rel=_rel(self.root, agent_dir),
            soul=core["SOUL.md"],
            agent=core["AGENT.md"],
            memory=core["MEMORY.md"],
            sessions=sessions,
        )

    def _read_node(self, kind: str, rel: str) -> MemoryNode | None:
        path = self.root / rel
        if not path.is_file():
            return None
        return MemoryNode.from_file(kind, rel, load_file(path))

    def _empty_node(self, kind: str, rel: str) -> MemoryNode:
        path = self.root / rel
        return MemoryNode(kind=kind, name=path.name, rel=rel, path=path, content="", mtime=0.0)


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name
