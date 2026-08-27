"""R1 bounded-read tests: read_preview streams a line window instead of loading
the whole file, binary files are sniffed before a read, and char caps bound a
single call's output (source-level fix, not a patchwork cap tweak)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workspace import (  # noqa: E402
    READ_FILE_MAX_CHARS,
    Workspace,
)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def _lines_file(tmp_path: Path, n: int) -> Path:
    p = tmp_path / "big.txt"
    p.write_text("\n".join(f"line {i}" for i in range(n)))
    return p


def test_read_preview_pages_and_reports_totals(ws: Workspace, tmp_path: Path):
    p = _lines_file(tmp_path, 1000)
    r = ws.read_preview("big.txt", offset=1, limit=10)
    assert r["total_lines"] == 1000
    assert r["truncated"] is True
    assert r["next_offset"] == 11
    assert r["content"].count("\n") == 9
    assert r["content"].startswith("line 0")

    r2 = ws.read_preview("big.txt", offset=990, limit=20)
    assert r2["truncated"] is False
    assert r2["next_offset"] == 0
    assert r2["content"].endswith("line 999")


def test_read_preview_char_cap_bounds_single_call(ws: Workspace, tmp_path: Path):
    p = tmp_path / "wide.txt"
    p.write_text("x" * (READ_FILE_MAX_CHARS + 20_000))
    r = ws.read_preview("wide.txt", max_chars=1000)
    assert r["truncated"] is True
    assert len(r["content"]) <= 1000


def test_read_preview_binary_sniffed_without_full_load(ws: Workspace, tmp_path: Path):
    # A ".md"-suffixed file (is_text_file True) whose head is binary must be
    # detected by the 4KB sniff (NUL byte), not stream-loaded as text.
    p = tmp_path / "trick.md"
    p.write_bytes(b"\x00\x01\x02" + b"x" * 4096)
    r = ws.read_preview("trick.md")
    assert r["binary"] is True
    assert r["content"] is None
    assert r["size"] == p.stat().st_size


def test_is_binary_bytes():
    from coworker.workspace import Workspace as W

    assert not W._is_binary_bytes(b"")
    assert W._is_binary_bytes(b"\x00hello")
    # 30%+ non-printable ⇒ binary
    assert W._is_binary_bytes(b"\x01" * 5 + b"aaaaa")
    assert not W._is_binary_bytes(b"hello world, printable text")


def test_read_preview_empty_file(ws: Workspace, tmp_path: Path):
    (tmp_path / "empty.txt").write_text("")
    r = ws.read_preview("empty.txt")
    assert r["binary"] is False
    assert r["content"] == ""
    assert r["total_lines"] == 0
    assert "Empty file" in r["hint"]
