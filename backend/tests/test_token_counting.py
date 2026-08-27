"""六、Token 計數不正確／不一致 (T1–T4) tests.

- T1: single counting source — the char-budget mirror derives from the SAME
      Latin chars/token constant as the estimator (no independent CHARS_PER_TOKEN).
- T2: CJK detection covers full-width punctuation / kana / hangul / Ext A, and
      dense text is not under-counted as Latin.
- T3: calibration folds cache-INCLUSIVE actual input tokens (cached tokens still
      occupy the window).
- T4: bare-base64 threshold lowered (short blobs not counted as prose).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent import core as agent_core  # noqa: E402
from coworker.agent.core import (  # noqa: E402
    _normalize_usage_total,
    context_budget_chars,
    context_budget_tokens,
)
from coworker.context import (  # noqa: E402
    BASE64_MIN_RUN,
    LATIN_CHARS_PER_TOKEN,
    _BASE64_RUN_RE,
    _cjk_count,
    estimate_text_tokens,
)


# --- T1: single counting source -----------------------------------------------


def test_no_independent_chars_per_token_constant():
    assert not hasattr(agent_core, "CHARS_PER_TOKEN")


def test_char_budget_derives_from_estimator_constant():
    bc = context_budget_chars(128_000, 8192)
    bt = context_budget_tokens(128_000, 8192)
    # The char mirror must sit on the SAME Latin ratio as the estimator.
    assert bc / bt <= LATIN_CHARS_PER_TOKEN + 0.01
    assert bc / bt >= LATIN_CHARS_PER_TOKEN - 0.01


# --- T2: CJK range coverage ---------------------------------------------------


def test_cjk_dense_chars_counted():
    assert _cjk_count("，。！？") == 4  # full-width punctuation
    assert _cjk_count("テストかたかな") == 7  # hiragana + katakana
    assert _cjk_count("한글테스트") == 5  # hangul
    assert _cjk_count("㐀㐁") == 2  # CJK Extension A
    assert _cjk_count("abc def") == 0  # plain Latin not CJK


def test_fullwidth_latin_not_dense():
    # Full-width Latin letters/digits tokenize ≈1 each — not CJK-dense.
    assert _cjk_count("ＡＢＣ１２３") == 0


def test_cjk_text_costs_more_than_latin():
    cjk = "汉" * 400
    latin = "x" * 400
    assert estimate_text_tokens(cjk) > estimate_text_tokens(latin)


# --- T3: cache-inclusive calibration -------------------------------------------


def test_normalize_usage_total_includes_cache():
    # Anthropic-style input_token_details.cache_read
    assert _normalize_usage_total({"input_tokens": 100, "output_tokens": 50, "input_token_details": {"cache_read": 300}}) == (400, 50)
    # OpenAI-style prompt_tokens_details.cached_tokens
    assert _normalize_usage_total({"prompt_tokens": 100, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 200}}) == (300, 50)
    # No cache fields → same as base.
    assert _normalize_usage_total({"input_tokens": 100, "output_tokens": 50}) == (100, 50)


# --- T4: bare-base64 threshold -------------------------------------------------


def test_base64_threshold_lowered():
    assert BASE64_MIN_RUN == 128
    # A 130-char run is now a base64 candidate (was missed at 256).
    m = _BASE64_RUN_RE.search("x" * 130)
    assert m is not None and len(m.group(0)) >= 128
