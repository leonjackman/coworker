"""Per-step run state (observability) persisted to workflows/.state.json."""

from __future__ import annotations

import json
from pathlib import Path

from coworker.workflows import WorkflowManager
from coworker.workflows.env import build_tool_environment
from coworker.workspace import Workspace

FLOW = """
name: state-flow
description: d
steps:
- id: id:1
  kind: command
  do: run
  params: {command: ["python", "-c", "print('hi')"]}
  description: run
- id: id:2
  kind: file
  do: exists
  params: {path: /tmp/does-not-exist-xyz}
  description: check
  post: [result.exists]
"""


def test_state_file_records_step_status_and_origin(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(FLOW)
    env = build_tool_environment(workspace=Workspace(tmp_path), tools=[], data_dir=tmp_path)
    result = mgr.run("state-flow", env=env)
    assert result["status"] == "failed"  # id:2 post fails (file missing)

    state_path = mgr.state_path
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))[("state-flow")]
    assert state["id:1"]["status"] == "ok"
    assert state["id:1"]["origin"] == "user"
    assert state["id:2"]["status"] == "failed"
    assert state["id:2"]["resolved"] is True

    # Exposed through get() for the Studio.
    detail = mgr.get("state-flow")
    assert detail["state"]["id:1"]["status"] == "ok"
