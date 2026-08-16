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
    (see ``memory_discovery.scan``). ``char_limit`` is a HARD read-side cap: the
    resident block is truncated at ``char_limit`` characters with a pointer to
    ``memory_read`` for on-demand retrieval. Truncation never destroys data — the
    files stay on disk.
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
    truncated = False
    for node in nodes:
        content = node.content or ""
        remaining = char_limit - total
        if remaining <= 0:
            truncated = True
            break
        source = _node_source(node)
        rendered, clipped = _render_file(node, content, remaining)
        lines.append(rendered)
        total += len(rendered)
        if clipped:
            truncated = True
        if remaining <= len(rendered):
            truncated = True
    if truncated:
        lines.append(
            "  <budget_warning>Memory is compacted to keep this prompt small. "
            "Additional session records and topic files are available on demand "
            "via the memory_read tool.</budget_warning>"
        )
    lines.append("</memory>")
    return "\n".join(lines)


def _render_file(node: Any, content: str, remaining: int) -> tuple[str, bool]:
    """Render one memory file, clipping its body so the total stays in budget.

    Returns ``(rendered, clipped)`` where ``clipped`` is True when the file body
    had to be cut to fit ``remaining``.
    """
    source = _node_source(node)
    header = f'  <file kind="{_escape(node.kind)}" name="{_escape(node.name)}" source="{source}">'
    if not content.strip():
        return f"{header}\n    (empty)\n  </file>", False
    parts = [header]
    total = len(header)
    clipped = False
    for line in content.splitlines():
        rendered_line = f"    {_escape(line)}"
        if total + len(rendered_line) + 1 > remaining:
            parts.append("    … (clipped — use memory_read to view)")
            clipped = True
            break
        parts.append(rendered_line)
        total += len(rendered_line) + 1
    parts.append("  </file>")
    return "\n".join(parts), clipped


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
