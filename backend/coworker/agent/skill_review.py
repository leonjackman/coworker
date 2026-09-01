"""Post-turn skill self-calibration review (Hermes-style background review).

After a settled turn that actually used tools, a lightweight review decides
whether a repeatable procedure — especially one the user corrected — should be
captured as a skill. Every capture is STAGED as a **draft** for human approval
via the skill draft queue; the agent never auto-enables a skill.

Calibration discipline: user guidance is the calibration source. The review may
propose from its own success too, but the proposal is only ever a draft — the
human approves or rejects it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from coworker.logger import get_logger

logger = get_logger(__name__)

# Cap the review's context so a busy host doesn't burn tokens on every turn.
TAIL_MESSAGES = 8
TAIL_MAX_CHARS = 12_000
CATALOG_MAX_ENTRIES = 40

REVIEW_SYSTEM_PROMPT = """You are a skill curator inside a personal coding agent. Your job: after a task,
decide whether a reusable *procedure* is worth capturing as a skill, following the SKILL.md house format.

Capture when (priority order):
1. The USER corrected the agent's approach in this conversation ("no, do it this way", "don't use X, use Y",
   explicit restatements of the desired method). This is the highest-value signal.
2. The agent worked out a non-trivial multi-step procedure worth repeating.
3. The agent hit errors/dead-ends and found the working path.

Rules:
- If an existing skill in <catalog> already covers this, respond with action=update using THAT skill's exact name.
  Otherwise action=create with a new lowercase-hyphen name.
- The SKILL.md content MUST include YAML frontmatter (name + description) and exactly these four body sections,
  in order: ## When to Use, ## Procedure (numbered steps), ## Pitfalls, ## Verification.
- description <= 160 chars, concrete enough for the agent to decide when to load it.
- If nothing is worth capturing, respond with action=none.
- Your capture is only a proposal: it will be staged as a draft and the human approves it. Do not mention this in content.

Respond with ONLY a JSON object, no commentary, no markdown fence:
{"action": "create"|"update"|"none", "name": "<slug>", "content": "<full SKILL.md string>"}"""


def _tool_summary(parts: list[Any], limit: int = 12) -> str:
    """Compact summary of the tools invoked this turn.

    Handles both the raw stream part types (``tool_start`` / ``tool_delta`` /
    ``tool_end``) and the merged ``tool`` events. Dedupes by tool-call id so a
    single call (start + end) counts once, preferring the terminal status.
    """
    seen: dict[str, dict[str, str]] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype not in ("tool_start", "tool_delta", "tool_end", "tool"):
            continue
        name = str(part.get("name") or "")
        if not name:
            continue
        tc_id = str(part.get("id") or "") or name
        entry = seen.setdefault(tc_id, {"name": name, "status": ""})
        status = part.get("status")
        if isinstance(status, str) and status:
            entry["status"] = status
    lines = [f"- {e['name']} ({e['status'] or 'started'})" for e in list(seen.values())[:limit]]
    return "\n".join(lines) or "(none)"


def _conversation_tail(messages: list[Any], max_chars: int = TAIL_MAX_CHARS) -> str:
    """Render the last few messages as plain text for the review."""
    chunks: list[str] = []
    total = 0
    for msg in reversed(messages[-TAIL_MESSAGES:]):
        role = getattr(msg, "type", None) or (msg.get("type") if isinstance(msg, dict) else "?")
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if not text.strip():
            continue
        line = f"[{role}] {text[:600]}"
        total += len(line)
        if total > max_chars:
            break
        chunks.append(line)
    return "\n".join(reversed(chunks))


def _parse_review(text: str) -> dict[str, Any] | None:
    """Parse the review model's JSON output (lenient: strip fences/extract block)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def run_skill_review(
    llm: Any,
    skill_manager: Any,
    *,
    session_id: str,
    messages: list[Any],
    parts: list[Any],
    aggressiveness: str = "cautious",
    approval_required: bool = True,
) -> dict[str, Any]:
    """Run one post-turn review; stages a draft (or applies directly when
    ``approval_required`` is false) when something is captured.

    ``aggressiveness`` selects the proposal strictness: ``active`` proposes
    eagerly, ``cautious`` (default) only captures genuinely repeatable
    procedures, ``passive`` should never be called (the runtime gate skips it).

    Never raises: the caller treats the review as best-effort.
    """
    tail = _conversation_tail(messages)
    tools = _tool_summary(parts)
    catalog = _format_catalog(skill_manager)
    system_prompt = _build_system_prompt(aggressiveness)

    human = (
        "## Conversation tail\n"
        f"{tail}\n\n"
        "## Tools used this turn\n"
        f"{tools}\n\n"
        "## Existing skills (catalog)\n"
        f"{catalog}\n\n"
        "Decide whether to capture a skill and respond with the JSON verdict."
    )

    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - a review failure must never break anything
        logger.warning("skill review call failed: %s", exc)
        return {"action": "none", "reason": f"review call failed: {exc}"}

    content = ""
    if isinstance(response, str):
        content = response
    elif isinstance(response, dict):
        content = response.get("content") or ""
    elif hasattr(response, "content"):
        content = response.content or ""
    if not isinstance(content, str):
        content = str(content)

    verdict = _parse_review(content)
    if not verdict:
        logger.info("skill review verdict: unparseable output — skipped")
        return {"action": "none", "reason": "unparseable output"}

    action = str(verdict.get("action") or "none").lower()
    if action == "none":
        logger.info("skill review verdict: none (no reusable procedure worth capturing)")
        return {"action": "none"}

    name = str(verdict.get("name") or "").strip()
    skill_content = str(verdict.get("content") or "").strip()
    if not name or not skill_content:
        logger.info("skill review verdict: %s but missing name/content", action)
        return {"action": "none", "reason": "verdict missing name/content"}

    try:
        if approval_required:
            if action == "update":
                result = skill_manager.stage_skill_replacement(
                    name, skill_content, sources=[f"session:{session_id}"]
                )
            else:
                result = skill_manager.stage_skill_draft(
                    name, skill_content, sources=[f"session:{session_id}"]
                )
        else:
            # Hermes-style free write: apply directly, bypassing the draft queue.
            result = skill_manager.apply_agent_skill(
                action, name, skill_content, sources=[f"session:{session_id}"]
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill review staging failed: %s", exc)
        return {"action": action, "name": name, "reason": f"staging failed: {exc}"}

    if result.get("status") == "ok":
        logger.info(
            "skill review %s skill '%s' (%s)",
            "applied" if not approval_required else "staged",
            name,
            "direct" if not approval_required else "draft",
        )
        return {
            "action": action,
            "name": name,
            "staged": approval_required,
            "applied": not approval_required,
        }
    # Common cause: update targeted a skill that doesn't exist / name collision.
    logger.debug("skill review staging skipped: %s", result.get("message"))
    return {"action": action, "name": name, "reason": result.get("message")}


AGGRESSIVENESS_RULES: dict[str, str] = {
    "active": (
        "Proposal strictness: ACTIVE. Propose a skill whenever there is a plausible "
        "reusable procedure — including one-off but high-value workflows the user is "
        "likely to repeat. When unsure, prefer proposing (a draft is cheap to reject)."
    ),
    "cautious": (
        "Proposal strictness: CAUTIOUS. Only capture genuinely repeatable procedures — "
        "never one-off facts or trivial chat. When in doubt, respond with action=none."
    ),
    "passive": (
        "Proposal strictness: PASSIVE. Do NOT propose any skill here; respond with action=none."
    ),
}


def _build_system_prompt(aggressiveness: str) -> str:
    rule = AGGRESSIVENESS_RULES.get(aggressiveness, AGGRESSIVENESS_RULES["cautious"])
    return f"{REVIEW_SYSTEM_PROMPT}\n\n{rule}"


def _format_catalog(skill_manager: Any) -> str:
    try:
        skills = skill_manager.injection_list()
    except Exception:  # noqa: BLE001
        return "(unavailable)"
    skills = skills[:CATALOG_MAX_ENTRIES]
    if not skills:
        return "(none)"
    return "\n".join(
        f"- {s.name}: {s.description[:120]}" for s in skills
    )
