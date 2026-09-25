"""Post-turn workflow authoring (W24/W27).

After a turn that actually operated the computer/browser/commands, ask the model
whether the turn encodes a reusable procedure and, if so, stage a workflow draft
for human approval. This is the workflow counterpart of ``skill_review``.

Best-effort: never raises to the caller.
"""

from __future__ import annotations

import json
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You review an automation session and decide whether it encodes a REUSABLE "
    "workflow — an ordered procedure with concrete steps (command / browser / "
    "app / tool / skill) that could be replayed later. Only propose something "
    "genuinely repeatable; skip one-off Q&A and trivial single actions.\n\n"
    "Respond with ONLY a JSON object (no markdown):\n"
    '{"create": false, "reason": "..."}\n'
    "or\n"
    '{"create": true, "name": "kebab-case-name", "description": "one sentence", '
    '"steps": [{"kind": "command|browser|app|tool|skill|set|assert", "do": "...", '
    '"params": {...}, "locator": {...}}]}\n'
    "Use {{inputs.<name>}} placeholders for values that vary per run."
)


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


async def run_workflow_review(
    llm: Any,
    workflow_manager: Any,
    *,
    session_id: str,
    messages: list[Any],
    parts: list[Any],
) -> dict[str, Any]:
    """Review a turn and stage a workflow draft when a reusable procedure exists."""
    if llm is None or workflow_manager is None:
        return {"action": "none"}

    from coworker.agent.skill_review import _conversation_tail, _tool_summary
    from langchain_core.messages import HumanMessage, SystemMessage

    human = (
        "## Conversation tail\n"
        f"{_conversation_tail(messages)}\n\n"
        "## Tools used this turn\n"
        f"{_tool_summary(parts)}\n\n"
        "Decide whether to capture a reusable workflow and respond with the JSON verdict."
    )
    try:
        response = await llm.ainvoke([SystemMessage(content=_SYSTEM), HumanMessage(content=human)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow review call failed: %s", exc)
        return {"action": "none", "reason": f"review call failed: {exc}"}

    verdict = _parse_json(_content_of(response))
    if not verdict or not verdict.get("create"):
        return {"action": "none"}

    name = str(verdict.get("name") or "").strip()
    steps = verdict.get("steps")
    if not name or not isinstance(steps, list) or not steps:
        return {"action": "none", "reason": "verdict missing name/steps"}

    try:
        result = workflow_manager.stage_recorded(
            name,
            steps,
            description=str(verdict.get("description") or name),
            sources=[f"session:{session_id}"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow review staging failed: %s", exc)
        return {"action": "create", "name": name, "reason": f"staging failed: {exc}"}

    if result.get("status") == "ok":
        logger.info("workflow review staged draft '%s'", result.get("name", name))
        return {"action": "create", "name": result.get("name", name), "staged": True}
    logger.debug("workflow review staging skipped: %s", result.get("message"))
    return {"action": "none", "reason": result.get("message", "staging skipped")}
