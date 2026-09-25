"""Lightweight notification log (W34).

Unattended runs (schedules, workflows) append failure/needs-human records here so
the UI can surface them even when the user was not watching. One JSONL file,
capped by trimming on read.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
MAX_RECORDS = 500


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "notifications.jsonl"


def notify(data_dir: Path, *, kind: str, title: str, detail: str = "", ref: str = "") -> dict[str, Any]:
    record = {
        "id": f"n_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "title": title,
        "detail": detail[:500],
        "ref": ref,
        "read": False,
    }
    try:
        with _LOCK:
            with _path(data_dir).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - notifications are best-effort
        pass
    return record


def list_notifications(data_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
    path = _path(data_dir)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def clear_notifications(data_dir: Path) -> int:
    path = _path(data_dir)
    count = len(list_notifications(data_dir, limit=MAX_RECORDS))
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    return count
