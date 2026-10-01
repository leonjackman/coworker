"""Regression: the `workflow` tool must survive the phase gate.

Session 166ff5ee exposed a silent failure: the system prompt advertised the
`workflow` tool (via <available_workflows>), but PhaseToolGateMiddleware filtered
it out of the model's schema in every phase, so the agent could never call it and
improvised by writing a markdown file. The tool must be in the allowlist for both
discuss (plan) and execute (build/chat) phases.
"""

from __future__ import annotations

from types import SimpleNamespace

from coworker.agent.core import _EXEC_TOOLS, _MEMORY_TOOLS, _READ_ONLY_TOOLS
from coworker.agent.middleware.phase_gate import PhaseToolGateMiddleware


def _gate() -> PhaseToolGateMiddleware:
    return PhaseToolGateMiddleware(mcp_tool_names_provider=None, workspace=None)


def test_workflow_visible_in_execute_phase():
    allowed = _gate()._allowed_tools({"work_mode": "build", "phase": "execute", "autonomy": "guarded"})
    assert "workflow" in allowed


def test_workflow_visible_in_discuss_phase():
    allowed = _gate()._allowed_tools({"work_mode": "plan", "phase": "discuss", "autonomy": "guarded"})
    assert "workflow" in allowed


def test_phase_gate_override_keeps_workflow_tool():
    tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="workflow")]
    request = SimpleNamespace(
        state={"work_mode": "build", "phase": "execute", "autonomy": "autonomous"},
        tools=tools,
    )
    kept = {t.name for t in _gate()._overrides(request)["tools"]}
    assert "workflow" in kept
    assert "read_file" in kept


def test_catalog_advertised_tool_is_gate_allowed(tmp_path):
    """Guard: any tool the workflow catalog tells the model to use must be in the
    phase-gate allowlist, otherwise the model is told to call a tool it never sees."""
    from coworker.workflows import WorkflowManager

    manager = WorkflowManager(tmp_path)
    manager.enforce_conformance = False
    manager.create(
        "name: demo\ndescription: demo flow\nsteps:\n  - id: a\n    kind: set\n"
        "    params:\n      name: k\n      value: v\n"
    )
    prompt = manager.prompt_block()
    assert "`workflow`" in prompt
    union = _READ_ONLY_TOOLS | _MEMORY_TOOLS | _EXEC_TOOLS
    assert "workflow" in union
