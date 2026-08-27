"""Automatic memory extraction (opt-in, default off).

Design decisions (per the architecture audit):

- Trigger by turn count, not token threshold or every turn (hermes nudge
  interval default 10). The counter lives in ``MemoryManager`` and is bumped at
  each settled turn.
- Extraction uses a *small* configured model (``memory_extract_model``) or the
  main provider model — never a fork of the full main model over the whole
  conversation budget. The LLM call runs as an ``asyncio`` background task so
  the main turn stream is never blocked.
- Extracted candidates are written DIRECTLY into long-term memory (no human
  confirmation step): project-scoped facts go to the agent's own ``MEMORY.md``,
  global facts to the system ``USER.md``. Writes are deduplicated against the
  target file's existing content.
- Failed extraction / model errors are logged and never break chat.
"""

from __future__ import annotations

import json
from typing import Any

from coworker.logger import get_logger
logger = get_logger(__name__)


def build_extract_llm(provider_entry: Any | None, extract_model: str = "") -> Any | None:
    """Build a lightweight OpenAI-compatible chat model for extraction."""
    if provider_entry is None:
        return None
    from langchain_openai import ChatOpenAI

    model = extract_model or provider_entry.model or ""
    base_url = (provider_entry.base_url or "").rstrip("/")
    if provider_entry.provider_type == "ollama" and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return ChatOpenAI(
        model=model,
        api_key=provider_entry.api_key or "not-needed",
        base_url=base_url,
        timeout=120,
    )


def _recent_transcript(messages: list[dict[str, Any]], max_chars: int = 12_000) -> str:
    """Join the tail of messages into one concise transcript for extraction.

    Newest messages first, filling the whole ``max_chars`` budget. Two hard
    guarantees:

    - The newest message is ALWAYS kept; if it alone exceeds the budget its
      head is clipped (the tail survives).
    - Older messages are included until the budget is exhausted; the oldest
      overflowing message is clipped from the front so the remaining budget is
      used instead of being discarded.

    Without these guarantees, a real workload whose assistant replies are each
    several thousand characters collapses the transcript to just the newest
    user message (a few dozen chars) — and the extractor then correctly finds
    nothing worth remembering.
    """
    lines: list[str] = []
    used = 0
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if not content:
            continue
        prefix = "USER: " if role == "user" else "ASSISTANT: "
        line = f"{prefix}{content}"
        if not lines:
            # Newest message: always keep, clip the head if oversized.
            kept = line if len(line) <= max_chars else line[-max_chars:]
            lines.append(kept)
            used = len(kept)
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(line) <= remaining:
            lines.append(line)
            used += len(line)
            continue
        # This (older) message overflows the remaining budget: fill it with the
        # message's tail, clipped from the front, then stop.
        if remaining > len(prefix):
            body = line[len(prefix):]
            lines.append(f"{prefix}{body[-(remaining - len(prefix)):]}")
        elif remaining > 0:
            lines.append(line[-remaining:])
        break
    lines.reverse()
    return "\n".join(lines)


def _parse_blocks_and_new(text: str) -> tuple[list[str], list[str]] | None:
    """Parse the merged response into ``(blocks, new)``; ``None`` when unparseable.

    Tolerates a ``{"blocks": [...], "new": [...]}`` object OR a bare JSON /
    Python-repr array (both quote styles) — local models occasionally answer
    with a bare list, in which case ``new`` stays empty.
    """
    text = text.strip()

    def _as_strings(v: Any) -> list[str]:
        return [str(x) for x in v if isinstance(x, str) and str(x).strip()] if isinstance(v, list) else []

    obj: dict[str, Any] | None = None
    bare: list[str] | None = None
    for candidate in (text, _bounded_json_slice(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            try:
                from ast import literal_eval

                parsed = literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, dict):
            obj = parsed
            break
        if isinstance(parsed, list):
            bare = _as_strings(parsed)
            break
    if obj is not None:
        blocks = _as_strings(obj.get("blocks"))
        new = _as_strings(obj.get("new"))
        if blocks:
            return blocks, new
        return None
    if bare:
        return bare, []
    return None


def _bounded_json_slice(text: str) -> str:
    """Return the outermost ``{...}`` (or ``[...]``) slice of ``text``, or ''."""
    open_ch, close_ch = ("{", "}") if "{" in text else ("[", "]")
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end <= start:
        return ""
    return text[start : end + 1]


EXTRACT_MERGE_PROMPT = """You are the long-term memory keeper for a coding
assistant called Coworker. Below are the agent's CURRENT memory blocks and a
recent conversation transcript.

Do TWO things in one pass:

1. Extract NEW durable facts from the transcript (user preferences, project
   conventions/constraints, lasting decisions). Only facts the user would want
   remembered long-term.
2. Produce the FINAL memory block list:
   - Keep every existing block that is still relevant. You may rewrite, translate
     or MERGE overlapping entries to keep the total within the size budget — but
     never silently drop a distinct fact.
   - Add the genuinely new durable facts as concise, self-contained sentences
     (~200 chars, one key fact each). Never paste raw transcript text, error
     logs or secrets.

Return ONLY a JSON object:
{{"blocks": ["<concise fact>", ...], "new": ["<genuinely new fact added this time>", ...]}}

The "new" array is the subset of "blocks" that did not exist before (used for
logging and a safe append-only fallback).

Current memory blocks:
{existing}

Recent transcript:
{transcript}
"""


async def run_extract_and_merge(
    *,
    llm: Any,
    messages: list[dict[str, Any]],
    existing_blocks: list[str],
    session_id: str,
    provider_name: str = "memory-extract",
    model_name: str = "",
    max_total_chars: int = 4000,
    max_prior_loss: float = 0.25,
    max_transcript_chars: int = 12_000,
) -> dict[str, Any]:
    """E1/E2 一步到位：SINGLE merged LLM call.

    One prompt extracts new durable facts AND merges them into the current
    memory blocks (consolidating overlapping entries in-band). Guardrails are
    RULE-based (textual coverage + size budget) — the separate
    ``_verify_preservation`` LLM call is gone. Returns:

    ``{"blocks": <final blocks or None>, "new": [...], "note": str, "added": int,
    "transcript": str}``

    ``blocks=None`` means the merge was rejected by a guardrail — the caller
    falls back to appending ``new`` (nothing is lost).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from .memory_file import render_blocks

    transcript = _recent_transcript(messages, max_chars=max_transcript_chars)
    if not transcript.strip():
        return {"blocks": None, "new": [], "note": "no transcript", "added": 0, "transcript": ""}

    prior = [b for b in existing_blocks if b.strip()]
    existing = render_blocks(prior) if prior else "(empty)"
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=EXTRACT_MERGE_PROMPT.format(
                        existing=existing,
                        transcript=transcript,
                    )
                ),
                HumanMessage(content='Return the consolidated JSON {"blocks": [...], "new": [...]}.'),
            ]
        )
        text = str(getattr(response, "content", "") or response or "")
    except Exception as exc:  # noqa: BLE001 - extraction must never break chat
        return {"blocks": None, "new": [], "note": f"model error: {str(exc)[:120]}", "added": 0, "transcript": transcript}

    parsed = _parse_blocks_and_new(text)
    if parsed is None:
        return {"blocks": None, "new": [], "note": "unparseable merge output", "added": 0, "transcript": transcript}
    blocks, new = parsed

    # Rule guardrail 1: must not lose more than max_prior_loss of prior facts.
    prior_facts = [b for b in prior if not _is_heading_block(b)]
    if prior_facts:
        missing = [i for i, p in enumerate(prior_facts) if not _is_covered_by(p, blocks)]
        loss = len(missing) / len(prior_facts)
        if loss > max_prior_loss:
            return {
                "blocks": None,
                "new": new,
                "note": f"guardrail: merge loses {loss:.0%} of prior facts (append-only fallback)",
                "added": len(new),
                "transcript": transcript,
            }

    # Rule guardrail 2: merged version must fit the injection budget.
    total = sum(len(b) for b in blocks)
    if total > max_total_chars:
        return {
            "blocks": None,
            "new": new,
            "note": f"guardrail: merged memory too large ({total} chars, cap {max_total_chars})",
            "added": len(new),
            "transcript": transcript,
        }

    return {
        "blocks": blocks,
        "new": new,
        "note": f"merged {len(prior)} -> {len(blocks)} blocks (+{len(new)} new)",
        "added": len(new),
        "transcript": transcript,
    }


# ---------------------------------------------------------------------------
# Session notes
# ---------------------------------------------------------------------------


def _is_heading_block(block: str) -> bool:
    """True for a pure Markdown heading (single line starting with ``#``)."""
    text = block.strip()
    return bool(text) and "\n" not in text and text.startswith("#")


def _is_covered_by(prior: str, new_blocks: list[str], similarity: float = 0.6) -> bool:
    """True if ``prior`` survives in any ``new_blocks`` entry.

    Matching is lenient: exact equality, substring containment (either
    direction — covers merges of duplicate entries), or fuzzy similarity above
    ``similarity`` (covers rewording).
    """
    import difflib

    p = prior.lower()
    for block in new_blocks:
        b = block.lower()
        if p == b or (p and (p in b or b in p)):
            return True
        if p and difflib.SequenceMatcher(None, p, b).ratio() >= similarity:
            return True
    return False


# ---------------------------------------------------------------------------
# Session summary (per-session notes in SESSIONS/<date>.md)
# ---------------------------------------------------------------------------

SESSION_SUMMARY_PROMPT = """You are the session notetaker for a coding assistant
called Coworker. Below is a transcript of the most recent conversation.

Write a concise session note (3-6 short bullet points) capturing what happened:
- What the user asked for and what was done / decided
- Any concrete outcome (features built, files touched, errors fixed, refactors)
- Any in-progress work or open threads the next session should pick up

Keep each bullet to ONE line (~120 chars max), factual and self-contained. Do
NOT include raw error logs, full file paths lists, or secrets. If the transcript
has nothing substantive, respond with the literal JSON [].

Respond with ONLY a JSON array of strings, e.g.
["用户要求全局排查记忆功能", "修复了自动记忆提取的转写截断问题"].

Transcript:
{transcript}
"""


def _parse_summary(text: str) -> list[str]:
    """Best-effort parse of the session-summary JSON-array response."""
    parsed = _parse_blocks_and_new(text)
    if parsed is None:
        return []
    blocks, new = parsed
    # A session-summary response is a bare array (blocks); "new" is empty.
    return blocks or new


async def run_session_summary(
    *,
    llm: Any,
    transcript: str,
    session_id: str,
    existing: str = "",
) -> tuple[list[str] | None, str]:
    """Produce a session note from ``transcript``.

    Returns ``(bullets, note)``. ``bullets`` is ``None`` when the model call
    failed or produced nothing usable (caller should skip the write). Existing
    notes in the target file are passed back so the caller can dedupe.
    Never raises: failures degrade to ``(None, note)``.
    """
    if not transcript:
        return None, "no transcript to summarize"
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = await llm.ainvoke(
            [
                SystemMessage(content=SESSION_SUMMARY_PROMPT.format(transcript=transcript)),
                HumanMessage(
                    content="Review the transcript and return the JSON array of "
                    "session-note bullets."
                ),
            ]
        )
        text = str(getattr(response, "content", "") or response or "")
    except Exception as exc:  # noqa: BLE001 - summary must never break the dream
        return None, f"model error: {str(exc)[:120]}"
    bullets = [b.strip() for b in _parse_summary(text) if b and b.strip()]
    if not bullets:
        return None, "summary produced no bullets"
    return bullets, f"{len(bullets)} bullets"

