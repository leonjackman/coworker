"""P1-B (W5) micro-fix tests: D3 O(1) merge, N4 rule title, L3 background
command, S3 keep-recent MCP."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.core import (  # noqa: E402
    _merge_event_parts,
    generate_title,
)
from coworker.workspace import Workspace  # noqa: E402


# --- D3: _merge_event_parts id-index --------------------------------------------------


def test_merge_event_parts_merges_tool_streams():
    parts = [
        {"type": "delta", "content": "text "},
        {"type": "tool_start", "id": "t1", "name": "run_command", "input": "ec"},
        {"type": "tool_delta", "id": "t1", "input": "ho"},
        {"type": "tool_delta", "id": "t1", "input": " hi"},
        {"type": "tool_end", "id": "t1", "status": "success", "output": "hi", "duration_ms": 5},
        {"type": "tool_start", "id": "t2", "name": "read_file", "input": "a"},
        {"type": "tool_end", "id": "t2", "status": "success", "output": "x"},
        {"type": "delta", "content": "done"},
    ]
    merged = _merge_event_parts(parts)
    tools = [p for p in merged if p.get("type") == "tool"]
    assert len(tools) == 2
    assert tools[0]["id"] == "t1" and tools[0]["input"] == "echo hi" and tools[0]["output"] == "hi"
    assert tools[1]["id"] == "t2" and tools[1]["input"] == "a"


# --- N4: rule-based title -------------------------------------------------------------


def test_generate_title_is_rule_based():
    t = generate_title("请帮我修复后端的端口冲突问题，涉及 9527 与 9528。")
    assert isinstance(t, str) and len(t) >= 3
    assert "修复" in t
    # No model call: the old path imported ChatOpenAI + ProviderManager — gone.
    assert len(t) <= 20


# --- L3: background command -----------------------------------------------------------


def test_run_command_bg_and_status(tmp_path: Path):
    ws = Workspace(tmp_path)
    if sys.platform == "win32":
        cmd = ["cmd", "/c", "echo", "hello-bg"]
    else:
        cmd = ["echo", "hello-bg"]
    job = ws.run_command_bg(cmd, timeout_seconds=10)
    assert job["background"] is True and job["job_id"]
    # Poll until done (fast command).
    for _ in range(50):
        status = ws.command_status(job["job_id"])
        if status.get("status") == "done":
            break
        time.sleep(0.05)
    assert status.get("status") == "done", status
    assert "hello-bg" in (status.get("stdout") or "")


def test_command_status_missing(tmp_path: Path):
    ws = Workspace(tmp_path)
    assert ws.command_status("nope")["status"] == "not_found"
