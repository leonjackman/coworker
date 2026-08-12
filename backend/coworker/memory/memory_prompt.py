"""System-prompt formatting for the injected memory block."""


def format_memory_prompt(
    sections: list[tuple[str, str, list[str]]],
    char_limit: int,
) -> str:
    """Render memory files into a system-prompt block.

    ``sections`` is a list of ``(label, source_path, entries)`` in precedence
    order (project first). The label carries the mtime so the model can weigh
    freshness; ``char_limit`` triggers an explicit budget warning.
    """
    if not sections:
        return ""
    lines: list[str] = [
        "\n\nThe following are long-term facts about the user or this project that "
        "you should remember across sessions. Treat them as background context, "
        "and prefer them over assumptions. These entries are only updated through "
        "the memory tool during execution.",
        "<memory>",
    ]
    total = 0
    for label, source, entries in sections:
        total += sum(len(e) for e in entries)
        lines.append(f"  <file scope=\"{label}\" source=\"{source}\">")
        if not entries:
            lines.append("    (no entries)")
        for entry in entries:
            lines.append(f"    <memory_item>{_escape(entry)}</memory_item>")
        lines.append("  </file>")
    if total > char_limit:
        lines.append(
            f"  <budget_warning>Memory is near its size limit "
            f"({total} chars / {char_limit} limit). Prefer the most important "
            "entries; you may propose consolidating or removing stale ones via "
            "the memory tool.</budget_warning>"
        )
    lines.append("</memory>")
    return "\n".join(lines)


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )