"""Intent-only nodes: the agent resolves the BINDING, verifies, and persists it.

A deterministic-capable step that states only its goal (no ``do``) is handed to
the environment's binding resolver. On success the resolved ``do``/``params``/
``locator`` (and ``origin=agent``) are written back to the workflow. 絕對遵守
nodes are exempt.
"""

from __future__ import annotations

from pathlib import Path

from coworker.workflows import WorkflowManager


class ResolveEnv:
    """Resolves a binding for a UI-transform step and records calls."""

    def __init__(self, binding=None):
        self.binding = binding or {"do": "template", "params": {"text": "hi"}}
        self.resolved = 0
        self.action_calls: list = []

    def supports_resolve(self) -> bool:
        return True

    def resolve_binding(self, step, context):
        self.resolved += 1
        return dict(self.binding)

    def action(self, kind, action, payload):
        self.action_calls.append((kind, action, payload))
        return {"result": "hi"}

    def supports_agentic(self) -> bool:
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


FLOW = """
name: resolve-flow
description: d
steps:
- id: id:1
  kind: transform
  intent:
    what: 生成问候文本
  binding: {}
"""

FLOW_ABS = """
name: resolve-abs
description: d
steps:
- id: id:1
  kind: transform
  intent:
    what: 生成问候文本
    absolute: true
  binding: {}
"""


def test_intent_only_step_is_resolved_and_persisted(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(FLOW)
    env = ResolveEnv()
    result = mgr.run("resolve-flow", env=env)
    assert result["status"] == "ok", result.get("run", {}).get("error")
    assert env.resolved == 1
    assert env.action_calls and env.action_calls[0][1] == "template"

    # The binding was written back to the stored workflow with origin=agent.
    wf = mgr.store.get("resolve-flow")
    step = wf.steps[0]
    assert step.do == "template"
    assert step.params.get("text") == "hi"
    assert step.origin == "agent"


def test_absolute_intent_only_step_is_not_resolved(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    # 絕對遵守 with no binding is invalid to author (saved as a draft here).
    mgr.create(FLOW_ABS, draft=True)
    env = ResolveEnv()
    result = mgr.run("resolve-abs", env=env)
    assert result["status"] == "failed"
    assert env.resolved == 0  # 絕對遵守: never auto-filled
