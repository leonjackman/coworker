"""Memory library discovery: scan the directory tree in injection order.

Discovers system files, project trees (BASE + BASE/PROJECT), agent trees
(core files + SESSIONS) and non-agent user folders. Nodes are returned in
injection precedence order:

    system → BASE/* (user) → BASE/PROJECT/* (system) → SOUL → AGENT → MEMORY
    → SESSIONS/*

Non-agent folders under a project root are listed on ``ProjectView.folders``
(kind ``folder_file``) for browsing/editing but are never injected.

Each ``MemoryNode`` carries a ``rel`` path (relative to the memory root) used
by the store and the API, so callers never need absolute paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .layout import (
    AGENT_BASE_DIR,
    AGENT_CORE_FILES,
    BASE_DIR,
    PROJECT_SUBDIR,
    SESSIONS_DIR,
    SYSTEM_FILES,
)
from .memory_file import MemoryFile, load_file
from .registry import normalize_agent_layout

logger = logging.getLogger(__name__)

_KINDS = ("system", "base_file", "project_file", "agent_file", "session_file", "folder_file")

_MEMORY_SUFFIXES = (".md", ".markdown")


def _is_memory_file(path: Path) -> bool:
    """Return True for regular files that match markdown extensions and aren't lock files."""
    return (
        path.is_file()
        and not path.name.endswith(".lock")
        and any(path.name.lower().endswith(suffix) for suffix in _MEMORY_SUFFIXES)
    )


def _looks_like_agent(agent_dir: Path) -> bool:
    """True if ``agent_dir`` carries an agent marker (core identity files).

    Recognizes both the current layout (``agent/BASE/SOUL|AGENT|MEMORY.md``)
    and the legacy pre-normalize layout (core files at the agent root). A plain
    user folder is not an agent and is surfaced as a regular folder instead.
    """
    base_dir = agent_dir / AGENT_BASE_DIR
    for name in AGENT_CORE_FILES:
        if (base_dir / name).is_file():
            return True
    for name in AGENT_CORE_FILES:
        if (agent_dir / name).is_file():
            return True
    return False


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
    id: str              # agent id (= directory name, stable across renames)
    name: str            # display name (from org roster, falls back to id)
    rel: str
    soul: MemoryNode | None = None
    agent: MemoryNode | None = None
    memory: MemoryNode | None = None
    base: list[MemoryNode] = field(default_factory=list)  # extra user files in BASE/
    sessions: list[MemoryNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "rel": self.rel,
            "soul": self.soul.to_dict() if self.soul else None,
            "agent": self.agent.to_dict() if self.agent else None,
            "memory": self.memory.to_dict() if self.memory else None,
            "base": [b.to_dict() for b in self.base],
            "sessions": [s.to_dict() for s in self.sessions],
        }


@dataclass(frozen=True)
class FolderView:
    name: str            # directory name
    rel: str             # path relative to the memory root
    files: list[MemoryNode] = field(default_factory=list)  # .md files under it (recursive)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rel": self.rel,
            "files": [f.to_dict() for f in self.files],
        }


TEAMS_DIR = "teams"
TEAM_FILES = ("GOALS.md", "CONTEXT.md", "MEMORY.md")


@dataclass(frozen=True)
class TeamView:
    id: str
    name: str
    rel: str             # path relative to the memory root
    goals: MemoryNode | None = None
    context: MemoryNode | None = None
    memory: MemoryNode | None = None
    files: list[MemoryNode] = field(default_factory=list)  # other .md files in the team dir

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "rel": self.rel,
            "goals": self.goals.to_dict() if self.goals else None,
            "context": self.context.to_dict() if self.context else None,
            "memory": self.memory.to_dict() if self.memory else None,
            "files": [f.to_dict() for f in self.files],
        }


@dataclass(frozen=True)
class ProjectView:
    name: str            # directory name = memory_dir
    rel: str
    project_name: str = ""   # real project display name (resolved, "" = fall back to name)
    base: list[MemoryNode] = field(default_factory=list)
    project: list[MemoryNode] = field(default_factory=list)
    agents: list[AgentView] = field(default_factory=list)
    folders: list[FolderView] = field(default_factory=list)  # non-agent user folders
    teams: list[TeamView] = field(default_factory=list)      # department (team) containers

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rel": self.rel,
            "project_name": self.project_name or self.name,
            "base": [b.to_dict() for b in self.base],
            "project": [p.to_dict() for p in self.project],
            "agents": [a.to_dict() for a in self.agents],
            "folders": [f.to_dict() for f in self.folders],
            "teams": [t.to_dict() for t in self.teams],
        }


@dataclass(frozen=True)
class MemoryLibrary:
    root: Path
    system: list[MemoryNode] = field(default_factory=list)
    projects: list[ProjectView] = field(default_factory=list)

    def injected(self, *, project_dir: str | None = None, agent: str | None = None, team_ids: list[str] | None = None) -> list[MemoryNode]:
        """Return nodes for injection: system + one project (+ agent + team) scoped.

        ``team_ids`` are the team containers (self + ancestors) whose shared
        memory (GOALS/CONTEXT/MEMORY) is injected alongside the agent's own core
        files. When omitted the legacy behavior is preserved (no team memory).
        """
        nodes: list[MemoryNode] = list(self.system)
        if project_dir:
            view = next((p for p in self.projects if p.name == project_dir), None)
            if view:
                nodes.extend(view.base)
                nodes.extend(view.project)
                if agent:
                    aview = next((a for a in view.agents if a.id == agent), None)
                    if aview:
                        for core in (aview.soul, aview.agent, aview.memory):
                            if core:
                                nodes.append(core)
                        nodes.extend(aview.base)
                        nodes.extend(aview.sessions)
                for tid in team_ids or []:
                    tview = next((t for t in view.teams if t.id == tid), None)
                    if tview:
                        for node in (tview.goals, tview.context, tview.memory):
                            if node:
                                nodes.append(node)
                        nodes.extend(tview.files)
        return nodes


class MemoryScanner:
    """Scan the memory library directory tree."""

    def __init__(self, root: Path, project_name_resolver: Callable[[str], str] | None = None, agent_name_resolver: Callable[[str, str], str] | None = None):
        self.root = Path(root).resolve()
        self.project_name_resolver = project_name_resolver
        self.agent_name_resolver = agent_name_resolver

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
        if self.root.is_dir():
            for entry in sorted(self.root.iterdir()):
                if (
                    not _is_memory_file(entry)
                    or entry.name in SYSTEM_FILES
                ):
                    continue
                rel = _rel(self.root, entry)
                system.append(self._read_node("system", rel) or self._empty_node("system", rel))

        projects: list[ProjectView] = []
        if self.root.is_dir():
            for entry in sorted(self.root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                view = self._scan_project(entry, include_missing)
                if view is not None:
                    projects.append(view)

        return MemoryLibrary(root=self.root, system=system, projects=projects)

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Case-insensitive substring search across every discoverable file.

        Returns up to ``limit`` results, each ``{rel, name, kind, location,
        snippet, match_count}``, sorted by descending match count. Files with
        no content (e.g. synthesized placeholders) are skipped.
        """
        needle = (query or "").strip().lower()
        if not needle:
            return []
        library = self.scan()
        hits: list[dict] = []
        seen: set[str] = set()

        def _consider(node: MemoryNode, location: str) -> None:
            if node is None or node.rel in seen:
                return
            if not node.content:
                return
            count = node.content.lower().count(needle)
            if count == 0:
                return
            seen.add(node.rel)
            snippet = _snippet(node.content, needle)
            hits.append(
                {
                    "rel": node.rel,
                    "name": node.name,
                    "kind": node.kind,
                    "location": location,
                    "snippet": snippet,
                    "match_count": count,
                }
            )

        for node in library.system:
            _consider(node, "system")
        for view in library.projects:
            label = view.project_name or view.name
            for node in view.base + view.project:
                _consider(node, label)
            for aview in view.agents:
                for node in [aview.soul, aview.agent, aview.memory] + aview.base + aview.sessions:
                    _consider(node, f"{label} / {aview.name}")
            for folder in view.folders:
                for node in folder.files:
                    _consider(node, f"{label} / {folder.name}")
            for tview in view.teams:
                for node in [tview.goals, tview.context, tview.memory] + tview.files:
                    _consider(node, f"{label} / team {tview.name}")

        hits.sort(key=lambda h: (-h["match_count"], h["rel"]))
        return hits[:limit]

    # -- helpers ------------------------------------------------------------

    def _scan_project(self, project_dir: Path, include_missing: bool) -> ProjectView | None:
        base_dir = project_dir / BASE_DIR
        base: list[MemoryNode] = []
        project: list[MemoryNode] = []
        if base_dir.is_dir():
            for entry in sorted(base_dir.iterdir()):
                if not _is_memory_file(entry):
                    continue
                rel = _rel(self.root, entry)
                base.append(self._read_node("base_file", rel) or self._empty_node("base_file", rel))
            proj_sub = base_dir / PROJECT_SUBDIR
            if proj_sub.is_dir():
                for entry in sorted(proj_sub.iterdir()):
                    if not _is_memory_file(entry):
                        continue
                    rel = _rel(self.root, entry)
                    project.append(self._read_node("project_file", rel) or self._empty_node("project_file", rel))
        elif include_missing:
            base_dir.mkdir(parents=True, exist_ok=True)
            (base_dir / PROJECT_SUBDIR).mkdir(parents=True, exist_ok=True)

        agents: list[AgentView] = []
        folders: list[FolderView] = []
        teams: list[TeamView] = []
        for entry in sorted(project_dir.iterdir()):
            if entry.name == BASE_DIR or not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name == TEAMS_DIR:
                teams = self._scan_teams(entry)
                continue
            if _looks_like_agent(entry):
                agents.append(self._scan_agent(project_dir, entry, include_missing))
            else:
                folders.append(
                    FolderView(
                        name=entry.name,
                        rel=_rel(self.root, entry),
                        files=self._scan_folder(entry),
                    )
                )

        return ProjectView(
            name=project_dir.name,
            rel=_rel(self.root, project_dir),
            project_name=self.project_name_resolver(project_dir.name) if self.project_name_resolver else "",
            base=base,
            project=project,
            agents=agents,
            folders=folders,
            teams=teams,
        )

    def _scan_teams(self, teams_dir: Path) -> list[TeamView]:
        """Scan ``teams/<team_id>/`` containers into TeamView objects."""
        views: list[TeamView] = []
        if not teams_dir.is_dir():
            return views
        for entry in sorted(teams_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            goals = context = memory = None
            files: list[MemoryNode] = []
            for f in sorted(entry.rglob("*")):
                if not _is_memory_file(f):
                    continue
                rel = _rel(self.root, f)
                node = self._read_node("folder_file", rel) or self._empty_node("folder_file", rel)
                if f.name == "GOALS.md":
                    goals = node
                elif f.name == "CONTEXT.md":
                    context = node
                elif f.name == "MEMORY.md":
                    memory = node
                else:
                    files.append(node)
            views.append(
                TeamView(
                    id=entry.name,
                    name=entry.name,
                    rel=_rel(self.root, entry),
                    goals=goals,
                    context=context,
                    memory=memory,
                    files=files,
                )
            )
        return views

    def _scan_folder(self, folder_dir: Path) -> list[MemoryNode]:
        """Recursively collect Markdown files under a non-agent user folder."""
        nodes: list[MemoryNode] = []
        for path in sorted(folder_dir.rglob("*")):
            if not _is_memory_file(path):
                continue
            rel = _rel(self.root, path)
            nodes.append(self._read_node("folder_file", rel) or self._empty_node("folder_file", rel))
        return nodes

    def _scan_agent(self, project_dir: Path, agent_dir: Path, include_missing: bool) -> AgentView:
        normalize_agent_layout(agent_dir)
        core: dict[str, MemoryNode | None] = {k: None for k in AGENT_CORE_FILES}
        base: list[MemoryNode] = []
        sessions: list[MemoryNode] = []
        core_dir = agent_dir / AGENT_BASE_DIR
        for name in AGENT_CORE_FILES:
            rel = _rel(self.root, core_dir / name)
            node = self._read_node("agent_file", rel)
            if node is None and include_missing:
                node = self._empty_node("agent_file", rel)
            core[name] = node
        if core_dir.is_dir():
            for entry in sorted(core_dir.iterdir()):
                if not _is_memory_file(entry) or entry.name in AGENT_CORE_FILES:
                    continue
                rel = _rel(self.root, entry)
                base.append(self._read_node("agent_file", rel) or self._empty_node("agent_file", rel))
        sessions_dir = agent_dir / SESSIONS_DIR
        if sessions_dir.is_dir():
            for entry in sorted(sessions_dir.iterdir()):
                if not _is_memory_file(entry):
                    continue
                rel = _rel(self.root, entry)
                node = self._read_node("session_file", rel) or self._empty_node("session_file", rel)
                sessions.append(node)
        agent_id = agent_dir.name
        display_name = agent_id
        if self.agent_name_resolver is not None:
            try:
                display_name = self.agent_name_resolver(project_dir.name, agent_id) or agent_id
            except Exception:  # noqa: BLE001 - a bad resolver must not break the scan
                display_name = agent_id
        return AgentView(
            id=agent_id,
            name=display_name,
            rel=_rel(self.root, agent_dir),
            soul=core["SOUL.md"],
            agent=core["AGENT.md"],
            memory=core["MEMORY.md"],
            base=base,
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


def _snippet(content: str, needle: str, radius: int = 40) -> str:
    """Return a short window of text around the first case-insensitive match."""
    idx = content.lower().find(needle)
    if idx < 0:
        return content[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(content), idx + len(needle) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}"
