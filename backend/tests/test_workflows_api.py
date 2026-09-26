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


def test_api_resume_needs_human():
    client = _client()
    client.delete("/workflows/gate-flow")
    flow = """name: gate-flow
description: pauses for approval
steps:
  - id: gate
    kind: set
    approval: true
    params:
      name: ok
      value: "yes"
"""
    assert client.post("/workflows", json={"content": flow}).status_code == 200
    run = client.post("/workflows/gate-flow/run", json={})
    body = run.json()
    assert body["status"] == "needs_human"
    run_id = body["run"]["run_id"]
    assert body["run"]["pending_step"] == "gate"
    resumed = client.post(f"/workflows/runs/{run_id}/resume", json={"decisions": {"gate": True}})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "ok"


def test_workflow_templates_install():
    client = _client()
    listed = client.get("/workflows/templates")
    assert listed.status_code == 200
    templates = listed.json()["templates"]
    assert len(templates) >= 3
    ids = {tpl["id"] for tpl in templates}
    assert "browser-publish-generic" in ids

    client.delete("/workflows/web-research-report")
    installed = client.post("/workflows/templates/web-research-report/install")
    assert installed.status_code == 200, installed.text
    assert installed.json()["status"] == "ok"
    detail = client.get("/workflows/web-research-report").json()["workflow"]
    # Installed template is renumbered to the system id scheme (id:1, id:2, …).
    assert [s["id"] for s in detail["steps"]] == ["id:1", "id:2"]
    client.delete("/workflows/web-research-report")


def test_api_render_steps_for_visual_editor():
    client = _client()
    result = client.post(
        "/workflows/render/steps",
        json={
            "name": "viz-api-flow",
            "description": "rendered from nodes",
            "steps": [
                {"id": "open", "kind": "set", "params": {"name": "k", "value": "v"}},
                {"id": "act", "kind": "tool", "do": "web_fetch", "mode": "agent", "goal": "do it",
                 "success": ["ok"], "on_error": {"then": "human"}},
            ],
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "ok"
    assert "mode: agent" in body["yaml"]
    assert "goal: do it" in body["yaml"]


def test_api_duplicate_workflow():
    client = _client()
    client.delete("/workflows/api-flow")
    client.delete("/workflows/api-flow-copy")
    client.post("/workflows", json={"content": FLOW})
    dup = client.post("/workflows/api-flow/duplicate", json={"new_name": "api-flow-copy"})
    assert dup.status_code == 200, dup.text
    assert dup.json()["name"] == "api-flow-copy"
    assert client.get("/workflows/api-flow-copy").status_code == 200
    client.delete("/workflows/api-flow")
    client.delete("/workflows/api-flow-copy")


def test_api_versions_list_read_delete():
    client = _client()
    client.delete("/workflows/ver-flow")
    client.post("/workflows", json={"content": FLOW.replace("api-flow", "ver-flow")})
    # bump to v2, v3
    client.put("/workflows/ver-flow", json={"content": FLOW.replace("api-flow", "ver-flow").replace("hi api", "hi v2")})
    client.put("/workflows/ver-flow", json={"content": FLOW.replace("api-flow", "ver-flow").replace("hi api", "hi v3")})

    versions = client.get("/workflows/ver-flow/versions").json()["versions"]
    numbers = {v["version"]: v["is_current"] for v in versions}
    assert numbers.get(3) is True  # current
    assert numbers.get(1) is False and numbers.get(2) is False

    one = client.get("/workflows/ver-flow/versions/1")
    assert one.status_code == 200
    assert one.json()["workflow"]["is_current"] is False
    assert "hi api" in one.json()["workflow"]["yaml"]

    # cannot delete the in-use version
    blocked = client.delete("/workflows/ver-flow/versions/3")
    assert blocked.status_code == 400
    # can delete an archived one
    removed = client.delete("/workflows/ver-flow/versions/1")
    assert removed.status_code == 200
    assert removed.json()["removed"] is True

    client.delete("/workflows/ver-flow")


def test_agent_prompt_block_present():
    block = main.workflow_manager.prompt_block()
    assert "available_workflows" in block or block == ""
