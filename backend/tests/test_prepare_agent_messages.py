"""prepare_agent_messages tool-history passthrough tests.

Guards the 降智 root cause: ``_parts_to_conversation`` rebuilds the standard
``assistant(tool_calls) → tool(result)`` pairs, but ``prepare_agent_messages``
was stripping ``role="tool"`` messages and the assistant ``tool_calls`` key
before they reached the model. The model then saw only narration-only assistant
messages across turns and imitated that ("先查看當前狀態，再一次性 commit：" then
stop) — the exact degradation reproduced in session 2d8080e8.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as _main_module  # noqa: E402
from coworker.agent.core import prepare_agent_messages  # noqa: E402
from coworker.sessions import SessionMessage  # noqa: E402


def _assistant(parts=None, content="assistant"):
    return SessionMessage(
        id="m1",
        role="assistant",
        content=content,
        created_at="2026-01-01T00:00:00",
        parts=parts or [],
    )


def _tool_part(pid="tc-1", name="run_command", input='{"command": "ls"}', output='{"ok": true}'):
    return {"type": "tool", "id": pid, "name": name, "status": "success", "input": input, "output": output}


def test_plain_user_and_text_assistant_still_pass():
    out = prepare_agent_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant"]


def test_tool_messages_preserved_with_tool_call_id():
    out = prepare_agent_messages(
        [{"role": "tool", "tool_call_id": "tc-1", "content": '{"ok": true}'}]
    )
    assert len(out) == 1
    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"] == "tc-1"
    assert '{"ok": true}' in out[0]["content"]


def test_assistant_tool_calls_key_preserved():
    tc = [{"id": "tc-1", "type": "function", "function": {"name": "run_command", "arguments": '{"command": "ls"}'}}]
    out = prepare_agent_messages(
        [{"role": "assistant", "content": "let me look", "tool_calls": tc}]
    )
    assert out[0]["role"] == "assistant"
    assert out[0]["tool_calls"] == tc


def test_tool_calls_only_assistant_with_none_content_preserved():
    tc = [{"id": "tc-1", "type": "function", "function": {"name": "run_command", "arguments": "{}"}}]
    out = prepare_agent_messages(
        [{"role": "assistant", "content": None, "tool_calls": tc}]
    )
    assert len(out) == 1
    assert out[0]["tool_calls"] == tc


def test_roundtrip_converts_to_toolmessage_and_aimessage():
    from langchain_core.messages import convert_to_messages

    history = _main_module._parts_to_conversation(
        _assistant(
            parts=[
                {"type": "text", "content": "Let me check git\n"},
                _tool_part(pid="tc-1", name="git_status", output="clean"),
            ]
        )
    )
    assert history[0]["tool_calls"], "precondition: _parts_to_conversation emits tool_calls"
    prepared = prepare_agent_messages(history)
    roles = [m["role"] for m in prepared]
    assert "tool" in roles and "assistant" in roles

    msgs = convert_to_messages(prepared)
    assert type(msgs[0]).__name__ == "AIMessage"
    assert msgs[0].tool_calls and msgs[0].tool_calls[0]["id"] == "tc-1"
    assert type(msgs[1]).__name__ == "ToolMessage"
    assert msgs[1].tool_call_id == "tc-1"


def test_degraded_session_history_no_longer_narration_only():
    """Regression: the turn-D history of session 2d8080e8 (a big tool-heavy
    first turn) must survive prepare_agent_messages WITH its tool calls/results,
    not collapse into bare narration the model then imitates."""
    m = _assistant(
        parts=[
            {"type": "text", "content": "Right, my workspace is the website.\n"},
            _tool_part(pid="tc-1", name="run_command", input='{"command": "pwd"}', output='{"command": ["pwd"]}'),
            {"type": "text", "content": "Now the main App.tsx with the Hero section\n"},
            _tool_part(pid="tc-2", name="write_file", input='{"file_path": "src/App.tsx"}', output="Wrote src/App.tsx"),
            {"type": "text", "content": "Everything looks clean. Let me now update the todo list"},
        ]
    )
    history = _main_module._parts_to_conversation(m)
    prepared = prepare_agent_messages(history)
    tc_msgs = [x for x in prepared if x.get("role") == "assistant" and x.get("tool_calls")]
    tool_msgs = [x for x in prepared if x.get("role") == "tool"]
    assert len(tc_msgs) == 2, "both narration+tool assistant turns must keep tool_calls"
    assert len(tool_msgs) == 2, "both tool results must survive"
    assert "write_file" in {t["tool_calls"][0]["function"]["name"] for t in tc_msgs}


def test_empty_input_tool_part_does_not_crash_convert():
    """Regression: a persisted tool part with EMPTY input (from the earlier
    tool-input-capture bug) must not produce `arguments: ""` — LangChain's
    convert_to_messages json.loads() would raise
    `Expecting value: line 1 column 1 (char 0)`."""
    from langchain_core.messages import convert_to_messages

    m = _assistant(
        parts=[
            {"type": "text", "content": "write it\n"},
            {
                "type": "tool",
                "id": "tc-empty",
                "name": "write_file",
                "status": "success",
                "input": "",  # empty input — legacy bug residue
                "output": '{"path": "x.txt"}',
            },
        ]
    )
    history = _main_module._parts_to_conversation(m)
    tc = history[0]["tool_calls"][0]
    assert tc["function"]["arguments"] == "{}", "empty input must become {}"

    prepared = prepare_agent_messages(history)
    msgs = convert_to_messages(prepared)  # must NOT raise JSONDecodeError
    assert type(msgs[0]).__name__ == "AIMessage"
    assert msgs[0].tool_calls[0]["args"] == {}


def test_non_json_string_input_is_reserialized():
    """A legacy tool input that is a bare non-JSON string must be reserialized
    into a valid JSON string (never passed through raw, which would crash the
    convert_to_messages json.loads())."""
    from langchain_core.messages import convert_to_messages

    m = _assistant(
        parts=[
            {"type": "text", "content": "run it\n"},
            {
                "type": "tool",
                "id": "tc-bad",
                "name": "run_command",
                "status": "success",
                "input": "this is not json",  # legacy malformed input
                "output": '{"ok": true}',
            },
        ]
    )
    history = _main_module._parts_to_conversation(m)
    tc = history[0]["tool_calls"][0]
    assert tc["function"]["arguments"] == "{}", "non-object input must become {}"

    prepared = prepare_agent_messages(history)
    msgs = convert_to_messages(prepared)  # must NOT raise JSONDecodeError / ValidationError
    assert msgs[0].tool_calls[0]["args"] == {}
