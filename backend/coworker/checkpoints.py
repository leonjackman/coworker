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

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from coworker.logger import get_logger
logger = get_logger(__name__)

_DEFAULT_CAP_PER_SESSION = 500
_DEFAULT_MAX_BYTES_PER_THREAD = 32 * 1024 * 1024  # 32 MB

_AUTO_VACUUM_INCREMENTAL = 2
_AUTO_VACUUM_FULL = 1


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
        cap_per_session: int = _DEFAULT_CAP_PER_SESSION,
        max_bytes_per_thread: int = _DEFAULT_MAX_BYTES_PER_THREAD,
    ) -> None:
        self.db_path = Path(db_path)
        self.sessions_dir = Path(sessions_dir) if sessions_dir else None
        self.cap_per_session = cap_per_session
        self.max_bytes_per_thread = max_bytes_per_thread
        # NOTE: this lock ONLY guards the in-memory ``_active`` set. It must
        # NEVER be held across SQLite I/O: a blocked statement (SQLite's busy
        # handler can wait seconds) would otherwise stall every
        # ``active_sessions()``/``mark_active``/``mark_idle`` call made on the
        # event loop, hanging the whole app. DB concurrency is left to SQLite's
        # own WAL + busy_timeout locking.
        self._lock = threading.Lock()
        # Session ids with an in-flight stream; the sweep must never touch them.
        self._active: set[str] = set()

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    def _connect(self, timeout: float = 30.0, busy_timeout_ms: int = 30000) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=timeout)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
        # NOTE: do NOT re-apply ``PRAGMA auto_vacuum=INCREMENTAL`` here. It is a
        # persistent DB-file property (set once at startup by the registry's
        # long-lived connection and by _ensure_autovacuum during the
        # sweep) and re-applying it per connection raises "database is locked"
        # while another writer (e.g. a streaming session's AsyncSqliteSaver) is
        # active — see the matching note in AgentRuntimeRegistry._open_sync_checkpointer.
        return conn

    def _ensure_autovacuum(self, conn: sqlite3.Connection) -> bool:
        """Migrate a legacy DB to an auto-recycling mode. Returns True if a
        full VACUUM was run (so callers can log it)."""
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode == _AUTO_VACUUM_INCREMENTAL:
            return False
        if mode == _AUTO_VACUUM_FULL:
            return False
        # Requires no open transaction and rewrites the whole file — only safe
        # on the startup/idle sweep path.
        conn.execute("VACUUM")
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.commit()  # writes the PRAGMA change into the new file header
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
        active = self._active_set()
        conn = self._connect()
        try:
            for thread_id in self._orphan_threads(conn):
                conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                stats["orphan_threads"] += 1

            for thread_id in self._all_thread_ids(conn):
                if thread_id in active:
                    continue
                stats["trimmed_checkpoints"] += self._trim_thread(conn, thread_id)

            stats["orphan_writes"] = self._delete_orphan_writes(conn)

            # Release the DELETE transaction before any VACUUM-style
            # statement (both VACUUM and incremental_vacuum require no open
            # transaction).
            conn.commit()
            try:
                self._ensure_autovacuum(conn)
                for v_attempt in range(5):
                    try:
                        conn.execute("PRAGMA incremental_vacuum")
                        conn.commit()
                        stats["vacuumed"] = True
                        break
                    except sqlite3.OperationalError:
                        if v_attempt >= 4:
                            logger.warning("sweep: vacuum step failed after %d attempts", v_attempt + 1)
                            break
                        time.sleep(0.5)
            except sqlite3.OperationalError as exc:
                logger.warning("sweep: vacuum step skipped (writer lock): %s", exc)
        finally:
            conn.close()
        return stats

    def clear_all(self) -> dict[str, Any]:
        """Delete every checkpoint thread (active-stream threads are skipped),
        then reclaim disk. Used by the Settings "clear checkpoints" action."""
        stats: dict[str, Any] = {"cleared_threads": 0, "skipped_active": 0, "vacuumed": False}
        active = self._active_set()
        conn = self._connect()
        try:
            for thread_id in self._all_thread_ids(conn):
                if thread_id in active:
                    stats["skipped_active"] += 1
                    continue
                conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                stats["cleared_threads"] += 1
            conn.commit()
            if self._has_checkpoints_table(conn):
                try:
                    self._ensure_autovacuum(conn)
                    for v_attempt in range(5):
                        try:
                            conn.execute("PRAGMA incremental_vacuum")
                            conn.commit()
                            stats["vacuumed"] = True
                            break
                        except sqlite3.OperationalError:
                            if v_attempt >= 4:
                                logger.warning("clear_all: vacuum step failed after %d attempts", v_attempt + 1)
                                break
                            time.sleep(0.5)
                except sqlite3.OperationalError as exc:
                    logger.warning("clear_all: vacuum step skipped (writer lock): %s", exc)
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
        """Delete one session's checkpoint thread. Also calls incremental_vacuum
        to reclaim disk space after deletions.

        Uses a generous busy timeout (matching the writer side) and several
        retries with back-off so transient writer locks do not cause silent
        skips.
        """
        for attempt in range(5):
            try:
                self._delete_thread_once(session_id)
                return
            except sqlite3.OperationalError as exc:
                if attempt >= 4:
                    logger.warning("delete_thread(%s) failed (writer lock): %s", session_id, exc)
                    return
                time.sleep(0.5 * (attempt + 1))

    def _delete_thread_once(self, session_id: str) -> None:
        conn = self._connect(timeout=5.0, busy_timeout_ms=30000)
        try:
            if not self._has_checkpoints_table(conn):
                return
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (session_id,))
            conn.execute("DELETE FROM writes WHERE thread_id = ?", (session_id,))
            conn.commit()
            # Reclaim disk space after deletion. Uses a short retry loop so a
            # transient writer lock doesn't leave space permanently unreclaimed.
            for v_attempt in range(3):
                try:
                    conn.execute("PRAGMA incremental_vacuum")
                    conn.commit()
                    break
                except sqlite3.OperationalError:
                    if v_attempt >= 2:
                        break
                    time.sleep(0.2)
        finally:
            conn.close()
