"""System-prompt formatting for the injected memory block.

Each memory file becomes a ``<file>`` section carrying its kind, name and a
freshness label; the whole set is injected in precedence order (system →
project BASE → project context → agent core → sessions).
"""

from __future__ import annotations

from typing import Any


def format_memory_prompt(
    nodes: list[Any],
    char_limit: int,
) -> str:
    """Render memory files into a system-prompt block.

    ``nodes`` is a list of ``MemoryNode`` objects in injection precedence order
    (see ``memory_discovery.scan``). ``char_limit`` triggers an explicit budget
    warning; content is never truncated.
    """
    if not nodes:
        return ""
    lines: list[str] = [
        "\n\nThe following are long-term facts about the user, this project, "
        "or the current agent that you should remember across sessions. Treat "
        "them as background context and prefer them over assumptions. These "
        "files are only updated through the memory tool or by the user.",
        "<memory>",
    ]
    total = 0
    for node in nodes:
        content = node.content or ""
        total += len(content)
        source = _node_source(node)
        lines.append(f'  <file kind="{_escape(node.kind)}" name="{_escape(node.name)}" source="{source}">')
        if not content.strip():
            lines.append("    (empty)")
        for line in content.splitlines():
            lines.append(f"    {_escape(line)}")
        lines.append("  </file>")
    if total > char_limit:
        lines.append(
            f"  <budget_warning>Memory is near its size limit "
            f"({total} chars / {char_limit} limit). Prefer the most important "
            "facts; you may propose consolidating or removing stale ones via "
            "the memory tool.</budget_warning>"
        )
    lines.append("</memory>")
    return "\n".join(lines)


def _node_source(node: Any) -> str:
    label = {
        "system": "system memory",
        "base_file": "project memory (user-maintained)",
        "project_file": "project memory (system-generated)",
        "agent_file": "agent memory",
        "session_file": "agent session memory",
    }.get(node.kind, node.kind)
    return f"{label} (updated {_format_mtime(getattr(node, 'mtime', 0))})"


def _format_mtime(mtime: float) -> str:
    if not mtime:
        return "never"
    import datetime

    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
