"""Result-contract conformance: a runtime result must satisfy declared outputs.

This is the systemic guard behind the `computer.script` defect (declared outputs
`result`/`text` vs a runtime that only returned `blocks`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from coworker.workflows.capabilities import CapabilityRegistry
from coworker.workflows.native import run_native

# The result envelope every computer-script REPL cell produces (kernel + adapter).
_SCRIPT_ENVELOPE = {"result", "text", "blocks", "error", "errorName"}


def _declared(kind: str, action: str):
    spec = CapabilityRegistry.declared().action(kind, action)
    return tuple(spec.outputs or ()) if spec is not None else ()


def test_script_declared_outputs_are_in_the_envelope():
    for kind in ("computer", "app"):
        declared = set(_declared(kind, "script"))
        assert declared, f"{kind}.script should declare outputs"
        assert declared <= _SCRIPT_ENVELOPE, f"{kind}.script outputs not in REPL envelope: {declared}"


def test_native_actions_return_their_declared_outputs(tmp_path: Path):
    tmp = Path(tempfile.mkdtemp(dir=tmp_path))
    f = tmp / "a.txt"
    d = tmp / "dir"
    samples = {
        ("file", "write"): {"path": str(f), "content": "hi", "mkdirs": True},
        ("file", "append"): {"path": str(f), "content": "!"},
        ("file", "read"): {"path": str(f)},
        ("file", "exists"): {"path": str(f)},
        ("file", "stat"): {"path": str(f)},
        ("file", "mkdir"): {"path": str(d)},
        ("file", "copy"): {"path": str(f), "to": str(tmp / "b.txt")},
        ("file", "move"): {"path": str(tmp / "b.txt"), "to": str(tmp / "c.txt")},
        ("file", "delete"): {"path": str(tmp / "c.txt")},
        ("file", "list"): {"path": str(tmp)},
        ("file", "glob"): {"path": str(tmp), "pattern": "*"},
        ("file", "newest"): {"path": str(tmp), "pattern": "*"},
        ("file", "zip"): {"path": str(d), "to": str(tmp / "d.zip")},
        ("file", "unzip"): {"path": str(tmp / "d.zip"), "to": str(tmp / "unz")},
        ("transform", "json_parse"): {"text": '{"a": 1}'},
        ("transform", "json_path"): {"data": {"a": {"b": 2}}, "path": "a.b"},
        ("transform", "regex"): {"text": "id=1", "pattern": r"id=(\d+)", "group": 1},
        ("transform", "template"): {"text": "{{x}}", "vars": {"x": "1"}},
        ("transform", "csv_parse"): {"text": "a,b\n1,2\n"},
        ("transform", "base64"): {"op": "encode", "text": "hi"},
        ("transform", "date_format"): {"value": "2026-09-27", "from_format": "%Y-%m-%d", "to_format": "%Y%m%d"},
    }
    failures = []
    for (kind, action), payload in samples.items():
        result = run_native(kind, action, payload)
        declared = _declared(kind, action)
        missing = [o for o in declared if not (isinstance(result, dict) and o in result)]
        if missing:
            failures.append((kind, action, missing))
    assert not failures, f"actions missing their declared outputs: {failures}"
