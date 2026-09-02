#!/usr/bin/env python3
"""Predefined MCP server templates offered as one-click quick-add entries.

Only servers that run without extra credentials are listed, so a template can
always be added and tested straight away.
"""

import copy
import os
import shlex
from typing import Any

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Secure file read/write/edit within sandboxed directories",
        "transport": "stdio",
        "command": "npx",
        "args": "-y @modelcontextprotocol/server-filesystem",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers",
        "color": "#000000",
        "category": "code",
    },
    {
        "id": "git",
        "name": "Git",
        "description": "Read, search, and manipulate Git repositories (status, diffs, commits, branches)",
        "transport": "stdio",
        "command": "uvx",
        "args": "mcp-server-git",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers",
        "color": "#F05032",
        "category": "code",
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
        "color": "#7C3AED",
        "category": "code",
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
        "color": "#F59E0B",
        "category": "data",
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "description": "Fetch web content and convert it to markdown",
        "transport": "stdio",
        "command": "uvx",
        "args": "mcp-server-fetch",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "color": "#0EA5E9",
        "category": "web",
    },
    {
        "id": "time",
        "name": "Time",
        "description": "Current time and timezone conversion",
        "transport": "stdio",
        "command": "uvx",
        "args": "mcp-server-time",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        "color": "#6366F1",
        "category": "productivity",
    },
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
        "color": "#10B981",
        "category": "code",
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
        "color": "#3B82F6",
        "category": "data",
    },
    {
        "id": "playwright",
        "name": "Playwright",
        "description": "Browser automation, screenshots, and web scraping",
        "transport": "stdio",
        "command": "npx",
        "args": "-y @playwright/mcp@latest",
        "url": "",
        "env": {},
        "headers": {},
        "homepage": "https://playwright.dev",
        "color": "#2EAD33",
        "category": "devops",
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
        "color": "#6366F1",
        "category": "code",
    },
]


def resolve_templates() -> list[dict[str, Any]]:
    """Return a per-request copy of TEMPLATES with runtime defaults applied.

    The official Filesystem server refuses to start without at least one
    allowed directory, so its quick-add args are filled with the current
    user's home directory at serve time (kept out of the static table so the
    path is always resolved on the machine that will actually run it).
    """
    payload: list[dict[str, Any]] = copy.deepcopy(TEMPLATES)
    for entry in payload:
        if entry.get("id") == "filesystem":
            entry["args"] = f"-y @modelcontextprotocol/server-filesystem {shlex.quote(os.path.expanduser('~'))}"
    return payload
