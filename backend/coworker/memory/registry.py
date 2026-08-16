"""Memory library scaffolding: ensure root / project / agent skeletons exist.

The memory library is directory-based; the registry materializes the empty
skeleton (files with headers) the first time a project or agent is touched, so
the frontend and the injection path always see a well-formed tree.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .layout import (
    AGENT_BASE_DIR,
    AGENT_CORE_FILES,
    AGENT_SKELETON,
    BASE_DIR,
    BASE_SKELETON,
    BASE_TEMPLATE_FILES,
    LEGACY_PROJECT_SKELETON,
    MEMORY_ROOT_NAME,
    PROJECT_SKELETON,
    PROJECT_SUBDIR,
    SESSIONS_DIR,
    SYSTEM_FILES,
)

# Deprecated system-default files from earlier layouts; converge them so all
# projects settle on the current ALL-CAPS skeleton.
_DEPRECATED_BASE_FILES = ("project.md", "game_rule.md", "BASE.md", "clean_code_rule.md")

logger = logging.getLogger(__name__)


class MemoryRegistry:
    """Creates the memory library skeleton on demand."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / MEMORY_ROOT_NAME
        self._lock = threading.Lock()

    # -- root ---------------------------------------------------------------

    def ensure_root(self) -> Path:
        """Create ``{data_dir}/memory`` plus the system files if missing."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for name in SYSTEM_FILES:
                path = self.root / name
                if not path.exists():
                    _write_skeleton(path, f"# {name}\n")
        return self.root

    # -- project ------------------------------------------------------------

    def project_dir(self, memory_dir: str) -> Path:
        return self.root / memory_dir

    def ensure_project(self, memory_dir: str) -> Path:
        """Create the project dir with ``BASE/`` (template only) + ``BASE/PROJECT/``.

        Also converges legacy lowercase system files from earlier layouts:
        empty skeleton files are removed, and user-edited files are either kept
        (BASE) or renamed to the current ALL-CAPS name (PROJECT).
        """
        with self._lock:
            project_dir = self.project_dir(memory_dir)
            project_dir.mkdir(parents=True, exist_ok=True)
            base_dir = project_dir / BASE_DIR
            base_dir.mkdir(parents=True, exist_ok=True)
            _prune_legacy_base_files(base_dir)
            for name in BASE_TEMPLATE_FILES:
                path = base_dir / name
                if not path.exists():
                    content = BASE_SKELETON.get(name, f"# {name}\n")
                    _write_skeleton(path, content)
            project_subdir = base_dir / PROJECT_SUBDIR
            project_subdir.mkdir(parents=True, exist_ok=True)
            _prune_legacy_project_files(project_subdir)
            for name, content in PROJECT_SKELETON.items():
                path = project_subdir / name
                if not path.exists():
                    _write_skeleton(path, content)
        return project_dir

    # -- agent --------------------------------------------------------------

    def agent_dir(self, project_dir: Path, agent: str) -> Path:
        return project_dir / agent

    def ensure_agent(self, project_dir: Path, agent: str) -> Path:
        """Create the agent dir with ``BASE/`` core files + ``SESSIONS/``."""
        with self._lock:
            agent_dir = self.agent_dir(project_dir, agent)
            agent_dir.mkdir(parents=True, exist_ok=True)
            normalize_agent_layout(agent_dir)
            base_dir = agent_dir / AGENT_BASE_DIR
            base_dir.mkdir(parents=True, exist_ok=True)
            for name in AGENT_CORE_FILES:
                path = base_dir / name
                if not path.exists():
                    content = AGENT_SKELETON.get(name, f"# {name}\n")
                    _write_skeleton(path, content)
            sessions_dir = agent_dir / SESSIONS_DIR
            sessions_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


def normalize_agent_layout(agent_dir: Path) -> None:
    """Idempotently move legacy agent core files into ``agent/BASE/``.

    Older layouts kept ``SOUL.md / AGENT.md / MEMORY.md`` at the agent root;
    they now live under ``agent/BASE/``. Any ``.lock`` sibling (a stale lock
    from a write on the old path) is moved along with its file so the store's
    lock-cleaning invariant stays intact.
    """
    base_dir = agent_dir / AGENT_BASE_DIR
    moved = False
    for name in AGENT_CORE_FILES:
        src = agent_dir / name
        if not src.is_file():
            continue
        base_dir.mkdir(parents=True, exist_ok=True)
        src.replace(base_dir / name)
        moved = True
        lock_src = agent_dir / f"{name}.lock"
        if lock_src.is_file():
            try:
                lock_src.replace(base_dir / f"{name}.lock")
            except OSError:  # pragma: no cover - defensive
                lock_src.unlink(missing_ok=True)
    if moved:
        logger.info("normalized legacy agent layout: %s", agent_dir)


def _prune_legacy_base_files(base_dir: Path) -> None:
    """Remove deprecated BASE files that still hold only their skeleton header.

    A deprecated file with real user content is kept — it becomes a regular
    user-maintained file (BASE is the user area, so any case is allowed).
    """
    for name in _DEPRECATED_BASE_FILES:
        path = base_dir / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            continue
        if content.strip() == f"# {name}":
            path.unlink(missing_ok=True)


def _prune_legacy_project_files(project_subdir: Path) -> None:
    """Drop legacy lowercase PROJECT files that still hold skeleton content.

    The ALL-CAPS file is then recreated by ``ensure_project``. Files with
    user-edited content are kept untouched (a case-only rename is impossible on
    case-insensitive filesystems, where ``goals.md`` and ``GOALS.md`` alias).
    """
    for name, content in LEGACY_PROJECT_SKELETON.items():
        path = project_subdir / name
        if not path.is_file():
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            continue
        if existing.strip() == content.strip():
            path.unlink(missing_ok=True)


def _write_skeleton(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to create memory skeleton %s: %s", path, exc)
