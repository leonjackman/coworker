"""Disk-backed schedule store: ``<data_dir>/schedules/<id>.json``."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from coworker.atomicio import atomic_write_text

from .model import Schedule


class ScheduleStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, schedule_id: str) -> Path:
        return self.root / f"{schedule_id}.json"

    def list(self) -> list[Schedule]:
        schedules: list[Schedule] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            schedule = Schedule.from_dict(data)
            if schedule.id:
                schedules.append(schedule)
        return schedules

    def get(self, schedule_id: str) -> Schedule | None:
        path = self.path_for(schedule_id)
        if not path.is_file():
            return None
        try:
            return Schedule.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, schedule: Schedule) -> Schedule:
        with self._lock:
            atomic_write_text(
                self.path_for(schedule.id),
                json.dumps(schedule.to_dict(), ensure_ascii=False, indent=2),
            )
        return schedule

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            path = self.path_for(schedule_id)
            if not path.is_file():
                return False
            try:
                path.unlink()
            except OSError:
                return False
            runs = self.runs_path(schedule_id)
            if runs.is_file():
                try:
                    runs.unlink()
                except OSError:
                    pass
            return True

    # ── run log (per-schedule JSONL history) ────────────────────────────

    def runs_path(self, schedule_id: str) -> Path:
        return self.root / f"{schedule_id}.runs.jsonl"

    def append_run(self, schedule_id: str, record: dict) -> None:
        import json

        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.runs_path(schedule_id).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_runs(self, schedule_id: str, limit: int = 50) -> list[dict]:
        import json

        path = self.runs_path(schedule_id)
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
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
