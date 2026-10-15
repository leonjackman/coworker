"""Outermost tool-call wrapper: the single tool-level error boundary.

Mounted FIRST in the middleware chain so it wraps every other tool wrapper
(phase gate, command approval, MCP). Its only job is to hand a tool exception
to the central policy (:func:`coworker.errors.handle`) and act on the outcome:

* behavioral -> return an error ``ToolMessage`` so the model sees the failure
  and reports/falls back instead of the whole turn dying;
* fatal / interrupt / cancellation -> re-raise unchanged.

All classification and logging live in ``coworker.errors`` -- this class adds no
policy of its own.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from ...errors import Action, Site, behavior_message, handle
from ..core import CoworkerAgentState


def _tool_call_name(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_id(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "unknown")
    return str(getattr(tool_call, "id", "unknown") or "unknown")


class ToolErrorFallbackMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Convert behavioral tool exceptions into model-visible error ToolMessages."""

    name = "ToolErrorFallbackMiddleware"

    def _fallback_message(self, request: Any, exc: BaseException) -> ToolMessage | None:
        tool = _tool_call_name(request)
        outcome = handle(Site.TOOL, exc, tool=tool, detail="tool_call")
        if outcome.action is not Action.TOOL_MESSAGE:
            return None
        return ToolMessage(
            content=behavior_message(exc, tool=tool),
            tool_call_id=_tool_call_id(request),
            name=tool or None,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            return handler(request)
        except BaseException as exc:  # noqa: BLE001 - policy decides
            message = self._fallback_message(request, exc)
            if message is None:
                raise
            return message

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            return await handler(request)
        except BaseException as exc:  # noqa: BLE001 - policy decides
            message = self._fallback_message(request, exc)
            if message is None:
                raise
            return message


__all__ = ["ToolErrorFallbackMiddleware"]
