"""API + agent-tool integration tests for the Workflow capability."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

_TMP = tempfile.mkdtemp(prefix="cw_workflow_api_")
os.environ["COWORKER_DATA_DIR"] = _TMP
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coworker.agent.graph import build_workspace_tools  # noqa: E402
from coworker.workspace import Workspace  # noqa: E402

FLOW = """name: api-flow
description: flow used by the API integration test
version: 1
inputs:
  who: world
steps:
  - id: greet
    kind: set
    name: greeting
    value: "hi {{inputs.who}}"
  - id: check
    kind: assert
    do: "equals vars.greeting hi api"
"""


def teardown_module(module):
    shutil.rmtree(_TMP, ignore_errors=True)


def _client():
    return TestClient(main.app)


def test_api_create_get_run():
    client = _client()
    # The backend singletons are shared across the test session, so start clean.
    client.delete("/workflows/api-flow")
    created = client.post("/workflows", json={"content": FLOW})
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "ok"

    got = client.get("/workflows/api-flow")
    assert got.status_code == 200
    assert got.json()["workflow"]["name"] == "api-flow"

    run = client.post("/workflows/api-flow/run", json={"inputs": {"who": "api"}})
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "ok"
    assert body["run"]["context"]["vars"]["greeting"] == "hi api"

    listed = client.get("/workflows")
    assert any(w["name"] == "api-flow" for w in listed.json()["workflows"])

    runs = client.get("/workflows/api-flow/runs")
    assert runs.status_code == 200
    assert len(runs.json()["runs"]) >= 1


def test_api_validate_and_render():
    client = _client()
    good = client.post("/workflows/validate", json={"content": FLOW})
    assert good.json()["valid"] is True
    bad = client.post("/workflows/validate", json={"content": "name: x\ndescription: ''\n"})
    assert bad.json()["valid"] is False
    rendered = client.post("/workflows/render", json={"content": FLOW})
    assert rendered.status_code == 200
    assert rendered.json()["status"] == "ok"


def test_api_draft_lifecycle():
    client = _client()
    draft = """name: api-draft
description: draft from the agent
provenance:
  action: create
steps:
  - id: a
    kind: set
    name: k
    value: v
"""
    main.workflow_manager.reject_pending("api-draft")
    _client().delete("/workflows/api-draft")
    stage = main.workflow_manager.stage_draft("api-draft", draft, sources=["session:test"])
    assert stage["status"] == "ok"
    pending = client.get("/workflows/pending").json()["pending"]
    assert any(p["name"] == "api-draft" for p in pending)
    approve = client.post("/workflows/pending/api-draft/approve")
    assert approve.status_code == 200
    assert client.get("/workflows/api-draft").status_code == 200


def test_agent_tool_list_and_run():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp), audit_path=Path(tmp) / "audit.jsonl")
        from coworker.workflows import WorkflowManager

        manager = WorkflowManager(Path(tmp) / "data")
        manager.create(FLOW)
        tools = build_workspace_tools(ws, workflow_manager=manager)
        workflow_tool = next((t for t in tools if getattr(t, "name", "") == "workflow"), None)
        assert workflow_tool is not None

        listed = json.loads(workflow_tool.invoke({"action": "list"}))
        assert any(w["name"] == "api-flow" for w in listed["workflows"])

        ran = json.loads(workflow_tool.invoke({"action": "run", "name": "api-flow", "inputs": {"who": "api"}}))
        assert ran["status"] == "ok"


def test_api_export_versions_rollback():
    client = _client()
    client.delete("/workflows/api-flow")
    client.post("/workflows", json={"content": FLOW})
    # Force a second version.
    client.put("/workflows/api-flow", json={"content": FLOW.replace("used by the API integration test", "v2")})
    versions = client.get("/workflows/api-flow/versions")
    assert versions.status_code == 200
    assert any(v["version"] == 1 for v in versions.json()["versions"])

    exported = client.get("/workflows/api-flow/export")
    assert exported.status_code == 200
    assert "api-flow" in exported.json()["yaml"]

    rolled = client.post("/workflows/api-flow/rollback", json={"version": 1})
    assert rolled.status_code == 200
    assert rolled.json()["rolled_back_to"] == 1

    imported = client.post("/workflows/import", json={"content": FLOW, "overwrite": True})
    assert imported.status_code == 200


def test_api_record_stages_generalized_draft():
    client = _client()
    client.post("/workflows/pending/recorded-flow/reject")
    payload = {
        "name": "recorded-flow",
        "description": "captured procedure",
        "inputs": [{"name": "q", "default": "hello"}],
        "steps": [
            {"kind": "command", "do": "echo hello"},
            {"kind": "set", "params": {"name": "seen", "value": "hello"}},
        ],
        "sources": ["session:test"],
    }
    result = client.post("/workflows/record", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ok"
    pending = client.get("/workflows/pending").json()["pending"]
    draft = next((d for d in pending if d["name"] == "recorded-flow"), None)
    assert draft is not None
    assert "{{inputs.q}}" in draft["content"]


def test_agent_prompt_block_present():
    block = main.workflow_manager.prompt_block()
    assert "available_workflows" in block or block == ""
