"""Tests for CoworkerSummarizationMiddleware._trim: the most recent user message
must never be dropped by rolling-window compaction (vLLM 400 root cause)."""

import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_trim_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from coworker.agent.middleware.context_compaction import CoworkerSummarizationMiddleware  # noqa: E402
from coworker.agent.core import _msg_tokens  # noqa: E402


def _tool_call(msg_id="t1", name="read_file"):
    return {"name": name, "args": {}, "id": msg_id, "type": "tool_call"}


def _make_middleware(budget_tokens: int) -> CoworkerSummarizationMiddleware:
    return CoworkerSummarizationMiddleware(
        budget_chars=20_000,
        context_window_tokens=budget_tokens,
        max_output_tokens=0,
    )


def test_trim_keeps_latest_user_message():
    """Regression for the vLLM 400: a tight-budget trim that would otherwise keep
    only [system, assistant(tool)+tool...] must still keep the user message."""
    # One user message followed by a LONG tool loop whose frames alone overflow
    # the (floored) token budget — the tail fills with tool frames, pushing the
    # user message into the dropped middle.
    messages: list = [SystemMessage(content="sys")]
    messages.append(HumanMessage(content="instruction " * 60))
    for i in range(20):
        messages.append(
            AIMessage(content="", tool_calls=[_tool_call(msg_id=f"t{i}", name="read_file")])
        )
        messages.append(ToolMessage(content=f"result {i} " * 120, tool_call_id=f"t{i}"))

    mw = _make_middleware(budget_tokens=5_000)
    assert sum(_msg_tokens(m) for m in messages) > mw.budget_tokens, "fixture must overflow"

    out = mw._trim({"messages": messages})
    assert out is not None, "expected a trim"
    kept = [m for m in out["messages"] if not getattr(m, "type", "") == "remove"]
    # The kept list must contain at least one human message.
    assert any(getattr(m, "type", "") == "human" for m in kept), "user message was dropped"
    # The system message stays first.
    assert kept[0].type == "system"


def test_trim_userless_tail_still_keeps_human():
    """Even when the recent tail is pure tool frames, the last human survives."""
    messages = [SystemMessage(content="sys"), HumanMessage(content="do the work")]
    for i in range(6):
        messages.append(AIMessage(content="", tool_calls=[_tool_call(msg_id=f"t{i}")]))
        messages.append(ToolMessage(content="y" * 4500, tool_call_id=f"t{i}"))
    mw = _make_middleware(budget_tokens=5_000)
    assert sum(_msg_tokens(m) for m in messages) > mw.budget_tokens
    out = mw._trim({"messages": messages})
    assert out is not None
    kept = [m for m in out["messages"] if not getattr(m, "type", "") == "remove"]
    assert any(getattr(m, "type", "") == "human" for m in kept)


def test_trim_within_budget_is_noop():
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    mw = _make_middleware(budget_tokens=5_000)
    assert mw._trim({"messages": messages}) is None
