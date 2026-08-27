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


def test_delegation_build_workspace_tools_kwargs(tmp_path: Path):
    """W1 上游鏈：delegation.py 以舊簽名呼叫 build_workspace_tools（含 turn_index）
    已移除——此處鎖定委派工具集仍可建構（無 TypeError）。"""
    from coworker.agent.graph import build_workspace_tools
    from coworker.workspace import Workspace

    ws = Workspace(tmp_path)
    tools = build_workspace_tools(
        ws,
        {"session_id": "s::delegate::agent", "provider": "p", "model": "m", "workspace_path": str(tmp_path)},
        change_store=None,
        session_store=None,
        referenced_sessions=set(),
        skill_manager=None,
        memory_store=None,
        memory_rel="",
        delegator=None,
        caller_agent="agent",
        web_tools=[],
        browser_tool=None,
        language="zh",
        worker_llm=object(),
        worker_session_id="s",
        worker_work_mode="build",
        worker_autonomy="guarded",
        worker_provider_name="p",
        worker_approval_store=None,
        worker_data_dir=None,
        worker_mcp_session_manager=None,
        worker_bus=None,
        session_id="s",
    )
    assert len(tools) > 0  # tools built without the removed turn_index kwarg


def test_skills_catalog_clip_frozen_dataclass(tmp_path: Path):
    """P4 regression: format_skills_prompt_bounded clips descriptions of the
    FROZEN SkillEntry — copy.copy+assign raised 'cannot assign to field
    description'; must use dataclasses.replace."""
    from coworker.skills.skills import SkillEntry, format_skills_prompt_bounded

    skills = [
        SkillEntry(name=f"s{i}", description="d" * 400, file_path=tmp_path / f"s{i}" / "SKILL.md", base_dir=tmp_path / f"s{i}", source="user")
        for i in range(80)
    ]
    out = format_skills_prompt_bounded(skills)
    assert isinstance(out, str) and out


def test_resume_runtime_positional_mapping(monkeypatch):
    """W1 regression: resume/rerun built a runtime with the OLD positional order
    (mode, provider, model, workspace); after adding session_id the provider
    field received the MODEL string -> 'Provider <model> is not enabled'."""
    from coworker.agent import runtime as rt_module
    from coworker.agent.runtime import AgentRuntimeRegistry

    calls: list[tuple] = []

    def _fake_get_stream_runtime(self, *a, **kw):
        calls.append((a, kw))
        return object()

    monkeypatch.setattr(AgentRuntimeRegistry, "get_stream_runtime", _fake_get_stream_runtime)
    registry = object.__new__(AgentRuntimeRegistry)
    registry.settings = type("S", (), {"data_dir": Path("/tmp/coworker-nonexistent")})()
    # _stream_runtime_from_context with the run context (includes session_id)
    ctx = {
        "session_id": "sess-1", "provider_id": "qwen-provider", "model": "qwen3.6-35b",
        "project_id": "p1", "workspace_path": None, "referenced_sessions": [], "agent": "a",
    }
    registry._stream_runtime_from_context(ctx)
    assert calls, "get_stream_runtime must be invoked"
    args, kwargs = calls[0]
    # positional: mode, session_id, provider_id, model, workspace
    assert args[1] == "sess-1", args
    assert args[2] == "qwen-provider", args  # provider_id must NOT be the model
    assert args[3] == "qwen3.6-35b", args
