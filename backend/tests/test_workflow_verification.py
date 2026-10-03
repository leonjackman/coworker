"""Verification language (assertions): boolean literals + authoring validation.

D2 root cause: the assertion grammar lacked boolean literals and had no
authoring-time well-formedness check, so `assert do: true` reached runtime as
`undefined ref 'true'`.
"""

from __future__ import annotations

from coworker.workflows.assertions import evaluate, validate_spec
from coworker.workflows.parser import parse_workflow
from coworker.workflows.validation import validate_workflow
from coworker.workflows.capabilities import CapabilityRegistry


def test_boolean_literals_evaluate():
    assert evaluate("true", {}, {})[0] is True
    assert evaluate("false", {}, {})[0] is False


def test_validate_spec_accepts_and_rejects():
    for ok in ("true", "false", "ok", "contains hi", "equals vars.x 1", "matches vars.x ^a", "result.ok"):
        assert validate_spec(ok)[0] is True, ok
    assert validate_spec("equals onlyone")[0] is False
    assert validate_spec("contains")[0] is False


def test_validate_spec_rejects_expression_syntax():
    # Models often write C-style expressions; there is no expression grammar,
    # so these must be rejected at authoring (not fail at run time).
    for bad in (
        "result.return_code != 0",
        "steps.id:1.stdout != ''",
        "vars.x == 1 && vars.y",
        "result.count > 0",
    ):
        assert validate_spec(bad)[0] is False, bad


def test_authoring_flags_malformed_assertion():
    wf, diags = parse_workflow(
        "name: t\ndescription: d\nsteps:\n"
        "- id: id:1\n  kind: assert\n  do: \"equals one\"\n  description: check\n"
    )
    assert wf is not None, diags
    codes = {d.code for d in validate_workflow(wf, CapabilityRegistry.declared())}
    assert "bad_assertion" in codes


def _success_specs(step):
    from coworker.workflows.executor import WorkflowExecutor

    return WorkflowExecutor._success_specs(step)


def test_assert_action_label_is_not_a_condition():
    """A canonical assert step (`binding.action: assert`) must not treat the
    action label as a spec (it caused `undefined ref 'assert'` at run time)."""
    from coworker.workflows.model import Step

    step = Step(id="id:1", kind="assert", do="assert", post=["result.exists"], description="v")
    assert _success_specs(step) == ["result.exists"]

    # A real `do` condition still works.
    step2 = Step(id="id:1", kind="assert", do="equals result.return_code 0", description="v")
    assert _success_specs(step2) == ["equals result.return_code 0"]


def test_authoring_rejects_assert_without_condition():
    wf, diags = parse_workflow(
        "name: t\ndescription: d\nsteps:\n"
        "- id: id:1\n  kind: assert\n  do: assert\n  description: check\n"
    )
    assert wf is not None, diags
    codes = {d.code for d in validate_workflow(wf, CapabilityRegistry.declared())}
    assert "bad_assertion" in codes


def test_assert_action_label_not_serialized():
    from coworker.workflows.parser import parse_workflow, render_workflow

    wf, diags = parse_workflow(
        "name: t\ndescription: d\nsteps:\n"
        "- id: id:1\n  kind: assert\n"
        "  binding: {action: assert, verify: [\"result.exists\"]}\n  description: v\n"
    )
    assert wf is not None, diags
    text = render_workflow(wf)
    assert "action: assert" not in text
    assert "result.exists" in text


def test_notify_body_from_dict_is_json_not_python_repr():
    from coworker.workflows.native import run_native

    res = run_native(
        "notify",
        "notification",
        {"title": "t", "body": {"blocks": [], "result": None, "text": ""}},
        notifier=lambda title, body: {"ok": True},
    )
    assert res["body"] == '{"blocks": [], "result": null, "text": ""}'
