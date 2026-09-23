"""Reasoning-stream display regressions.

OpenAI-compatible reasoning models (DeepSeek, ...) stream ``reasoning_content``
incrementally, and the per-token text carries significant leading whitespace
(e.g. ``" user"``). Stripping each chunk used to concatenate into
``"Theuserasks…"`` (word spaces lost) — the frontend accumulates these tokens
verbatim, so the extractor must not trim them. These tests pin that behaviour.
"""

import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker.agent.core import _extract_reasoning_from_chunk  # noqa: E402


class _Chunk:
    def __init__(self, **kwargs):
        self.additional_kwargs = kwargs


def test_reasoning_tokens_preserve_whitespace():
    tokens = ["The", " user", " asks", " about", " balance。"]
    joined = "".join(_extract_reasoning_from_chunk(_Chunk(reasoning=t)) or "" for t in tokens)
    assert joined == "The user asks about balance。"


def test_reasoning_blank_chunk_is_none():
    assert _extract_reasoning_from_chunk(_Chunk(reasoning="  ")) is None
    assert _extract_reasoning_from_chunk(_Chunk()) is None


def test_reasoning_content_fallback_preserved():
    assert _extract_reasoning_from_chunk(_Chunk(reasoning_content=" hi")) == " hi"
