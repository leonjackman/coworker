from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_TRACE_FILENAME = "agent_trace.jsonl"

# Rolling retention: the trace log is append-only and would grow unboundedly,
# so each record trims the file back to the most recent MAX_TRACE_LINES.
MAX_TRACE_LINES = 100


def _trim_jsonl(path: Path, max_lines: int) -> None:
    if max_lines <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    try:
        path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except OSError:
        return


class AgentTraceStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event: str,
        status: str,
        context: dict[str, Any],
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "status": status,
            "context": context,
            "details": details or {},
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return
        _trim_jsonl(self.path, MAX_TRACE_LINES)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events[-max(1, min(limit, 500)):][::-1]
