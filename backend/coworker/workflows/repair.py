"""Bounded LLM self-heal (W16).

When a step fails and self-heal is enabled, this asks the configured model for a
minimal JSON patch (a new semantic ``locator`` and/or ``params``). It is
deliberately conservative: only ``locator``/``params``/``do`` may be changed, and
any parse/LLM failure simply returns ``None`` so the step fails normally.
"""

from __future__ import annotations

import json
import re
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

_ALLOWED = {"locator", "params", "do"}
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM = (
    "You repair a failing automation step in a deterministic workflow. "
    "Given the step and the error, return ONLY a JSON object with the minimal "
    "fix. Allowed keys: 'locator' (a semantic descriptor object such as "
    '{"role": "button", "name": "..."} or {"selector": "#id"}), "params" '
    "(object), or 'do' (string). If no confident fix exists, return {}. "
    "Never include prose, markdown fences, or extra keys."
)


def _extract_json(text: str) -> dict[str, Any] | None:
    match = _JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def suggest_repair(
    step: Any,
    error: str,
    *,
    provider_manager: Any | None = None,
    llm: Any | None = None,
    data_dir: Any | None = None,
) -> dict[str, Any] | None:
    if llm is None:
        from coworker.agent.headless import build_default_llm

        llm = build_default_llm(provider_manager, data_dir)
    if llm is None:
        return None

    payload = {
        "step": {
            "id": getattr(step, "id", ""),
            "kind": getattr(step, "kind", ""),
            "do": getattr(step, "do", ""),
            "params": getattr(step, "params", {}),
            "locator": getattr(step, "locator", None),
        },
        "error": error[:1000],
    }
    try:
        response = llm.invoke(
            [("system", _SYSTEM), ("human", json.dumps(payload, ensure_ascii=False))]
        )
        content = response.content if hasattr(response, "content") else str(response)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        data = _extract_json(content) or {}
    except Exception as exc:  # noqa: BLE001 - healing is always best-effort
        logger.warning("self-heal llm call failed: %s", exc)
        return None

    repaired = {k: v for k, v in data.items() if k in _ALLOWED and v is not None}
    return repaired or None


_BINDING_SYSTEM = (
    "You fill in the BINDING (the 'how') of a workflow step that only states its "
    "INTENT (the goal / 'what'). Given the step's kind, its goal, and the list of "
    "available actions and parameters for that kind, choose the concrete action, "
    "parameters, and (for UI actions) a semantic locator. Return ONLY a JSON object "
    '{"do": <action>, "params": {<object>}, "locator": {<object>}} using ONLY '
    "actions/parameters that appear in the catalog. For GUI steps prefer a semantic "
    'locator like {"role": "button", "name": "..."} or {"text": "..."}. Return {} if '
    "you cannot confidently choose. Never include prose, markdown fences, or extra keys."
)


def suggest_binding(
    step: Any,
    registry: Any,
    *,
    provider_manager: Any | None = None,
    llm: Any | None = None,
    data_dir: Any | None = None,
) -> dict[str, Any] | None:
    """Resolve an intent-only step's binding from the capability catalog (P2).

    Best-effort: returns ``{do, params, locator}`` (only present keys) or ``None``.
    """
    if llm is None:
        from coworker.agent.headless import build_default_llm

        llm = build_default_llm(provider_manager, data_dir)
    if llm is None:
        return None

    kind = str(getattr(step, "kind", "") or "")
    kspec = registry.kinds.get(kind) if registry is not None else None
    catalog = []
    if kspec is not None:
        catalog = [
            {"action": a.name, "params": [p.name for p in a.params]}
            for a in kspec.actions
        ]
    payload = {
        "kind": kind,
        "goal": str(getattr(step, "goal", "") or getattr(step, "description", "") or ""),
        "available_actions": catalog,
    }
    try:
        response = llm.invoke(
            [("system", _BINDING_SYSTEM), ("human", json.dumps(payload, ensure_ascii=False))]
        )
        content = response.content if hasattr(response, "content") else str(response)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        data = _extract_json(content) or {}
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        logger.warning("binding resolve llm call failed: %s", exc)
        return None

    out: dict[str, Any] = {}
    if isinstance(data.get("do"), str) and data["do"].strip():
        out["do"] = data["do"].strip()
    if isinstance(data.get("params"), dict):
        out["params"] = data["params"]
    if isinstance(data.get("locator"), dict):
        out["locator"] = data["locator"]
    return out or None
