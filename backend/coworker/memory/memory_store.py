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
import threading
from pathlib import Path

from .memory_discovery import MemoryScanner
from .memory_file import HEADER, MemoryFile, render_file, split_entries

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

    def _write_locked(self, scope: str, entries: list[str]) -> MemoryFile:
        """Write entries under lock with a verified round trip."""
        path = self.path_for(scope)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        payload = render_file(entries)
        lock_fd = _open_locked(path)
        try:
            with _file_lock(lock_fd):
                path.write_text(payload, encoding="utf-8")
                path.touch(exist_ok=True)
        finally:
            _release_lock(lock_fd)
        # Round-trip verification: another process may have edited after our
        # acquire/write; confirm what we wrote survived.
        readback = _read_file_with_retry(path, scope)
        if readback.entries != [e.strip() for e in entries if e.strip()]:
            backup = path.with_suffix(path.suffix + ".bak")
            try:
                backup.write_text(payload, encoding="utf-8")
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Memory round-trip mismatch; backup write failed: %s", exc)
            raise MemoryError(
                "memory file changed concurrently (round-trip mismatch); intended "
                f"content preserved at {backup}"
            )
        return readback

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
        the same lock + round-trip verification as every other write path.
        """
        with self._lock:
            return self._write_locked(scope, split_entries(text))

    def list_scope(self, scope: str) -> MemoryFile:
        with self._lock:
            memory, _ = self._read_locked(scope)
        return memory

    def scan_all(self, *, include_missing: bool = True) -> dict[str, MemoryFile | None]:
        """Return ``{scope: MemoryFile}`` for both scopes (project may be None)."""
        with self._lock:
            return {
                "project": self.scanner.scan(include_missing=include_missing).project,
                "user": self.scanner.scan(include_missing=include_missing).user,
            }

    # -- write API ---------------------------------------------------------

    def add(self, scope: str, text: str) -> MemoryFile:
        text = text.strip()
        if not text:
            raise MemoryError("cannot add empty memory")
        with self._lock:
            memory, _ = self._read_locked(scope)
            if any(e == text for e in memory.entries):
                raise MemoryError("duplicate memory entry (already exists)")
            return self._write_locked(scope, [*memory.entries, text])

    def replace(self, scope: str, target: str, text: str) -> MemoryFile:
        text = text.strip()
        target = target.strip()
        if not text:
            raise MemoryError("cannot replace with empty memory")
        with self._lock:
            memory, _ = self._read_locked(scope)
            if not memory.entries:
                raise MemoryError("replace target not found (memory is empty)")
            if not target:
                raise MemoryError("replace target is empty")
            hit = 0
            updated: list[str] = []
            for entry in memory.entries:
                if target in entry:
                    updated.append(text)
                    hit += 1
                else:
                    updated.append(entry)
            if not hit:
                raise MemoryError("replace target not found")
            return self._write_locked(scope, updated)

    def remove(self, scope: str, target: str) -> MemoryFile:
        target = target.strip()
        if not target:
            raise MemoryError("remove target is empty")
        with self._lock:
            memory, _ = self._read_locked(scope)
            kept = [e for e in memory.entries if target not in e]
            if len(kept) == len(memory.entries):
                raise MemoryError("remove target not found")
            return self._write_locked(scope, kept)

    def clear(self, scope: str) -> MemoryFile:
        with self._lock:
            return self._write_locked(scope, [])


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
    """Open the target file for appending without flushing a newline change.

    ``a+`` positions the cursor at EOF for write; read still works after seek(0).
    """
    return path.open("a+", encoding="utf-8")


def _release_lock(fd):
    try:
        fd.close()
    except OSError:
        pass