from __future__ import annotations

import difflib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .atomicio import atomic_write_lines


CHANGE_MAX_CONTENT_BYTES = 512 * 1024
CHANGE_MAX_ENTRIES = 500
CHANGE_MAX_HUNKS = 200
CHANGE_MAX_DIFF_LINES = 2000

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def compute_file_diff(before: str | None, after: str | None) -> dict[str, Any]:
    """Compute a structured unified diff between two file contents.

    Returns ``{added, removed, hunks, truncated}`` where each hunk is
    ``{old_start, old_lines, new_start, new_lines, lines}`` and each line is
    ``{type: context|add|del, old_no, new_no, text}``.
    """
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()

    added = 0
    removed = 0
    total_lines = 0
    truncated = False
    hunks: list[dict[str, Any]] = []
    current_hunk: dict[str, Any] | None = None
    old_no = 0
    new_no = 0

    def flush_hunk() -> None:
        nonlocal current_hunk
        if current_hunk is not None:
            hunks.append(current_hunk)
            current_hunk = None

    for line in difflib.unified_diff(before_lines, after_lines, lineterm=""):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("\\"):
            continue
        header_match = _HUNK_HEADER.match(line)
        if header_match:
            flush_hunk()
            if len(hunks) >= CHANGE_MAX_HUNKS:
                truncated = True
                break
            current_hunk = {
                "old_start": int(header_match.group(1)),
                "old_lines": int(header_match.group(2) or "1"),
                "new_start": int(header_match.group(3)),
                "new_lines": int(header_match.group(4) or "1"),
                "lines": [],
            }
            old_no = current_hunk["old_start"]
            new_no = current_hunk["new_start"]
            continue
        if current_hunk is None:
            continue

        if total_lines >= CHANGE_MAX_DIFF_LINES:
            truncated = True
            break

        if line.startswith("+"):
            current_hunk["lines"].append({"type": "add", "old_no": None, "new_no": new_no, "text": line[1:]})
            new_no += 1
            added += 1
        elif line.startswith("-"):
            current_hunk["lines"].append({"type": "del", "old_no": old_no, "new_no": None, "text": line[1:]})
            old_no += 1
            removed += 1
        else:
            current_hunk["lines"].append({"type": "context", "old_no": old_no, "new_no": new_no, "text": line[1:]})
            old_no += 1
            new_no += 1
        total_lines += 1

    flush_hunk()
    return {"added": added, "removed": removed, "hunks": hunks, "truncated": truncated}


class ChangeStore:
    """Durable per-session record of every write/edit tool application.

    Each entry captures the before/after file contents (size-capped) plus a
    structured unified diff so the UI can render real diffs and, later, revert
    individual changes.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        max_content_bytes: int = CHANGE_MAX_CONTENT_BYTES,
        max_entries: int = CHANGE_MAX_ENTRIES,
    ):
        self.root = Path(data_dir) / "changes"
        self.max_content_bytes = max_content_bytes
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def _session_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        return self.root / f"{safe}.jsonl"

    def _read_entries(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def _write_entries(self, path: Path, entries: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_lines(
            path,
            (json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in entries),
        )

    def record(
        self,
        *,
        session_id: str,
        turn_index: int,
        tool_name: str,
        file_path: str,
        kind: str,
        before: str | None,
        after: str | None,
        diff: dict[str, Any] | None = None,
        file_existed: bool = True,
    ) -> dict[str, Any]:
        """Append a change record for one write/edit tool application."""
        computed = diff or compute_file_diff(before, after)
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            entry: dict[str, Any] = {
                "id": uuid4().hex,
                "session_id": session_id,
                "turn_index": int(turn_index),
                "tool_name": tool_name,
                "file_path": file_path,
                "kind": kind,
                "file_existed": bool(file_existed),
                "seq": len(entries),
                "message_id": "",
                "state": "active",
                "added": int(computed.get("added") or 0),
                "removed": int(computed.get("removed") or 0),
                "truncated": bool(computed.get("truncated")),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if computed.get("hunks"):
                entry["hunks"] = computed["hunks"]

            fits = bool(after is not None) or bool(before is not None)
            too_large = False
            if fits:
                try:
                    too_large = (before is not None and len(before.encode("utf-8")) > self.max_content_bytes) or (after is not None and len(after.encode("utf-8")) > self.max_content_bytes)
                except (UnicodeEncodeError, AttributeError):
                    too_large = True
            else:
                too_large = True
            entry["too_large"] = too_large
            if not too_large:
                entry["before"] = before or ""
                entry["after"] = after or ""

            entries.append(entry)
            if len(entries) > self.max_entries:
                entries = entries[-self.max_entries :]
            self._write_entries(path, entries)
        return entry

    def assign_message(self, session_id: str, message_id: str) -> int:
        """Bind any unbound change records of a session to the given message id.

        Returns the number of records bound. Called once per assistant message
        after it is persisted so rollback can map code changes to messages.
        """
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            count = 0
            for entry in entries:
                if entry.get("session_id") == session_id and not entry.get("message_id"):
                    entry["message_id"] = message_id
                    count += 1
            if count:
                self._write_entries(path, entries)
        return count

    def list_changes(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._read_entries(self._session_path(session_id))))

    def changes_for_message_ids(self, session_id: str, message_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(message_ids or [])
        if not wanted:
            return []
        with self._lock:
            entries = self._read_entries(self._session_path(session_id))
        matched = [e for e in entries if e.get("session_id") == session_id and e.get("message_id") in wanted]
        matched.sort(key=lambda e: (int(e.get("turn_index") or 0), int(e.get("seq") or 0)))
        return matched

    def delete_records(self, session_id: str, record_ids: list[str]) -> int:
        ids = set(record_ids or [])
        if not ids:
            return 0
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            remaining = [e for e in entries if e.get("id") not in ids]
            self._write_entries(path, remaining)
        return len(entries) - len(remaining)

    def revert_changes(
        self,
        session_id: str,
        message_ids: list[str],
        workspace: Any,
    ) -> dict[str, Any]:
        """Revert the code changes produced by the given messages.

        Changes are applied in reverse application order so the newest edit is
        undone first. Each change goes through ``workspace.revert_change`` which
        performs a safe inverse edit and reports a conflict (without writing) when
        the file no longer matches the recorded state. Returns a summary of
        reverted and conflicted changes; conflicted changes are left untouched.
        """
        changes = self.changes_for_message_ids(session_id, message_ids)
        reverted: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for change in reversed(changes):
            result = workspace.revert_change(change)
            if result.get("status") == "reverted":
                reverted.append(result)
            else:
                conflicts.append(result)
        return {
            "reverted": reverted,
            "conflicts": conflicts,
            "total": len(changes),
            "reverted_count": len(reverted),
            "conflict_count": len(conflicts),
        }

    def mark_reverted(self, session_id: str, record_ids: list[str], reverted_by: str) -> int:
        """Mark change records as reverted (files restored to ``before``).

        The records are kept (not deleted) so a later "redo" can re-apply the
        ``after`` content. Only ``active`` records transition to ``reverted``.
        """
        ids = set(record_ids or [])
        if not ids:
            return 0
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            count = 0
            for entry in entries:
                if entry.get("id") in ids and entry.get("state", "active") == "active":
                    entry["state"] = "reverted"
                    entry["reverted_by"] = reverted_by
                    count += 1
            if count:
                self._write_entries(path, entries)
        return count

    def mark_abandoned(self, session_id: str, record_ids: list[str]) -> int:
        """Mark change records as abandoned (truncated/conflicted).

        Abandoned records are hidden from the changes panel; their files are
        left untouched on disk.
        """
        ids = set(record_ids or [])
        if not ids:
            return 0
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            count = 0
            for entry in entries:
                if entry.get("id") in ids and entry.get("state", "active") == "active":
                    entry["state"] = "abandoned"
                    count += 1
            if count:
                self._write_entries(path, entries)
        return count

    def reverted_records(self, session_id: str, reverted_by: str) -> list[dict[str, Any]]:
        """Return records reverted by editing the given user message (for redo)."""
        with self._lock:
            entries = self._read_entries(self._session_path(session_id))
        return [e for e in entries if e.get("state") == "reverted" and e.get("reverted_by") == reverted_by]

    def next_turn_index(self, session_id: str) -> int:
        entries = self.list_changes(session_id)
        indices = [int(entry.get("turn_index") or 0) for entry in entries]
        return (max(indices) if indices else 0) + 1

    def changes_by_turn(self, session_id: str) -> list[dict[str, Any]]:
        entries = self.list_changes(session_id)
        visible = [e for e in entries if e.get("state", "active") == "active"]
        turns: dict[int, list[dict[str, Any]]] = {}
        for entry in reversed(visible):
            turns.setdefault(int(entry.get("turn_index") or 1), []).append(entry)
        return [{"turn_index": index, "changes": turns[index]} for index in sorted(turns)]

    def match_and_claim(self, session_id: str, tool_name: str, file_path: str) -> dict[str, Any] | None:
        """Return and durably mark the first unclaimed record matching the tool.

        Tool execution is serial within an agent turn, so the oldest unclaimed
        record for ``(tool_name, file_path)`` is the one the just-completed tool
        call produced.
        """
        with self._lock:
            path = self._session_path(session_id)
            entries = self._read_entries(path)
            for entry in entries:
                if entry.get("claimed"):
                    continue
                if entry.get("tool_name") == tool_name and entry.get("file_path") == file_path:
                    entry["claimed"] = True
                    self._write_entries(path, entries)
                    return entry
        return None

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            path = self._session_path(session_id)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
