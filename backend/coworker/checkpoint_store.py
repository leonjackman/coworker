"""Per-session JSON checkpoint saver (LangGraph ``BaseCheckpointSaver``).

Replaces the shared ``runtime_checkpoints.sqlite`` with ONE small JSON file per
session (thread), written atomically via temp-file + ``os.replace``. This is the
per-session-file persistence model used by cline (and the spirit of codex's
"files are the source of truth, SQLite is a rebuildable projection"): there is
no central SQLite file, so the SQLite write-lock / ``busy_timeout`` /
``database is locked`` failure modes are physically impossible. Different
sessions never contend because each writes its own file.

The checkpoint DB is disposable per turn: every /chat/stream starts fresh from
the session history and deletes its thread when the turn ends (kept only for a
pending approval/question so HITL resume works). Each file therefore holds at
most a handful of checkpoints, and is deleted on ``adelete_thread``.

Serialization mirrors ``AsyncSqliteSaver``: each checkpoint + pending write is
encoded with the framework's ``self.serde`` (``dumps_typed`` -> ``(type, bytes)``)
and the bytes are stored base64 in the JSON file, so ``aget_tuple``/``alist``
deserialize with identical semantics (checkpoint ordering by uuid6 id, parent
config chain, pending writes).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.base import SerializerProtocol

from coworker.logger import get_logger

logger = get_logger(__name__)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


class JsonFileCheckpointSaver(BaseCheckpointSaver):
    """A ``BaseCheckpointSaver`` that stores one JSON file per thread/session.

    All mutation happens under an ``asyncio.Lock`` (single-writer model within
    the process) and is written atomically (temp file + ``os.replace`` + fsync),
    so a crash never leaves a half-written checkpoint and there is no database
    to lock.
    """

    def __init__(self, checkpoints_dir: Path, serde: SerializerProtocol | None = None) -> None:
        super().__init__(serde=serde)
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # File plumbing
    # ------------------------------------------------------------------ #
    def _thread_file(self, thread_id: str) -> Path:
        return self.checkpoints_dir / f"{thread_id}.json"

    def _empty(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": str(thread_id), "entries": {}, "writes": {}}

    def _load(self, thread_id: str) -> dict[str, Any]:
        path = self._thread_file(thread_id)
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return self._empty(thread_id)
        except (json.JSONDecodeError, OSError):
            # Corrupt / partial file (crash during write) — the checkpoint is a
            # disposable per-turn cache, so treat it as empty rather than
            # shipping corruption into a graph run.
            logger.warning("checkpoint file corrupt for %s; discarding", thread_id)
            try:
                path.unlink()
            except OSError:
                pass
            return self._empty(thread_id)

    def _save(self, thread_id: str, data: dict[str, Any]) -> None:
        path = self._thread_file(thread_id)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{id(data)}")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # BaseCheckpointSaver implementation
    # ------------------------------------------------------------------ #
    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        target_cid = get_checkpoint_id(config)
        data = self._load(thread_id)
        entries = data.get("entries", {})

        if target_cid is not None:
            entry = entries.get(target_cid)
            if entry is None or entry.get("checkpoint_ns", "") != checkpoint_ns:
                return None
            cid = target_cid
        else:
            candidates = [
                cid for cid, e in entries.items() if e.get("checkpoint_ns", "") == checkpoint_ns
            ]
            if not candidates:
                return None
            cid = max(candidates)  # uuid6 strings sort lexically == chronologically

        return self._build_tuple(thread_id, checkpoint_ns, cid, entries[cid], data.get("writes", {}).get(cid, []))

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        thread_id = str(config["configurable"]["thread_id"]) if config else None
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", "")) if config else ""
        before_cid = get_checkpoint_id(before) if before else None

        def _matches(metadata: dict[str, Any]) -> bool:
            return all(metadata.get(k) == v for k, v in (filter or {}).items())

        # All threads when no config given, else just the one thread.
        files = [self._thread_file(thread_id)] if thread_id else sorted(self.checkpoints_dir.glob("*.json"))
        rows: list[tuple[str, str, str, dict[str, Any], list[Any]]] = []
        for f in files:
            try:
                data = json.load(f.open("r", encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for cid, entry in data.get("entries", {}).items():
                if entry.get("checkpoint_ns", "") != checkpoint_ns:
                    continue
                if before_cid is not None and cid >= before_cid:
                    continue
                if not _matches(entry.get("metadata", {})):
                    continue
                rows.append((str(data.get("thread_id", "")), checkpoint_ns, cid, entry, data.get("writes", {}).get(cid, [])))

        rows.sort(key=lambda r: r[2], reverse=True)
        if limit is not None:
            rows = rows[:limit]
        for tid, ns, cid, entry, writes in rows:
            yield self._build_tuple(tid, ns, cid, entry, writes)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        cid = checkpoint["id"]
        type_, serialized = self.serde.dumps_typed(checkpoint)
        meta = get_checkpoint_metadata(config, metadata)

        async with self.lock:
            data = self._load(thread_id)
            data.setdefault("entries", {})[cid] = {
                "checkpoint_ns": checkpoint_ns,
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                "type": type_,
                "checkpoint": _b64(serialized),
                "metadata": meta,
            }
            self._save(thread_id, data)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": cid,
            }
        }

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        cid = str(config["configurable"].get("checkpoint_id"))

        encoded: list[list[Any]] = []
        replace_all = True
        for idx, (channel, value) in enumerate(writes):
            type_, blob = self.serde.dumps_typed(value)
            i = WRITES_IDX_MAP.get(channel, idx)
            if channel not in WRITES_IDX_MAP:
                replace_all = False
            encoded.append([task_id, channel, type_, _b64(blob), i])

        async with self.lock:
            data = self._load(thread_id)
            existing = data.setdefault("writes", {}).setdefault(cid, [])
            # Mirror SqliteSaver's PK semantics: (thread_id, checkpoint_ns,
            # checkpoint_id, task_id, idx). Only special channels
            # (interrupt/scheduled/resume/error) use INSERT OR REPLACE; regular
            # writes use INSERT OR IGNORE, so a retry never duplicates.
            for row in encoded:
                key = (row[0], row[4])  # (task_id, idx) == SQLite PK tail
                found_idx = next(
                    (k for k, x in enumerate(existing) if (x[0], x[4]) == key),
                    None,
                )
                if found_idx is not None:
                    if replace_all:
                        existing[found_idx] = row
                else:
                    existing.append(row)
            self._save(thread_id, data)

    async def adelete_thread(self, thread_id: str) -> None:
        async with self.lock:
            path = self._thread_file(thread_id)
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
    # Tuple construction
    # ------------------------------------------------------------------ #
    def _build_tuple(
        self,
        thread_id: str,
        checkpoint_ns: str,
        cid: str,
        entry: dict[str, Any],
        writes: list[Any],
    ) -> CheckpointTuple:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": cid,
            }
        }
        checkpoint = self.serde.loads_typed((entry["type"], _unb64(entry["checkpoint"])))
        parent_cid = entry.get("parent_checkpoint_id")
        parent_config = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_cid,
                }
            }
            if parent_cid
            else None
        )
        pending = [
            (task_id, channel, self.serde.loads_typed((type_, _unb64(b64))))
            for task_id, channel, type_, b64, _idx in sorted(writes, key=lambda w: w[4])
        ]
        return CheckpointTuple(
            config,
            checkpoint,
            entry.get("metadata", {}),
            parent_config,
            pending,
        )
