"""WS3: the verification guard must block a success claim that follows an
unconfirmed desktop mutation, and must not fire otherwise."""

import json
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from coworker.agent.middleware.verification_guard import VerificationGuardMiddleware  # noqa: E402


def _click(verified=None, action="click_ref"):
    payload = {"ok": True, "action": action, "verified": verified}
    return ToolMessage(content=json.dumps(payload), name="computer", tool_call_id="t1")


def _run(messages, done=False):
    state = {"messages": messages, "verification_guard_done": done}
    return VerificationGuardMiddleware().after_model(state, runtime=None)


def test_blocks_success_after_unverified_click():
    messages = [
        HumanMessage(content="save it to the desktop"),
        AIMessage(content="", tool_calls=[{"name": "computer", "args": {}, "id": "c1"}]),
        _click(verified=None),
        AIMessage(content="✅ 已保存到桌面"),
    ]
    result = _run(messages)
    assert result is not None
    assert result["jump_to"] == "model"
    assert result["verification_guard_done"] is True
    assert len(result["messages"]) == 1


def test_allows_when_action_verified_true():
    messages = [
        HumanMessage(content="open pages"),
        AIMessage(content="", tool_calls=[{"name": "computer", "args": {}, "id": "c1"}]),
        _click(verified=True, action="launch_app"),
        AIMessage(content="已成功打開"),
    ]
    assert _run(messages) is None


def test_allows_when_run_command_followed():
    messages = [
        HumanMessage(content="save it"),
        AIMessage(content="", tool_calls=[{"name": "computer", "args": {}, "id": "c1"}]),
        _click(verified=None),
        ToolMessage(content=json.dumps({"return_code": 0, "stdout": "ok"}), name="run_command", tool_call_id="t2"),
        AIMessage(content="已保存"),
    ]
    assert _run(messages) is None


def test_allows_without_success_claim():
    messages = [
        HumanMessage(content="click it"),
        AIMessage(content="", tool_calls=[{"name": "computer", "args": {}, "id": "c1"}]),
        _click(verified=None),
        AIMessage(content="我點擊了按鈕，但不確定是否生效。"),
    ]
    assert _run(messages) is None


def test_allows_when_already_nudged():
    messages = [
        HumanMessage(content="save it"),
        AIMessage(content="", tool_calls=[{"name": "computer", "args": {}, "id": "c1"}]),
        _click(verified=None),
        AIMessage(content="已保存"),
    ]
    assert _run(messages, done=True) is None


def test_ignores_tool_call_rounds():
    messages = [
        HumanMessage(content="save it"),
        _click(verified=None),
        AIMessage(content="已保存", tool_calls=[{"name": "computer", "args": {}, "id": "c2"}]),
    ]
    assert _run(messages) is None


def test_ignores_failures():
    error = ToolMessage(
        content=json.dumps({"error": "computer_error: no AX element", "error_code": "computer_error"}),
        name="computer", tool_call_id="t1",
    )
    messages = [HumanMessage(content="click"), error, AIMessage(content="已保存")]
    assert _run(messages) is None


def test_resets_at_user_boundary():
    """An unverified click in a PREVIOUS turn must not nudge the current one."""
    messages = [
        HumanMessage(content="first"),
        _click(verified=None),
        HumanMessage(content="second"),
        AIMessage(content="已完成"),
    ]
    assert _run(messages) is None
