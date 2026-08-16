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
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


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

    Always keeps the newest message even when it alone exceeds the budget — an
    oversized tail line must never produce an empty transcript (which would
    silently skip extraction).
    """
    lines: list[str] = []
    total = 0
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if not content:
            continue
        prefix = "USER" if role == "user" else "ASSISTANT"
        line = f"{prefix}: {content}"
        if lines and total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    lines.reverse()
    return "\n".join(lines)


def _parse_candidates(text: str) -> list[str]:
    """Best-effort parse of the model's JSON-array response."""
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
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, list):
                    candidates = [str(x) for x in parsed if isinstance(x, str)]
            except json.JSONDecodeError:
                pass
    return candidates


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
        return {"added": 0, "error": None}
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
        return {"added": 0, "error": str(exc)[:200]}

    added = write_facts(candidates)
    if added:
        logger.info("auto-extract wrote %d memories for %s", added, session_id)
    return {"added": added, "error": None}