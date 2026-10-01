"""Native action kinds: http / file / transform / notify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworker.workflows.capabilities import CapabilityRegistry
from coworker.workflows.env import build_tool_environment
from coworker.workflows.native import run_native


# ── transform ────────────────────────────────────────────────────────────


def test_transform_json_path_and_template():
    data = {"a": {"b": [10, 20]}}
    assert run_native("transform", "json_path", {"data": data, "path": "a.b.1"})["result"] == 20
    out = run_native("transform", "template", {"text": "hi {{name}}", "vars": {"name": "cw"}})
    assert out["result"] == "hi cw"


def test_transform_regex_csv_base64_date():
    m = run_native("transform", "regex", {"text": "id=42", "pattern": r"id=(\d+)", "group": 1})
    assert m["result"] == "42"
    r = run_native("transform", "regex", {"text": "a-b-c", "pattern": "-", "replace": "_", "mode": "replace"})
    assert r["result"] == "a_b_c"
    csv_out = run_native("transform", "csv_parse", {"text": "a,b\n1,2\n"})
    assert csv_out["rows"] == [["a", "b"], ["1", "2"]]
    enc = run_native("transform", "base64", {"op": "encode", "text": "hi"})
    assert run_native("transform", "base64", {"op": "decode", "text": enc["result"]})["result"] == "hi"
    df = run_native("transform", "date_format", {"value": "2026-09-27", "from_format": "%Y-%m-%d", "to_format": "%Y%m%d"})
    assert df["result"] == "20260927"


# ── file ─────────────────────────────────────────────────────────────────


def test_file_roundtrip(tmp_path: Path):
    p = tmp_path / "a" / "x.txt"
    run_native("file", "write", {"path": str(p), "content": "hello", "mkdirs": True})
    assert run_native("file", "read", {"path": str(p)})["content"] == "hello"
    assert run_native("file", "exists", {"path": str(p)})["exists"] is True
    run_native("file", "append", {"path": str(p), "content": " world"})
    assert run_native("file", "read", {"path": str(p)})["content"] == "hello world"
    dest = tmp_path / "b.txt"
    run_native("file", "copy", {"path": str(p), "to": str(dest)})
    assert dest.read_text() == "hello world"
    run_native("file", "delete", {"path": str(dest)})
    assert not dest.exists()
    assert str(p) in run_native("file", "glob", {"path": str(tmp_path), "pattern": "**/*.txt"})["items"]


def test_file_zip_unzip_roundtrip(tmp_path: Path):
    src = tmp_path / "data"
    (src / "sub").mkdir(parents=True)
    (src / "cost.csv").write_text("cost\n1\n")
    (src / "sub" / "amount.csv").write_text("amount\n2\n")
    archive = tmp_path / "out.zip"

    zipped = run_native("file", "zip", {"path": str(src), "to": str(archive)})
    assert archive.exists() and zipped["count"] == 2

    dest = tmp_path / "extracted"
    unzipped = run_native("file", "unzip", {"path": str(archive), "to": str(dest)})
    assert unzipped["count"] == 2
    assert (dest / "cost.csv").read_text().startswith("cost")
    assert (dest / "sub" / "amount.csv").read_text().startswith("amount")


def test_file_unzip_and_zip_registered():
    reg = CapabilityRegistry.declared()
    for action in ("unzip", "zip"):
        spec = reg.action("file", action)
        assert spec is not None
        assert {p.name for p in spec.params} >= {"path", "to"}


# ── http ─────────────────────────────────────────────────────────────────


class _FakeResp:
    status_code = 201
    is_success = True
    headers = {"content-type": "application/json"}
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, **kwargs):
        type(self).last = (method, url, kwargs)
        return _FakeResp()


def test_http_request(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    result = run_native("http", "request", {"url": "https://x.test/api", "method": "post", "json": {"a": 1}})
    assert result["status"] == 201
    assert _FakeClient.last[0] == "POST"


def test_http_status_rule():
    from coworker.workflows.capabilities import check_success

    assert check_success("http_status", {"status": 200}, {}) is True
    assert check_success("http_status", {"status": 404}, {}) is False
    assert check_success("http_status", {"status": 404}, {"expect_status": 404}) is True


def test_notify_notification(monkeypatch):
    import subprocess

    calls = []

    def _fake_run(*a, **k):
        calls.append(a)
        return None

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("sys.platform", "darwin")
    out = run_native("notify", "notification", {"title": "T", "body": "B"})
    assert out["notified"] is True
    assert calls  # osascript invoked


# ── integration through the executor ─────────────────────────────────────


def test_workflow_runs_transform_steps(tmp_path: Path):
    from coworker.workflows import WorkflowManager

    manager = WorkflowManager(tmp_path)
    manager.enforce_conformance = False
    flow = """name: xform-flow
description: transform steps
steps:
  - id: a
    kind: transform
    do: json_parse
    params:
      text: '{"n": 7}'
  - id: b
    kind: transform
    do: json_path
    params:
      data: "{{steps.a.result}}"
      path: n
"""
    manager.create(flow)
    env = build_tool_environment(workspace=None, tools=[])
    result = manager.run("xform-flow", env=env)
    assert result["status"] == "ok", result
    assert result["run"]["context"]["steps"]["b"]["result"] == 7


def test_registry_flags_missing_url_for_http():
    reg = CapabilityRegistry.declared()
    wf = _parse("name: h\ndescription: d\nsteps:\n  - id: a\n    kind: http\n    do: request\n")
    from coworker.workflows.validation import validate_workflow

    codes = {d.code for d in validate_workflow(wf, reg)}
    assert "missing_param" in codes


def _parse(yaml_text: str):
    from coworker.workflows.parser import parse_workflow

    wf, diags = parse_workflow(yaml_text)
    assert wf is not None, diags
    return wf


def test_capabilities_include_native_kinds():
    reg = CapabilityRegistry.declared()
    for kind, action in [("http", "request"), ("file", "copy"), ("transform", "json_path"), ("notify", "notification")]:
        assert reg.kind(kind) is not None, kind
        assert reg.action(kind, action) is not None, (kind, action)
