"""Semantic validation + dry-run simulation."""

from __future__ import annotations

from coworker.workflows.capabilities import CapabilityRegistry
from coworker.workflows.parser import parse_workflow
from coworker.workflows.simulation import simulate_workflow
from coworker.workflows.validation import validate_workflow


def _wf(steps_block: str, extra: str = ""):
    yaml = f"name: v-flow\ndescription: d\n{extra}steps:\n{steps_block}"
    workflow, diags = parse_workflow(yaml)
    assert workflow is not None, diags
    return workflow


def _codes(workflow):
    return {d.code for d in validate_workflow(workflow, CapabilityRegistry.declared())}


def test_missing_do_is_rejected():
    assert "missing_do" in _codes(_wf("  - id: a\n    kind: tool\n"))


def test_unknown_action_is_rejected():
    assert "unknown_action" in _codes(_wf("  - id: a\n    kind: browser\n    do: file_exists\n"))


def test_unknown_param_is_rejected():
    wf = _wf("  - id: a\n    kind: tool\n    do: web_search\n    params:\n      query: x\n      seconds: 10\n")
    assert "unknown_param" in _codes(wf)


def test_command_without_command_is_rejected():
    assert "missing_command" in _codes(_wf("  - id: a\n    kind: command\n"))


def test_command_legacy_do_form_is_accepted():
    assert _codes(_wf("  - id: a\n    kind: command\n    do: echo hi\n")) == set()


def test_undeclared_input_is_rejected():
    wf = _wf("  - id: a\n    kind: tool\n    do: web_search\n    params:\n      query: '{{inputs.topic}}'\n")
    assert "unknown_input" in _codes(wf)


def test_unknown_step_ref_is_rejected():
    wf = _wf("  - id: a\n    kind: command\n    do: echo {{steps.ghost}}\n")
    assert "unknown_step_ref" in _codes(wf)


def test_unknown_template_root_is_rejected():
    wf = _wf("  - id: a\n    kind: command\n    do: mv {{last_command_output}} /tmp/x\n")
    assert "unknown_template_root" in _codes(wf)


def test_skill_step_requires_name_only():
    # `skill` has no fixed action list; do = skill name; params must be declared.
    from coworker.workflows.capabilities import CapabilityRegistry as R

    wf = _wf("  - id: a\n    kind: skill\n    do: ego-browser\n")
    diags = validate_workflow(wf, R.declared())
    assert not [d for d in diags if d.code in ("missing_do", "unknown_action")]


def test_shell_metachars_require_shell_flag():
    wf = _wf("  - id: a\n    kind: command\n    do: run\n    params:\n      command: ls | head -1\n")
    assert "shell_required" in _codes(wf)
    ok = _wf("  - id: a\n    kind: command\n    do: run\n    params:\n      command: ls | head -1\n      shell: true\n")
    assert "shell_required" not in _codes(ok)


def test_bundled_agentic_goal_is_warning_not_error():
    wf = _wf(
        "  - id: a\n    kind: agentic\n    goal: 打開 Safari 然後導航到平台並點擊導出，再等待下載完成\n"
    )
    diags = validate_workflow(wf, CapabilityRegistry.declared())
    bundled = [d for d in diags if d.code == "might_bundle_actions"]
    assert bundled and bundled[0].severity == "warning"
    # warnings must not block validity (the user wants running workflows to pass)
    assert not [d for d in diags if d.severity == "error"]


def test_unknown_output_field_is_warning():
    wf = _wf(
        "  - id: a\n    kind: command\n    do: run\n    params:\n      command: echo hi\n"
        "  - id: b\n    kind: transform\n    do: template\n    params:\n      text: '{{steps.a.nope}}'\n"
    )
    diags = validate_workflow(wf, CapabilityRegistry.declared())
    out = [d for d in diags if d.code == "unknown_output"]
    assert out and out[0].severity == "warning"
    assert not [d for d in diags if d.severity == "error"]


def test_known_output_field_is_ok():
    wf = _wf(
        "  - id: a\n    kind: command\n    do: run\n    params:\n      command: echo hi\n"
        "  - id: b\n    kind: transform\n    do: template\n    params:\n      text: '{{steps.a.stdout}}'\n"
    )
    assert "unknown_output" not in {d.code for d in validate_workflow(wf, CapabilityRegistry.declared())}


def test_simulate_reports_target_and_args():
    wf = _wf(
        "  - id: a\n    kind: command\n    do: run\n    params:\n      command: echo hi\n",
    )
    report = simulate_workflow(wf, CapabilityRegistry.declared())
    assert report["status"] == "ok"
    step = report["steps"][0]
    assert step["target"] == "run_command"
    assert step["args"]["command"] == "echo hi"


def test_simulate_flags_missing_required_input():
    wf = _wf(
        "  - id: a\n    kind: tool\n    do: web_search\n    params:\n      query: '{{inputs.q}}'\n",
        extra="inputs:\n  q:\n    type: string\n    required: true\n",
    )
    report = simulate_workflow(wf, CapabilityRegistry.declared())
    assert report["status"] == "error"
    assert "q" in report["missing_inputs"]
