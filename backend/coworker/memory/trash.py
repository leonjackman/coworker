"""OS-trash integration for memory deletions.

Deleted memory files are moved to the OS Trash (macOS ``~/.Trash``) so the
user can recover them manually; the app itself offers no undo. When no OS
trash is available (or the move fails) we fall back to a hidden ``.trash/``
directory, which the scanner ignores (dot-prefixed path parts).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def system_trash_dir() -> Path | None:
    """Return the OS trash directory on supported platforms, else None."""
    if sys.platform == "darwin":
        home = Path.home()
        trash = home / ".Trash"
        if trash.is_dir():
            return trash
    return None


def _unique_dest(folder: Path, name: str) -> Path:
    """Return a non-colliding path under ``folder`` (case-insensitive aware)."""
    stem, suffix = os.path.splitext(name)
    existing = {p.name.lower() for p in folder.iterdir()} if folder.is_dir() else set()
    candidate = folder / name
    counter = 2
    while candidate.exists() or candidate.name.lower() in existing:
        candidate = folder / f"{stem} {counter}{suffix}"
        counter += 1
    return candidate


def send_to_trash(path: Path, dest_dir: Path) -> str:
    """Move ``path`` into ``dest_dir``, avoiding name collisions.

    Returns the destination path string. Raises ``OSError`` when the move
    fails. ``dest_dir`` is chosen by the caller — ``system_trash_dir()`` on
    macOS, or a hidden in-library ``.trash/`` fallback.
    """
    path = Path(path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir, path.name)
    shutil.move(str(path), str(dest))
    return str(dest)
