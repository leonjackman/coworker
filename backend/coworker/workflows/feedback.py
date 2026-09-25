"""Feedback-driven workflow generalization (learning loop).

When the user reviews a run and points out that a step/result was wrong, this
asks the model to revise the workflow so the same situation is handled next
time — generalizing the fix into the paradigm (new params, branches, assertions,
or takeover modes) rather than patching a single run.

Returns revised YAML; the caller stages it as a draft (or applies with a version
bump). Best-effort: never raises.
"""

from __future__ import annotations

import json
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You maintain a deterministic automation workflow. The user reviewed a run "
    "and gave feedback. Revise the workflow YAML so the SAME kind of situation is "
    "handled next time — generalize the fix (parameters, branch/loop, assertions, "
    "per-step on_error policy, or mode: agent) instead of hard-coding one run.\n\n"
    "Rules:\n"
    "- Return the COMPLETE revised workflow YAML, no prose, no markdown fences.\n"
    "- Keep the same top-level shape (name/description/inputs/steps).\n"
    "- Keep the name unchanged.\n"
    "- Prefer parameterizing literals and adding verifiable success conditions.\n"
    "- Only change what the feedback justifies."
)


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("yaml"):
            cleaned = cleaned[4:]
    return cleaned.strip()


async def run_workflow_revision(
    llm: Any,
    workflow_yaml: str,
    feedback: str,
    *,
    step_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    if llm is None:
        return {"status": "error", "message": "no model available"}
    if not feedback.strip():
        return {"status": "error", "message": "empty feedback"}

    from langchain_core.messages import HumanMessage, SystemMessage

    human = (
        f"## Current workflow YAML\n{workflow_yaml}\n\n"
        + (f"## Step in focus\n{step_id}\n\n" if step_id else "")
        + (f"## Run id\n{run_id}\n\n" if run_id else "")
        + f"## User feedback\n{feedback}\n\n"
        "Return the complete revised workflow YAML."
    )
    try:
        response = await llm.ainvoke([SystemMessage(content=_SYSTEM), HumanMessage(content=human)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow revision call failed: %s", exc)
        return {"status": "error", "message": f"revision call failed: {exc}"}

    content = response.content if hasattr(response, "content") else str(response)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {"status": "ok", "yaml": _strip_fences(content)}
