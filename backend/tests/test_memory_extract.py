"""E1/E2 one-step dream tests: a SINGLE merged LLM call extracts new facts and
merges them into existing memory blocks; guardrails are RULE-based (coverage +
size budget) with NO second LLM verify call (hermes/codex mainstream)."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.memory.auto_extract import run_extract_and_merge  # noqa: E402


class _CountingLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return type("R", (), {"content": self.response})()


def _mkllm(blocks, new=None):
    return _CountingLLM(json.dumps({"blocks": blocks, "new": new if new is not None else []}))


TRANSCRIPT = [
    {"role": "user", "content": "请用中文回复"},
    {"role": "assistant", "content": "好的，backend 跑在 9527 端口"},
]


def _run(**kw):
    return asyncio.run(run_extract_and_merge(**kw))


def test_merge_is_single_call_and_preserves_existing():
    llm = _mkllm(["用中文回复", "backend 端口 9527", "新增事实"], new=["新增事实"])
    r = _run(llm=llm, messages=TRANSCRIPT, existing_blocks=["用中文回复"], session_id="s1", max_total_chars=4000)
    assert llm.calls == 1  # no extract→consolidate→verify chain
    assert r["blocks"] == ["用中文回复", "backend 端口 9527", "新增事实"]
    assert r["new"] == ["新增事实"]
    assert r["added"] == 1


def test_merge_guardrail_coverage_rejects_without_second_call():
    # The model drops ALL prior facts → rule guardrail rejects (append-only).
    llm = _mkllm(["只保留新事实"], new=["只保留新事实"])
    r = _run(llm=llm, messages=TRANSCRIPT, existing_blocks=["必须保留的既有事实"], session_id="s1", max_prior_loss=0.25, max_total_chars=4000)
    assert llm.calls == 1  # NO _verify_preservation LLM call
    assert r["blocks"] is None
    assert r["new"] == ["只保留新事实"]  # fallback candidates preserved
    assert "guardrail" in r["note"]


def test_merge_guardrail_budget_rejects():
    llm = _mkllm(["x" * 5000], new=["x" * 5000])
    r = _run(llm=llm, messages=TRANSCRIPT, existing_blocks=[], session_id="s1", max_total_chars=1000)
    assert llm.calls == 1
    assert r["blocks"] is None
    assert "too large" in r["note"]


def test_merge_unparseable_degrades_safely():
    llm = _CountingLLM("definitely not json")
    r = _run(llm=llm, messages=TRANSCRIPT, existing_blocks=["old"], session_id="s1")
    assert llm.calls == 1
    assert r["blocks"] is None
    assert r["new"] == []


def test_merge_no_transcript_skips():
    llm = _mkllm(["whatever"])
    r = _run(llm=llm, messages=[], existing_blocks=["old"], session_id="s1")
    assert llm.calls == 0  # nothing to review → no LLM call at all
    assert r["blocks"] is None
