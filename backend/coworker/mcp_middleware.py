"""MCP middleware for the Coworker agent graph.

Attaches dispatch tools from the persistent :class:`McpSessionManager` to the
model call, registers them with the agent's ``ToolNode`` when they are already
loaded, and dynamically resolves MCP tool calls at execution time (the pattern
the langchain agent factory documents for middleware-added tools).

MCP tools are only exposed in the **execute** phase. In ``discuss`` (plan
mode) they are hidden entirely, matching the product decision that planning is
read-only and never touches external services.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from .mcp_session import McpSessionManager

logger = logging.getLogger(__name__)


def _phase_is_discuss(state: Any) -> bool:
    """Mirror of ``agents.normalize_phase`` (avoid a circular import)."""
    phase = str((state or {}).get("phase") or "")
    if phase in ("discuss", "execute"):
        return phase == "discuss"
    work_mode = str((state or {}).get("work_mode") or "build")
    return work_mode == "plan"


def _tool_name(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


class McpToolMiddleware(AgentMiddleware):

    def __init__(self, session_manager: McpSessionManager):
        self.mcp_manager = session_manager.mcp_manager
        self.session_manager = session_manager
        # Opportunistic ToolNode registration: populated once sessions are
        # connected. Execution does not depend on this -- the dynamic
        # ``wrap_tool_call`` path below resolves unregistered MCP tools.
        self.tools: list[Any] = list(session_manager.all_tools())
        self._servers: list[dict[str, Any]] = []

    def _refresh_tools(self) -> None:
        self._servers = []
        try:
            self._servers = self.mcp_manager.list_runtime_configs(enabled_only=True)
        except Exception as exc:  # noqa: BLE001 - config problems must not break chat
            logger.warning("Failed to read MCP config: %s", exc)
        self.tools = list(self.session_manager.all_tools())

    def tool_names(self) -> set[str]:
        """Names of currently-connected MCP tools (for the phase gate)."""
        return self.session_manager.tool_names()

    def _server_names(self) -> dict[str, str]:
        return {
            str(server.get("id") or "").strip(): str(server.get("name") or server.get("id") or "")
            for server in getattr(self, "_servers", [])
            if str(server.get("id") or "").strip()
        }

    def _mcp_summary(self) -> str | None:
        """One line per enabled server with the tools the model can call."""
        tools = self.tools
        if not tools:
            return None
        names = self._server_names()
        if not names:
            return None

        by_server: dict[str, list[str]] = {}
        for tool in tools:
            metadata = getattr(tool, "metadata", None)
            server_id = metadata.get("coworker_server") if isinstance(metadata, dict) else None
            tool_name = getattr(tool, "name", None)
            if server_id and tool_name:
                by_server.setdefault(str(server_id), []).append(str(tool_name))

        if not by_server:
            return None

        lines = []
        for server_id, name in names.items():
            server_tools = sorted(by_server.get(server_id) or [])
            if not server_tools:
                continue
            lines.append(f"- {name}: {', '.join(server_tools)}")
        return "\n".join(lines) if lines else None

    def _overrides(self, request: Any) -> dict[str, Any]:
        self.session_manager.ensure_connected(enable_browser_flow=False)
        self._refresh_tools()
        tools = self.tools
        if not tools:
            return {}

        if _phase_is_discuss(getattr(request, "state", None)):
            logger.debug("MCP tools hidden in discuss phase")
            return {}

        existing = list(getattr(request, "tools", None) or [])
        taken = {getattr(tool, "name", None) for tool in existing}

        additions = []
        for t in tools:
            name = getattr(t, "name", None)
            if name in taken:
                logger.debug("MCP tool %r already exposed (registered or colliding)", name)
                continue
            taken.add(name)
            additions.append(t)

        if not additions:
            return {}

        overrides: dict[str, Any] = {"tools": existing + additions}

        # Build an explicit attribution section and PREPEND it to the system prompt.
        # Placing it first ensures the model sees it before other content, making
        # the MCP attribution authoritative rather than just another block.
        summary = self._mcp_summary()
        if summary:
            current = getattr(request, "system_message", None)
            base_text = getattr(current, "text", "") or ""
            section = (
                f"## MCP 服务与工具归属 / MCP Server Attribution\n\n"
                "以下工具来自已连接的 MCP 服务（按服务器分组）。"
                "When identifying tools, tools listed below belong to the named MCP server.\n\n"
                f"{summary}\n\n"
                "If asked which tools belong to MCP servers, use this section as your reference.\n"
            )
            overrides["system_message"] = SystemMessage(content=f"{section}{base_text}" if base_text else section)

        return overrides

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if overrides:
            return handler(request.override(**overrides))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # Streaming/resume run the graph asynchronously (`astream`/`ainvoke`).
        # The initial MCP connect can block for seconds, so run it off the
        # event loop via `to_thread`; `_overrides` itself is idempotent.
        overrides = await asyncio.to_thread(self._overrides, request)
        if overrides:
            return await handler(request.override(**overrides))
        return await handler(request)

    def _resolve_tool(self, request: Any) -> Any:
        name = _tool_name(request)
        if not name:
            return None
        for tool in self.session_manager.all_tools():
            if getattr(tool, "name", None) == name:
                return tool
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        # Already registered tools (or builtins) pass straight through.
        if request.tool is not None:
            return handler(request)
        tool = self._resolve_tool(request)
        if tool is None:
            return handler(request)
        return handler(request.override(tool=tool))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if request.tool is not None:
            return await handler(request)
        tool = self._resolve_tool(request)
        if tool is None:
            return await handler(request)
        return await handler(request.override(tool=tool))
