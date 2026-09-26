"""Post-turn workflow authoring (W24/W27).

After a turn that actually operated the computer/browser/commands, ask the model
whether the turn encodes a reusable WORKFLOW — an ordered, parameterized,
replayable business process (distinct from a *skill*, which is knowledge/SOP for
doing one thing). When it does, either stage a draft for human approval or apply
it directly, per the user's settings. Best-effort: never raises to the caller.
"""

from __future__ import annotations

import json
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

CATALOG_MAX_ENTRIES = 40
NAME_MAX_CHARS = 64

_SYSTEM = (
    "You are a workflow curator inside a personal automation agent.\n\n"
    "A WORKFLOW is a repeatable, ordered, PARAMETERIZED business process that can be "
    "REPLAYED deterministically — typically several concrete operations (open/click/type/"
    "upload a page, run a command, call a tool, ask a human, branch/loop). It is NOT mere "
    "knowledge or a how-to; that belongs to a Skill. Capture only when the session did a "
    "concrete MULTI-STEP procedure worth running the same way every time.\n\n"
    "Step kinds: command | browser | app | tool | skill | set | assert | human | branch | loop | wait. "
    "Use {{inputs.<name>}} placeholders for values that vary per run.\n\n"
    "Deduplication: if an existing workflow in <catalog> already covers this, respond with "
    "action=update using THAT exact name; otherwise action=create with a new lowercase, "
    "hyphen-separated, <=64 char name; if nothing is worth capturing, action=none.\n\n"
    "Respond with ONLY a JSON object (no markdown):\n"
    '{"action": "create"|"update"|"none", "name": "...", "description": "one sentence", '
    '"steps": [{"kind": "...", "do": "...", "params": {...}, "locator": {...}}]}'
)

_AGGRESSIVENESS: dict[str, str] = {
    "active": (
        "Proposal strictness: ACTIVE. Propose a workflow whenever there is a plausible "
        "replayable procedure — a draft is cheap to reject."
    ),
    "cautious": (
        "Proposal strictness: CAUTIOUS. Only capture genuinely repeatable multi-step business "
        "processes; never one-off facts or knowledge. When in doubt, respond with action=none."
    ),
    "passive": "Proposal strictness: PASSIVE. Do NOT propose any workflow; respond with action=none.",
}


def _content_of(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return str(response.get("content") or "")
    if hasattr(response, "content"):
        return response.content or ""
    return str(response)


def _parse_json(text: str) -> dict[str, Any] | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _format_catalog(workflow_manager: Any) -> str:
    try:
        entries = workflow_manager.list()
    except Exception:  # noqa: BLE001
        return "(unavailable)"
    entries = entries[:CATALOG_MAX_ENTRIES]
    if not entries:
        return "(none)"
    return "\n".join(f"- {w.get('name')}: {str(w.get('description') or '')[:120]}" for w in entries)


def _system_prompt(aggressiveness: str) -> str:
    return f"{_SYSTEM}\n\n{_AGGRESSIVENESS.get(aggressiveness, _AGGRESSIVENESS['cautious'])}"


def _validate_name(name: str) -> str:
    from .parser import is_valid_name

    candidate = (name or "").strip()
    if len(candidate) > NAME_MAX_CHARS or not is_valid_name(candidate):
        return ""
    return candidate


async def run_workflow_review(
    llm: Any,
    workflow_manager: Any,
    *,
    session_id: str,
    messages: list[Any],
    parts: list[Any],
    aggressiveness: str = "cautious",
    approval_required: bool = True,
) -> dict[str, Any]:
    """Review a turn; stage a draft (or apply directly) when a workflow fits."""
    if llm is None or workflow_manager is None:
        return {"action": "none"}

    from coworker.agent.skill_review import _conversation_tail, _tool_summary
    from langchain_core.messages import HumanMessage, SystemMessage

    human = (
        "## Conversation tail\n"
        f"{_conversation_tail(messages)}\n\n"
        "## Tools used this turn\n"
        f"{_tool_summary(parts)}\n\n"
        "## Existing workflows (catalog)\n"
        f"{_format_catalog(workflow_manager)}\n\n"
        "Decide whether to capture a workflow and respond with the JSON verdict."
    )
    try:
        response = await llm.ainvoke(
            [SystemMessage(content=_system_prompt(aggressiveness)), HumanMessage(content=human)]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow review call failed: %s", exc)
        return {"action": "none", "reason": f"review call failed: {exc}"}

    verdict = _parse_json(_content_of(response))
    if not verdict:
        return {"action": "none", "reason": "unparseable output"}

    action = str(verdict.get("action") or "none").lower()
    if action == "none":
        return {"action": "none"}

    name = _validate_name(str(verdict.get("name") or ""))
    steps = verdict.get("steps")
    if not name or not isinstance(steps, list) or not steps:
        return {"action": "none", "reason": "verdict missing valid name/steps"}

    description = str(verdict.get("description") or name)
    sources = [f"session:{session_id}"]
    try:
        if approval_required:
            result = workflow_manager.stage_recorded(
                name, steps, description=description, sources=sources,
                action="update" if action == "update" else "create",
            )
        else:
            result = workflow_manager.apply_agent_workflow(
                action, name, steps, description=description, sources=sources,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow review staging failed: %s", exc)
        return {"action": action, "name": name, "reason": f"staging failed: {exc}"}

    if result.get("status") == "ok":
        logger.info(
            "workflow review %s '%s'", "applied" if not approval_required else "staged", name
        )
        return {
            "action": action,
            "name": result.get("name", name),
            "staged": approval_required,
            "applied": not approval_required,
        }
    logger.debug("workflow review staging skipped: %s", result.get("message"))
    return {"action": "none", "reason": result.get("message", "staging skipped")}
