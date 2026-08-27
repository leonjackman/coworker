"""System-prompt formatting for the injected memory block.

The resident block is a COMPACT INDEX, not full file bodies — codex-aligned:
only a condensed summary is resident, full files load on demand via the
``memory_read`` tool. Each memory file is listed with its kind/name/rel path
and a one-line-per-block preview of its facts; the whole set is rendered in
relevance order so an agent's own core files (SOUL/AGENT/MEMORY) are never
starved by system or team files.

Budgeting is token-based (``coworker.context.estimate_text_tokens``) so dense
CJK memory does not silently inflate the per-call overhead; structural markup
(``<file>`` headers, wrapper tags) is NOT counted against the content budget,
so boilerplate never eats the facts.
"""

from __future__ import annotations

from typing import Any

# Agent core files are the most relevant memory and always render first.
AGENT_CORE_NAMES = ("SOUL.md", "AGENT.md", "MEMORY.md")
# First line of each Markdown block is kept, clipped to this width.
PREVIEW_LINE_CHARS = 160


def _priority(node: Any) -> int:
    """Relevance order (lower = rendered first).

    agent core (SOUL/AGENT/MEMORY) -> agent base files -> project (BASE/PROJECT)
    -> system -> team/other. This is the M1 fix: an agent's own MEMORY.md
    (last in the old system-first precedence) can no longer be starved.
    """
    kind = getattr(node, "kind", "") or ""
    name = getattr(node, "name", "") or ""
    if kind == "agent_file" and name in AGENT_CORE_NAMES:
        return 0
    if kind == "agent_file":
        return 1
    if kind in ("base_file", "project_file"):
        return 2
    if kind == "system":
        return 3
    return 4  # team / other (team files scan as folder_file)


def _estimate(text: str) -> int:
    from coworker.context import estimate_text_tokens

    return estimate_text_tokens(text)


def format_memory_index(
    nodes: list[Any],
    token_budget: int = 2500,
    estimate_fn: Any = None,
) -> str:
    """Render the resident memory INDEX for a set of ``MemoryNode`` objects.

    ``nodes`` may arrive in any order — they are re-sorted by relevance inside.
    ``token_budget`` caps the CONTENT previews (structural markup is free, M5).
    ``estimate_fn`` defaults to ``coworker.context.estimate_text_tokens``.
    """
    if not nodes:
        return ""
    est = estimate_fn or _estimate
    ordered = sorted(nodes, key=_priority)

    header_lines = [
        "\n\nThe following are long-term facts about the user, this project, or the "
        "current agent. This is a compact INDEX of your memory files — full content "
        "is loaded on demand: read any file with the `memory_read` tool using its "
        "`rel` path below, and cite that rel path when you rely on a memory in your "
        "final answer. Treat these as background context and prefer them over "
        "assumptions.",
        "<memory>",
    ]
    lines: list[str] = list(header_lines)
    budget = max(1, int(token_budget or 0))
    used = 0
    truncated = False
    for node in ordered:
        header = _file_header(node)
        lines.append(header)
        content = (node.content or "").strip()
        if not content:
            lines.append("    (empty)")
            continue
        preview_lines, cost, clipped = _render_preview(node, budget - used, est)
        if cost > 0:
            lines.extend(preview_lines)
            used += cost
        if clipped or (cost == 0 and budget - used <= 0):
            truncated = True
    if truncated:
        lines.append(
            "  <budget_warning>Memory is compacted to an index to keep this prompt "
            "small. Full files, older facts and additional session records are "
            "available on demand via the memory_read tool.</budget_warning>"
        )
    lines.append("</memory>")
    return "\n".join(lines)


def _render_preview(
    node: Any,
    remaining_tokens: int,
    est: Any,
) -> tuple[list[str], int, bool]:
    """Render the one-line-per-block preview for one node.

    Returns ``(lines, tokens_used, clipped)``. ``clipped`` is True when the
    preview had to be cut to fit ``remaining_tokens``.
    """
    content = (node.content or "").strip()
    blocks = tuple(getattr(node, "blocks", None) or ())
    if not blocks:
        from .memory_file import split_blocks

        blocks = tuple(split_blocks(content))
    lines: list[str] = []
    used = 0
    clipped = False
    for block in blocks:
        if not block or not block.strip():
            continue
        first = block.strip().splitlines()[0]
        line = first[:PREVIEW_LINE_CHARS]
        cost = est(line)
        if used + cost > remaining_tokens:
            clipped = True
            break
        lines.append(f"    {_escape(line)}")
        used += cost
    if not lines:
        # Nothing fit, but we still want the model to know this file has facts.
        clipped = True
    return lines, used, clipped


def format_memory_prompt(nodes: list[Any], char_limit: int) -> str:
    """Backward-compatible char-budget entry point.

    Converted to a token budget using the same estimator the token path uses
    (~4 chars/token) so existing callers keep working. The manager uses the
    token-budget path directly (``format_memory_index``).
    """
    budget = max(8, int(char_limit or 0) // 4)
    return format_memory_index(nodes, token_budget=budget)


def _file_header(node: Any) -> str:
    source = _node_source(node)
    rel = getattr(node, "rel", "") or ""
    return (
        f'  <file kind="{_escape(node.kind)}" name="{_escape(node.name)}" '
        f'rel="{_escape(rel)}" source="{_escape(source)}">'
    )


def _node_source(node: Any) -> str:
    label = {
        "system": "system memory",
        "base_file": "project memory (user-maintained)",
        "project_file": "project memory (system-generated)",
        "agent_file": "agent memory",
        "session_file": "agent session memory",
    }.get(getattr(node, "kind", ""), getattr(node, "kind", ""))
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
