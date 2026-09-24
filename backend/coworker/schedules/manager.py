"""ScheduleManager: facade over the store, cron math and engine for API/tools."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import cron as cronlib
from .engine import ScheduleEngine
from .model import (
    OVERLAP_POLICIES,
    MISFIRE_POLICIES,
    TARGET_TYPES,
    Schedule,
    ScheduleError,
    utc_now_iso,
    validate,
)
from .runner import ScheduleRunner
from .store import ScheduleStore

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "schedule"


class ScheduleManager:
    def __init__(self, data_dir: Path, runner: ScheduleRunner):
        self.store = ScheduleStore(Path(data_dir) / "schedules")
        self.runner = runner
        self.engine = ScheduleEngine(self.store, runner)

    # ── reads ───────────────────────────────────────────────────────────

    def list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.engine.list_with_next()]

    def get(self, schedule_id: str) -> dict[str, Any] | None:
        schedule = self.store.get(schedule_id)
        if schedule is None:
            return None
        self.engine.ensure_next(schedule)
        self.store.save(schedule)
        return schedule.to_dict()

    def preview(self, cron: str, timezone: str, count: int = 5) -> dict[str, Any]:
        if not cronlib.cron_ok(cron):
            return {"status": "error", "message": f"invalid cron: {cron!r}", "runs": []}
        if not cronlib.timezone_ok(timezone):
            return {"status": "error", "message": f"unknown timezone: {timezone!r}", "runs": []}
        return {
            "status": "ok",
            "description": cronlib.describe(cron),
            "runs": cronlib.preview(cron, timezone, count),
        }

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("name", "validation")
        data.setdefault("id", "validation")
        schedule = self._from_payload(data)
        # Partial validation: name/id are placeholders, never reported.
        errors = [e for e in self._validate(schedule) if not e.startswith(("name ", "name is", "id "))]
        result: dict[str, Any] = {"status": "ok" if not errors else "error", "valid": not errors, "errors": errors}
        if cronlib.cron_ok(schedule.cron):
            result["description"] = cronlib.describe(schedule.cron)
        return result

    # ── mutations ───────────────────────────────────────────────────────

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        schedule = self._from_payload(payload)
        if not schedule.id:
            schedule.id = self._unique_id(slugify(schedule.name))
        elif self.store.get(schedule.id) is not None:
            return {"status": "error", "message": f"schedule already exists: {schedule.id}"}
        schedule.created_at = utc_now_iso()
        schedule.updated_at = schedule.created_at
        errors = self._validate(schedule)
        if errors:
            return {"status": "error", "message": "; ".join(errors)}
        self.engine.ensure_next(schedule)
        self.store.save(schedule)
        return {"status": "ok", "schedule": schedule.to_dict()}

    def update(self, schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.store.get(schedule_id)
        if existing is None:
            return {"status": "error", "message": f"schedule not found: {schedule_id}"}
        incoming = self._from_payload(payload, base=existing)
        incoming.id = schedule_id
        incoming.created_at = existing.created_at
        incoming.updated_at = utc_now_iso()
        incoming.last_run_at = existing.last_run_at
        incoming.last_status = existing.last_status
        incoming.last_run_id = existing.last_run_id
        incoming.last_error = existing.last_error
        incoming.run_count = existing.run_count
        errors = self._validate(incoming)
        if errors:
            return {"status": "error", "message": "; ".join(errors)}
        # Recompute next run; if the user changed the cron/tz, next_run_at is stale.
        if (
            incoming.cron != existing.cron
            or incoming.timezone != existing.timezone
            or incoming.enabled != existing.enabled
            or not incoming.next_run_at
        ):
            incoming.next_run_at = ""
        self.engine.ensure_next(incoming)
        self.store.save(incoming)
        return {"status": "ok", "schedule": incoming.to_dict()}

    def delete(self, schedule_id: str) -> dict[str, Any]:
        if not self.store.delete(schedule_id):
            return {"status": "error", "message": f"schedule not found: {schedule_id}"}
        return {"status": "ok", "id": schedule_id, "removed": True}

    def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        schedule = self.store.get(schedule_id)
        if schedule is None:
            return {"status": "error", "message": f"schedule not found: {schedule_id}"}
        schedule.enabled = bool(enabled)
        schedule.updated_at = utc_now_iso()
        schedule.next_run_at = ""
        self.engine.ensure_next(schedule)
        self.store.save(schedule)
        return {"status": "ok", "schedule": schedule.to_dict()}

    async def run_now(self, schedule_id: str) -> dict[str, Any]:
        schedule = self.store.get(schedule_id)
        if schedule is None:
            return {"status": "error", "message": f"schedule not found: {schedule_id}"}
        result = await self.runner.execute(schedule)
        schedule.last_run_at = utc_now_iso()
        schedule.last_status = str(result.get("status", "failed"))
        schedule.last_run_id = str(result.get("run_id", ""))
        schedule.last_error = str(result.get("error", ""))
        schedule.run_count = (schedule.run_count or 0) + 1
        self.store.save(schedule)
        self.store.append_run(
            schedule_id,
            {
                "at": schedule.last_run_at,
                "status": schedule.last_status,
                "run_id": schedule.last_run_id,
                "error": schedule.last_error,
                "output": str(result.get("output", ""))[:1000],
                "trigger": "manual",
            },
        )
        return {"status": result.get("status", "failed"), "result": result, "schedule": schedule.to_dict()}

    def list_runs(self, schedule_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.read_runs(schedule_id, limit)

    # ── helpers ─────────────────────────────────────────────────────────

    def _unique_id(self, base: str) -> str:
        candidate = base
        index = 2
        while self.store.get(candidate) is not None:
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def _validate(self, schedule: Schedule) -> list[str]:
        return validate(schedule, timezone_ok=cronlib.timezone_ok, cron_ok=cronlib.cron_ok)

    def _from_payload(self, payload: dict[str, Any], base: Schedule | None = None) -> Schedule:
        current = base.to_dict() if base is not None else {}
        merged = {**current, **{k: v for k, v in payload.items() if v is not None}}
        return Schedule.from_dict(merged)


__all__ = ["ScheduleManager", "ScheduleError", "slugify"]
