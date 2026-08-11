"""Lifecycle management for the LangGraph runtime checkpoint SQLite database.

The runtime checkpoint DB grows without bound: LangGraph's SQLite checkpointer
stores a *full* state snapshot (the entire message history) at every graph step
and never prunes on its own. We keep it bounded with three cooperating
mechanisms:

1. Per-thread cap — each thread (session) keeps at most ``cap_per_session``
   checkpoints, plus a byte budget. LangGraph only ever resumes from the
   *latest* checkpoint (``aget_tuple`` uses ``ORDER BY checkpoint_id DESC
   LIMIT 1``); older checkpoints are pure redundancy, so trimming them never
   costs context.

2. Orphan cleanup — a checkpoint thread whose session JSON no longer exists is
   deleted outright. Deleting a session already calls ``delete_thread``, but
   interrupted/failed deletions can leave orphaned threads behind (the biggest
   real leak we observed: one 20 MB orphan).

3. Incremental vacuum — deleted rows release their file pages back to disk
   (``auto_vacuum=INCREMENTAL``), so the file actually shrinks over time
   instead of only growing.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CAP_PER_SESSION = 500
DEFAULT_MAX_BYTES_PER_THREAD = 32 * 1024 * 1024  # 32 MB

_AUTO_VACUUM_INCREMENTAL = 2


class CheckpointManager:
    """Owns the runtime checkpoint DB file and all its maintenance operations.

    The manager is deliberately framework-agnostic: it talks to the same
    ``runtime_checkpoints.sqlite`` file that ``SqliteSaver``/``AsyncSqliteSaver``
    write to, using raw SQL against the documented LangGraph checkpoint schema
    (``checkpoints`` + ``writes`` tables). This lets us prune without relying on
    the framework's missing ``prune()`` implementation.
    """

    def __init__(
        self,
        db_path: Path,
        sessions_dir: Path | None = None,
        cap_per_session: int = DEFAULT_CAP_PER_SESSION,
        max_bytes_per_thread: int = DEFAULT_MAX_BYTES_PER_THREAD,
    ) -> None:
        self.db_path = Path(db_path)
        self.sessions_dir = Path(sessions_dir) if sessions_dir else None
        self.cap_per_session = cap_per_session
        self.max_bytes_per_thread = max_bytes_per_thread
        # RLock: sweep() holds the lock while calling helpers (_active_set,
        # _trim_thread) that may also take it.
        self._lock = threading.RLock()
        # Session ids with an in-flight stream; the sweep must never touch them.
        self._active: set[str] = set()

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Persistent DB-file property. On a fresh file this takes effect
        # immediately; on a legacy file it is a no-op until a full VACUUM
        # (handled by _ensure_incremental_autovacuum during sweep).
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        return conn

    def _ensure_incremental_autovacuum(self, conn: sqlite3.Connection) -> bool:
        """Migrate a legacy DB to auto_vacuum=INCREMENTAL. Returns True if a
        full VACUUM was run (so callers can log it)."""
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode == _AUTO_VACUUM_INCREMENTAL:
            return False
        # Requires no open transaction and rewrites the whole file — only safe
        # on the startup/idle sweep path.
        conn.execute("VACUUM")
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        return True

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

    def _has_checkpoints_table(self, conn: sqlite3.Connection) -> bool:
        """The LangGraph schema tables are created lazily by the first
        SqliteSaver write, so a fresh DB may not have them yet. Treat absence
        as "nothing stored" instead of failing."""
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        ).fetchone()
        return row is not None

    def _all_thread_ids(self, conn: sqlite3.Connection) -> list[str]:
        if not self._has_checkpoints_table(conn):
            return []
        rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        return [row[0] for row in rows]

    # ------------------------------------------------------------------ #
    # Per-thread trimming (cap + byte budget)
    # ------------------------------------------------------------------ #
    def _thread_checkpoints(self, conn: sqlite3.Connection, thread_id: str) -> list[tuple[str, str, int]]:
        """Return ``(checkpoint_ns, checkpoint_id, blob_bytes)`` oldest-first."""
        if not self._has_checkpoints_table(conn):
            return []
        return [
            (row[0], row[1], int(row[2]))
            for row in conn.execute(
                """SELECT checkpoint_ns, checkpoint_id, length(checkpoint)
                   FROM checkpoints
                   WHERE thread_id = ?
                   ORDER BY CAST(json_extract(metadata, '$.step') AS INTEGER) ASC,
                            checkpoint_id ASC""",
                (thread_id,),
            )
        ]

    def _trim_thread(self, conn: sqlite3.Connection, thread_id: str) -> int:
        """Trim one thread to the cap + byte budget. Returns checkpoints removed."""
        checkpoints = self._thread_checkpoints(conn, thread_id)
        total = len(checkpoints)
        if total == 0:
            return 0

        drop: list[tuple[str, str]] = []
        if total > self.cap_per_session:
            drop.extend((row[0], row[1]) for row in checkpoints[: total - self.cap_per_session])

        # Byte budget: keep dropping oldest until under budget (always keep the
        # newest checkpoint so resumption stays intact).
        kept = checkpoints[len(drop):]
        while len(kept) > 1 and sum(size for _, _, size in kept) > self.max_bytes_per_thread:
            drop.append((kept[0][0], kept[0][1]))
            kept = kept[1:]

        if not drop:
            return 0
        for checkpoint_ns, checkpoint_id in drop:
            conn.execute(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (thread_id, checkpoint_ns, checkpoint_id),
            )
            conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (thread_id, checkpoint_ns, checkpoint_id),
            )
        return len(drop)

    # ------------------------------------------------------------------ #
    # Orphan cleanup
    # ------------------------------------------------------------------ #
    def _orphan_threads(self, conn: sqlite3.Connection) -> list[str]:
        if self.sessions_dir is None:
            return []
        existing = {path.stem for path in self.sessions_dir.glob("*.json")}
        return [thread_id for thread_id in self._all_thread_ids(conn) if thread_id not in existing]

    # ------------------------------------------------------------------ #
    # Sweep
    # ------------------------------------------------------------------ #
    def sweep(self) -> dict[str, Any]:
        """One maintenance pass: orphan cleanup, per-thread cap, orphan writes,
        then disk reclaim. Skips threads with an in-flight stream.

        Returns a small stats dict for logging.
        """
        stats: dict[str, Any] = {
            "orphan_threads": 0,
            "trimmed_checkpoints": 0,
            "orphan_writes": 0,
            "vacuumed": False,
        }
        with self._lock:
            conn = self._connect()
            try:
                for thread_id in self._orphan_threads(conn):
                    conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                    conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                    stats["orphan_threads"] += 1

                active = self._active_set()
                for thread_id in self._all_thread_ids(conn):
                    if thread_id in active:
                        continue
                    stats["trimmed_checkpoints"] += self._trim_thread(conn, thread_id)

                stats["orphan_writes"] = self._delete_orphan_writes(conn)

                # Release the DELETE transaction before any VACUUM-style
                # statement (both VACUUM and incremental_vacuum require no open
                # transaction).
                conn.commit()
                stats["vacuumed"] = self._ensure_incremental_autovacuum(conn)
                conn.execute("PRAGMA incremental_vacuum")
                conn.commit()
            finally:
                conn.close()
        return stats

    def _delete_orphan_writes(self, conn: sqlite3.Connection) -> int:
        """Delete writes rows whose checkpoint no longer exists."""
        if not self._has_checkpoints_table(conn):
            return 0
        cur = conn.execute(
            """DELETE FROM writes
               WHERE NOT EXISTS (
                   SELECT 1 FROM checkpoints c
                   WHERE c.thread_id = writes.thread_id
                     AND c.checkpoint_ns = writes.checkpoint_ns
                     AND c.checkpoint_id = writes.checkpoint_id
               )"""
        )
        return cur.rowcount

    # ------------------------------------------------------------------ #
    # Whole-thread deletion (session deleted / rolled back / re-run)
    # ------------------------------------------------------------------ #
    def delete_thread(self, session_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                if not self._has_checkpoints_table(conn):
                    return
                conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (session_id,))
                conn.execute("DELETE FROM writes WHERE thread_id = ?", (session_id,))
                conn.commit()
                conn.execute("PRAGMA incremental_vacuum")
                conn.commit()
            finally:
                conn.close()
