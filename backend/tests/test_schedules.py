"""Tests for the independent cron scheduling capability."""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker.schedules import Schedule, ScheduleEngine, ScheduleManager, ScheduleStore  # noqa: E402
from coworker.schedules import cron as cronlib  # noqa: E402

UTC = timezone.utc


class FakeRunner:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, schedule):
        self.calls.append(schedule.id)
        return {"status": "ok", "output": "done"}


def test_cron_validation_and_description():
    assert cronlib.cron_ok("0 10 * * *")
    assert not cronlib.cron_ok("bad")
    assert not cronlib.cron_ok("0 10 * * * *")  # 6 fields rejected (no seconds)
    assert cronlib.describe("0 10 * * *") == "Every day at 10:00"
    assert "minute" in cronlib.describe("*/15 * * * *").lower()
    assert cronlib.timezone_ok("Asia/Shanghai")
    assert not cronlib.timezone_ok("Mars/Olympus")


def test_cron_preview_is_timezone_aware():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    runs = cronlib.preview("0 10 * * *", "Asia/Shanghai", 3, base=base)
    assert runs[0].startswith("2026-01-01T10:00")
    assert len(runs) == 3


def test_store_crud_and_run_log():
    with tempfile.TemporaryDirectory() as tmp:
        store = ScheduleStore(Path(tmp))
        schedule = Schedule(id="s1", name="S1", target_type="command", command="echo hi")
        store.save(schedule)
        assert store.get("s1").name == "S1"
        assert [s.id for s in store.list()] == ["s1"]
        store.append_run("s1", {"at": "now", "status": "ok"})
        assert store.read_runs("s1")[0]["status"] == "ok"
        assert store.delete("s1") is True
        assert store.get("s1") is None
        assert store.read_runs("s1") == []


def test_manager_create_update_enable_delete(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        runner = FakeRunner()
        manager = ScheduleManager(Path(tmp), runner)
        created = manager.create(
            {"name": "Nightly Report", "target_type": "command", "command": "echo hi", "cron": "0 2 * * *", "timezone": "UTC"}
        )
        assert created["status"] == "ok"
        sid = created["schedule"]["id"]
        assert sid == "nightly-report"
        assert created["schedule"]["next_run_at"]  # computed

        assert manager.validate_payload({"cron": "bad"})["valid"] is False
        assert manager.validate_payload({"cron": "0 2 * * *", "timezone": "UTC", "target_type": "command", "command": "x"})["valid"] is True

        updated = manager.update(sid, {"cron": "30 3 * * *"})
        assert updated["status"] == "ok"

        disabled = manager.set_enabled(sid, False)
        assert disabled["schedule"]["enabled"] is False
        assert disabled["schedule"]["next_run_at"] == ""

        assert manager.delete(sid)["status"] == "ok"
        assert manager.get(sid) is None


def test_manager_run_now_executes_and_logs():
    with tempfile.TemporaryDirectory() as tmp:
        runner = FakeRunner()
        manager = ScheduleManager(Path(tmp), runner)
        sid = manager.create({"name": "R", "target_type": "command", "command": "echo hi"})["schedule"]["id"]
        result = asyncio.run(manager.run_now(sid))
        assert result["status"] == "ok"
        assert runner.calls == [sid]
        assert manager.list_runs(sid)[0]["trigger"] == "manual"


def test_engine_fires_due_and_advances(monkeypatch):
    monkeypatch.setenv("COWORKER_WORKFLOW_SCHEDULER", "1")
    with tempfile.TemporaryDirectory() as tmp:
        store = ScheduleStore(Path(tmp))
        runner = FakeRunner()
        engine = ScheduleEngine(store, runner)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        schedule = Schedule(
            id="s1", name="S1", target_type="command", command="echo hi",
            cron="0 10 * * *", timezone="UTC", enabled=True,
            misfire="run_once", next_run_at=(now - timedelta(seconds=5)).isoformat(),
        )
        store.save(schedule)
        asyncio.run(engine.tick(now))
        assert runner.calls == ["s1"]
        refreshed = store.get("s1")
        assert refreshed.last_status == "ok"
        # next run advanced into the future
        assert cronlib.parse_iso(refreshed.next_run_at) > now


def test_engine_misfire_skip(monkeypatch):
    monkeypatch.setenv("COWORKER_WORKFLOW_SCHEDULER", "1")
    with tempfile.TemporaryDirectory() as tmp:
        store = ScheduleStore(Path(tmp))
        runner = FakeRunner()
        engine = ScheduleEngine(store, runner)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        schedule = Schedule(
            id="s1", name="S1", target_type="command", command="echo hi",
            cron="0 10 * * *", timezone="UTC", enabled=True,
            misfire="skip", grace_seconds=0,
            next_run_at=(now - timedelta(hours=2)).isoformat(),
        )
        store.save(schedule)
        asyncio.run(engine.tick(now))
        assert runner.calls == []  # missed run skipped
        assert store.get("s1").last_status == "skipped"


def test_engine_master_switch_off(monkeypatch):
    monkeypatch.setenv("COWORKER_WORKFLOW_SCHEDULER", "0")
    with tempfile.TemporaryDirectory() as tmp:
        store = ScheduleStore(Path(tmp))
        runner = FakeRunner()
        engine = ScheduleEngine(store, runner)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        store.save(
            Schedule(
                id="s1", name="S1", target_type="command", command="echo hi",
                cron="0 10 * * *", timezone="UTC", enabled=True, misfire="run_once",
                next_run_at=(now - timedelta(seconds=1)).isoformat(),
            )
        )
        asyncio.run(engine.tick(now))
        assert runner.calls == []
