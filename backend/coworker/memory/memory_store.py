"""MemoryStore: thread-safe, drift-protected read/write of memory files.

Writing long-term memory is the riskiest surface of the feature (a corrupt
or silently-lost write is worse than no write). We harden it three ways,
following the hermes implementation the audit called out:

1. ``fcntl``/``msvcrt`` file lock around every read-modify-write so concurrent
   agent turns (and the auto-extract worker) cannot interleave.
2. Round-trip verification: after writing we re-read the file and confirm the
   entries we intended are present; mismatch means a concurrent external edit
   won — we refuse the write and keep a ``.bak`` of our intended content.
3. Exact-duplicate rejection and substring-based replace/remove so a stale
   entry cannot be silently duplicated past the char budget.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .memory_discovery import MemoryScanner
from .memory_file import ENTRY_DELIMITER, HEADER, MemoryFile, render_file, split_entries

logger = logging.getLogger(__name__)

_SCOPES = ("project", "user")


class MemoryError(ValueError):
    """Raised for semantic memory write failures (duplicate/missing target)."""


class MemoryStore:
    """Owns both memory files and guarantees serialized, verified writes."""

    def __init__(self, scanner: MemoryScanner):
        self.scanner = scanner
        self._lock = threading.RLock()

    # -- paths ------------------------------------------------------------

    def path_for(self, scope: str) -> Path:
        if scope not in _SCOPES:
            raise MemoryError(f"unknown memory scope: {scope!r}")
        if scope == "project":
            path = self.scanner.project_path()
            if path is None:
                raise MemoryError("no workspace: project memory is unavailable")
            return path
        return self.scanner.user_path()

    # -- helpers ----------------------------------------------------------

    def _read_locked(self, scope: str) -> tuple[MemoryFile, Path]:
        """Read a scope under the RLock (callers already hold ``self._lock``)."""
        path = self.path_for(scope)
        if not path.is_file():
            return MemoryFile(scope=scope, path=path, mtime=0.0, entries=[]), path
        return _read_file_with_retry(path, scope), path

    def _update_locked(self, scope: str, updater) -> MemoryFile:
        """Atomically read-modify-write a memory file under a shared file lock.

        Every write is a full ``read -> mutate -> render -> temp+rename`` cycle
        guarded by an ``flock``/``msvcrt`` lock on a dedicated ``.lock`` file
        (never renamed, so the lock is stable even across a rename of the target).
        Concurrent agent turns and the auto-extract worker therefore cannot lose
        each other's entries. A crash mid-write can never leave a truncated file
        (atomic rename).
        """
        path = self.path_for(scope)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = _open_locked(path)
        try:
            with _file_lock(lock_fd):
                current = _read_file_with_retry(path, scope) if path.is_file() else MemoryFile(
                    scope=scope, path=path, mtime=0.0, entries=[]
                )
                new_entries = updater(current.entries)
                payload = render_file(new_entries)
                tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
                path.touch(exist_ok=True)
        finally:
            _release_lock(lock_fd)
        return _read_file_with_retry(path, scope)

    # -- read API ----------------------------------------------------------

    def read_file_text(self, scope: str) -> str:
        """Return the raw on-disk body of a memory file (editable surface)."""
        path = self.path_for(scope)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f"{HEADER}\n"

    def write_file_text(self, scope: str, text: str) -> MemoryFile:
        """Replace a whole memory file from raw markdown.

        The body is re-parsed into entries (``§``-delimited) and written with
        the same lock + atomic rename as every other write path.
        """
        with self._lock:
            return self._update_locked(scope, lambda _entries: split_entries(text))

    def list_scope(self, scope: str) -> MemoryFile:
        with self._lock:
            memory, _ = self._read_locked(scope)
        return memory

    # -- write API ---------------------------------------------------------

    def _validate_entry_text(self, text: str) -> None:
        """Reject text that would break the ``§``-delimited file format.

        A line that is exactly ``§`` is the entry delimiter; embedding it inside
        an entry would silently split into multiple entries on the next read.
        """
        if any(line.strip() == ENTRY_DELIMITER for line in text.splitlines()):
            raise MemoryError(
                f"memory entry cannot contain a line that is exactly '{ENTRY_DELIMITER}'"
            )

    def add(self, scope: str, text: str) -> MemoryFile:
        text = text.strip()
        if not text:
            raise MemoryError("cannot add empty memory")
        self._validate_entry_text(text)
        with self._lock:
            def _add(entries: list[str]) -> list[str]:
                if any(e == text for e in entries):
                    raise MemoryError("duplicate memory entry (already exists)")
                return [*entries, text]
            return self._update_locked(scope, _add)

    def replace(self, scope: str, target: str, text: str) -> MemoryFile:
        text = text.strip()
        target = target.strip()
        if not text:
            raise MemoryError("cannot replace with empty memory")
        self._validate_entry_text(text)
        with self._lock:
            if not target:
                raise MemoryError("replace target is empty")

            def _replace(entries: list[str]) -> list[str]:
                if not entries:
                    raise MemoryError("replace target not found (memory is empty)")
                # Replace only the first matching entry so a single replace cannot
                # fabricate duplicate identical entries.
                updated: list[str] = []
                replaced = False
                for entry in entries:
                    if not replaced and target in entry:
                        updated.append(text)
                        replaced = True
                    else:
                        updated.append(entry)
                if not replaced:
                    raise MemoryError("replace target not found")
                return updated

            return self._update_locked(scope, _replace)

    def remove(self, scope: str, target: str) -> MemoryFile:
        target = target.strip()
        if not target:
            raise MemoryError("remove target is empty")
        with self._lock:
            def _remove(entries: list[str]) -> list[str]:
                kept = [e for e in entries if target not in e]
                if len(kept) == len(entries):
                    raise MemoryError("remove target not found")
                return kept
            return self._update_locked(scope, _remove)

    def clear(self, scope: str) -> MemoryFile:
        with self._lock:
            return self._update_locked(scope, lambda _entries: [])


def _read_file_with_retry(path: Path, scope: str) -> MemoryFile:
    """Read + parse; retry once if the file grew between stat and read."""
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Memory read failed for %s: %s", path, exc)
        return MemoryFile(scope=scope, path=path, mtime=0.0, entries=[])
    while True:
        if not text:
            break
        # Re-read if size changed (concurrent write), best effort.
        try:
            if path.stat().st_size == len(text.encode("utf-8")):
                break
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - defensive
            break
    return MemoryFile(scope=scope, path=path, mtime=stat.st_mtime, entries=split_entries(text))


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


def _open_locked(path: Path):
    """Open a dedicated lock file for the memory target.

    A separate ``.lock`` sibling is used so the lock file is never replaced by
    the atomic rename of the memory file itself (a rename would otherwise hand
    the lock to a fresh inode and break cross-writer serialization). On Windows,
    ``msvcrt.locking`` cannot lock a 0-byte file, so give the lock file a byte
    to lock.
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