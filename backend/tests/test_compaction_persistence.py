"""五、壓縮精簡不正確 (C1-C4) tests.

- C1: compaction summary + fingerprints persist to the session and survive
      across turns / goal rounds (the per-turn LangGraph checkpoint is
      discarded); the runtime re-injects them at turn start.
- C3: the summarizer uses the default model; a failed summarizer flags
      ``context_compact_failed`` so the runtime can prompt the user.
- C4: oversized messages are truncated to a TOKEN budget (not a chars ratio).
- C2: summary input budget aligned with codex (20k).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.middleware.base import (  # noqa: E402
    SUMMARY_INPUT_MAX_TOKENS,
    SUMMARY_OUTPUT_TOKENS,
)
from coworker.agent.middleware.context_compaction import (  # noqa: E402
    CoworkerSummarizationMiddleware,
    _summarizer_candidates,
)
from coworker.sessions import SessionStore  # noqa: E402


# --- C1: session persistence + runtime re-injection ----------------------------


def test_session_compaction_round_trip(tmp_path: Path):
    store = SessionStore(tmp_path)
    s = store.create("t")
    store.update_compaction(s.id, summary="SUMMARY_S1", fingerprints=["f1", "f2"], count=2)
    loaded = store.load(s.id)
    assert loaded.context_summary == "SUMMARY_S1"
    assert loaded.context_summarized_fingerprints == ["f1", "f2"]
    assert loaded.context_compact_count == 2
    # Reload from disk (fresh store instance) proves it persisted.
    assert SessionStore(tmp_path).load(s.id).context_summary == "SUMMARY_S1"


def test_runtime_prepends_prior_summary_message():
    from langchain_core.messages import AIMessage, HumanMessage

    from coworker.agent.runtime import _prepend_compaction_summary

    base = [HumanMessage(content="user msg"), AIMessage(content="assistant")]
    out = _prepend_compaction_summary(base, "SUMMARY_XYZ", "zh")
    assert len(out) == 3
    assert out[0].type == "human"
    assert "SUMMARY_XYZ" in out[0].content
    assert "先前对话摘要" in out[0].content
    assert out[1] is base[0] and out[2] is base[1]  # prepared messages untouched

    # Empty summary → unchanged.
    assert _prepend_compaction_summary(base, "", "zh") is base


# --- C3: summarizer uses the default model; failure flags ----------------------


def test_summarizer_candidates_use_default_model():
    llm = object()
    assert _summarizer_candidates(None, llm) == [llm]
    assert _summarizer_candidates(Path("/nonexistent"), llm) == [llm]
    assert _summarizer_candidates(None, None) == []


def test_compact_failure_sets_flag():
    """A summarize plan that produces no usable summary (default model failed)
    must set ``context_compact_failed`` AND still trim."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    class _FailingLLM:
        def invoke(self, *a, **k):
            raise RuntimeError("default model unavailable")

        async def ainvoke(self, *a, **k):
            raise RuntimeError("default model unavailable")

    # Build a state whose message list is well over the 8k keep window AND the
    # tight token budget, so a "summarize" plan is selected.
    messages = [HumanMessage(content="system-ish first message", id="h0")]
    for i in range(120):
        messages.append(HumanMessage(content=f"long message {i} " + "x" * 400, id=f"u{i}"))
    state = {"messages": messages, "language": "zh"}

    mw = CoworkerSummarizationMiddleware(
        llm=_FailingLLM(),
        budget_chars=2_000_000,
        context_window_tokens=8_000,
        summarizer_candidates=[_FailingLLM()],
        language="zh",
        max_output_tokens=1024,
    )
    # Tighten the budget so the list overflows it.
    mw.budget_tokens = 500
    result = mw._compact_sync(state)
    assert result is not None
    assert result.get("context_compact_failed") is True
    assert "messages" in result  # still trimmed
    assert result.get("context_compact_count") == 1


# --- C4: token-accurate truncation ---------------------------------------------


def test_truncate_message_to_tokens_bounded():
    from coworker.agent.core import _estimate_tokens
    from langchain_core.messages import HumanMessage

    from coworker.agent.middleware.context_compaction import CoworkerSummarizationMiddleware

    big = HumanMessage(content="x" * 20_000)
    out = CoworkerSummarizationMiddleware._truncate_message_to_tokens(big, 300)
    assert _estimate_tokens(out.content) <= 320
    assert "[content truncated" in out.content or "truncated" in out.content

    # Small message is untouched.
    small = HumanMessage(content="tiny")
    assert CoworkerSummarizationMiddleware._truncate_message_to_tokens(small, 300) is small


# --- C2: summary input budget aligned -------------------------------------------


def test_summary_input_budget_aligned():
    assert SUMMARY_INPUT_MAX_TOKENS == 20_000  # codex COMPACT_USER_MESSAGE_MAX_TOKENS
    assert SUMMARY_OUTPUT_TOKENS == 4_096  # opencode legacy cap
