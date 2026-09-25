"""Run evidence persistence (W32).

The executor hands screenshot/data-URL/output evidence to the environment; this
module stores it under ``<data_dir>/workflows/.evidence/<run_id>/`` and keeps a
per-run index so the run view can list it.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: str) -> str:
    return _SAFE.sub("_", value)[:120] or "item"


def evidence_dir(data_dir: Path | str | None, run_id: str) -> Path:
    root = Path(data_dir) / "workflows" / ".evidence" if data_dir else Path(".coworker/evidence")
    return root / _safe(run_id)


def save_evidence(data_dir: Path | str | None, name: str, data: Any) -> dict[str, Any] | None:
    """Persist one evidence item. ``name`` is ``<run_id>:<step_id>``.

    Returns an index entry, or ``None`` when there is nothing to store.
    """
    if ":" in name:
        run_id, step_id = name.split(":", 1)
    else:
        run_id, step_id = "run", name
    folder = evidence_dir(data_dir, run_id)
    folder.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "step_id": step_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": "text",
    }
    try:
        if isinstance(data, str) and data.startswith("data:image") and "," in data:
            header, b64 = data.split(",", 1)
            ext = "png" if "png" in header else "jpg"
            target = folder / f"{_safe(step_id)}.{ext}"
            target.write_bytes(base64.b64decode(b64))
            entry.update(kind="image", path=str(target))
        else:
            target = folder / f"{_safe(step_id)}.json"
            payload = data if isinstance(data, (dict, list)) else {"text": str(data)}
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            entry.update(kind="json", path=str(target))
    except Exception:  # noqa: BLE001 - evidence is best-effort
        return None

    index = folder / "index.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_index(data_dir: Path | str | None, run_id: str) -> list[dict[str, Any]]:
    path = evidence_dir(data_dir, run_id) / "index.jsonl"
    if not path.is_file():
        return []
    items: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(json.loads(line))
    except Exception:  # noqa: BLE001
        return items
    return items
