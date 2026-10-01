"""Per-node EXECUTION contracts (P4): every pure-execution action is actually
run at least once, not just structurally validated.

Covered here: file / transform / http / notify (via ``run_native`` and a local
HTTP server) plus command / set / wait / assert (via a real workflow run).
GUI kinds (browser / computer) need the desktop app and are covered separately
by real-machine smoke tests, so they are intentionally excluded here.
"""

from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path

import pytest

from coworker.workflows import WorkflowManager
from coworker.workflows.capabilities import CapabilityRegistry
from coworker.workflows.env import build_tool_environment
from coworker.workflows.native import run_native
from coworker.workspace import Workspace


def _actions(kind: str) -> set[str]:
    spec = CapabilityRegistry.declared().kinds[kind]
    return {a.name for a in spec.actions}


def _start_http_server() -> tuple[str, http.server.HTTPServer]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"received")

        def log_message(self, *args):  # silence
            pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", server


# ── file ─────────────────────────────────────────────────────────────────


def test_every_file_action_executes(tmp_path: Path):
    f = tmp_path / "a.txt"
    d = tmp_path / "dir"
    out = {
        "write": {"path": str(f), "content": "hello", "mkdirs": True},
        "append": {"path": str(f), "content": " world"},
        "read": {"path": str(f)},
        "exists": {"path": str(f)},
        "stat": {"path": str(f)},
        "mkdir": {"path": str(d)},
        "copy": {"path": str(f), "to": str(tmp_path / "b.txt")},
        "move": {"path": str(tmp_path / "b.txt"), "to": str(tmp_path / "c.txt")},
        "delete": {"path": str(tmp_path / "c.txt")},
        "list": {"path": str(tmp_path)},
        "glob": {"path": str(tmp_path), "pattern": "*.txt"},
        "newest": {"path": str(tmp_path), "pattern": "*.txt"},
        "zip": {"path": str(d), "to": str(tmp_path / "d.zip")},
        "unzip": {"path": str(tmp_path / "d.zip"), "to": str(tmp_path / "unz")},
    }
    assert _actions("file") == set(out), "a file action has no execution contract"
    zipped = None
    for action, payload in out.items():
        result = run_native("file", action, payload)
        assert isinstance(result, dict), action
    assert run_native("file", "read", {"path": str(f)})["content"] == "hello world"


# ── transform ────────────────────────────────────────────────────────────


def test_every_transform_action_executes():
    out = {
        "json_parse": {"text": '{"a": 1}'},
        "json_path": {"data": {"a": {"b": 2}}, "path": "a.b"},
        "regex": {"text": "id=42", "pattern": r"id=(\d+)", "group": 1},
        "template": {"text": "hi {{x}}", "vars": {"x": "cw"}},
        "csv_parse": {"text": "a,b\n1,2\n"},
        "base64": {"op": "encode", "text": "hi"},
        "date_format": {"value": "2026-09-27", "from_format": "%Y-%m-%d", "to_format": "%Y%m%d"},
    }
    assert _actions("transform") == set(out), "a transform action has no execution contract"
    for action, payload in out.items():
        assert isinstance(run_native("transform", action, payload), dict), action


# ── notify + http ────────────────────────────────────────────────────────


def test_notify_and_http_actions_execute():
    url, server = _start_http_server()
    try:
        assert run_native("http", "request", {"url": url, "method": "GET"})["status"] == 200
        assert run_native("notify", "notification", {"title": "t", "body": "b"}) is not None
        assert run_native("notify", "webhook", {"url": url, "method": "POST", "body": {"x": 1}}) is not None
    finally:
        server.shutdown()
    assert _actions("http") == {"request"}
    assert _actions("notify") == {"notification", "webhook"}


# ── command / set / wait / assert (via a real run) ───────────────────────


def _run_env(tmp_path: Path):
    return build_tool_environment(workspace=Workspace(tmp_path), tools=[], data_dir=tmp_path)


def test_command_set_wait_assert_execute(tmp_path: Path):
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    flow = """
name: node-exec
description: exercise control/native nodes
steps:
- id: id:1
  kind: command
  do: run
  params: {command: ["echo", "hi"]}
  post: ["contains hi"]
  description: run echo and verify output
- id: id:2
  kind: set
  params: {name: greeting, value: "{{steps.id:1.stdout}}"}
  description: store output
- id: id:3
  kind: wait
  params: {seconds: 0.01}
  description: brief wait
- id: id:4
  kind: assert
  post: ["vars.greeting"]
  description: verify stored var is truthy
"""
    mgr.create(flow)
    result = mgr.run("node-exec", env=_run_env(tmp_path))
    assert result["status"] == "ok", result.get("run", {}).get("error")
    steps = result["run"]["context"]["steps"]
    for sid in ("id:1", "id:2", "id:3", "id:4"):
        assert steps[sid].get("status", "ok") == "ok", (sid, steps[sid])
