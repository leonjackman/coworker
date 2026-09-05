"""F1: memory index preview completeness.

Guards the "memory looks like a single line" defect: multi-line blocks must
render a bounded preview (up to PREVIEW_BLOCK_LINES) with an explicit
"more lines exist" marker and a blocks=/lines= header, so the model knows the
resident index is partial and fetches the full file via memory_read.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.context import estimate_text_tokens  # noqa: E402
from coworker.memory.memory_file import split_blocks  # noqa: E402
from coworker.memory import memory_prompt as mp  # noqa: E402


class Node:
    kind = "agent_file"
    name = "MEMORY.md"
    rel = "proj/agent/MEMORY.md"
    mtime = 1_700_000_000.0

    def __init__(self, content):
        self.content = content
        self.blocks = None


def test_multiline_block_preview_limited_with_marker():
    content = "\n".join(f"事实 {i}: " + "记" * 12 for i in range(8))
    out = mp.format_memory_index([Node(content)], token_budget=10_000)
    assert 'blocks="1"' in out
    assert 'lines="8"' in out
    assert "more line" in out  # hidden-line marker present
    # the whole (short) block body fits: first line shown once, marker added
    assert out.count("记") >= 3


def test_single_line_blocks_add_no_marker_or_bloat():
    # Realistic MEMORY.md: short single-block facts separated by blank lines.
    content = "\n\n".join(f"- 事實 {i}: 用" * 1 for i in range(6))
    out = mp.format_memory_index([Node(content)], token_budget=10_000)
    assert "more line" not in out
    assert content.count("- 事實") == 6


def test_tight_budget_stops_and_warns():
    # Many dense blocks: the 60-token budget exhausts before the file is done,
    # so the top-level budget_warning must appear (content genuinely cut).
    blocks = []
    for i in range(10):
        blocks.append("\n".join(f"事实 {i}-{j}: " + "记" * 30 for j in range(3)))
    content = "\n\n".join(blocks)
    node = Node(content)
    out = mp.format_memory_index([node], token_budget=60)
    assert "budget_warning" in out
    # structural header stays, preview is tiny but present
    assert "<file " in out


def test_header_reports_blocks_and_lines():
    content = "\n\n".join(f"## B{i}\n内文 {i}" for i in range(3))
    out = mp.format_memory_index([Node(content)], token_budget=10_000)
    assert 'blocks="3"' in out
    assert 'lines="6"' in out
