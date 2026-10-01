"""Failures must surface the REAL adapter error, and takeover prompts must
include the step's own params (regression for the opaque
`computer step 'id:1' did not succeed (rule: no_error)` message)."""

from __future__ import annotations

from pathlib import Path

from coworker.workflows import WorkflowManager


class ErrEnv:
    """Minimal env: computer returns a bridge error envelope; optionally agentic."""

    def __init__(self, *, agentic: bool = False, verdict: str = "VERDICT: BLOCKED"):
        self._agentic = agentic
        self._verdict = verdict
        self.prompts: list[str] = []

    def supports_agentic(self) -> bool:
        return self._agentic

    def agentic(self, prompt, step):
        self.prompts.append(prompt)
        return {"output": f"cannot\n{self._verdict}"}

    def app(self, action, payload, locator):
        return {"error": "open -a failed for 家庭", "error_code": "launch_failed"}

    def self_heal(self, *a, **k):
        return None

    def evidence(self, *a, **k):
        return None

    def human(self, *a, **k):
        return True

    def _blow(self):
        raise NotImplementedError("not used")

    command = tool = browser = skill = _blow


FLOW_ABORT = """name: vis-fail
description: surfaces the real error
steps:
  - id: id:1
    kind: computer
    do: launch_app
    params:
      app: 家庭
    on_error:
      then: abort
"""

FLOW_AGENT = """name: vis-agent
description: takeover sees the step params
steps:
  - id: id:1
    kind: computer
    do: launch_app
    params:
      app: 家庭
    on_error:
      then: agent
"""


def test_failure_message_contains_real_error_and_is_recorded(tmp_path: Path):
    manager = WorkflowManager(tmp_path)
    manager.enforce_conformance = False
    manager.create(FLOW_ABORT)
    result = manager.run("vis-fail", env=ErrEnv())
    assert result["status"] == "failed"
    error = result["run"]["error"]
    assert "launch_failed" in error and "家庭" in error
    recorded = result["run"]["context"]["steps"]["id:1"]
    assert recorded["status"] == "failed"
    assert "launch_failed" in recorded["error"]


def test_takeover_prompt_includes_step_params(tmp_path: Path):
    manager = WorkflowManager(tmp_path)
    manager.enforce_conformance = False
    manager.create(FLOW_AGENT)
    env = ErrEnv(agentic=True)
    result = manager.run("vis-agent", env=env)
    assert result["status"] == "needs_human"
    assert env.prompts, "agent takeover should have been invoked"
    prompt = env.prompts[0]
    assert '"app": "家庭"' in prompt
    assert "launch_failed" in prompt
