"""MCP middleware for the Coworker agent graph.

Loads tools from every enabled MCP server and appends them to the tool list of
each model call. Loading is lazy (first model call) and cached in
``mcp_loader`` so repeated graph builds do not respawn server processes.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from .mcp import McpManager
from .mcp_loader import load_mcp_tools_sync

logger = logging.getLogger(__name__)


class McpToolMiddleware(AgentMiddleware):
    """Augments every model call with tools exposed by enabled MCP servers."""

    def __init__(self, mcp_manager: McpManager):
        self.mcp_manager = mcp_manager
        self.mcp_tools: list[Any] = []
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

    def _overrides(self, request: Any) -> dict[str, Any]:
        self._ensure_loaded()
        if not self.mcp_tools:
            return {}

        existing = list(getattr(request, "tools", []) or [])
        taken = {getattr(tool, "name", None) for tool in existing}

        additions = []
        for tool in self.mcp_tools:
            name = getattr(tool, "name", None)
            if name in taken:
                logger.warning("Skipping MCP tool %r: name collides with a builtin tool", name)
                continue
            taken.add(name)
            additions.append(tool)

        if not additions:
            return {}
        return {"tools": existing + additions}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if overrides:
            return handler(request.override(**overrides))
        return handler(request)
