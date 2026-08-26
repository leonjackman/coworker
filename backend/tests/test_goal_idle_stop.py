"""Goal-idle-stop guard tests.

The goal continuation loop must stop once a round made no real progress (pure
text, no tool executed) — otherwise it spins forever (qwen3 fragmented replies
like "Done. Key improvements…" each round), inflating context until a recursion
error. Guarded by ``_goal_round_has_tool_execution``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as _main_module  # noqa: E402
from coworker.sessions import SessionMessage  # noqa: E402


def _session(*messages):
    return type("S", (), {"messages": list(messages)})()


def _assistant(content="", parts=None):
    return SessionMessage(
        id="a1",
        role="assistant",
        content=content,
        created_at="2026-01-01T00:00:00",
        parts=parts or [],
    )


def test_pure_text_round_returns_false():
    """A text-only assistant turn (fragmented/spinning) must stop the loop."""
    m = _assistant(content="Done. Key improvements made:\n1. Removed unsafe regex")
    assert _main_module._goal_round_has_tool_execution(_session(m)) is False


def test_tool_round_returns_true():
    """A round that executed a real tool must continue (legal multi-step tasks)."""
    m = _assistant(
        content="fixing",
        parts=[
            {"type": "text", "content": "Let me fix"},
            {
                "type": "tool",
                "id": "tc-1",
                "name": "replace_in_file",
                "status": "success",
                "input": '{"file_path": "x.ts"}',
                "output": '{"replacements": 1}',
            },
        ],
    )
    assert _main_module._goal_round_has_tool_execution(_session(m)) is True


def test_read_only_round_returns_true():
    """A read-only research round still counts as progress (tool executed)."""
    m = _assistant(
        parts=[
            {"type": "tool", "id": "tc-1", "name": "read_file", "status": "success", "input": "{}", "output": "x"},
        ]
    )
    assert _main_module._goal_round_has_tool_execution(_session(m)) is True


def test_empty_session_returns_false():
    assert _main_module._goal_round_has_tool_execution(_session()) is False


def test_user_last_returns_false():
    """If the latest message is a user message there is no round progress to judge."""
    u = SessionMessage(id="u", role="user", content="hi", created_at="t", parts=[])
    assert _main_module._goal_round_has_tool_execution(_session(u)) is False


def test_non_dict_parts_returns_false():
    m = _assistant(parts=[{"type": "text", "content": "hi"}, "not-a-dict"])
    assert _main_module._goal_round_has_tool_execution(_session(m)) is False
