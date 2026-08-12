"""Multi-root memory discovery.

Mirrors ``coworker.skills.skill_discovery``: project-level memory (the
workspace) precedes user-level memory, and each root is only scanned once.
Unlike skills, memory is *not* first-match-only — user-level and project-level
are both injected so global preferences survive across projects (opencode
loads global + project instructions together). At most one file per root.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .memory_file import MEMORY_FILE_NAME, MemoryFile, load_file

logger = logging.getLogger(__name__)

COWORKER_DIR = ".coworker"


@dataclass(frozen=True)
class MemoryScan:
    """Files found across all roots, project-first."""

    project: MemoryFile | None = None
    user: MemoryFile | None = None

    def files(self) -> list[MemoryFile]:
        return [f for f in (self.project, self.user) if f is not None]


class MemoryScanner:
    """Resolve project + user memory file paths and read them."""

    def __init__(self, workspace_root: Path | None = None, user_home: Path | None = None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.user_home = Path(user_home).resolve() if user_home else Path.home()

    def project_path(self) -> Path | None:
        """The project-level memory file path (or ``None`` without a workspace)."""
        if self.workspace_root is None:
            return None
        return self.workspace_root / COWORKER_DIR / MEMORY_FILE_NAME

    def user_path(self) -> Path:
        """The user-level memory file path."""
        return self.user_home / COWORKER_DIR / MEMORY_FILE_NAME

    def _path_missing(self, path: Path | None) -> bool:
        return path is None or not path.is_file()

    def scan(self, *, include_missing: bool = False) -> MemoryScan:
        """Load both memory files.

        By default files that do not exist are skipped entirely (``None``);
        with ``include_missing=True`` a synthetic empty ``MemoryFile`` is
        returned for every root, so the UI can show "no memory yet" for each
        scope. Unreadable files degrade to an empty project/user view.
        """
        project_path = self.project_path()
        user_path = self.user_path()

        project: MemoryFile | None = None
        if project_path is not None and (include_missing or not self._path_missing(project_path)):
            try:
                project = load_file(project_path, "project")
            except Exception as exc:  # noqa: BLE001 - one broken file must not break discovery
                logger.warning("Failed to read project memory %s: %s", project_path, exc)
                project = MemoryFile(scope="project", path=project_path, mtime=0.0, entries=[])

        user: MemoryFile | None = None
        if include_missing or not self._path_missing(user_path):
            try:
                user = load_file(user_path, "user")
            except Exception as exc:  # noqa: BLE001 - defensive
                logger.warning("Failed to read user memory %s: %s", user_path, exc)
                user = MemoryFile(scope="user", path=user_path, mtime=0.0, entries=[])

        return MemoryScan(project=project, user=user)