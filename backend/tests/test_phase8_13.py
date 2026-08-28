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


def test_no_step_cap_and_graceful_recursion_backstop():
    """N1 revision: NO recursion_limit override (unbounded by design — runaways
    are governed by loop guards); the runtime still catches GraphRecursionError
    (create_agent's built-in 9_999) gracefully instead of a raw error."""
    from coworker.agent.core import LOOP_REASON_STEP_CAP, agent_run_config

    cfg = agent_run_config(session_id="s", provider="p", model="m", language="zh", work_mode="build", autonomy="guarded", streaming=True)
    assert "recursion_limit" not in cfg
    assert LOOP_REASON_STEP_CAP == "step_cap"
    src = Path(__file__).resolve().parents[1] / "coworker" / "agent" / "runtime.py"
    text = src.read_text(encoding="utf-8")
    assert "GraphRecursionError as exc:" in text


def _last_content(ov) -> str:
    if not ov:
        return ""
    msgs = ov.get("messages") or []
    return str(getattr(msgs[-1], "content", "") or "") if msgs else ""


def _run_idle(mw, steps):
    """Feed the middleware a sequence of step flags via real messages.

    Each ``steps`` entry is ``("tool", name, args, output)`` or
    ``("text", content)``; we build the state messages incrementally so the
    middleware's sliding window sees each step.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    messages: list[Any] = []
    out = []
    for entry in steps:
        if entry[0] == "text":
            messages.append(AIMessage(content=entry[1]))
            req = type("R", (), {"state": {"messages": list(messages)}, "tools": []})
            out.append(mw._overrides(req))
        else:
            _kind, name, args, output = entry
            tc = {"name": name, "args": {"arg": args}, "id": f"tc-{len(messages)}"}
            messages.append(AIMessage(content="", tool_calls=[tc]))
            messages.append(ToolMessage(content=output, tool_call_id=tc["id"], name=name))
            req = type("R", (), {"state": {"messages": list(messages)}, "tools": []})
            out.append(mw._overrides(req))
    return out


def test_idle_loop_varying_stuck_hard_stops():
    """Variant loop (different args, identical output) is caught: warn on the
    first ≥7/10, hard stop once the 20-step window fills still ≥7/10."""
    from coworker.agent.middleware.loop_guard import IdleLoopMiddleware

    mw = IdleLoopMiddleware()
    steps = []
    for i in range(22):
        steps.append(("tool", "read_file", f"/tmp/x{i}.txt", "SAME_OUTPUT"))
    out = _run_idle(mw, steps)
    # warn injected once (non-terminal, tools intact)
    warned = [o for o in out if "WARNING" in _last_content(o)]
    assert len(warned) >= 1
    # hard stop: last override strips tools + idle_hard
    final = out[-1]
    assert final.get("tools") == []
    # loop_reason 写在覆盖的 state 里（不是顶层）。
    assert final.get("state", {}).get("loop_reason") == "idle_hard"
    assert "硬停" in _last_content(final)


def test_idle_loop_recovers_to_unlimited():
    """After the warn, progress steps slide the stuck flags out → recovery:
    warned resets, window clears, no hard stop, further steps are unlimited."""
    from coworker.agent.middleware.loop_guard import IdleLoopMiddleware

    mw = IdleLoopMiddleware()
    steps = [("tool", "read_file", f"/tmp/x{i}.txt", "SAME") for i in range(10)]  # warn fires
    steps += [("tool", "run_command", "ls", f"DIFF_OUTPUT_{i}") for i in range(6)]  # recover
    steps += [("tool", "run_command", "ls", f"DIFF_OUTPUT_{i}") for i in range(30)]  # unlimited continue
    out = _run_idle(mw, steps)
    assert all(o.get("tools") is None and o.get("loop_reason") is None for o in out)  # no hard stop
    # warned fired then recovered: exactly one warning present
    warned = [o for o in out if "WARNING" in _last_content(o)]
    assert len(warned) == 1


def test_idle_loop_sliding_window_scan_recovers():
    """Stuck in the middle (steps 5-14) triggers the warn, but progress after it
    slides the stuck flags out of the trailing window → recover (no hard stop)."""
    from coworker.agent.middleware.loop_guard import IdleLoopMiddleware

    mw = IdleLoopMiddleware()
    steps = [("tool", "read_file", f"/tmp/h{i}.txt", f"HEAD_{i}") for i in range(4)]
    steps += [("tool", "read_file", f"/tmp/m{i}.txt", "STUCK_SAME") for i in range(10)]
    steps += [("tool", "read_file", f"/tmp/t{i}.txt", f"TAIL_{i}") for i in range(6)]
    out = _run_idle(mw, steps)
    # warn fired, then recovered — no hard stop.
    assert any("WARNING" in _last_content(o) for o in out)
    assert all(o.get("tools") is None and o.get("loop_reason") is None for o in out)


def test_idle_loop_early_stuck_slides_out():
    """5 early stuck steps (below the 7/10 threshold) then progress → never
    warns or hard-stops."""
    from coworker.agent.middleware.loop_guard import IdleLoopMiddleware

    mw = IdleLoopMiddleware()
    steps = [("tool", "read_file", f"/tmp/e{i}.txt", "STUCK") for i in range(5)]
    steps += [("tool", "run_command", "git", f"PROGRESS_{i}") for i in range(15)]
    out = _run_idle(mw, steps)
    assert all(not o for o in out)
    assert all(o.get("loop_reason") is None for o in out)


def test_tool_input_capture_without_index_continuation():
    """Bug 1 regression: continuation tool-call chunks whose index was never
    registered (first chunk lacks an index) were silently dropped → the
    persisted tool part had EMPTY input. The name-based fallback routing must
    accumulate the args so the write/edit trail survives."""
    from langchain_core.messages import AIMessageChunk, ToolMessage

    from coworker.agent.core import _message_chunk_events

    parts: list = []
    content_parts: list = []
    tool_state: dict = {}

    def _emit(msg):
        return _message_chunk_events(msg, content_parts, tool_state, parts, "s1")

    # First chunk: id present, empty args, NO index → index never registered.
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"id": "tc1", "name": "write_file", "args": "", "index": None}]))
    # Continuation chunks: args arrive with index=0 (not in the map).
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"name": "write_file", "args": '{"file_path": "/tmp/test-preload.ts", "content": "let x=1"', "index": 0}]))
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"name": "write_file", "args": '"}', "index": 0}]))
    # Tool end.
    _emit(ToolMessage(content='{"error": "Write denied"}', tool_call_id="tc1", name="write_file"))

    tool_ends = [p for p in parts if p.get("type") == "tool_end"]
    assert len(tool_ends) == 1
    captured = tool_ends[0].get("input") or ""
    assert "file_path" in captured, captured
    assert "/tmp/test-preload.ts" in captured, captured
    assert "let x=1" in captured, captured


def test_tool_input_capture_with_index_routing_still_works():
    """The indexed routing path (index registered on the first chunk) must keep
    working for parallel tool calls."""
    from langchain_core.messages import AIMessageChunk, ToolMessage

    from coworker.agent.core import _message_chunk_events

    parts: list = []
    content_parts: list = []
    tool_state: dict = {}

    def _emit(msg):
        return _message_chunk_events(msg, content_parts, tool_state, parts, "s1")

    # Two parallel tool calls, each with a registered index from the first chunk.
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"id": "t1", "name": "read_file", "args": '{"file_path": "a"', "index": 0}]))
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"id": "t2", "name": "run_command", "args": '{"command": "ls"', "index": 1}]))
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"name": "read_file", "args": '"}', "index": 0}]))
    _emit(AIMessageChunk(content="", tool_call_chunks=[{"name": "run_command", "args": '"}', "index": 1}]))
    _emit(ToolMessage(content="c", tool_call_id="t1", name="read_file"))
    _emit(ToolMessage(content="ok", tool_call_id="t2", name="run_command"))

    inputs = {p.get("id"): (p.get("input") or "") for p in parts if p.get("type") == "tool_end"}
    assert '"file_path": "a"' in inputs["t1"], inputs
    assert '"command": "ls"' in inputs["t2"], inputs
