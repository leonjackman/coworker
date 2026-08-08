"""MCP connection tester -- validates a server config and discovers its tools."""

from __future__ import annotations

import logging
import time
from typing import Any

from .mcp_loader import (
    DEFAULT_TIMEOUT_SECONDS,
    build_connection,
    normalize_transport,
    run_blocking,
)

logger = logging.getLogger(__name__)


def _friendly_error(exc: BaseException, transport: str) -> str:
    """Turn raw adapter/SDK exceptions into something a user can act on."""
    if isinstance(exc, TimeoutError):
        return "Connection timed out"

    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()

    if isinstance(exc, FileNotFoundError) or "no such file or directory" in lowered:
        return f"Command not found: {text}"
    if "unauthorized" in lowered or "401" in lowered:
        return f"Authentication required (401): {text}"
    if "403" in lowered:
        return f"Access denied (403): {text}"
    if "404" in lowered:
        return f"Endpoint not found (404) -- check the URL: {text}"
    if transport == "sse" and "text/event-stream" in lowered:
        return f"Server did not return an SSE stream -- try HTTP transport: {text}"

    if len(text) > 300:
        text = text[:297] + "..."
    return text


def test_mcp_connection_sync(
    transport: str,
    command: str = "",
    args: str = "",
    url: str = "",
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
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
            "error": _friendly_error(exc, canonical),
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
