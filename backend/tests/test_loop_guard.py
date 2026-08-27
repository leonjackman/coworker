"""Loop-guard tests for the 降智 (degenerate repetition) investigation.

Covers the hardened RepeatedToolCallMiddleware detectors and the
_aclose_on_exit wrapper that aborts the provider HTTP request when a stream is
cancelled:

- consecutive identical tool calls (original guard);
- trailing tool-call turns regardless of args (varying-args loop);
- consecutive identical assistant text across turns;
- a single message that already degenerated into repeated text (the qwen3-on-
  vLLM "讓我做X：" × 40 failure mode that the old tool-only guard could not
  catch);
- total tool-call budget;
- a fresh user question is never hijacked by stale degenerate history;
- _aclose_on_exit always closes the wrapped generator on cancellation.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.runtime import _aclose_on_exit  # noqa: E402
from coworker.agent.middleware import RepeatedToolCallMiddleware  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402


def _ai(content: str = "", tool_calls=None, msg_id: str | None = None) -> AIMessage:
    kwargs: dict = {"content": content}
    if tool_calls is not None:
        kwargs["tool_calls"] = tool_calls
    if msg_id is not None:
        kwargs["id"] = msg_id
    return AIMessage(**kwargs)


def _human(content: str) -> HumanMessage:
    return HumanMessage(content=content)


def _tool(name: str, command: str) -> ToolMessage:
    return ToolMessage(content="ok", name=name, tool_call_id="t1")


def _call(name: str, args: dict):
    return {"name": name, "args": args, "id": "c1", "type": "tool_call"}


class _FakeRequest:
    def __init__(self, messages):
        self.messages = messages
        self.tools = []
        self.state = {"autonomy": "guarded"}

    def override(self, **kwargs):
        return kwargs


def _overrides(middleware, messages):
    return middleware._overrides(_FakeRequest(list(messages)))


def test_identical_tool_call_loop_stops_after_cap():
    mw = RepeatedToolCallMiddleware()
    msgs = [
        _human("do it"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
    ]
    ov = _overrides(mw, msgs)
    assert "tools" in ov and ov["tools"] == []
    assert "STOP" in str(ov["messages"][-1].content)
    assert "ask_user" in str(ov["messages"][-1].content)  # W3: permission gate


def test_identical_tool_call_warns_before_cap():
    mw = RepeatedToolCallMiddleware()
    msgs = [
        _human("do it"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "pkill node"})]),
    ]
    ov = _overrides(mw, msgs)
    assert "tools" not in ov
    assert "WARNING" in str(ov["messages"][-1].content)


def test_varying_args_tool_turns_no_override():
    # 连续工具轮（参数不同）完全不干预：与 opencode(默认 Infinity)/codex(无上限)
    # 对齐——真实多步任务需要大量工具调用，只有真死循环（同一调用/重复文本/退化）才拦。
    mw = RepeatedToolCallMiddleware()
    msgs = [
        _human("do it"),
        _ai("", [_call("run_command", {"command": "a"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "b"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "c"})]),
        _tool("run_command", "ok"),
        _ai("", [_call("run_command", {"command": "d"})]),
    ]
    ov = _overrides(mw, msgs)
    assert ov == {}


def test_consecutive_identical_text_stops():
    mw = RepeatedToolCallMiddleware(text_stop_after=4)
    msgs = [
        _human("hi"),
        _ai("same answer"),
        _human("again"),
        _ai("same answer"),
        _human("again"),
        _ai("same answer"),
        _human("again"),
        _ai("same answer"),
    ]
    ov = _overrides(mw, msgs)
    assert "tools" not in ov  # W3: text-repeat does not strip tools
    assert "identical reply" in str(ov["messages"][-1].content)


def test_single_message_degenerate_repetition_stops():
    mw = RepeatedToolCallMiddleware()
    degenerate = "讓我搜索一下 Ego Browser 的緩存位置：\n\n" * 20
    # Same-turn continuation: last message is the degenerate assistant reply
    # (no newer user message) — the qwen3 loop.
    msgs = [
        _human("清理一下coworker內置瀏覽器的緩存。"),
        _ai(degenerate),
    ]
    ov = _overrides(mw, msgs)
    assert "tools" not in ov  # W3: degenerate text does not strip tools
    assert "degenerated into endless repetition" in str(ov["messages"][-1].content)


def test_fresh_user_question_not_hijacked_by_stale_degenerate_history():
    mw = RepeatedToolCallMiddleware()
    degenerate = "讓我搜索一下 Ego Browser 的緩存位置：\n\n" * 20
    # The degenerate reply is in history, but a NEW user question came after it
    # — the user wants a real answer, never a forced text-only stop.
    msgs = [
        _human("清理一下coworker內置瀏覽器的緩存。"),
        _ai(degenerate),
        _human("順便看看 package.json"),
    ]
    assert _overrides(mw, msgs) == {}


def test_many_tool_calls_no_override():
    # 大量工具调用（即使超过任意数值）完全不干预：legit 任务可超 100+ 次调用，
    # 次数类守卫是「多调几次工具就卡」的源头，已整体移除。
    mw = RepeatedToolCallMiddleware()
    msgs = [_human("go")]
    for i in range(60):
        msgs += [_ai("", [_call("run_command", {"command": f"cmd {i}"})]), _tool("run_command", "ok")]
    ov = _overrides(mw, msgs)
    assert ov == {}


def test_normal_short_task_no_override():
    mw = RepeatedToolCallMiddleware()
    msgs = [
        _human("read the file"),
        _ai("", [_call("read_file", {"path": "a.py"})]),
        _tool("read_file", "ok"),
        _ai("the file says hello"),
    ]
    assert _overrides(mw, msgs) == {}


def test_aclose_on_exit_closes_underlying_on_cancel():
    async def scenario():
        closed = asyncio.Event()

        async def inner():
            try:
                for i in range(1000):
                    yield i
                    await asyncio.sleep(0.01)
            finally:
                closed.set()

        wrapper = _aclose_on_exit(inner())

        # Simulate the _sse_events producer: consume one item, then block on
        # something else (e.g. queue.put) while the generator chain is suspended
        # at a `yield`. A task.cancel() that lands on that other await would
        # leave the generator suspended — only an explicit aclose() can tear the
        # chain down (the "vLLM still running after Stop" leak).
        await wrapper.__anext__()
        await wrapper.aclose()

        await asyncio.wait_for(closed.wait(), timeout=2)
        assert closed.is_set()

    asyncio.run(scenario())


def test_pytest_collect():
    assert RepeatedToolCallMiddleware is not None
