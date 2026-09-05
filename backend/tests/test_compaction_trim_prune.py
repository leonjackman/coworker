"""F5: emergency `_trim` is prune-first.

When the summarizer is unavailable/failed, the emergency trim must clear stale
tool blobs BEFORE dropping conversational messages, so the agent's decisions
and the user's recent intent survive.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from coworker.agent.middleware import RecoveryToolClearEdit  # noqa: E402
from coworker.agent.middleware.context_compaction import (  # noqa: E402
    CoworkerSummarizationMiddleware,
)
from coworker.context import estimate_text_tokens  # noqa: E402


def _msg_tokens(m):
    return estimate_text_tokens(str(getattr(m, "content", "") or ""))


def _middleware(edit=None):
    mw = CoworkerSummarizationMiddleware(
        budget_chars=20_000,
        llm=None,
        summarizer_candidates=[],
        tool_edit=edit,
    )
    return mw


def _big_transcript(n_tool=8, big_size=6_000):
    messages = [HumanMessage(content="開始執行重構")]
    for i in range(n_tool):
        messages.append(
            AIMessage(content="", tool_calls=[{"name": "run_command", "args": {}, "id": f"c{i}", "type": "tool_call"}])
        )
        messages.append(ToolMessage(content='{"return_code": 0, "stdout": "' + "x" * big_size + '", "stdout_truncated": true}', tool_call_id=f"c{i}"))
    messages.append(AIMessage(content="我已分析完畢，結論是改 A 不改 B。"))
    messages.append(HumanMessage(content="好，繼續按結論執行。"))
    return messages


def test_trim_prune_first_keeps_conversational_messages():
    mw = _middleware(edit=RecoveryToolClearEdit(trigger=0, keep=0))
    messages = _big_transcript()
    total = sum(_msg_tokens(m) for m in messages)
    assert total > 10_000
    mw.budget_tokens = 600  # far below total: prune alone must fit

    updates = mw._trim({"messages": messages})
    assert updates is not None
    kept = updates["messages"][1:]  # first element is the RemoveMessage sentinel
    # prune-first: clearing tool blobs is enough — nothing conversational dropped
    assert any(getattr(m, "type", "") == "human" and "繼續按結論執行" in str(m.content) for m in kept)
    assert any(getattr(m, "type", "") == "ai" and "我已分析完畢" in str(m.content) for m in kept)
    # old tool blobs were cleared to recovery placeholders, not kept in full
    tool_bodies = [str(m.content) for m in kept if getattr(m, "type", "") == "tool"]
    assert tool_bodies and all(b.startswith("[tool result cleared") or b.startswith("[command output cleared") for b in tool_bodies)


def test_trim_tail_fallback_without_edit_drops_oldest_keeps_last_user():
    mw = _middleware(edit=None)  # no cheap layer: falls through to tail trim
    messages = _big_transcript(n_tool=12, big_size=2_000)
    mw.budget_tokens = 1_200
    updates = mw._trim({"messages": messages})
    assert updates is not None
    kept = updates["messages"][1:]
    text = " ".join(str(m.content) for m in kept)
    assert "繼續按結論執行" in text  # most recent user always preserved
    assert updates.get("context_compact_count") == 1
