"""Tool-source truncation tests (git_status diff budget + read_file preview).

These guard the "source-side truncation": a large working tree
must never inject megabytes of diff into the model context, and read_file must
return a bounded preview (binary hint / truncated text) instead of an unbounded
read or a UnicodeDecodeError.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workspace import (  # noqa: E402
    GIT_MAX_DIFF_CHARS,
    GIT_MAX_FILES,
    GIT_MAX_PER_FILE_DIFF_CHARS,
    READ_FILE_MAX_CHARS,
    Workspace,
    workspace_git_diff,
)


def _git(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _commit_all(git_repo: Path, message: str) -> None:
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", message)


def test_git_diff_single_file_truncated(git_repo: Path):
    target = git_repo / "big.txt"
    target.write_text("base\n", encoding="utf-8")
    _commit_all(git_repo, "base")
    # ~300 changed lines → a unified diff well over the 2K per-file cap.
    target.write_text("".join(f"line {i}\n" for i in range(400)), encoding="utf-8")

    result = workspace_git_diff(git_repo)
    assert result["git"] is True
    assert result["truncated_diff"] is True
    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["path"] == "big.txt"
    # Statistics are preserved even when the diff body is truncated.
    assert entry["added"] >= 400
    # The diff body is capped and flagged.
    assert entry["diff"].endswith("[diff truncated]")
    assert len(entry["diff"]) <= GIT_MAX_PER_FILE_DIFF_CHARS + len("\n…[diff truncated]")


def test_git_diff_file_count_capped(git_repo: Path):
    for i in range(60):
        (git_repo / f"f{i}.txt").write_text(f"v1 {i}\n", encoding="utf-8")
    _commit_all(git_repo, "base")
    for i in range(60):
        (git_repo / f"f{i}.txt").write_text(f"v2 {i}\n", encoding="utf-8")

    result = workspace_git_diff(git_repo)
    assert result["git"] is True
    assert len(result["files"]) == GIT_MAX_FILES
    assert result["note"] == f"showing first {GIT_MAX_FILES} files"


def test_git_diff_total_cap_respected(git_repo: Path):
    # Even with many files the assembled diff body must not exceed the total cap.
    for i in range(40):
        (git_repo / f"f{i}.txt").write_text("".join(f"base {i} {j}\n" for j in range(200)), encoding="utf-8")
    _commit_all(git_repo, "base")
    for i in range(40):
        (git_repo / f"f{i}.txt").write_text("".join(f"changed {i} {j}\n" for j in range(200)), encoding="utf-8")

    result = workspace_git_diff(git_repo)
    assert result["git"] is True
    # No per-file diff may exceed the total cap either (a single huge file is
    # truncated before it could approach GIT_MAX_DIFF_CHARS).
    for entry in result["files"]:
        assert len(entry.get("diff") or "") <= GIT_MAX_DIFF_CHARS


def test_read_preview_binary_returns_hint(tmp_path: Path):
    workspace = Workspace(tmp_path)
    png = tmp_path / "img.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)

    preview = workspace.read_preview(str(png), max_chars=READ_FILE_MAX_CHARS)
    assert preview["binary"] is True
    assert preview["size"] == png.stat().st_size
    assert "content" not in preview or preview["content"] is None


def test_read_preview_large_text_truncated(tmp_path: Path):
    workspace = Workspace(tmp_path)
    big = tmp_path / "big.log"
    big.write_text("x" * (READ_FILE_MAX_CHARS + 10_000), encoding="utf-8")

    preview = workspace.read_preview(str(big), max_chars=READ_FILE_MAX_CHARS)
    assert preview["binary"] is False
    assert preview["truncated"] is True
    assert len(preview["content"]) == READ_FILE_MAX_CHARS
