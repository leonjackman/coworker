"""Regressions for the root-cause engine fixes (contract outputs, coercion,
JS-safe templating, loop scope, native text coercion)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workflows import WorkflowManager  # noqa: E402
from coworker.workflows.capabilities import CapabilityRegistry  # noqa: E402
from coworker.workflows.executor import _enforce_success  # noqa: E402
from coworker.workflows.model import Step  # noqa: E402
from coworker.workflows.native import run_native  # noqa: E402
from coworker.workflows.templating import resolve_js  # noqa: E402


def _action(kind: str, name: str):
    return CapabilityRegistry.declared().action(kind, name)


def test_regex_replace_and_no_match_do_not_contract_fail():
    # transform.regex declares (result, matched, groups); replace/no-match
    # branches omit some — the executor must backfill instead of failing.
    action = _action("transform", "regex")
    step = Step(id="id:1", kind="transform", do="regex", description="d")
    _enforce_success(step, action, {"result": "aXc"}, {}, None)          # replace
    _enforce_success(step, action, {"result": None, "matched": False}, {}, None)  # no match


def test_file_newest_not_found_does_not_contract_fail():
    action = _action("file", "newest")
    step = Step(id="id:1", kind="file", do="newest", description="d")
    result = {"path": "/nonexistent", "found": False}
    _enforce_success(step, action, result, {}, None)
    assert result["found"] is False and "mtime" in result


def test_workflow_args_coerces_json_strings():
    from coworker.agent.core import WorkflowArgs

    args = WorkflowArgs(action="capabilities", kinds='["computer","file"]', verbose=True)
    assert args.kinds == ["computer", "file"]
    assert args.verbose is True

    parsed = WorkflowArgs(action="run", name="x", inputs='{"a": 1}')
    assert parsed.inputs == {"a": 1}

    # A comma-separated fallback when it is not valid JSON.
    assert WorkflowArgs(action="capabilities", kinds="computer, file").kinds == ["computer", "file"]


def test_resolve_js_escapes_embedded_refs():
    ctx = {"steps": {"id:1": {"text": 'he said "hi"'}}}
    out = resolve_js('cua.emitText("Hi {{steps.id:1.text}}!");', ctx)
    assert '\\"' in out and out.count('"') % 2 == 0  # balanced, escaped
    # Unquoted ref -> JSON literal (valid JS).
    assert resolve_js("const n = {{steps.id:2.count}};", {"steps": {"id:2": {"count": 3}}}) == "const n = 3;"


def test_native_file_write_serializes_structured_content():
    import tempfile as _tf

    with _tf.TemporaryDirectory() as d:
        path = str(Path(d) / "out.json")
        run_native("file", "write", {"path": path, "content": {"a": None, "b": [1, 2]}})
        written = Path(path).read_text(encoding="utf-8")
        assert json.loads(written) == {"a": None, "b": [1, 2]}  # JSON, not Python repr


def test_not_empty_assertion_alias():
    from coworker.workflows.assertions import evaluate, validate_spec

    assert validate_spec("not_empty result.stdout")[0] is True
    assert evaluate("not_empty result.stdout", {"stdout": "x"}, {})[0] is True
    assert evaluate("not_empty result.stdout", {"stdout": ""}, {})[0] is False


def test_node_syntax_error_reports_the_real_error():
    from coworker.workflows.validation import _node_syntax_error

    err = _node_syntax_error("const x = ;")  # if node is absent, err is None
    if err is not None:
        assert "Node.js v" not in err
        assert "error" in err.lower()


def test_validate_scripts_matches_kernel_surface():
    from coworker.workflows.parser import parse_workflow
    from coworker.workflows.validation import validate_scripts

    yaml = (
        "name: t\ndescription: d\nsteps:\n"
        "- id: \"id:1\"\n  kind: computer\n  do: script\n  params:\n    code: |\n"
        "      const app = await cua.getApp('X');\n"
        "      await app.getAXStateText();\n"
        "      await app.shortcut('save');\n"
        "      await app.doubleClick('a');\n"
        "      await app.isRunning('x');\n"
        "  description: s\n  post: [ok]\n"
    )
    wf, _ = parse_workflow(yaml)
    msgs = [d.message for d in validate_scripts(wf)]
    # Real kernel methods must NOT be flagged as unknown (look at the CALLED name,
    # not the 'allowed:' list which legitimately contains them).
    assert not any(f"app.{m}'" in msg for msg in msgs for m in ("getAXStateText", "shortcut", "doubleClick"))
    # A non-existent method must be flagged.
    assert any("app.isRunning'" in msg for msg in msgs)


def test_loop_item_is_a_valid_template_root():
    flow = (
        "name: loop-flow\ndescription: d\nsteps:\n"
        "- id: \"id:1\"\n  kind: set\n  params: {name: items, value: [1, 2]}\n  description: seed\n"
        "- id: \"id:2\"\n  kind: file\n  do: write\n"
        "  params: {path: \"out/{{loop.index}}.txt\", content: \"{{loop.item}}\"}\n"
        "  description: write item\n  foreach: \"{{vars.items}}\"\n"
        "- id: \"id:3\"\n  kind: assert\n"
        "  do: \"exists {{steps.id:2.path}}\"\n  description: verify\n"
    )
    # Must not be rejected for an unknown template root `loop`.
    WorkflowManager(tempfile.mkdtemp()).create(flow)
