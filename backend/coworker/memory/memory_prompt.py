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
# Max lines of each block rendered into the resident preview. A block with more
# lines shows a "more lines exist" marker so the model never mistakes the
# preview for the full fact (memory_read loads the complete file on demand).
PREVIEW_BLOCK_LINES = 3


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
        content = (node.content or "").strip()
        blocks = tuple(getattr(node, "blocks", None) or ())
        if not blocks and content:
            from .memory_file import split_blocks

            blocks = tuple(split_blocks(content))
        header = _file_header(node, blocks=len(blocks), lines=_block_line_count(blocks))
        lines.append(header)
        if not content:
            lines.append("    (empty)")
            continue
        preview_lines, cost, clipped = _render_preview(node, blocks, budget - used, est)
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


def _block_line_count(blocks: tuple[str, ...]) -> int:
    return sum(len(b.splitlines()) for b in blocks if b and b.strip())


def _render_preview(
    node: Any,
    blocks: tuple[str, ...],
    remaining_tokens: int,
    est: Any,
) -> tuple[list[str], int, bool]:
    """Render the token-budgeted preview for one node.

    Renders up to ``PREVIEW_BLOCK_LINES`` lines of EVERY block that fits (not
    just the first line). When a block has more lines than shown, a marker
    records the hidden count so the model knows the preview is partial and can
    fetch the full file with ``memory_read``. ``clipped`` is True only when the
    token budget forced content to be cut.
    """
    if not blocks:
        content = (node.content or "").strip()
        if not content:
            return [], 0, False
        from .memory_file import split_blocks

        blocks = tuple(split_blocks(content))
    lines: list[str] = []
    used = 0
    clipped = False
    for block in blocks:
        if not block or not block.strip():
            continue
        raw_lines = block.strip().splitlines()
        if not raw_lines:
            continue
        shown = 0
        stopped = False
        for raw in raw_lines[:PREVIEW_BLOCK_LINES]:
            line = raw[:PREVIEW_LINE_CHARS]
            cost = est(line)
            if used + cost > remaining_tokens:
                clipped = True
                stopped = True
                break
            lines.append(f"    {_escape(line)}")
            used += cost
            shown += 1
        hidden = len(raw_lines) - shown
        if stopped:
            # Token budget ran out mid-block: signal any unshown remainder.
            if shown > 0 and hidden > 0:
                marker = _hidden_marker(hidden)
                cost = est(marker)
                if used + cost <= remaining_tokens:
                    lines.append(f"    {_escape(marker)}")
                    used += cost
            break
        if hidden > 0:
            # Block has more lines than the bounded preview shows: say so, then
            # CONTINUE rendering later blocks (a big first block must not starve
            # the rest of the file).
            marker = _hidden_marker(hidden)
            cost = est(marker)
            if used + cost > remaining_tokens:
                clipped = True
                break
            lines.append(f"    {_escape(marker)}")
            used += cost
        if used >= remaining_tokens:
            clipped = True
            break
    if not lines:
        # Nothing fit, but we still want the model to know this file has facts.
        clipped = True
    return lines, used, clipped


def _hidden_marker(hidden: int) -> str:
    return (
        f"…(block has {hidden} more line{'s' if hidden != 1 else ''}; "
        "use memory_read for the full content)"
    )


def format_memory_prompt(nodes: list[Any], char_limit: int) -> str:
    """Backward-compatible char-budget entry point.

    Converted to a token budget using the same estimator the token path uses
    (~4 chars/token) so existing callers keep working. The manager uses the
    token-budget path directly (``format_memory_index``).
    """
    budget = max(8, int(char_limit or 0) // 4)
    return format_memory_index(nodes, token_budget=budget)


def _file_header(node: Any, *, blocks: int = 0, lines: int = 0) -> str:
    source = _node_source(node)
    rel = getattr(node, "rel", "") or ""
    stats = ""
    if blocks > 0:
        stats += f' blocks="{blocks}"'
    if lines > 0:
        stats += f' lines="{lines}"'
    return (
        f'  <file kind="{_escape(node.kind)}" name="{_escape(node.name)}" '
        f'rel="{_escape(rel)}"{stats} source="{_escape(source)}">'
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
