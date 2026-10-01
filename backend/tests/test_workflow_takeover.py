"""Agent takeover: (P4) write the working method back; (P5) default failure policy."""

from __future__ import annotations

from pathlib import Path

from coworker.workflows import WorkflowManager
from coworker.workflows.settings import write_workflow_settings


class TakeoverEnv:
    """A step fails; the agent takes over and reports a reusable BINDING."""

    def __init__(self, binding='{"do": "template", "params": {"text": "hi"}}'):
        self.binding = binding
        self.agentic_calls = 0

    def action(self, kind, action, payload):
        raise RuntimeError("boom")

    def supports_agentic(self) -> bool:
        return True

    def agentic(self, prompt, step, autonomy=None):
        self.agentic_calls += 1
        return {"output": f"did it\nBINDING: {self.binding}\nVERDICT: DONE"}

    def supports_resolve(self) -> bool:
        return False

    def self_heal(self, *a, **k):
        return None

    def evidence(self, *a, **k):
        return None

    def human(self, *a, **k):
        return True

    def _blow(self):
        raise NotImplementedError("not used")

    command = tool = browser = app = skill = _blow


FLOW_EXPLICIT = """
name: takeover-flow
description: d
steps:
- id: id:1
  kind: transform
  do: template
  params: {text: x}
  on_error: {then: agent}
"""

FLOW_DEFAULT = """
name: default-flow
description: d
steps:
- id: id:1
  kind: transform
  do: template
  params: {text: x}
"""


def test_takeover_success_writes_binding_back(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(FLOW_EXPLICIT)
    env = TakeoverEnv()
    result = mgr.run("takeover-flow", env=env)
    assert result["status"] == "ok", result.get("run", {}).get("error")
    assert env.agentic_calls == 1
    # The concrete method was written back with origin=agent.
    step = mgr.store.get("takeover-flow").steps[0]
    assert step.params.get("text") == "hi"
    assert step.origin == "agent"


def test_default_policy_abort_does_not_take_over(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(FLOW_DEFAULT)
    env = TakeoverEnv()
    result = mgr.run("default-flow", env=env)
    assert result["status"] == "failed"
    assert env.agentic_calls == 0


def test_default_policy_agent_takes_over(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(FLOW_DEFAULT)
    write_workflow_settings(mgr.store.root.parent, {"default_on_error": "agent"})
    env = TakeoverEnv()
    result = mgr.run("default-flow", env=env)
    assert result["status"] == "ok", result.get("run", {}).get("error")
    assert env.agentic_calls == 1
