"""Shared fixtures for the Coworker backend test suite.

The MCP tests drive **real** stdio MCP servers (FastMCP subprocesses) rather
than mocks: the whole point of the session layer is the cross-loop /
cross-process plumbing, which a mock would paper over.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.coworker.mcp.mcp import McpManager  # noqa: E402
from backend.coworker.mcp.mcp_session import McpSessionManager  # noqa: E402

# A minimal stdio MCP server. ``annotations`` is what the approval ladder reads,
# so the fixture exposes one tool of each risk class.
SERVER_TEMPLATE = textwrap.dedent(
    '''
    import sys
    from pathlib import Path
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("{server_name}")
    MARKER = Path(sys.argv[1])
    MARKER.write_text(MARKER.read_text() + "x" if MARKER.exists() else "x")

    @mcp.tool(annotations={{"readOnlyHint": True}})
    def peek(text: str) -> str:
        """Read-only echo."""
        return "peek:" + text

    @mcp.tool(annotations={{"readOnlyHint": False, "destructiveHint": True}})
    def wipe(target: str) -> str:
        """Destructive operation."""
        return "wiped:" + target

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Undeclared annotations -> treated as write."""
        return a + b

    @mcp.tool()
    def {unique_tool}(value: str) -> str:
        """Server-specific tool (no cross-server conflict)."""
        return "{server_name}:" + value

    mcp.run()
    '''
)


def write_server(tmp: Path, name: str, unique_tool: str) -> Path:
    path = tmp / f"server_{name}.py"
    path.write_text(SERVER_TEMPLATE.format(server_name=name, unique_tool=unique_tool), encoding="utf-8")
    return path


def content_text(result: Any) -> str:
    """Flatten a tool result (content block list / tuple) into text."""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        return "".join(str(block.get("text", "")) for block in result if isinstance(block, dict))
    return str(result)


@pytest.fixture
def mcp_manager(tmp_path: Path) -> McpManager:
    return McpManager(tmp_path / "mcp_servers.json")


@pytest.fixture
def single_server(tmp_path: Path, mcp_manager: McpManager):
    """One connected stdio MCP server; yields ``(session_manager, server_id)``."""
    server = write_server(tmp_path, "alpha", "alpha_only")
    marker = tmp_path / "alpha.marker"
    entry = mcp_manager.add_server(
        name="Alpha",
        transport="stdio",
        command=sys.executable,
        args=f"{server} {marker}",
    )
    sessions = McpSessionManager(tmp_path, mcp_manager, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    sessions.ensure_connected(enable_browser_flow=False)
    try:
        yield sessions, entry["id"], marker
    finally:
        sessions.shutdown()


@pytest.fixture
def two_servers(tmp_path: Path, mcp_manager: McpManager):
    """Two stdio servers advertising overlapping tool names (conflict case)."""
    ids = []
    for name, unique in (("alpha", "alpha_only"), ("beta", "beta_only")):
        server = write_server(tmp_path, name, unique)
        marker = tmp_path / f"{name}.marker"
        entry = mcp_manager.add_server(
            name=name.capitalize(),
            transport="stdio",
            command=sys.executable,
            args=f"{server} {marker}",
        )
        ids.append(entry["id"])
    sessions = McpSessionManager(tmp_path, mcp_manager, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    sessions.ensure_connected(enable_browser_flow=False)
    try:
        yield sessions, ids
    finally:
        sessions.shutdown()
