"""絕對遵守 (absolute-obey): user-only hard constraint.

- Only user-authored workflows may set it (agent writes are rejected).
- The executor must NOT walk locator fallbacks, self-heal, or promote for an
  absolute step: the specified intent/binding is frozen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.workflows import WorkflowManager
from coworker.workflows.model import WorkflowValidationError
from coworker.workflows.parser import parse_workflow, render_workflow


class FallbackEnv:
    """Browser env whose PRIMARY locator fails but a fallback would work."""

    def __init__(self):
        self.browser_calls: list = []
        self.heal_calls = 0

    def supports_agentic(self) -> bool:
        return False

    def browser(self, action, payload, locator):
        self.browser_calls.append(locator)
        loc = locator or {}
        if loc.get("role"):
            raise RuntimeError("primary locator failed")
        return {"ok": True, "changed": True}

    def self_heal(self, *a, **k):
        self.heal_calls += 1
        return None

    def evidence(self, *a, **k):
        return None

    def human(self, *a, **k):
        return True

    def _blow(self):
        raise NotImplementedError("not used")

    command = tool = app = action = skill = _blow


FLOW_ABS = """name: abs-flow
description: absolute step must not be substituted
steps:
  - id: id:1
    kind: browser
    do: click
    description: tap export
    absolute: true
    params: {x: 1, y: 1}
    locator:
      role: button
      fallback:
        - name: 导出
    on_error:
      then: abort
"""

FLOW_SOFT = FLOW_ABS.replace("    absolute: true\n", "").replace("abs-flow", "soft-flow")


def _manager(tmp_path: Path) -> WorkflowManager:
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    return mgr


def test_absolute_parses_and_roundtrips(tmp_path: Path):
    wf, _ = parse_workflow(FLOW_ABS)
    assert wf.steps[0].absolute is True
    assert "absolute: true" in render_workflow(wf)


def test_agent_cannot_set_absolute(tmp_path: Path):
    mgr = _manager(tmp_path)
    with pytest.raises(WorkflowValidationError):
        mgr.create(FLOW_ABS, source="agent")
    # The user may set it.
    assert mgr.create(FLOW_ABS, source="user")["status"] == "ok"


def test_agent_cannot_modify_workflow_with_absolute(tmp_path: Path):
    mgr = _manager(tmp_path)
    mgr.create(FLOW_ABS, source="user")
    with pytest.raises(WorkflowValidationError):
        mgr.update("abs-flow", FLOW_ABS, source="agent")


def test_absolute_step_does_not_use_fallback(tmp_path: Path):
    mgr = _manager(tmp_path)
    mgr.create(FLOW_ABS, source="user")
    env = FallbackEnv()
    result = mgr.run("abs-flow", env=env)
    assert result["status"] == "failed"
    assert len(env.browser_calls) == 1  # primary only, no fallback
    assert env.heal_calls == 0


def test_soft_step_uses_fallback(tmp_path: Path):
    mgr = _manager(tmp_path)
    mgr.create(FLOW_SOFT, source="user")
    env = FallbackEnv()
    result = mgr.run("soft-flow", env=env)
    assert result["status"] == "ok"
    assert len(env.browser_calls) == 2  # primary then fallback
