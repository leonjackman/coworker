"""Memory file model: a plain Markdown file plus parsed block view.

The memory library is directory-based; every file is a standalone Markdown
document the user can open and edit in any editor. ``agent/BASE/MEMORY.md`` and
``SESSIONS/*.md`` carry *blocks* (paragraphs separated by blank lines) so the
memory tool can add/replace/remove individual entries while the file stays
human-readable Markdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryFile:
    """One memory file plus a lazy block view."""

    path: Path
    content: str = ""
    mtime: float = 0.0
    blocks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "mtime": self.mtime,
            "content": self.content,
            "blocks": list(self.blocks),
            "char_count": len(self.content),
            "block_count": len(self.blocks),
        }


def split_blocks(text: str) -> list[str]:
    """Split Markdown into trimmed blocks separated by blank lines.

    A block is a non-empty run of lines. Markdown headings and list items are
    preserved verbatim. Blank lines separate blocks.
    """
    out: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                out.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        out.append("\n".join(current).strip())
    return [b for b in out if b]


def render_blocks(blocks: list[str]) -> str:
    """Rejoin blocks back into canonical file content."""
    return "\n\n".join(b.strip() for b in blocks if b.strip()) + ("\n" if blocks else "")


def load_file(path: Path) -> MemoryFile:
    """Read a memory file into a MemoryFile (empty if missing/unreadable)."""
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = stat.st_mtime
    except OSError:
        return MemoryFile(path=path, content="", mtime=0.0, blocks=())
    return MemoryFile(path=path, content=text, mtime=mtime, blocks=tuple(split_blocks(text)))
