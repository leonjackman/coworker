"""Atomicity / Studio-readability conformance linter (write-time enforcement).

Every rule is an ERROR unless the step explicitly lists the code in its
``bypass`` field, which downgrades it to a visible warning.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workflows.capabilities import CapabilityRegistry  # noqa: E402
from coworker.workflows.parser import parse_workflow  # noqa: E402
from coworker.workflows.validation import validate_workflow  # noqa: E402


def _diags(yaml_text: str, *, live_tools: bool = False):
    workflow, parse_diags = parse_workflow(yaml_text)
    assert workflow is not None, parse_diags
    registry = CapabilityRegistry.declared()
    registry.live_tools = live_tools
    return validate_workflow(workflow, registry)


def _codes(diags, severity="error"):
    return {d.code for d in diags if d.severity == severity}


_HEAD = "name: t\ndescription: test workflow\nsteps:\n"


def test_missing_verification_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: browser\n  do: navigate\n  params: {url: \"https://x\"}\n  description: open\n"
    ))
    assert "missing_verification" in _codes(diags)


def test_missing_description_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: browser\n  do: navigate\n  params: {url: \"https://x\"}\n"
        "  post: [ok]\n"
    ))
    assert "missing_description" in _codes(diags)


def test_mutating_evaluate_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: browser\n  do: evaluate\n"
        "  params: {expression: \"document.querySelectorAll('div').forEach(el => el.click()); 'done'\"}\n"
        "  description: click export\n  post: [ok]\n"
    ))
    assert "mutating_evaluate" in _codes(diags)


def test_read_only_evaluate_is_allowed():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: browser\n  do: evaluate\n"
        "  params: {expression: \"document.title\"}\n  description: read title\n  post: [ok]\n"
    ))
    assert "mutating_evaluate" not in _codes(diags)


def test_opaque_command_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: command\n  do: run\n"
        "  params: {command: \"cd /tmp && unzip a.zip && cp x y\"}\n  description: extract\n  post: [ok]\n"
    ))
    assert "opaque_command" in _codes(diags)


def test_non_deterministic_target_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: command\n  do: run\n"
        "  params: {command: \"find ~/Downloads -name '*.zip' -mmin -5\"}\n  description: find zip\n  post: [ok]\n"
    ))
    assert "non_deterministic_target" in _codes(diags)


def test_missing_platform_is_error():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: command\n  do: run\n"
        "  params: {command: \"unzip archive.zip\"}\n  description: extract\n  post: [ok]\n"
    ))
    assert "missing_platform" in _codes(diags)


def test_coord_only_locator_is_error_but_bypassable():
    body = (
        "- id: \"id:1\"\n  kind: computer\n  do: click_coords\n"
        "  params: {x: 10, y: 20}\n  locator: {coords: [10, 20]}\n"
        "  description: tap\n  post: [ok]\n"
    )
    assert "coord_only_locator" in _codes(_diags(_HEAD + body))

    bypassed = body.replace("description: tap", "description: tap\n  bypass: [coord_only_locator]")
    diags = _diags(_HEAD + bypassed)
    assert "coord_only_locator" not in _codes(diags, "error")
    assert "coord_only_locator" in _codes(diags, "warning")


def test_unknown_tool_only_flagged_with_live_registry():
    body = (
        "- id: \"id:1\"\n  kind: tool\n  do: computer_observe\n  params: {}\n"
        "  description: observe\n  post: [ok]\n"
    )
    assert "unknown_tool" not in _codes(_diags(_HEAD + body, live_tools=False))
    assert "unknown_tool" in _codes(_diags(_HEAD + body, live_tools=True))


def test_known_tool_is_fine():
    body = (
        "- id: \"id:1\"\n  kind: tool\n  do: web_search\n  params: {query: x}\n"
        "  description: search\n  post: [ok]\n"
    )
    assert "unknown_tool" not in _codes(_diags(_HEAD + body, live_tools=True))


def test_conformant_workflow_has_no_conformance_errors():
    diags = _diags(_HEAD + (
        "- id: \"id:1\"\n  kind: browser\n  do: navigate\n  params: {url: \"https://x\"}\n"
        "  description: 打开页面\n"
        "- id: \"id:2\"\n  kind: browser\n  do: click_text\n  params: {text: 导出}\n"
        "  description: 点击导出\n"
        "- id: \"id:3\"\n  kind: file\n  do: exists\n  params: {path: \"/tmp/out.csv\"}\n"
        "  description: 确认文件\n  post: [\"result.exists\"]\n"
    ))
    assert not _codes(diags)


def test_create_returns_warnings_and_blocks_errors(tmp_path: Path):
    from coworker.workflows import WorkflowManager, WorkflowValidationError

    mgr = WorkflowManager(tmp_path)
    conformant = (
        "name: ok-flow\ndescription: d\nsteps:\n"
        "- id: \"id:1\"\n  kind: browser\n  do: navigate\n  params: {url: \"https://x\"}\n"
        "  description: open\n"
        "- id: \"id:2\"\n  kind: file\n  do: exists\n  params: {path: \"/tmp/x\"}\n"
        "  description: verify\n  post: [\"result.exists\"]\n"
    )
    result = mgr.create(conformant)
    assert result["status"] == "ok"
    assert "diagnostics" in result and result["warnings"] == []

    nonconformant = (
        "name: bad-flow\ndescription: d\nsteps:\n"
        "- id: \"id:1\"\n  kind: browser\n  do: navigate\n  params: {url: \"https://x\"}\n"
        "  description: open\n"
    )
    try:
        mgr.create(nonconformant)
    except WorkflowValidationError as exc:
        assert "verification" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-conformant workflow should have been rejected")
