"""MemoryStore: thread-safe, drift-protected read/write of memory files.

The memory library is directory-based; ``MemoryStore`` operates on file paths
within the memory root (validated by ``layout.resolve_rel_path``). Two write
shapes exist:

- **Whole-file** writes (``write_file``) replace an entire Markdown file.
- **Block** writes (``add_block`` / ``replace_block`` / ``remove_block``) edit
  the individual paragraphs of ``agent/MEMORY.md`` / ``SESSIONS/*.md`` while
  keeping the file human-readable Markdown.

The concurrency hardening from the original ``§``-delimited store is preserved:

1. ``fcntl``/``msvcrt`` file lock around every read-modify-write.
2. Round-trip verification: after writing we re-read and confirm the intended
   content is present; mismatch means a concurrent external edit won — we merge
   rather than overwrite.
3. Duplicate rejection for blocks; substring-based replace/remove.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .memory_file import MemoryFile, load_file, render_blocks, split_blocks

logger = logging.getLogger(__name__)


class MemoryError(ValueError):
    """Raised for semantic memory write failures (duplicate/missing target)."""


class MemoryStore:
    """Owns memory files under a root and guarantees serialized, verified writes."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lock = threading.RLock()

    # -- path helpers ------------------------------------------------------

    def _resolve(self, rel: str) -> Path:
        from .layout import resolve_rel_path

        return resolve_rel_path(self.root, rel)

    # -- read API ----------------------------------------------------------

    def read_file(self, rel: str) -> MemoryFile:
        path = self._resolve(rel)
        return load_file(path)

    def read_raw(self, rel: str) -> str:
        path = self._resolve(rel)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def file_exists(self, rel: str) -> bool:
        return self._resolve(rel).is_file()

    # -- whole-file write --------------------------------------------------

    def write_file(self, rel: str, content: str) -> MemoryFile:
        """Replace a whole memory file from raw Markdown."""
        if not content.strip():
            content = ""
        path = self._resolve(rel)
        with self._lock:
            return self._update_locked(path, lambda _raw: content)

    def remove_file(self, rel: str) -> bool:
        """Delete a file (or empty directory) under the memory root."""
        path = self._resolve(rel)
        with self._lock:
            if path.is_dir():
                try:
                    path.rmdir()
                    return True
                except OSError:
                    return False
            if not path.exists():
                return False
            try:
                path.unlink()
                _cleanup_lock(path)
                return True
            except OSError:
                return False

    # -- block API (agent MEMORY.md / SESSIONS) ----------------------------

    def list_blocks(self, rel: str) -> list[str]:
        return list(load_file(self._resolve(rel)).blocks)

    def add_block(self, rel: str, text: str) -> list[str]:
        text = text.strip()
        if not text:
            raise MemoryError("cannot add empty memory")
        path = self._resolve(rel)

        def _add(_raw: str) -> str:
            blocks = split_blocks(_raw)
            if any(b == text for b in blocks):
                raise MemoryError("duplicate memory entry (already exists)")
            return render_blocks([*blocks, text])

        with self._lock:
            self._update_locked(path, _add)
        return self.list_blocks(rel)

    def replace_block(self, rel: str, target: str, text: str) -> list[str]:
        text = text.strip()
        target = target.strip()
        if not text:
            raise MemoryError("cannot replace with empty memory")
        if not target:
            raise MemoryError("replace target is empty")
        path = self._resolve(rel)

        def _replace(_raw: str) -> str:
            blocks = split_blocks(_raw)
            replaced = False
            updated: list[str] = []
            for block in blocks:
                if not replaced and target in block:
                    updated.append(text)
                    replaced = True
                else:
                    updated.append(block)
            if not replaced:
                raise MemoryError("replace target not found")
            return render_blocks(updated)

        with self._lock:
            self._update_locked(path, _replace)
        return self.list_blocks(rel)

    def remove_block(self, rel: str, target: str) -> list[str]:
        target = target.strip()
        if not target:
            raise MemoryError("remove target is empty")
        path = self._resolve(rel)

        def _remove(_raw: str) -> tuple[str, set[str]]:
            blocks = split_blocks(_raw)
            removed = {b for b in blocks if target in b}
            if not removed:
                raise MemoryError("remove target not found")
            kept = [b for b in blocks if target not in b]
            return render_blocks(kept), removed

        with self._lock:
            self._update_locked(path, _remove)
        return self.list_blocks(rel)

    def clear_file(self, rel: str) -> list[str]:
        path = self._resolve(rel)
        with self._lock:
            self._update_locked(path, lambda _raw: "")
        return []

    # -- core write pipeline ------------------------------------------------

    def _update_locked(self, path: Path, updater) -> MemoryFile:
        """Atomically read-modify-write a memory file under a shared file lock.

        The updater receives the current raw text and returns either a new raw
        text or ``(text, removed_set)``; ``removed_set`` is the set of blocks the
        operation deliberately deleted so a concurrent external save cannot
        resurrect them during the conflict merge.
        """
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = _open_locked(path)
        try:
            with _file_lock(lock_fd):
                current_raw = _read_text(path)
                result = updater(current_raw)
                if isinstance(result, tuple):
                    new_raw, removed_set = result
                    removed_set = set(removed_set)
                else:
                    new_raw, removed_set = result, set()
                self._write_payload_atomic(path, new_raw)
                # Detect a non-cooperating writer (no lock held) that interleaved
                # between our read and write, and merge rather than overwrite.
                try:
                    disk_raw = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    disk_raw = ""
                if disk_raw != new_raw:
                    merged = _merge_text(disk_raw, new_raw, removed_set)
                    if merged != new_raw:
                        self._write_payload_atomic(path, merged)
        finally:
            _release_lock(lock_fd)
        return load_file(path)

    @staticmethod
    def _write_payload_atomic(path: Path, payload: str) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        path.touch(exist_ok=True)


def _read_text(path: Path) -> str:
    """Read raw text with a bounded stability retry (concurrent-write safe)."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    for _ in range(3):
        try:
            raw = path.read_bytes()
            size_now = path.stat().st_size
        except OSError:
            return ""
        if len(raw) == size_now:
            return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _merge_text(disk_raw: str, ours: str, removed_set: set[str]) -> str:
    """Merge a disk state with our just-written content.

    Disk blocks keep their order except those our operation explicitly removed;
    our blocks that are not already present are appended.
    """
    disk_blocks = split_blocks(disk_raw)
    our_blocks = split_blocks(ours)
    seen: set[str] = set()
    merged: list[str] = []
    for block in disk_blocks:
        if block not in removed_set:
            merged.append(block)
            seen.add(block)
    for block in our_blocks:
        if block not in seen:
            merged.append(block)
            seen.add(block)
    return render_blocks(merged)


# -- platform lock helpers --------------------------------------------------

try:
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def _file_lock(fd):  # pragma: no cover - platform branch
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows branch
    import contextlib
    import msvcrt

    @contextlib.contextmanager
    def _file_lock(fd):  # pragma: no cover - Windows branch
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)


def _cleanup_lock(path: Path) -> None:
    """Remove the sibling ``.lock`` file left by a prior write, if any."""
    lock_path = path.with_name(path.name + ".lock")
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:  # pragma: no cover - defensive
        pass


def _open_locked(path: Path):
    """Open a dedicated lock file for the memory target.

    A separate ``.lock`` sibling is used so the lock file is never replaced by
    the atomic rename of the memory file itself. On Windows ``msvcrt.locking``
    cannot lock a 0-byte file, so give the lock file a byte to lock.
    """
    lock_path = path.with_name(path.name + ".lock")
    fd = lock_path.open("a+", encoding="utf-8")
    try:
        if lock_path.stat().st_size == 0:
            fd.write("\n")
            fd.flush()
    except OSError:  # pragma: no cover - defensive
        pass
    return fd


def _release_lock(fd):
    try:
        fd.close()
    except OSError:
        pass
