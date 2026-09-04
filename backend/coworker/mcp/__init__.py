"""MCP (Model Context Protocol) integration package.

Layout:

- :mod:`coworker.mcp.mcp` -- server registry / CRUD persisted to JSON
- :mod:`coworker.mcp.mcp_loader` -- config entry -> transport connection
- :mod:`coworker.mcp.mcp_session` -- long-lived sessions on a private event loop
- :mod:`coworker.mcp.mcp_middleware` -- exposes MCP tools to the agent graph
- :mod:`coworker.mcp.mcp_oauth` -- OAuth 2.1 + PKCE for remote servers
- :mod:`coworker.mcp.mcp_discover` -- built-in server templates
- :mod:`coworker.mcp.mcp_test` -- one-shot connection probe
- :mod:`coworker.mcp.mcp_utils` -- shared utilities (error formatting)

Note: this package is named ``mcp`` but must never shadow the third-party
``mcp`` SDK. Python 3 uses absolute imports by default, so ``from mcp.types
import ...`` inside this package still resolves to the SDK; sibling modules
must be imported relatively (``from .mcp_session import ...``).
"""

# Note: All consumers import directly from submodules (e.g. from .mcp import McpManager)
# rather than from the package level, so __all__ is not maintained.
