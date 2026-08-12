from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import append_jsonl_retained, trim_jsonl


AGENT_TRACE_FILENAME = "agent_trace.jsonl"

# Rolling retention: the trace log is append-only and would grow unboundedly,
# so each record trims the file back to the most recent MAX_TRACE_LINES.
MAX_TRACE_LINES = 100

# Runtime-adjustable retention (Settings page overrides the default; applied on
# every record so the change takes effect without a restart).
ACTIVE_TRACE_RETENTION = MAX_TRACE_LINES


def set_trace_retention(lines: int) -> None:
    global ACTIVE_TRACE_RETENTION
    ACTIVE_TRACE_RETENTION = max(1, min(int(lines or MAX_TRACE_LINES), 10_000))


def _trim_jsonl(path: Path, max_lines: int) -> None:
    trim_jsonl(path, max_lines)


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
        append_jsonl_retained(self.path, entry, ACTIVE_TRACE_RETENTION)

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
