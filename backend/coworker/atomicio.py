"""Atomic filesystem writes shared by the JSON/JSONL stores.

A crash mid-``write_text`` truncates the target; all durable stores in the app
switch their whole-file rewrites through here so a hard kill can never leave a
corrupted session / provider / project / change-log file.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling temp file + atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: object, *, indent: int = 2) -> None:
    """Write a JSON-serialisable object atomically (with a trailing newline)."""
    import json

    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=indent) + "\n")


def atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    """Write newline-joined lines atomically."""
    atomic_write_text(path, "".join(line if line.endswith("\n") else line + "\n" for line in lines))


# Per-path locks serialising the "append one JSONL line + trim" cycle. Without
# them a trim's read-modify-write can observe a file *before* a concurrent
# append lands and then rewrite it without that line, silently dropping audit
# / trace events.
_jsonl_locks: dict[str, threading.RLock] = {}
_jsonl_locks_guard = threading.Lock()


def _jsonl_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _jsonl_locks_guard:
        lock = _jsonl_locks.get(key)
        if lock is None:
            lock = _jsonl_locks[key] = threading.RLock()
        return lock


def append_jsonl_retained(path: Path, payload: object, max_lines: int) -> None:
    """Append one JSONL line, then trim to ``max_lines``, atomically and locked.

    Never raises. ``max_lines <= 0`` disables retention (pure append).
    """
    import json

    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    path = Path(path)
    lock = _jsonl_lock(path)
    with lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            return
        trim_jsonl(path, max_lines)


def trim_jsonl(path: Path, max_lines: int) -> None:
    """Rolling retention for an append-only JSONL log (best effort, atomic)."""
    path = Path(path)
    if max_lines <= 0:
        return
    lock = _jsonl_lock(path)
    with lock:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        if len(lines) <= max_lines:
            return
        try:
            atomic_write_text(path, "\n".join(lines[-max_lines:]) + "\n")
        except OSError:
            return
