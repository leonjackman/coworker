"""API + integration tests for independent cron schedules."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

_TMP = tempfile.mkdtemp(prefix="cw_schedules_api_")
os.environ["COWORKER_DATA_DIR"] = _TMP
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FLOW = """name: sched-flow
description: workflow used by schedule tests
steps:
  - id: a
    kind: set
    name: k
    value: v
"""


def teardown_module(module):
    shutil.rmtree(_TMP, ignore_errors=True)


def _client():
    return TestClient(main.app)


def test_schedule_crud_preview_and_run():
    client = _client()
    client.delete("/schedules/echo-job")

    created = client.post(
        "/schedules",
        json={
            "name": "Echo Job",
            "target_type": "command",
            "command": "echo hello-scheduler",
            "cron": "0 2 * * *",
            "timezone": "UTC",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "ok"
    schedule = created.json()["schedule"]
    assert schedule["id"] == "echo-job"
    assert schedule["next_run_at"]

    got = client.get("/schedules/echo-job")
    assert got.status_code == 200

    preview = client.post("/schedules/preview", json={"cron": "0 10 * * *", "timezone": "UTC", "count": 3})
    assert preview.status_code == 200
    assert len(preview.json()["runs"]) == 3

    bad = client.post("/schedules/validate", json={"cron": "nope", "timezone": "UTC"})
    assert bad.json()["valid"] is False

    disabled = client.post("/schedules/echo-job/disable")
    assert disabled.json()["schedule"]["enabled"] is False
    enabled = client.post("/schedules/echo-job/enable")
    assert enabled.json()["schedule"]["enabled"] is True

    run = client.post("/schedules/echo-job/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "ok"

    runs = client.get("/schedules/echo-job/runs")
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["trigger"] == "manual"

    deleted = client.delete("/schedules/echo-job")
    assert deleted.json()["removed"] is True
    assert client.get("/schedules/echo-job").status_code == 404


def test_schedule_workflow_target_runs():
    client = _client()
    client.delete("/workflows/sched-flow")
    assert client.post("/workflows", json={"content": FLOW}).status_code == 200
    client.delete("/schedules/run-flow")

    created = client.post(
        "/schedules",
        json={
            "name": "Run Flow",
            "target_type": "workflow",
            "workflow": "sched-flow",
            "cron": "*/30 * * * *",
            "timezone": "UTC",
        },
    )
    assert created.status_code == 200, created.text

    run = client.post("/schedules/run-flow/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "ok"
    assert run.json()["result"]["run_id"]

    client.delete("/schedules/run-flow")
