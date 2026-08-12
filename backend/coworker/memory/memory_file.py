"""Memory entry model and Markdown ``§``-delimited serialization.

The on-disk format is deliberately simple so users can read and edit it with
any editor while the tool layer can still parse entries reliably:

.. code-block:: markdown

    # Coworker 记忆

    <entry text one>
    §
    <entry text two>
    §
    ...

Each entry is a single block of free text; ``§`` on its own line separates
entries. Trailing/leading whitespace is trimmed. There is no frontmatter and
no per-entry metadata — freshness comes from the file mtime (Claude's lesson:
use timestamps, not importance scoring).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEADER = "# Coworker 记忆"

ENTRY_DELIMITER = "§"

MEMORY_FILE_NAME = "MEMORY.md"

# Matches a ``§`` delimiter on its own line (allowing surrounding blank lines).
_ENTRY_SPLIT_RE = re.compile(r"\n*\s*§\s*\n+")


@dataclass(frozen=True)
class MemoryFile:
    """One discovered memory file plus a parsed entry view."""

    scope: str  # "project" | "user"
    path: Path
    mtime: float
    entries: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "path": str(self.path),
            "mtime": self.mtime,
            "entries": self.entries,
            "char_count": sum(len(e) for e in self.entries),
            "entry_count": len(self.entries),
        }


def split_entries(text: str) -> list[str]:
    """Split raw file content into trimmed entries, dropping the header."""
    body = text
    header_idx = body.find(HEADER)
    if header_idx != -1:
        body = body[header_idx + len(HEADER):]
    parts = _ENTRY_SPLIT_RE.split(body)
    entries: list[str] = []
    for part in parts:
        entry = part.strip().strip("\n")
        if entry:
            entries.append(entry)
    return entries


def render_file(entries: list[str]) -> str:
    """Render entries back into canonical file content."""
    if not entries:
        return f"{HEADER}\n"
    body = f"\n{ENTRY_DELIMITER}\n\n".join(e.strip() for e in entries if e.strip())
    return f"{HEADER}\n\n{body}\n"


def load_file(path: Path, scope: str) -> MemoryFile:
    """Read a memory file into a MemoryFile (empty entries if missing/unreadable)."""
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = stat.st_mtime
    except OSError:
        return MemoryFile(scope=scope, path=path, mtime=0.0, entries=[])
    return MemoryFile(scope=scope, path=path, mtime=mtime, entries=split_entries(text))
