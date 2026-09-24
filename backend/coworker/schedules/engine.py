"""Cron scheduling engine.

A single async loop sleeps until the soonest ``next_run_at`` (capped so it
reacts to edits), then fires due schedules respecting overlap and misfire
policies. ``next_run_at`` is persisted, so restarts are honest about missed runs.

Master switch: ``workflow_feature`` (Settings toggle / env). When off the loop
still ticks but fires nothing, so toggling takes effect within a tick.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from coworker.logger import get_logger

from . import cron as cronlib
from .model import Schedule
from .runner import ScheduleRunner
from .store import ScheduleStore

logger = get_logger(__name__)

MAX_CATCHUP = 50


class ScheduleEngine:
    def __init__(
        self,
        store: ScheduleStore,
        runner: ScheduleRunner,
        *,
        tick_cap_seconds: int = 30,
    ):
        self.store = store
        self.runner = runner
        self.tick_cap_seconds = max(5, int(tick_cap_seconds))
        self._running: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._stop = False

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _master_enabled() -> bool:
        from coworker.workflow_feature import workflow_feature

        return workflow_feature.is_enabled()

    def _advance(self, schedule: Schedule, after: datetime) -> None:
        try:
            schedule.next_run_at = cronlib.iso(cronlib.next_run(schedule.cron, schedule.timezone, after))
        except Exception:  # noqa: BLE001 - invalid cron leaves next_run_at empty
            schedule.next_run_at = ""

    def ensure_next(self, schedule: Schedule, base: datetime | None = None) -> Schedule:
        """Compute/persist ``next_run_at`` when missing or stale."""
        if not schedule.enabled or not cronlib.cron_ok(schedule.cron):
            schedule.next_run_at = ""
            return schedule
        # Only fill in a missing/invalid next_run_at. A PAST next_run_at is a
        # real overdue occurrence and must be left for _process to apply the
        # misfire policy — advancing here would silently erase missed runs.
        anchor = cronlib.parse_iso(schedule.next_run_at)
        now = base or datetime.now(timezone.utc)
        if anchor is None:
            self._advance(schedule, now)
        return schedule

    def list_with_next(self) -> list[Schedule]:
        schedules = self.store.list()
        changed = False
        for schedule in schedules:
            before = schedule.next_run_at
            self.ensure_next(schedule)
            if schedule.next_run_at != before:
                self.store.save(schedule)
                changed = True
        _ = changed
        return schedules

    # ── firing ──────────────────────────────────────────────────────────

    async def _run(self, schedule_id: str) -> None:
        schedule = self.store.get(schedule_id)
        if schedule is None:
            return
        schedule.last_status = "running"
        self.store.save(schedule)

        result: dict[str, Any] = {"status": "failed", "error": "not executed"}
        attempts = max(0, schedule.retry_max) + 1
        for attempt in range(attempts):
            result = await self.runner.execute(schedule)
            if result.get("status") == "ok":
                break
            if attempt < attempts - 1 and schedule.retry_backoff > 0:
                await asyncio.sleep(min(schedule.retry_backoff, 300))

        latest = self.store.get(schedule_id) or schedule
        latest.last_run_at = cronlib.iso(datetime.now(timezone.utc))
        latest.last_status = str(result.get("status", "failed"))
        latest.last_run_id = str(result.get("run_id", ""))
        latest.last_error = str(result.get("error", ""))
        latest.run_count = (latest.run_count or 0) + 1
        self.store.save(latest)
        try:
            self.store.append_run(
                schedule_id,
                {
                    "at": latest.last_run_at,
                    "status": latest.last_status,
                    "run_id": latest.last_run_id,
                    "error": latest.last_error,
                    "output": str(result.get("output", ""))[:1000],
                    "trigger": "schedule",
                },
            )
        except Exception:  # noqa: BLE001 - history persistence is best-effort
            pass
        logger.info("schedule %s -> %s", schedule_id, latest.last_status)

    async def fire(self, schedule: Schedule, *, mark_skipped: bool = False) -> None:
        if mark_skipped:
            schedule.last_status = "skipped"
            schedule.last_run_at = cronlib.iso(datetime.now(timezone.utc))
            self.store.save(schedule)
            return

        existing = self._running.get(schedule.id)
        if existing is not None and not existing.done():
            policy = schedule.overlap
            if policy == "skip":
                schedule.last_status = "skipped"
                schedule.last_run_at = cronlib.iso(datetime.now(timezone.utc))
                self.store.save(schedule)
                return
            if policy == "replace":
                existing.cancel()
            elif policy == "queue":
                lock = self._locks.setdefault(schedule.id, asyncio.Lock())
                async with lock:
                    await self._run(schedule.id)
                return
        self._running[schedule.id] = asyncio.create_task(self._run(schedule.id))

    async def _process(self, schedule: Schedule, now: datetime) -> None:
        anchor = cronlib.parse_iso(schedule.next_run_at)
        if anchor is None:
            self.ensure_next(schedule, now)
            self.store.save(schedule)
            return
        if anchor > now:
            return
        overdue = (now - anchor).total_seconds()
        if overdue > max(0, schedule.grace_seconds):
            if schedule.misfire == "skip":
                self._advance(schedule, now)
                self.store.save(schedule)
                await self.fire(schedule, mark_skipped=True)
                return
            if schedule.misfire == "catchup":
                count = 0
                cursor = anchor
                while cursor <= now and count < MAX_CATCHUP:
                    self._advance(schedule, cursor)
                    self.store.save(schedule)
                    await self.fire(schedule)
                    cursor = cronlib.parse_iso(schedule.next_run_at) or now
                    count += 1
                return
            # run_once
        self._advance(schedule, max(anchor, now))
        self.store.save(schedule)
        await self.fire(schedule)

    async def tick(self, now: datetime | None = None) -> float:
        """Run due schedules; return seconds until the next one is due."""
        now = now or datetime.now(timezone.utc)
        schedules = self.store.list()
        next_times: list[datetime] = []
        for schedule in schedules:
            if not schedule.enabled or not cronlib.cron_ok(schedule.cron):
                continue
            self.ensure_next(schedule, now)
            if self._master_enabled():
                await self._process(schedule, now)
            refreshed = self.store.get(schedule.id)
            if refreshed is not None:
                schedule = refreshed
            nr = cronlib.parse_iso(schedule.next_run_at)
            if nr is not None:
                next_times.append(nr)
        if not next_times:
            return float(self.tick_cap_seconds)
        soonest = min(next_times)
        return max(0.0, (soonest - now).total_seconds())

    async def loop(self) -> None:
        logger.info("schedule engine started")
        while not self._stop:
            delay = float(self.tick_cap_seconds)
            try:
                delay = await self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop must survive any failure
                logger.warning("schedule tick failed: %s", exc)
            await asyncio.sleep(cronlib.clamp_sleep(delay, self.tick_cap_seconds))

    def stop(self) -> None:
        self._stop = True
