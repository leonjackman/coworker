"""MemoryMiddleware: injects long-term memory into the agent graph.

Unlike skills (which are hidden entirely in the ``discuss`` phase — planning is
read-only), memory is *injected in every phase*: planning is exactly when the
model needs the user's background preferences and project conventions. The
write side is available in every phase too — the ``memory`` tool is exposed by
the phase gate in both ``discuss`` and ``execute``, and writes pause for the
user's approval via the HITL middleware (supervised/guarded) or pass directly
(autonomous). Only file/system mutation stays execute-gated.

Files are read directly from the backend through the MemoryManager — never via
the workspace ``resolve_path`` guard — so the workspace boundary invariant is
not weakened (an agent holding the memory files still cannot touch them with
the regular file tools). The injected section is a compact, token-budgeted
INDEX (codex-aligned): each file's path and freshness are resident so the model
can reason about what is available and load full content on demand via
``memory_read``.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from coworker.logger import get_logger

logger = get_logger(__name__)


class MemoryMiddleware(AgentMiddleware):

    def __init__(self, manager: Any):
        # Typed as Any to avoid a circular import with MemoryManager.
        self.manager = manager

    def _overrides(self, request: Any) -> dict[str, Any]:
        try:
            if self.manager.bound_project:
                section = self.manager.render_for(self.manager.bound_project, self.manager.bound_agent)
            else:
                section = self.manager.render_prompt()
        except Exception as exc:  # noqa: BLE001 - a scan failure must never break chat
            logger.warning("Memory load failed: %s", exc)
            return {}
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
