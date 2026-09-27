"""Event triggers: webhook + file_watch schedules."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

from coworker.schedules import ScheduleManager


class FakeRunner:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, schedule):
        self.calls.append(schedule.id)
        return {"status": "ok", "output": "done"}


def _manager(tmp):
    return ScheduleManager(Path(tmp), FakeRunner())


def test_webhook_schedule_gets_token_and_triggers():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(tmp)
        created = manager.create({"name": "Hook", "trigger_type": "webhook", "workflow": "w"})
        assert created["status"] == "ok", created
        sched = created["schedule"]
        assert sched["trigger_type"] == "webhook"
        assert sched["webhook_token"]
        assert sched["next_run_at"] == ""

        result = asyncio.run(manager.trigger_webhook(sched["id"]))
        assert result["status"] == "ok"
        assert manager.runner.calls == [sched["id"]]


def test_file_watch_fires_on_change(monkeypatch):
    monkeypatch.setattr(
        "coworker.schedules.engine.ScheduleEngine._master_enabled", staticmethod(lambda: True)
    )
    with tempfile.TemporaryDirectory() as tmp:
        watch_dir = Path(tmp) / "watched"
        watch_dir.mkdir()
        file_path = watch_dir / "data.txt"
        file_path.write_text("one")

        manager = _manager(tmp)
        created = manager.create(
            {
                "name": "Watch",
                "trigger_type": "file_watch",
                "workflow": "w",
                "watch_path": str(watch_dir),
                "watch_pattern": "*.txt",
            }
        )
        assert created["status"] == "ok", created
        sid = created["schedule"]["id"]

        # First tick establishes the baseline (no fire).
        asyncio.run(manager.engine.tick())
        assert manager.runner.calls == []

        # Touch the file; the next tick fires exactly once.
        time.sleep(0.01)
        os.utime(file_path, None)
        asyncio.run(manager.engine.tick())
        assert manager.runner.calls == [sid]
        asyncio.run(manager.engine.tick())
        assert manager.runner.calls == [sid]


def test_file_watch_requires_path():
    with tempfile.TemporaryDirectory() as tmp:
        manager = _manager(tmp)
        result = manager.create({"name": "Bad", "trigger_type": "file_watch", "workflow": "w"})
        assert result["status"] == "error"
        assert "watch_path" in result["message"]



