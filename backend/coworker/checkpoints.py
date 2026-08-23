"""Lifecycle management for the per-session JSON checkpoint files.

Each session's runtime checkpoint lives in its OWN ``checkpoints/<session_id>.json``
file (written atomically by ``JsonFileCheckpointSaver``), so there is no shared
SQLite file and the write-lock / busy_timeout / "database is locked" failure
modes cannot occur. Because every /chat/stream deletes its thread when the turn
ends, files exist only while a turn is in flight or an approval is pending;
maintenance therefore reduces to:

1. Orphan cleanup — a checkpoint file whose session JSON no longer exists is
   deleted outright (crash leftovers, deleted sessions).
2. Stale cleanup — a checkpoint file for a live session that is NOT currently
   streaming is a leftover from a process crash mid-turn; it is safe to delete
   because the next turn rebuilds fresh from session history anyway.

The in-memory ``_active`` set (the "one stream per session" gate) is unchanged.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from coworker.logger import get_logger
logger = get_logger(__name__)


class CheckpointManager:
    """Owns the per-session checkpoint files and their maintenance operations.

    The manager is framework-agnostic: it talks directly to the
    ``checkpoints/<session_id>.json`` files that ``JsonFileCheckpointSaver``
    writes, so pruning needs no knowledge of LangGraph's checkpoint schema.
    """

    def __init__(
        self,
        checkpoints_dir: Path,
        sessions_dir: Path | None = None,
    ) -> None:
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = Path(sessions_dir) if sessions_dir else None
        # NOTE: this lock ONLY guards the in-memory ``_active`` set. It must
        # NEVER be held across file I/O.
        self._lock = threading.Lock()
        # Session ids with an in-flight stream; the sweep must never touch them.
        self._active: set[str] = set()

    # ------------------------------------------------------------------ #
    # Active-stream guard
    # ------------------------------------------------------------------ #
    def mark_active(self, session_id: str) -> None:
        with self._lock:
            self._active.add(session_id)

    def mark_idle(self, session_id: str) -> None:
        with self._lock:
            self._active.discard(session_id)

    def _active_set(self) -> set[str]:
        with self._lock:
            return set(self._active)

    def active_sessions(self) -> set[str]:
        """Public snapshot of session ids with an in-flight stream."""
        return self._active_set()

    # ------------------------------------------------------------------ #
    # File helpers
    # ------------------------------------------------------------------ #
    def _thread_file(self, session_id: str) -> Path:
        return self.checkpoints_dir / f"{session_id}.json"

    def _all_thread_ids(self) -> list[str]:
        return [f.stem for f in self.checkpoints_dir.glob("*.json")]

    def _orphan_thread_ids(self) -> list[str]:
        """Thread ids whose session JSON no longer exists (crash leftovers)."""
        if self.sessions_dir is None:
            return []
        existing = {path.stem for path in self.sessions_dir.glob("*.json")}
        return [tid for tid in self._all_thread_ids() if tid not in existing]

    # ------------------------------------------------------------------ #
    # Whole-thread deletion (session deleted / rolled back / re-run)
    # ------------------------------------------------------------------ #
    def delete_thread(self, session_id: str) -> bool:
        """Delete one session's checkpoint file (best-effort, no DB to lock).

        Runs on the user-request critical path (edit / regenerate / fresh start)
        but is just a single file unlink, so it can never block.
        """
        try:
            self._delete_thread_once(session_id)
            return True
        except OSError as exc:
            logger.warning("delete_thread(%s) failed: %s", session_id, exc)
            return False

    def _delete_thread_once(self, session_id: str) -> None:
        path = self._thread_file(session_id)
        for tmp in self.checkpoints_dir.glob(f".{path.name}.tmp.*"):
            try:
                tmp.unlink()
            except OSError:
                pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------ #
    # Sweep
    # ------------------------------------------------------------------ #
    def sweep(self) -> dict[str, Any]:
        """One maintenance pass: remove orphan checkpoint files.

        Only files whose session JSON no longer exists are deleted. Files for
        live sessions are left alone even when not streaming: they may be a
        pending approval's interrupt checkpoint (the session turns idle after an
        interrupt, so "not streaming" must NOT be treated as "stale"), and any
        genuine crash leftover is deleted by the next /chat/stream's fresh-start
        delete anyway.
        """
        stats: dict[str, Any] = {"orphan_threads": 0}
        for thread_id in self._orphan_thread_ids():
            self._delete_thread_once(thread_id)
            stats["orphan_threads"] += 1
        return stats

    def clear_all(self) -> dict[str, Any]:
        """Delete every checkpoint file (active-stream sessions are skipped).

        Used by the Settings "clear checkpoints" action.
        """
        stats: dict[str, Any] = {"cleared_threads": 0, "skipped_active": 0}
        active = self._active_set()
        for thread_id in self._all_thread_ids():
            if thread_id in active:
                stats["skipped_active"] += 1
                continue
            self._delete_thread_once(thread_id)
            stats["cleared_threads"] += 1
        return stats
