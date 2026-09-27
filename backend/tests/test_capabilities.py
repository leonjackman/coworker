"""Capability registry: single source of truth + drift guards."""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_args

from coworker.workflows.capabilities import CapabilityRegistry


def _step(kind, do="", params=None, locator=None, id="s1"):
    return SimpleNamespace(id=id, kind=kind, do=do, params=params or {}, locator=locator)


def _names(reg, kind):
    return {a.name for a in reg.actions(kind)}


def test_declared_kinds_present():
    reg = CapabilityRegistry.declared()
    for kind in ("browser", "computer", "app", "tool", "command", "skill", "agentic", "human", "set", "assert", "wait", "branch", "loop", "parallel", "subworkflow"):
        assert reg.kind(kind) is not None, kind


def test_browser_actions_match_tool_literal():
    """Drift guard: the declared browser actions must equal the real tool enum."""
    from coworker.browser.bridge_client import BrowserAction

    assert _names(CapabilityRegistry.declared(), "browser") == set(get_args(BrowserAction))


def test_computer_actions_cover_tool_literal():
    from coworker.computer.bridge_client import ComputerAction

    declared = _names(CapabilityRegistry.declared(), "computer")
    # declared ⊇ the tool enum, plus workflow-only actions (script).
    assert set(get_args(ComputerAction)) <= declared
    assert "script" in declared


def test_resolve_missing_do():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("tool"))
    assert resolved is None
    assert diags and diags[0].code == "missing_do"


def test_resolve_unknown_action_lists_valid():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("browser", do="file_exists"))
    assert resolved is None
    assert diags[0].code == "unknown_action"
    assert "navigate" in diags[0].message


def test_resolve_unknown_param():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("tool", do="web_search", params={"query": "x", "seconds": 10}))
    assert resolved is None
    codes = {d.code for d in diags}
    assert "unknown_param" in codes


def test_resolve_click_missing_target():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("browser", do="click"))
    assert resolved is None
    assert any(d.code == "missing_param" for d in diags)


def test_resolve_click_locator_target_satisfies_required():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("browser", do="click", locator={"role": "button", "name": "Export"}))
    assert diags == []
    assert resolved.action == "click"


def test_resolve_command_maps_args():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("command", do="run", params={"command": "ls", "timeout": 5}))
    assert diags == []
    assert resolved.target == "run_command"
    assert resolved.args["command"] == "ls"
    assert resolved.args["timeout_seconds"] == 5
    assert resolved.success == "command_rc"


def test_resolve_browser_click_via_locator_coords():
    reg = CapabilityRegistry.declared()
    resolved, diags = reg.resolve(_step("browser", do="click", locator={"coords": [10, 20]}))
    assert diags == []
    assert resolved.args["x"] == 10 and resolved.args["y"] == 20


def test_schema_has_document_step_keys_and_example():
    schema = CapabilityRegistry.declared().to_schema()
    assert schema["dsl_version"] == 2
    assert any("name" in d for d in schema["document_keys"])
    assert any("params" in d for d in schema["step_keys"])
    assert "steps:" in schema["example"]


def test_authoring_example_is_valid_and_simulates():
    """The example shown to the model MUST itself validate and dry-run."""
    from coworker.workflows.parser import parse_workflow
    from coworker.workflows.simulation import simulate_workflow
    from coworker.workflows.validation import validate_workflow

    reg = CapabilityRegistry.declared()
    from coworker.workflows.capabilities import EXAMPLE_YAML

    wf, diags = parse_workflow(EXAMPLE_YAML)
    assert wf is not None, diags
    assert validate_workflow(wf, reg) == []
    report = simulate_workflow(wf, reg, {"url": "https://example.com"})
    assert report["status"] == "ok", report


def test_manager_authoring_block_always_present(tmp_path):
    """Even with zero saved workflows, the authoring spec must be injected."""
    from coworker.workflows import WorkflowManager

    manager = WorkflowManager(tmp_path)
    assert manager.prompt_block() == ""  # no existing workflows
    block = manager.authoring_block()
    assert "Workflow authoring" in block
    assert "capabilities" in block


def test_authoring_text_mentions_skeleton_and_simulate():
    text = CapabilityRegistry.declared().authoring_text()
    assert "Top-level keys" in text and "Step keys" in text
    assert "action=capabilities" in text
    assert "simulate" in text
    assert "```yaml" in text


def test_authoring_text_requires_decomposition():
    text = CapabilityRegistry.declared().authoring_text()
    assert "ONE ACTION PER NODE" in text
    assert "BAD" in text and "GOOD" in text
    assert "click_ref" in text and "locator" in text


def test_script_action_present_and_survives_introspection():
    from pydantic import BaseModel, Field

    class FakeCompArgs(BaseModel):
        action: str = Field(..., description="a")
        ref: str = Field("")

    class FakeTool:
        args_schema = FakeCompArgs

    reg = CapabilityRegistry.from_tools({"computer": FakeTool()})
    assert reg.action("computer", "script") is not None


def test_from_tools_introspection():
    from pydantic import BaseModel, Field

    class FakeArgs(BaseModel):
        action: str = Field(..., description="a")
        query: str = Field(..., description="q")
        max_results: int = Field(5)

    class FakeTool:
        args_schema = FakeArgs

    reg = CapabilityRegistry.from_tools({"web_search": FakeTool()})
    assert "web_search" in _names(reg, "tool")
