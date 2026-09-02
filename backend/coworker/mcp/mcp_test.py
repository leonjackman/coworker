"""MCP connection tester -- validates a server config and discovers its tools."""

from __future__ import annotations

import time
from typing import Any

from .mcp_loader import (
    DEFAULT_TIMEOUT_SECONDS,
    build_connection,
    normalize_transport,
    run_blocking,
)
from .mcp_utils import friendly_error

from coworker.logger import get_logger
logger = get_logger(__name__)


def test_mcp_connection_sync(
    transport: str,
    command: str = "",
    args: str = "",
    cwd: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    url: str = "",
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Test a single MCP server connection and return diagnostics.

    Returns a flat dict: ``{ok, latency_ms, tool_count, tools, error}``.
    """
    canonical = normalize_transport(transport)
    started_at = time.monotonic()

    def _elapsed() -> int:
        return round((time.monotonic() - started_at) * 1000)

    try:
        connection = build_connection(
            {
                "id": "test",
                "transport": transport,
                "command": command,
                "args": args,
                "cwd": cwd,
                "timeout": timeout,
                "url": url,
                "env": env or {},
                "headers": headers or {},
            }
        )
    except ValueError as exc:
        return {
            "ok": False,
            "latency_ms": 0,
            "error": str(exc),
            "tool_count": 0,
            "tools": [],
        }

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({"test": connection})
        tools = run_blocking(client.get_tools, timeout=timeout) or []
    except BaseException as exc:  # noqa: BLE001 - report everything to the UI
        logger.info("MCP test connection failed (%s): %s", canonical, exc)
        return {
            "ok": False,
            "latency_ms": _elapsed(),
            "error": friendly_error(exc, canonical),
            "tool_count": 0,
            "tools": [],
        }

    return {
        "ok": True,
        "latency_ms": _elapsed(),
        "error": "",
        "tool_count": len(tools),
        "tools": [
            {
                "name": getattr(tool, "name", "?"),
                "description": getattr(tool, "description", "") or "",
            }
            for tool in tools
        ],
    }
