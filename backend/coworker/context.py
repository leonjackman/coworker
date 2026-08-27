"""Unified context accounting: calibrated token measurement for model requests.

Single source of truth for "how big is this request, really". Every consumer —
the topbar meter, the trim/compact budget, the tool-result clearing trigger and
the pre-send guard — measures through this module so they can never disagree
about message size again (the B-series bug: four different under-counting
heuristics, none of which counted the system prompt, tool schemas, per-message
template overhead or the provider's reserved output tokens).

Design (industry-aligned, framework-primitive based):

* Content-class aware base estimator: CJK (~0.6 tokens/char), Latin prose
  (~3.8 chars/token) and — critically — base64/data-URL runs, which tokenize
  ~2.8x denser than prose on modern BPE tokenizers (measured 1.4 chars/token).
  Screenshots smuggled into tool results as truncated base64 were the incident
  class that blew the window while the meter showed 50%.
* Closed-loop calibration, same formula as langchain-core's
  ``count_tokens_approximately(use_usage_metadata_scaling=True)``: scale the
  approximation by ``actual_usage_tokens / approximation`` observed on the most
  recent model call. The factor is persisted per (provider, model) so it
  survives process restarts and session-history rebuilds (which drop
  usage_metadata), and is also fed by provider 400 overflow errors that report
  the real input token count — one overflow makes the next turn self-correct.
* ``measure_request`` counts what actually goes over the wire: system prompt +
  tool schemas + messages + per-message template overhead, times calibration —
  mirroring ``count_tokens_approximately(tools=..., extra_tokens_per_message=...)``.
* Binary-blob scrubbing helpers: base64/data-URL payloads are useless as text
  (a truncated data URL is a corrupted image) and cost ~36k tokens per 50k
  chars; ``scrub_text`` replaces them with compact placeholders at every tool
  ingestion point.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .logger import get_logger

logger = get_logger(__name__)

CALIBRATION_FILENAME = "token_calibration.json"

# Bootstrap calibration factor applied before any real usage observation.
# Measured on the incident session against qwen3.6-35b: prose 1.05–1.17x,
# project TSX/CSS/i18n 1.3–1.5x, SVG-path/lockfile content up to 1.76x, base64
# 2.79x. A blended code-heavy session lands ~1.3 — conservative enough to keep
# small contexts snappy, high enough that the guard/budget start from truth.
CALIBRATION_BOOTSTRAP = 1.3
# EMA learning rate when folding a new actual/estimated observation.
CALIBRATION_ALPHA = 0.25
# Clamp band. The framework's built-in scaling clamps to [1.0, 1.25]; measured
# reality (dense code, base64-in-text) needs more headroom. The floor of 1.0
# means we never trust an estimator that claims to OVER-count.
CALIBRATION_MIN = 1.0
CALIBRATION_MAX = 3.0

# Chars per token by content class (base estimator, before calibration).
LATIN_CHARS_PER_TOKEN = 3.8
CJK_TOKENS_PER_CHAR = 0.6
BASE64_CHARS_PER_TOKEN = 1.4

# Per-message chat-template overhead (role markers/separators), measured ~4
# tokens/message on the qwen3 template; 4 is also langchain's default +1 sigma.
PER_MESSAGE_OVERHEAD_TOKENS = 4

# Vision tokens per image block (bootstrap). Real cost depends on resolution and
# the model's vision encoder (qwen-family 720p JPEG ≈ 1.1–1.6k); the
# closed-loop calibration corrects the aggregate, this keeps single-image
# budgets honest from the first call. Framework default is 85 (OpenAI
# low-detail); that badly under-counts qwen-style dynamic tiling.
TOKENS_PER_IMAGE_DEFAULT = 1_200

# A run of base64 alphabet this long is treated as a binary blob, not prose.
# Minimum run length for a bare base64 candidate (T4: 256 → 128 so short
# truncated base64 / small data blobs are not counted as prose). Short runs
# still require the entropy check (_is_likely_base64) to pass.
BASE64_MIN_RUN = 128
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{" + str(BASE64_MIN_RUN) + r",}={0,2}")
# A data URL header makes the following body base64 BY DEFINITION (the model
# file format, not a heuristic) — always authoritative.
_DATA_URL_RE = re.compile(r"data:[\w.+:-]+/[\w.+:-]+;base64,", re.I)


def _is_likely_base64(s: str) -> bool:
    """Bare alphanumeric runs need an entropy check before being treated as
    base64. Every letter/digit is in the base64 alphabet, so a long run of
    ONE repeated character (ASCII-art, ``xxxx...`` log padding, ``yyyy``
    filler) would otherwise be a false positive. Real base64 always mixes
    many distinct characters; requiring ≥4 distinct chars keeps prose safe
    while catching genuine binary payloads (and all-same runs like a base64
    body of one repeated byte are still authoritative under a data-URL header,
    which never reaches this helper)."""
    return len(set(s)) >= 4

# Placeholder substituted for externalized/scrubbed binary blobs.
BLOB_PLACEHOLDER = "[binary content removed from context]"

# Overflow error parsing: providers report the real prompt size on rejection.
_OVERFLOW_VALUE_RE = re.compile(r"input[_ ]tokens?[,)\s]*value[=:\s]+(\d+)", re.I)
_OVERFLOW_AT_LEAST_RE = re.compile(r"at least (\d+) input tokens", re.I)
_OVERFLOW_PROMPT_TOKENS_RE = re.compile(r"prompt_tokens[:=]\s*(\d+)", re.I)


# ---------------------------------------------------------------------------
# Base estimator (content-class aware)
# ---------------------------------------------------------------------------

def _cjk_count(text: str) -> int:
    """Count CJK-dense characters (T2).

    Expanded beyond the old U+4E00–U+9FFF window so full-width punctuation
    (，。！？), Japanese kana, Korean hangul, CJK Extension A and CJK compatibility
    ideographs are NOT counted as Latin (3.8 chars/token) — that under-counted
    dense text and could overflow. Full-width Latin letters/digits are excluded
    (≈1 token each, same as ASCII).
    """
    return sum(1 for ch in text if _is_cjk_dense(ord(ch)))


_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3000, 0x303F),  # CJK Symbols & Punctuation (、。「」)
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Fullwidth Forms
)


def _is_cjk_dense(cp: int) -> bool:
    # Full-width Latin digits/letters (０-９ Ａ-Ｚ ａ-ｚ) tokenize ≈1 each, like
    # ASCII — not CJK-dense. The surrounding full-width punctuation stays dense.
    if 0xFF10 <= cp <= 0xFF19 or 0xFF21 <= cp <= 0xFF3A or 0xFF41 <= cp <= 0xFF5A:
        return False
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_text_tokens(text: str) -> int:
    """Base token estimate for a text payload (no calibration applied).

    Content-class aware: base64/data-URL runs are counted at their true
    density (~1.4 chars/token measured) instead of the prose rate (~3.8),
    which previously under-counted a single truncated screenshot by ~2.8x.
    """

    if not text:
        return 0
    tokens = 0.0
    rest = text
    def _count(body: str) -> None:
        nonlocal tokens
        if not body:
            return
        cjk = _cjk_count(body)
        other = len(body) - cjk
        tokens += other / LATIN_CHARS_PER_TOKEN + cjk * CJK_TOKENS_PER_CHAR

    # Data URLs first (scheme prefix included in the blob span).
    parts: list[str] = []
    last = 0
    for match in _DATA_URL_RE.finditer(rest):
        parts.append(rest[last:match.start()])
        # The base64 body starts right after the header; find its extent.
        body_start = match.end()
        body_match = re.match(r"[A-Za-z0-9+/=]+", rest[body_start:])
        span_end = body_start + (len(body_match.group(0)) if body_match else 0)
        tokens += max(1.0, (span_end - match.start()) / BASE64_CHARS_PER_TOKEN)
        last = span_end
        parts.append("")
    parts.append(rest[last:])
    remainder = "".join(parts)

    # Bare base64 runs (e.g. mid-JSON truncation leftovers). Data-URL bodies
    # were consumed above (authoritative); bare runs need the entropy check so
    # prose filler like ``xxxxx...`` is not mistaken for binary.
    last = 0
    for match in _BASE64_RUN_RE.finditer(remainder):
        run = match.group(0)
        if not _is_likely_base64(run):
            continue
        _count(remainder[last:match.start()])
        tokens += max(1.0, len(run) / BASE64_CHARS_PER_TOKEN)
        last = match.end()
    _count(remainder[last:])

    return max(1, round(tokens)) if tokens > 0 else 0


def contains_binary_blob(text: str) -> bool:
    """True when the payload carries data URLs or long high-entropy base64 runs."""
    if not text:
        return False
    if _DATA_URL_RE.search(text):
        return True
    return any(_is_likely_base64(m.group(0)) for m in _BASE64_RUN_RE.finditer(text))


def truncate_to_token_budget(text: str, budget_tokens: int) -> tuple[str, bool]:
    """Keep the leading part of ``text`` within ``budget_tokens``.

    codex ``TruncationPolicy::Tokens`` equivalent (tools/src/response_history.rs):
    the model-visible window is bounded by TOKENS — not raw chars, which
    misbehave across CJK / dense code / base64 (T1). Returns ``(text, truncated)``.
    """
    budget = max(1, int(budget_tokens))
    if not text or estimate_text_tokens(text) <= budget:
        return text, False

    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = max(1, estimate_text_tokens(line))
        if used + cost > budget:
            if not kept and line:
                # Single giant line (e.g. one long base64 run): slice proportionally.
                keep_chars = max(1, int(len(line) * (budget - used) / cost))
                kept.append(line[:keep_chars])
            break
        kept.append(line)
        used += cost
        if used >= budget:
            break

    out = "\n".join(kept)
    out += "\n…[content truncated to fit the token budget]"
    return out, True


def scrub_text(text: str, placeholder: str = BLOB_PLACEHOLDER) -> tuple[str, int]:
    """Replace data URLs / base64 runs with a compact placeholder.

    Returns ``(scrubbed_text, replaced_count)``. A truncated base64 blob is a
    corrupted binary — pure token waste for zero model value — so every tool
    ingestion point funnels through here.
    """
    if not text or not contains_binary_blob(text):
        return text, 0
    count = 0

    def _sub_data_url(match: re.Match) -> str:
        nonlocal count
        count += 1
        return placeholder

    out = _DATA_URL_RE.sub(_sub_data_url, text)
    # Consume the base64 body that followed each stripped header.
    out = re.sub(re.escape(placeholder) + r"[A-Za-z0-9+/=]+", placeholder, out)

    def _sub_run(match: re.Match) -> str:
        # Entropy gate: only high-entropy runs (real base64) are binary; a
        # single repeated character is prose/ASCII-art, not a blob.
        if not _is_likely_base64(match.group(0)):
            return match.group(0)
        nonlocal count
        count += 1
        return placeholder

    out = _BASE64_RUN_RE.sub(_sub_run, out)
    return out, count


# ---------------------------------------------------------------------------
# Message-level measurement
# ---------------------------------------------------------------------------

def message_text(msg: Any) -> str:
    """All textual content of a message (content blocks + tool calls).

    Image/audio blocks are deliberately NOT text — they are counted separately
    at a per-item vision cost (framework ``tokens_per_image`` convention).
    """
    try:
        content = msg.content
    except Exception:  # noqa: BLE001 - defensive: message-like objects vary
        content = None
    chunks: list[str] = []
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type")
                if ptype in ("image", "image_url", "audio", "video", "file"):
                    continue  # counted via per-item cost, never as text
                if ptype == "text":
                    chunks.append(part.get("text") or "")
                else:
                    chunks.append(str(part.get("input") or part.get("content") or part.get("text") or ""))
            elif isinstance(part, str):
                chunks.append(part)
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            if isinstance(tc, dict):
                chunks.append(str(tc.get("name") or ""))
                chunks.append(str(tc.get("args") or ""))
            else:
                fn = getattr(tc, "function", None)
                if fn is not None:
                    chunks.append(str(getattr(fn, "name", "") or ""))
                    chunks.append(str(getattr(fn, "arguments", "") or ""))
    return "".join(chunks)


def message_media_count(msg: Any) -> int:
    """Number of image/audio/file blocks in a message (per-item token cost)."""
    try:
        content = msg.content
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, dict) and part.get("type") in ("image", "image_url", "audio", "video", "file")
    )


def message_tokens(
    msg: Any,
    *,
    tokens_per_image: int = TOKENS_PER_IMAGE_DEFAULT,
    estimate: Callable[[str], int] = estimate_text_tokens,
) -> int:
    """Token estimate for one message: text + media + tool-call arguments."""
    total = estimate(message_text(msg))
    media = message_media_count(msg)
    if media:
        total += media * tokens_per_image
    return total


def messages_tokens(
    messages: Iterable[Any],
    *,
    tokens_per_image: int = TOKENS_PER_IMAGE_DEFAULT,
    estimate: Callable[[str], int] = estimate_text_tokens,
) -> int:
    return sum(message_tokens(m, tokens_per_image=tokens_per_image, estimate=estimate) for m in messages)


def tool_schema_tokens(tools: Iterable[Any] | None, estimate: Callable[[str], int] = estimate_text_tokens) -> int:
    """Token estimate of serialized tool schemas (they ride on every request)."""
    if not tools:
        return 0
    total = 0
    for tool in tools:
        try:
            if isinstance(tool, dict):
                schema = tool
            else:
                from langchain_core.utils.function_calling import convert_to_openai_tool

                schema = convert_to_openai_tool(tool)
            total += estimate(json.dumps(schema, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - one bad schema must not break measurement
            continue
    return total


def measure_request(
    messages: Iterable[Any],
    *,
    system_text: str = "",
    tools: Iterable[Any] | None = None,
    factor: float = 1.0,
    tokens_per_image: int = TOKENS_PER_IMAGE_DEFAULT,
    per_message_overhead: int = PER_MESSAGE_OVERHEAD_TOKENS,
) -> int:
    """Calibrated estimate of the FULL request the provider will tokenize.

    Messages + system prompt + tool schemas + per-message template overhead,
    scaled by the closed-loop calibration factor. This is the number the guard
    compares against ``window − max_output`` — nothing sent over the wire is
    left uncounted.
    """
    message_list = list(messages)
    raw = messages_tokens(message_list, tokens_per_image=tokens_per_image)
    raw += tool_schema_tokens(tools)
    if system_text:
        raw += estimate_text_tokens(system_text)
    raw += per_message_overhead * len(message_list)
    factor = max(CALIBRATION_MIN, min(CALIBRATION_MAX, float(factor or 1.0)))
    return int(round(raw * factor))


def effective_input_limit(window_tokens: int, max_output_tokens: int) -> int:
    """True input ceiling: providers reserve ``max_output`` from the window.

    vLLM enforces ``input + max_tokens <= max_model_len`` — the incident
    request died at exactly ``262144 − 8192 + 1`` input tokens. Budgets that
    ignore the reservation have zero real margin.
    """
    window = max(0, int(window_tokens or 0))
    reserved = max(0, int(max_output_tokens or 0))
    return max(1_024, window - reserved)


def parse_overflow_actual_tokens(error_text: str) -> int | None:
    """Extract the provider-reported input token count from an overflow error.

    Overflow 400s usually carry the exact number (``parameter=input_tokens,
    value=253953`` / ``at least 253953 input tokens``) — feeding it back into
    calibration makes the very failure that slipped through calibrate the next
    turn. Returns ``None`` when no count is present.
    """
    if not error_text:
        return None
    for pattern in (_OVERFLOW_VALUE_RE, _OVERFLOW_AT_LEAST_RE, _OVERFLOW_PROMPT_TOKENS_RE):
        match = pattern.search(error_text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:  # noqa: BLE001 - malformed capture
                return None
    return None


# ---------------------------------------------------------------------------
# Calibration store (persisted per provider/model)
# ---------------------------------------------------------------------------

class CalibrationStore:
    """Persisted EMA of actual/estimated token ratios keyed by provider:model.

    Same principle as langchain-core's ``use_usage_metadata_scaling`` (scale
    the approximation by the most recent real usage) but durable: session
    rebuilds drop ``usage_metadata`` from history and process restarts lose
    in-memory state, yet the learned density of a model's tokenizer is a
    property of the model, not of the session.
    """

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] | None = None
        self._cache_mtime: float = -1.0

    @staticmethod
    def key_for(provider_id: str, model: str) -> str:
        return f"{provider_id or 'unknown'}::{model or 'unknown'}"

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.path is None:
            # In-memory store (no data_dir): the cache IS the storage.
            if self._cache is None:
                self._cache = {}
            return self._cache
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._cache = {}
            self._cache_mtime = -1.0
            return self._cache
        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self._cache = data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - corrupt store falls back to bootstrap
            self._cache = {}
        self._cache_mtime = mtime
        return self._cache

    def get(self, key: str) -> float:
        """Current calibration factor for a provider:model (bootstrap if new)."""
        with self._lock:
            data = self._load()
            entry = data.get(key)
            if isinstance(entry, dict):
                factor = entry.get("factor")
                if isinstance(factor, (int, float)) and factor > 0:
                    return max(CALIBRATION_MIN, min(CALIBRATION_MAX, float(factor)))
        return CALIBRATION_BOOTSTRAP

    def update(self, key: str, *, actual_tokens: int, estimated_tokens: int) -> float:
        """Fold one observation (actual usage vs pre-send estimate) into the EMA.

        ``estimated_tokens`` must be the RAW (uncalibrated) measurement of the
        same request that produced ``actual_tokens``. Returns the new factor.
        """
        try:
            actual = int(actual_tokens)
            estimated = int(estimated_tokens)
        except (TypeError, ValueError):
            return self.get(key)
        if actual <= 0 or estimated <= 0:
            return self.get(key)
        observed = max(CALIBRATION_MIN, min(CALIBRATION_MAX, actual / estimated))
        with self._lock:
            data = self._load()
            entry = data.get(key) if isinstance(data.get(key), dict) else {}
            previous = entry.get("factor")
            if isinstance(previous, (int, float)) and previous > 0:
                factor = (1.0 - CALIBRATION_ALPHA) * float(previous) + CALIBRATION_ALPHA * observed
            else:
                # First observation: trust it directly (clamped) instead of
                # blending with a bootstrap the model may sit far from.
                factor = observed
            factor = max(CALIBRATION_MIN, min(CALIBRATION_MAX, factor))
            entry.update(
                {
                    "factor": round(factor, 4),
                    "samples": int(entry.get("samples", 0)) + 1,
                    "last_actual": actual,
                    "last_estimated": estimated,
                    "updated_at": time.time(),
                }
            )
            data[key] = entry
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                    tmp.replace(self.path)
                    self._cache = data
                    self._cache_mtime = self.path.stat().st_mtime
                except Exception:  # noqa: BLE001 - calibration is best-effort
                    logger.debug("calibration persist failed", exc_info=True)
            else:
                self._cache = data
            return factor


_STORES: dict[str, CalibrationStore] = {}
_STORES_LOCK = threading.Lock()


def get_calibration_store(data_dir: Path | str | None) -> CalibrationStore:
    """Process-wide CalibrationStore per data_dir (shared by all runtimes)."""
    key = str(Path(data_dir)) if data_dir is not None else ""
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = CalibrationStore(Path(data_dir) / CALIBRATION_FILENAME if data_dir is not None else None)
            _STORES[key] = store
        return store
