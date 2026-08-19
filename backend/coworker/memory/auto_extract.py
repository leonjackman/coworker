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
from pathlib import Path
from typing import Any, Callable

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
        temperature=0,
        api_key=provider_entry.api_key or "not-needed",
        base_url=base_url,
        timeout=120,
    )


EXTRACT_PROMPT = """You are a memory curator for a coding assistant called Coworker.

Review the conversation transcript below and decide whether any stable, durable
facts are worth remembering across sessions. Only extract facts that the user
would want Coworker to remember LONG-TERM:

- User identity / preferences (language, communication style, tooling tastes)
- Project conventions and constraints (build commands, ports, architecture rules)
- Decisions with lasting consequences that will matter in future sessions

Do NOT extract:
- One-off errors, temporary state, or exploratory guesses
- Anything secret (API keys, passwords, credentials)
- Facts already answered entirely within this session

CONDENSATION RULES (important):
- Write each memory as a CONCISE, SELF-CONTAINED fact in your OWN words — distill
  the durable takeaway, never paste or quote raw transcript text.
- Do NOT copy error messages, step-by-step procedures, or long explanations.
  Reduce them to their lasting essence (e.g. "backend binds port 9527" instead of
  a debugging dialogue).
- Each entry must be at most ~200 characters.
- One key fact per entry.

Respond with ONLY a JSON array of strings, e.g. ["user prefers Chinese replies",
"frontend builds with npm run build only"]. If nothing is worth saving, respond
with the literal JSON [].

Transcript:
{transcript}
"""


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


def _parse_candidates(text: str) -> list[str]:
    """Best-effort parse of the model's JSON-array response.

    Real local models (e.g. qwen3.x on vLLM) sometimes answer with a
    Python-style single-quoted list ``['a', 'b']``, which strict ``json.loads``
    rejects. We try strict JSON first, then a single-quote-tolerant pass.
    """
    text = text.strip()
    candidates: list[str] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            candidates = [str(x) for x in parsed if isinstance(x, str)]
    except json.JSONDecodeError:
        # Fall back: scan for a JSON array in the response.
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            candidates = _parse_array_slice(text[start : end + 1])
    if not candidates:
        # Final fallback: tolerate single-quoted arrays (Python repr style).
        candidates = _parse_array_slice(text)
    return candidates


def _parse_array_slice(text: str) -> list[str]:
    """Parse a JSON/Python array slice, accepting both quote styles."""
    from ast import literal_eval

    parsed: Any = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = literal_eval(text)
        except (ValueError, SyntaxError):
            return []
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, str)]
    return []


async def run_auto_extract(
    *,
    llm: Any,
    messages: list[dict[str, Any]],
    session_id: str,
    provider_name: str,
    model_name: str,
    write_facts: Callable[[list[str]], int],
    project_dir: str = "",
    agent: str = "",
) -> dict[str, Any]:
    """Extract candidate memories from recent messages and write them directly.

    ``write_facts`` receives the parsed candidate list and persists them into
    long-term memory (deduplicated); it returns the number actually added.
    Never raises: all failures degrade to a logged no-op so the caller can fire
    this as a fire-and-forget task.
    """
    transcript = _recent_transcript(messages)
    if not transcript:
        logger.debug("auto-extract: no transcript to review for %s", session_id)
        return {"added": 0, "error": None, "_transcript": "", "_candidates": []}
    try:
        from langchain_core.messages import SystemMessage, HumanMessage

        response = await llm.ainvoke(
            [
                SystemMessage(content=EXTRACT_PROMPT.format(transcript=transcript)),
                HumanMessage(
                    content="Review the transcript and return the JSON array of "
                    "long-term facts worth remembering."
                ),
            ]
        )
        text = ""
        if isinstance(response, str):
            text = response
        else:
            text = getattr(response, "content", "") or ""
        candidates = _parse_candidates(str(text))
    except Exception as exc:  # noqa: BLE001 - auto-extract must never break chat
        logger.warning("auto-extract failed for %s: %s", session_id, exc)
        return {"added": 0, "error": str(exc)[:200], "_transcript": transcript, "_candidates": []}

    added = write_facts(candidates)
    if added:
        logger.info("auto-extract wrote %d memories for %s", added, session_id)
    return {"added": added, "error": None, "_transcript": transcript, "_candidates": candidates}


# ---------------------------------------------------------------------------
# Dream / consolidation
# ---------------------------------------------------------------------------

CONSOLIDATE_PROMPT = """You are a memory consolidator for Coworker. You merge new
durable facts into an existing memory index, deduplicating, superseding stale
entries and compressing while preserving meaning.

Existing MEMORY.md blocks (each block is one durable fact, separated by blank lines):

{existing}

New candidate facts to integrate:

{candidates}

RULES:
- Merge overlapping facts: when a new candidate is a duplicate or a refinement of
  an existing block, UPDATE that block in place (do not keep both).
- Remove or rewrite stale / superseded entries.
- Keep each block as a CONCISE, SELF-CONTAINED fact (~200 chars). Never paste raw
  transcript text.
- Preserve every existing block UNLESS it is merged or clearly stale. You may
  restructure but must not drop meaning.
- If a new candidate is already covered, do not add it again.
- Do not invent facts that are not in the existing blocks or the candidates.

Return ONLY a JSON object with one key "blocks": an array of the final memory
blocks (strings). Every entry must be a plain string; no extra commentary.
"""


def _parse_consolidation(text: str) -> list[str] | None:
    """Best-effort parse of the consolidation response.

    Returns ``None`` when the output is not a usable JSON object (caller falls
    back to append-only). Expects ``{"blocks": [ ... ]}``, with single-quoted
    lists tolerated for local models.
    """
    text = (text or "").strip()
    candidates: list[str] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            blocks = parsed.get("blocks")
            if isinstance(blocks, list):
                candidates = [str(b).strip() for b in blocks if str(b).strip()]
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            parsed = _parse_array_slice(text[start : end + 1])
            if parsed:
                candidates = [str(b).strip() for b in parsed if str(b).strip()]
    return candidates


async def run_consolidation(
    *,
    llm: Any,
    existing: str,
    candidates: list[str],
    session_id: str,
    max_prior_loss: float = 0.25,
    max_total_chars: int = 4000,
) -> tuple[list[str] | None, str]:
    """Consolidate ``existing`` memory with ``candidates`` via the model.

    Returns ``(new_blocks, note)``. ``new_blocks`` is ``None`` when the rewrite
    is rejected by a guardrail (falls back to append-only). ``note`` explains the
    outcome for the dream diary. Never raises: model errors degrade to
    ``(None, note)``.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from .memory_file import split_blocks

    prior = split_blocks(existing)
    if not candidates:
        return None, "no new candidates to consolidate"
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=CONSOLIDATE_PROMPT.format(
                        existing=existing or "(empty)",
                        candidates="\n".join(f"- {c}" for c in candidates),
                    )
                ),
                HumanMessage(content="Return the consolidated JSON {\"blocks\": [...]}."),
            ]
        )
        text = str(getattr(response, "content", "") or response or "")
    except Exception as exc:  # noqa: BLE001 - consolidation must never break chat
        return None, f"model error: {str(exc)[:120]}"

    new_blocks = _parse_consolidation(text)
    if new_blocks is None:
        return None, "unparseable consolidation output"
    if not new_blocks:
        return None, "consolidation produced no blocks"

    # Guardrail 1: must not drop more than max_prior_loss of prior entries.
    # Pure Markdown headings are structure, not facts — the model may drop or
    # restructure them freely, so they are excluded from the reference set.
    # Text matching is a fast path; when a rewrite paraphrases / translates /
    # merges facts (so no block textually resembles the original) we ask the
    # model itself to confirm semantic preservation before accepting.
    prior_facts = [b for b in prior if not _is_heading_block(b)]
    if prior_facts:
        missing = [i for i, p in enumerate(prior_facts) if not _is_covered_by(p, new_blocks)]
        if missing:
            missing = await _verify_preservation(llm, prior_facts, new_blocks)
        loss = len(missing) / len(prior_facts)
        if loss > max_prior_loss:
            return None, (
                f"guardrail: rewrite loses {loss:.0%} of prior entries "
                f"(need >= {1 - max_prior_loss:.0%})"
            )

    # Guardrail 2: new version must fit the read-side injection budget.
    total = sum(len(b) for b in new_blocks)
    if total > max_total_chars:
        return None, f"guardrail: consolidated memory too large ({total} chars)"

    return new_blocks, f"consolidated {len(prior)} -> {len(new_blocks)} blocks"


async def _verify_preservation(
    llm: Any, prior_facts: list[str], new_blocks: list[str]
) -> list[int]:
    """Ask the model which prior entries lose their meaning in the rewrite.

    Returns the 0-based indices of ``prior_facts`` whose meaning is COMPLETELY
    absent from ``new_blocks``. Paraphrase, translation and merging all count
    as preserved — naive text similarity cannot detect those. Never raises:
    an unusable answer degrades conservatively to "all prior entries at risk"
    so the caller rejects the rewrite and falls back to append-only (safe).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    numbered = "\n".join(f"{i}. {f}" for i, f in enumerate(prior_facts))
    blocks_text = "\n".join(f"- {b}" for b in new_blocks)
    system = (
        "You are a memory consistency checker. Below are the OLD long-term "
        "memory entries (numbered) and the NEW consolidated blocks produced by "
        "another model.\n\n"
        "For each OLD entry, decide whether its MEANING is preserved somewhere "
        "in the NEW blocks. Paraphrasing, translating into another language, or "
        "merging into a combined entry all count as preserved.\n\n"
        "Respond with ONLY a JSON array of the 0-based indices of OLD entries "
        "whose meaning is COMPLETELY absent from the NEW blocks. If every OLD "
        "entry is preserved, respond with the literal JSON []."
    )
    human = f"OLD entries:\n{numbered}\n\nNEW blocks:\n{blocks_text}"
    try:
        response = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        text = str(getattr(response, "content", "") or response or "")
    except Exception:  # noqa: BLE001 - a check failure must not break the dream
        return list(range(len(prior_facts)))
    parsed = _parse_index_array(text)
    if parsed is None:
        return list(range(len(prior_facts)))
    return [i for i in parsed if isinstance(i, int) and 0 <= i < len(prior_facts)]


def _parse_index_array(text: str) -> list[int] | None:
    """Parse a JSON/Python array of integers; ``None`` when not an array.

    ``None`` (not ``[]``) signals "cannot confirm" so callers can degrade to a
    conservative action. A genuine empty ``[]`` is preserved.
    """
    from ast import literal_eval

    text = text.strip()
    if not text:
        return None
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = literal_eval(text)
        except (ValueError, SyntaxError):
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                try:
                    parsed = json.loads(text[start : end + 1])
                except (json.JSONDecodeError, ValueError):
                    try:
                        parsed = literal_eval(text[start : end + 1])
                    except (ValueError, SyntaxError):
                        return None
            else:
                return None
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, int)]
    return None


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
    return _parse_candidates(text)


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

