"""SkillMiddleware: injects the skill catalog into the agent graph.

Only the catalog (name + description + location + version) is injected into
the system prompt; the full ``SKILL.md`` body is loaded on demand through the
existing ``read_file`` tool (progressive disclosure, Agent Skills standard).

The user can also **explicitly activate** a skill by including a
``[skill:<name>]`` (or ``[skill:<name>:<command>]`` for a sub-command) tag in
their message — the frontend ``/`` command card produces these tags. Activated
skills get their full body injected straight into the system prompt (hidden,
never echoed into the visible conversation), following the mainstream
"label in the message, body stays hidden" pattern.

Skills are hidden entirely in the ``discuss`` (plan) phase, matching the
product decision that planning is read-only and never pulls in workflow
instructions.
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from .skill_manager import SkillManager
from .skills import format_skills_prompt_bounded

from coworker.logger import get_logger
logger = get_logger(__name__)

# ``[skill:name]`` for a whole skill, ``[skill:name:command]`` for a sub-command.
# Skill/command slugs only contain [A-Za-z0-9_-] (plus '.' for skill dirs).
SKILL_MARKER_RE = re.compile(
    r"\[skill:([A-Za-z0-9][A-Za-z0-9_.-]*)(?::([A-Za-z0-9][A-Za-z0-9_.-]*))?\]"
)


def _phase_is_discuss(state: Any) -> bool:
    """Mirror of ``agents.normalize_phase`` (avoid a circular import)."""
    phase = str((state or {}).get("phase") or "")
    if phase in ("discuss", "execute"):
        return phase == "discuss"
    work_mode = str((state or {}).get("work_mode") or "build")
    return work_mode == "plan"


def _message_text(message: Any) -> str:
    """Best-effort text of a message (BaseMessage or dict, plain or multimodal)."""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


# Hard cap per activated skill body. Skill bodies are injected into the system
# prompt on EVERY model call of the turn; an unbounded one is a context bomb
# (the same class of failure as uncapped tool results, just through a
# different door). 12k chars ≈ ~3-4k tokens — a full workflow body without
# letting a single skill blow the window.
SKILL_BODY_MAX_CHARS = 12_000


def _format_active_skills(active: list[dict[str, str]]) -> str:
    """Render the full bodies of explicitly activated skills into a prompt block."""
    lines = [
        "\n\nThe user explicitly activated the following skill(s) with their `[skill:...]` tag. "
        "Follow their instructions for the current task.",
        "<activated_skills>",
    ]
    for item in active:
        body = item["body"]
        truncated = len(body) > SKILL_BODY_MAX_CHARS
        if truncated:
            body = body[:SKILL_BODY_MAX_CHARS] + "\n[skill instructions truncated by Coworker to fit context]"
        lines.append("  <skill>")
        lines.append(f"    <name>{item['name']}</name>")
        if item.get("location"):
            lines.append(f"    <location>{item['location']}</location>")
        lines.append("    <instructions>")
        lines.append(body)
        lines.append("    </instructions>")
        lines.append("  </skill>")
    lines.append("</activated_skills>")
    return "\n".join(lines)


def resolve_active_skills(
    manager: SkillManager,
    messages: list[Any],
    body_cache: dict[tuple[str, str], tuple[str, str] | None] | None = None,
) -> list[dict[str, str]]:
    """Scan the conversation for ``[skill:...]`` tags and load their bodies.

    Shared by SkillMiddleware and the SystemAssembler. ``body_cache`` memoizes
    SKILL.md reads across a turn.
    """
    cache = body_cache if body_cache is not None else {}
    allowed = {s.name for s in manager.injection_list()}
    seen: set[tuple[str, str]] = set()
    active: list[dict[str, str]] = []
    for msg in messages:
        if getattr(msg, "type", None) != "human":
            continue
        content = _message_text(msg)
        if not content:
            continue
        for match in SKILL_MARKER_RE.finditer(content):
            name = match.group(1)
            command = match.group(2) or ""
            key = (name, command)
            if key in seen or name not in allowed:
                continue
            seen.add(key)
            if key not in cache:
                if command:
                    cache[key] = manager.read_command_body(name, command)
                else:
                    cache[key] = manager.read_body(name)
            body = cache[key]
            if body is None:
                continue
            active.append(
                {"name": name, "command": command, "body": body[0], "location": body[1]}
            )
    return active


def build_skill_section(
    manager: SkillManager,
    messages: list[Any],
    body_cache: dict[tuple[str, str], tuple[str, str] | None] | None = None,
) -> str:
    """Render the full skills section: bounded catalog + activated-skill bodies.

    Returns ``""`` when there is nothing to inject (no skills / hidden phase).
    """
    try:
        skills = manager.injection_list()
        active = resolve_active_skills(manager, messages, body_cache)
    except Exception as exc:  # noqa: BLE001 - a scan failure must not break chat
        logger.warning("Skill catalog refresh failed: %s", exc)
        return ""
    section = format_skills_prompt_bounded(skills) if skills else ""
    if active:
        section = f"{section}\n\n{_format_active_skills(active)}".strip()
    return section


class SkillMiddleware(AgentMiddleware):

    def __init__(self, manager: SkillManager):
        self.manager = manager
        # Per-request memo of resolved (name, command) -> (body, base_dir) so a
        # long agent turn doesn't re-read the same SKILL.md on every model call.
        self._body_cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    def _overrides(self, request: Any) -> dict[str, Any]:
        if _phase_is_discuss(getattr(request, "state", None)):
            logger.debug("skills hidden in discuss phase")
            return {}
        messages = (getattr(request, "state", None) or {}).get("messages", []) or []
        section = build_skill_section(self.manager, messages, self._body_cache)
        if not section:
            return {}

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