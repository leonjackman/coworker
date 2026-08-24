"""OS-trash integration for memory deletions.

Deleted memory files are moved to the OS trash (macOS ``~/.Trash``, Linux
``$XDG_DATA_HOME/Trash``, Windows Recycle Bin) so the user can recover them
manually; the app itself offers no undo. ``send2trash`` is preferred where it
works (files on every platform, plus directories on macOS/Windows). When no OS
trash is available (or the move fails) callers fall back to a hidden
``.trash/`` directory, which the scanner ignores (dot-prefixed path parts).
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from coworker.logger import get_logger
logger = get_logger(__name__)


def system_trash_dir() -> Path | None:
    """Return a plain-directory OS trash location, else ``None``.

    macOS exposes ``~/.Trash`` as a plain directory; Linux uses the freedesktop
    XDG Trash ``files/`` directory. Windows has no plain-directory Recycle Bin,
    so it returns ``None`` — use :func:`send_to_os_trash` there (send2trash).
    """
    if sys.platform == "darwin":
        trash = Path.home() / ".Trash"
        return trash if trash.is_dir() else None
    if sys.platform.startswith("linux"):
        return _xdg_trash_files_dir()
    return None


def _xdg_trash_root() -> Path | None:
    env = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(env) if env else (Path.home() / ".local" / "share")
    root = base / "Trash"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return root


def _xdg_trash_files_dir() -> Path | None:
    root = _xdg_trash_root()
    if root is None:
        return None
    files = root / "files"
    try:
        files.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return files


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


def _move_into(path: Path, dest_dir: Path) -> str:
    """Move ``path`` into ``dest_dir``, avoiding name collisions.

    Returns the destination path string. Raises ``OSError`` when the move
    fails. ``dest_dir`` is chosen by the caller — the OS trash area, or a
    hidden in-library ``.trash/`` fallback.
    """
    path = Path(path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir, path.name)
    shutil.move(str(path), str(dest))
    return str(dest)


def _xdg_trash_move(path: Path) -> str | None:
    """Move ``path`` into the freedesktop XDG Trash with a ``.trashinfo`` sidecar."""
    root = _xdg_trash_root()
    if root is None:
        return None
    files_dir = root / "files"
    info_dir = root / "info"
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_dest(files_dir, path.name)
        shutil.move(str(path), str(dest))
        try:
            import urllib.parse

            escaped = urllib.parse.quote(str(path.resolve()), safe="/:")
            info_dir.joinpath(f"{dest.name}.trashinfo").write_text(
                "[Trash Info]\nPath={}\nDeletionDate={}\n".format(
                    escaped,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                ),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001 - a missing sidecar only weakens discoverability
            pass
        return str(dest)
    except OSError:
        return None


def send_to_os_trash(path: Path) -> str | None:
    """Send ``path`` to the real OS trash.

    Tries ``send2trash`` first — the only option on Windows (the Recycle Bin
    is not a plain directory) and the most faithful on macOS/Linux. Falls back
    to a plain-directory OS trash (macOS ``~/.Trash`` / Linux XDG spec move).
    Returns a short description of where the item went, or ``None`` when no OS
    trash worked (callers then use the in-library ``.trash/`` fallback).
    """
    path = Path(path)
    try:
        import send2trash
    except Exception:  # pragma: no cover - dependency missing
        send2trash = None
    if send2trash is not None:
        try:
            send2trash.send2trash(str(path))
            return f"os-trash:{path.name}"
        except Exception as exc:  # noqa: BLE001 - directories fail on Linux trash_spec
            logger.warning("send2trash failed for %s: %s", path, exc)
    if sys.platform == "darwin":
        try:
            return _move_into(path, Path.home() / ".Trash")
        except OSError:
            return None
    if sys.platform.startswith("linux"):
        return _xdg_trash_move(path)
    return None


def send_to_trash(path: Path, dest_dir: Path) -> str:
    """Move ``path`` into an explicit ``dest_dir`` (collision-safe).

    Used for tests and for the in-library ``.trash/`` fallback. For the real
    OS trash, use :func:`send_to_os_trash` instead.
    """
    return _move_into(Path(path), Path(dest_dir))
