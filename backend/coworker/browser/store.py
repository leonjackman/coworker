"""Persistent stores for the embedded browser's personal data.

Bookmarks, browsing history and download records are user-level data (not
per-session) and live under ``<data_dir>/browser/`` as three JSON files. They
are written atomically through :mod:`coworker.atomicio` and guarded by a
per-store re-entrant lock so the Electron main process's frequent history
writes never race a UI read/clear.

Passwords are intentionally NOT stored here — they never leave the Electron
main process (OS keychain via ``safeStorage``).
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coworker.atomicio import atomic_write_json

#: Newest-first history cap. Older entries are dropped on overflow so the file
#: (rewritten whole on every visit) stays small and fast to parse.
MAX_HISTORY_ENTRIES = 5000

#: Two consecutive visits to the same URL within this window collapse into one
#: entry (bumping ``visit_count``) instead of spamming the list — matches how
#: a browser treats a redirect/reload chain.
_HISTORY_DEDUPE_SECONDS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _load(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else dict(default)
    except Exception:  # noqa: BLE001 - missing/corrupt file falls back to empty
        return dict(default)


class BrowserStore:
    """Read/write bookmarks, history and downloads for one data directory."""

    def __init__(self, data_dir: Path | str):
        self.root = Path(data_dir) / "browser"
        self._lock = threading.RLock()

    # -- paths ---------------------------------------------------------------
    @property
    def bookmarks_path(self) -> Path:
        return self.root / "bookmarks.json"

    @property
    def history_path(self) -> Path:
        return self.root / "history.json"

    @property
    def downloads_path(self) -> Path:
        return self.root / "downloads.json"

    def _save(self, path: Path, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)

    # -- bookmarks -----------------------------------------------------------
    def list_bookmarks(self) -> list[dict[str, Any]]:
        with self._lock:
            data = _load(self.bookmarks_path, {"version": 1, "items": []})
        items = data.get("items")
        return [b for b in items if isinstance(b, dict)] if isinstance(items, list) else []

    def add_bookmark(self, url: str, title: str = "") -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            raise ValueError("url is required")
        with self._lock:
            bookmarks = self.list_bookmarks()
            for existing in bookmarks:
                if existing.get("url") == url:
                    return existing
            item = {
                "id": _new_id(),
                "url": url,
                "title": (title or url).strip(),
                "created_at": _now_iso(),
            }
            bookmarks.append(item)
            self._save(self.bookmarks_path, {"version": 1, "items": bookmarks})
            return item

    def remove_bookmark(self, bookmark_id: str) -> bool:
        with self._lock:
            bookmarks = self.list_bookmarks()
            remaining = [b for b in bookmarks if b.get("id") != bookmark_id]
            if len(remaining) == len(bookmarks):
                return False
            self._save(self.bookmarks_path, {"version": 1, "items": remaining})
            return True

    def update_bookmark(self, bookmark_id: str, *, url: str | None = None, title: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            bookmarks = self.list_bookmarks()
            updated = None
            for item in bookmarks:
                if item.get("id") == bookmark_id:
                    if url is not None and url.strip():
                        item["url"] = url.strip()
                    if title is not None:
                        item["title"] = title.strip() or item.get("url", "")
                    updated = item
                    break
            if updated is not None:
                self._save(self.bookmarks_path, {"version": 1, "items": bookmarks})
            return updated

    def reorder_bookmarks(self, ids: list[str]) -> list[dict[str, Any]]:
        """Reorder bookmarks by an explicit id list; unknown ids keep their
        relative trailing order, missing ids are appended in their old order."""
        with self._lock:
            bookmarks = self.list_bookmarks()
            by_id = {b.get("id"): b for b in bookmarks}
            ordered: list[dict[str, Any]] = []
            seen: set[str] = set()
            for bookmark_id in ids or []:
                item = by_id.get(bookmark_id)
                if item is not None and bookmark_id not in seen:
                    ordered.append(item)
                    seen.add(bookmark_id)
            for item in bookmarks:
                if item.get("id") not in seen:
                    ordered.append(item)
            self._save(self.bookmarks_path, {"version": 1, "items": ordered})
            return ordered

    # -- history -------------------------------------------------------------
    def list_history(self, *, query: str = "", limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            data = _load(self.history_path, {"version": 1, "items": []})
        items = data.get("items")
        entries = [h for h in items if isinstance(h, dict)] if isinstance(items, list) else []
        needle = (query or "").strip().lower()
        if needle:
            entries = [
                h for h in entries
                if needle in str(h.get("url", "")).lower() or needle in str(h.get("title", "")).lower()
            ]
        try:
            cap = max(1, min(10000, int(limit)))
        except (TypeError, ValueError):
            cap = 500
        return entries[:cap]

    def add_history(self, url: str, title: str = "") -> dict[str, Any]:
        url = (url or "").strip()
        if not url or url == "about:blank":
            return {}
        with self._lock:
            data = _load(self.history_path, {"version": 1, "items": []})
            items = data.get("items")
            entries = [h for h in items if isinstance(h, dict)] if isinstance(items, list) else []
            now = _now_iso()
            if entries and entries[0].get("url") == url and self._recent(entries[0].get("visited_at"), _HISTORY_DEDUPE_SECONDS):
                entries[0]["visited_at"] = now
                entries[0]["visit_count"] = int(entries[0].get("visit_count", 1)) + 1
                if title:
                    entries[0]["title"] = title
                self._save(self.history_path, {"version": 1, "items": entries[:MAX_HISTORY_ENTRIES]})
                return entries[0]
            entry = {"id": _new_id(), "url": url, "title": (title or url).strip(), "visited_at": now, "visit_count": 1}
            entries.insert(0, entry)
            self._save(self.history_path, {"version": 1, "items": entries[:MAX_HISTORY_ENTRIES]})
            return entry

    def remove_history(self, entry_id: str) -> bool:
        with self._lock:
            data = _load(self.history_path, {"version": 1, "items": []})
            entries = data.get("items") if isinstance(data.get("items"), list) else []
            remaining = [h for h in entries if isinstance(h, dict) and h.get("id") != entry_id]
            if len(remaining) == len(entries):
                return False
            self._save(self.history_path, {"version": 1, "items": remaining})
            return True

    def clear_history(self) -> None:
        with self._lock:
            self._save(self.history_path, {"version": 1, "items": []})

    @staticmethod
    def _recent(iso: Any, seconds: int) -> bool:
        try:
            then = datetime.fromisoformat(str(iso))
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - then).total_seconds() <= seconds
        except (TypeError, ValueError):
            return False

    # -- downloads -----------------------------------------------------------
    def list_downloads(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            data = _load(self.downloads_path, {"version": 1, "items": []})
        items = data.get("items")
        entries = [d for d in items if isinstance(d, dict)] if isinstance(items, list) else []
        try:
            cap = max(1, min(2000, int(limit)))
        except (TypeError, ValueError):
            cap = 200
        return entries[:cap]

    def add_download(self, *, download_id: str, url: str, filename: str, path: str = "", mime: str = "", total_bytes: int = 0) -> dict[str, Any]:
        with self._lock:
            data = _load(self.downloads_path, {"version": 1, "items": []})
            entries = data.get("items") if isinstance(data.get("items"), list) else []
            entry = {
                "id": str(download_id),
                "url": url,
                "filename": filename,
                "path": path,
                "mime": mime,
                "state": "progressing",
                "total_bytes": int(total_bytes or 0),
                "received_bytes": 0,
                "started_at": _now_iso(),
                "ended_at": "",
            }
            entries = [d for d in entries if isinstance(d, dict) and d.get("id") != entry["id"]]
            entries.insert(0, entry)
            self._save(self.downloads_path, {"version": 1, "items": entries[:500]})
            return entry

    def update_download(self, download_id: str, **patch: Any) -> dict[str, Any] | None:
        with self._lock:
            data = _load(self.downloads_path, {"version": 1, "items": []})
            entries = data.get("items") if isinstance(data.get("items"), list) else []
            updated = None
            for item in entries:
                if isinstance(item, dict) and item.get("id") == download_id:
                    for key in ("path", "state", "received_bytes", "total_bytes", "filename", "ended_at"):
                        if key in patch and patch[key] is not None:
                            item[key] = patch[key]
                    updated = item
                    break
            if updated is not None:
                self._save(self.downloads_path, {"version": 1, "items": entries})
            return updated

    def remove_download(self, download_id: str) -> bool:
        with self._lock:
            data = _load(self.downloads_path, {"version": 1, "items": []})
            entries = data.get("items") if isinstance(data.get("items"), list) else []
            remaining = [d for d in entries if isinstance(d, dict) and d.get("id") != download_id]
            if len(remaining) == len(entries):
                return False
            self._save(self.downloads_path, {"version": 1, "items": remaining})
            return True

    def clear_downloads(self) -> None:
        with self._lock:
            self._save(self.downloads_path, {"version": 1, "items": []})
