"""MCP middleware for the Coworker agent graph.

Loads tools from every enabled MCP server and appends them to the tool list of
each model call. Loading is lazy (first model call) and cached in
``mcp_loader`` so repeated graph builds do not respawn server processes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from .mcp import McpManager
from .mcp_loader import load_mcp_tools_sync

logger = logging.getLogger(__name__)


class McpToolMiddleware(AgentMiddleware):

    def __init__(self, mcp_manager: McpManager):
        self.mcp_manager = mcp_manager
        self.mcp_tools: list[Any] = []
        self._servers: list[dict[str, Any]] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        try:
            servers = self.mcp_manager.list_runtime_configs(enabled_only=True)
        except Exception as exc:  # noqa: BLE001 - config problems must not break chat
            logger.warning("Failed to read MCP config: %s", exc)
            return

        self._servers = servers

        if not servers:
            return

        try:
            self.mcp_tools = load_mcp_tools_sync(servers)
            logger.info(
                "Loaded %d MCP tool(s) from %d enabled server(s)",
                len(self.mcp_tools),
                len(servers),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load MCP tools: %s", exc)
            self.mcp_tools = []

    def _server_names(self) -> dict[str, str]:
        return {
            str(server.get("id") or "").strip(): str(server.get("name") or server.get("id") or "")
            for server in getattr(self, "_servers", [])
            if str(server.get("id") or "").strip()
        }

    def _mcp_summary(self) -> str | None:
        """One line per enabled server with the tools the model can call."""
        if not self.mcp_tools:
            return None
        names = self._server_names()
        if not names:
            return None

        by_server: dict[str, list[str]] = {}
        for tool in self.mcp_tools:
            metadata = getattr(tool, "metadata", None)
            server_id = metadata.get("coworker_server") if isinstance(metadata, dict) else None
            tool_name = getattr(tool, "name", None)
            if server_id and tool_name:
                by_server.setdefault(str(server_id), []).append(str(tool_name))

        if not by_server:
            return None

        lines = []
        for server_id, name in names.items():
            tools = sorted(by_server.get(server_id) or [])
            if not tools:
                continue
            lines.append(f"- {name}: {', '.join(tools)}")
        return "\n".join(lines) if lines else None

    def _overrides(self, request: Any) -> dict[str, Any]:
        self._ensure_loaded()
        if not self.mcp_tools:
            return {}

        existing = list(getattr(request, "tools", []) or [])
        taken = {getattr(tool, "name", None) for tool in existing}

        additions = []
        for t in self.mcp_tools:
            name = getattr(t, "name", None)
            if name in taken:
                logger.warning("Skipping MCP tool %r: name collides with a builtin tool", name)
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
            # Put attribution BEFORE the rest of the prompt so the model uses it
            # to correctly attribute all tools it sees.
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
        # The initial MCP tool load can block for seconds, so run it off the
        # event loop via `to_thread`; `_overrides` itself is idempotent.
        overrides = await asyncio.to_thread(self._overrides, request)
        if overrides:
            return await handler(request.override(**overrides))
        return await handler(request)
