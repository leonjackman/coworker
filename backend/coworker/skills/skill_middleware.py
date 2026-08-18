"""SkillMiddleware: injects the skill catalog into the agent graph.

Only the catalog (name + description + location + version) is injected into
the system prompt; the full ``SKILL.md`` body is loaded on demand through the
existing ``read_file`` tool (progressive disclosure, Agent Skills standard).

Skills are hidden entirely in the ``discuss`` (plan) phase, matching the
product decision that planning is read-only and never pulls in workflow
instructions.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from .skill_manager import SkillManager
from .skills import format_skills_prompt

from coworker.logger import get_logger
logger = get_logger(__name__)


def _phase_is_discuss(state: Any) -> bool:
    """Mirror of ``agents.normalize_phase`` (avoid a circular import)."""
    phase = str((state or {}).get("phase") or "")
    if phase in ("discuss", "execute"):
        return phase == "discuss"
    work_mode = str((state or {}).get("work_mode") or "build")
    return work_mode == "plan"


class SkillMiddleware(AgentMiddleware):

    def __init__(self, manager: SkillManager):
        self.manager = manager

    def _overrides(self, request: Any) -> dict[str, Any]:
        if _phase_is_discuss(getattr(request, "state", None)):
            logger.debug("skills hidden in discuss phase")
            return {}
        try:
            skills = self.manager.injection_list()
        except Exception as exc:  # noqa: BLE001 - a scan failure must not break chat
            logger.warning("Skill catalog refresh failed: %s", exc)
            return {}
        if not skills:
            return {}

        section = format_skills_prompt(skills)
        current = getattr(request, "system_message", None)
        base_text = getattr(current, "text", "") or ""
        overrides: dict[str, Any] = {}
        if base_text:
            overrides["system_message"] = SystemMessage(
                content=f"{section}\n\n{base_text}"
            )
        else:
            overrides["system_message"] = SystemMessage(content=section)
        return overrides

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if overrides:
            return handler(request.override(**overrides))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if overrides:
            return await handler(request.override(**overrides))
        return await handler(request)
