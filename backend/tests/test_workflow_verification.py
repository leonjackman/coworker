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


def test_authoring_flags_malformed_assertion():
    wf, diags = parse_workflow(
        "name: t\ndescription: d\nsteps:\n"
        "- id: id:1\n  kind: assert\n  do: \"equals one\"\n  description: check\n"
    )
    assert wf is not None, diags
    codes = {d.code for d in validate_workflow(wf, CapabilityRegistry.declared())}
    assert "bad_assertion" in codes
