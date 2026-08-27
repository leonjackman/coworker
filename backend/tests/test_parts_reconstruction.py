"""Tool-result history reconstruction tests.

These guard the "assistant turn collapse" fix: a persisted assistant message
stores its interleaved text + tool results as ``parts``. Replaying only
``message.content`` would drop every tool round and invite the model to
imitate bare chatter on continuation (goal) rounds — the observed degraded
"spinning" failure mode. ``_parts_to_conversation`` must rebuild the standard
``assistant(tool_calls) -> tool(result)`` pairs instead.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as _main_module  # noqa: E402
from coworker.sessions import SessionMessage  # noqa: E402


def _msg(*, parts=None, content="", role="assistant"):
    return SessionMessage(
        id="m1",
        role=role,
        content=content,
        created_at="2026-01-01T00:00:00",
        parts=parts or [],
    )


def test_plain_content_fallback():
    m = _msg(content="just text")
    assert _main_module._parts_to_conversation(m) == [{"role": "assistant", "content": "just text"}]


def test_no_parts_empty_content():
    m = _msg(content="")
    out = _main_module._parts_to_conversation(m)
    assert out and out[0]["role"] == "assistant"


def test_single_text_part():
    m = _msg(parts=[{"type": "text", "content": "hello"}])
    out = _main_module._parts_to_conversation(m)
    assert len(out) == 1
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "hello"


def test_text_then_tool_calls_collapse_into_one_assistant():
    m = _msg(
        parts=[
            {"type": "text", "content": "Let me look at this\n"},
            {
                "type": "tool",
                "id": "tc-1",
                "name": "read_file",
                "status": "success",
                "input": '{"file_path": "package.json"}',
                "output": '{"content": "x"}',
            },
        ]
    )
    out = _main_module._parts_to_conversation(m)
    assert len(out) == 2
    assert out[0]["role"] == "assistant"
    assert "Let me look" in out[0]["content"]
    assert out[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "tc-1"


def test_interleaved_text_and_tools():
    m = _msg(
        parts=[
            {"type": "text", "content": "first narration\n"},
            {
                "type": "tool",
                "id": "tc-1",
                "name": "search_files",
                "status": "success",
                "input": '{"query": "a"}',
                "output": '{"files": []}',
            },
            {"type": "text", "content": "second narration\n"},
            {
                "type": "tool",
                "id": "tc-2",
                "name": "read_file",
                "status": "success",
                "input": '{"file_path": "f.py"}',
                "output": '{"content": "c"}',
            },
            {"type": "text", "content": "final summary"},
        ]
    )
    out = _main_module._parts_to_conversation(m)
    # assistant(1) tool(1) assistant(2) tool(2) assistant(final)
    assert len(out) == 5
    assert out[0]["role"] == "assistant" and "first narration" in out[0]["content"]
    assert out[0]["tool_calls"][0]["function"]["name"] == "search_files"
    assert out[1]["role"] == "tool" and out[1]["tool_call_id"] == "tc-1"
    assert out[2]["role"] == "assistant" and "second narration" in out[2]["content"]
    assert out[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "tc-2"
    assert out[4]["role"] == "assistant" and "final summary" in out[4]["content"]


def test_user_messages_preserved_by_history_builder():
    session = type("S", (), {"messages": [_msg(role="user", content="hi"), _msg(parts=[])]})()
    history = _main_module._session_message_history(session)
    assert history[0]["role"] == "user"
    # format_user_message wraps plain text as a multimodal part list.
    assert "hi" in str(history[0]["content"])


def test_tool_result_replays_output_full_not_display_cap():
    """O1 display/persist split: the model replay must use `output_full` (the
    full persisted copy), never the display-capped `output` the frontend sees."""
    full = "FULL_RESULT_" + "x" * 5000
    display = full[:2000]
    m = _msg(
        content="assistant",
        parts=[{"type": "tool", "id": "t1", "name": "read_file", "status": "success", "input": "a.txt", "output": display, "output_full": full}],
    )
    out = _main_module._parts_to_conversation(m)
    assistant = next(c for c in out if c.get("role") == "assistant" and c.get("tool_calls"))
    result = assistant["tool_calls"][0]["result"]
    # The replayed result carries the full content (far beyond the 2000-char
    # display cap the frontend sees).
    assert "FULL_RESULT_" in result
    assert len(result) > 4000
    assert result != display
