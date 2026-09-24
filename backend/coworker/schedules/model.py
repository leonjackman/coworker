"""Schedule domain model.

A Schedule is a first-class, independent cron job (like n8n / Celery Beat /
K8s CronJob): it references a target (workflow / bare command / agent task),
carries its own cron + timezone + policies, and tracks next/last run.

It is deliberately decoupled from workflow definitions — the old
``triggers: [cron:...]`` form is deprecated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TARGET_TYPES = frozenset({"workflow", "command", "agent"})
OVERLAP_POLICIES = frozenset({"skip", "queue", "replace", "allow"})
MISFIRE_POLICIES = frozenset({"skip", "run_once", "catchup"})
STATUSES = frozenset({"idle", "running", "ok", "failed", "skipped"})

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 80
MAX_ID = 64


@dataclass
class Schedule:
    id: str
    name: str
    target_type: str = "workflow"
    workflow: str = ""
    command: str = ""
    prompt: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    cron: str = "0 9 * * *"
    timezone: str = "UTC"
    enabled: bool = True
    overlap: str = "skip"
    misfire: str = "skip"
    grace_seconds: int = 0
    retry_max: int = 0
    retry_backoff: int = 30
    timeout_seconds: int = 0
    last_run_at: str = ""
    last_status: str = "idle"
    last_run_id: str = ""
    last_error: str = ""
    run_count: int = 0
    next_run_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "target_type": self.target_type,
            "workflow": self.workflow,
            "command": self.command,
            "prompt": self.prompt,
            "inputs": self.inputs,
            "cron": self.cron,
            "timezone": self.timezone,
            "enabled": self.enabled,
            "overlap": self.overlap,
            "misfire": self.misfire,
            "grace_seconds": self.grace_seconds,
            "retry_max": self.retry_max,
            "retry_backoff": self.retry_backoff,
            "timeout_seconds": self.timeout_seconds,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_run_id": self.last_run_id,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            target_type=str(data.get("target_type") or "workflow"),
            workflow=str(data.get("workflow") or ""),
            command=str(data.get("command") or ""),
            prompt=str(data.get("prompt") or ""),
            inputs=dict(data.get("inputs") or {}),
            cron=str(data.get("cron") or "0 9 * * *"),
            timezone=str(data.get("timezone") or "UTC"),
            enabled=bool(data.get("enabled", True)),
            overlap=str(data.get("overlap") or "skip"),
            misfire=str(data.get("misfire") or "skip"),
            grace_seconds=int(data.get("grace_seconds") or 0),
            retry_max=int(data.get("retry_max") or 0),
            retry_backoff=int(data.get("retry_backoff") or 30),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
            last_run_at=str(data.get("last_run_at") or ""),
            last_status=str(data.get("last_status") or "idle"),
            last_run_id=str(data.get("last_run_id") or ""),
            last_error=str(data.get("last_error") or ""),
            run_count=int(data.get("run_count") or 0),
            next_run_at=str(data.get("next_run_at") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


class ScheduleError(Exception):
    """Base schedule error."""


def validate(schedule: Schedule, *, timezone_ok: Any, cron_ok: Any) -> list[str]:
    """Structural validation; returns a list of error strings ([] = valid)."""
    errors: list[str] = []
    if not schedule.id:
        errors.append("id is required")
    elif len(schedule.id) > MAX_ID:
        errors.append(f"id exceeds {MAX_ID} characters")
    elif not _ID_RE.match(schedule.id):
        errors.append("id must be lowercase alphanumeric with single hyphen separators")
    if not schedule.name.strip():
        errors.append("name is required")
    elif len(schedule.name) > MAX_NAME:
        errors.append(f"name exceeds {MAX_NAME} characters")
    if schedule.target_type not in TARGET_TYPES:
        errors.append(f"invalid target_type: {schedule.target_type}")
    if schedule.target_type == "workflow" and not schedule.workflow.strip():
        errors.append("workflow target requires a workflow name")
    if schedule.target_type == "command" and not schedule.command.strip():
        errors.append("command target requires a command")
    if schedule.target_type == "agent" and not schedule.prompt.strip():
        errors.append("agent target requires a prompt")
    if schedule.overlap not in OVERLAP_POLICIES:
        errors.append(f"invalid overlap policy: {schedule.overlap}")
    if schedule.misfire not in MISFIRE_POLICIES:
        errors.append(f"invalid misfire policy: {schedule.misfire}")
    if not cron_ok(schedule.cron):
        errors.append(f"invalid cron expression: {schedule.cron!r}")
    if not timezone_ok(schedule.timezone):
        errors.append(f"unknown timezone: {schedule.timezone!r}")
    if schedule.grace_seconds < 0:
        errors.append("grace_seconds must be >= 0")
    if schedule.retry_max < 0 or schedule.retry_max > 10:
        errors.append("retry_max must be between 0 and 10")
    if schedule.timeout_seconds < 0:
        errors.append("timeout_seconds must be >= 0")
    return errors


def utc_now_iso() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()
