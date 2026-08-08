#!/usr/bin/env python3
"""Predefined MCP server templates offered as one-click quick-add entries.

Only servers that run without extra credentials are listed, so a template can
always be added and tested straight away.
"""

from typing import Any

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Structured step-by-step reasoning tool",
        "transport": "stdio",
        "command": "npx",
        "args": "-y @modelcontextprotocol/server-sequential-thinking",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "memory-server",
        "name": "Memory Server",
        "description": "Knowledge graph memory for persistent context",
        "transport": "stdio",
        "command": "npx",
        "args": "-y @modelcontextprotocol/server-memory",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "everything-server",
        "name": "Everything (reference)",
        "description": "Official reference server exercising the full MCP spec",
        "transport": "stdio",
        "command": "npx",
        "args": "-y @modelcontextprotocol/server-everything",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "deepwiki",
        "name": "DeepWiki",
        "description": "Ask questions about any public GitHub repository",
        "transport": "http",
        "command": "",
        "args": "",
        "url": "https://mcp.deepwiki.com/mcp",
        "env": {},
        "headers": {},
        "homepage": "https://mcp.deepwiki.com",
    },
    {
        "id": "context7",
        "name": "Context7",
        "description": "Up-to-date documentation and code examples for libraries",
        "transport": "http",
        "command": "",
        "args": "",
        "url": "https://mcp.context7.com/mcp",
        "env": {},
        "headers": {},
        "homepage": "https://context7.com",
    },
]
