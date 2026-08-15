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
    AGENT_CORE_FILES,
    AGENT_SKELETON,
    BASE_DIR,
    DEFAULT_BASE_FILES,
    MEMORY_ROOT_NAME,
    PROJECT_SKELETON,
    PROJECT_SUBDIR,
    SESSIONS_DIR,
    SYSTEM_FILES,
)

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
        """Create the project dir with ``BASE/`` and ``BASE/PROJECT/``."""
        with self._lock:
            project_dir = self.project_dir(memory_dir)
            project_dir.mkdir(parents=True, exist_ok=True)
            base_dir = project_dir / BASE_DIR
            base_dir.mkdir(parents=True, exist_ok=True)
            for name in DEFAULT_BASE_FILES:
                path = base_dir / name
                if not path.exists():
                    _write_skeleton(path, f"# {name}\n")
            project_subdir = base_dir / PROJECT_SUBDIR
            project_subdir.mkdir(parents=True, exist_ok=True)
            for name, content in PROJECT_SKELETON.items():
                path = project_subdir / name
                if not path.exists():
                    _write_skeleton(path, content)
        return project_dir

    # -- agent --------------------------------------------------------------

    def agent_dir(self, project_dir: Path, agent: str) -> Path:
        return project_dir / agent

    def ensure_agent(self, project_dir: Path, agent: str) -> Path:
        """Create the agent dir with core files + SESSIONS/."""
        with self._lock:
            agent_dir = self.agent_dir(project_dir, agent)
            agent_dir.mkdir(parents=True, exist_ok=True)
            for name in AGENT_CORE_FILES:
                path = agent_dir / name
                if not path.exists():
                    content = AGENT_SKELETON.get(name, f"# {name}\n")
                    _write_skeleton(path, content)
            sessions_dir = agent_dir / SESSIONS_DIR
            sessions_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


def _write_skeleton(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to create memory skeleton %s: %s", path, exc)
