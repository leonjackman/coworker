"""R3 search tests: ripgrep fast path (when ``rg`` exists) and an ignore-aware
pure-Python fallback (when it does not) must both respect ``.gitignore`` and
cap results. Source-level fix: the scan never loads a whole file and never
walks ignored directories."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workspace import Workspace  # noqa: E402


def _make_tree(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("findme line\n")
    (tmp_path / "keep.md").write_text("findme\n")
    d = tmp_path / "dist"
    d.mkdir()
    (d / "x.txt").write_text("findme ignored\n")
    (tmp_path / ".gitignore").write_text("dist/\n")
    return tmp_path


def test_fallback_respects_gitignore(tmp_path: Path):
    root = _make_tree(tmp_path)
    ws = Workspace(root)
    r = ws.search_text("findme", "")
    paths = [x["path"] for x in r["results"]]
    assert "a.txt" in paths
    assert "keep.md" in paths
    assert all("dist" not in p for p in paths)


def test_fallback_caps_results(tmp_path: Path):
    root = tmp_path
    for i in range(10):
        (root / f"f{i}.txt").write_text("hit\n")
    ws = Workspace(root)
    r = ws.search_text("hit", "", max_results=3)
    assert r["result_count"] == 3
    assert r["truncated"] is True


def test_fallback_skips_oversized_file(tmp_path: Path):
    root = tmp_path
    (root / "big.log").write_text("x" * (300_000) + "\nhit\n")
    (root / "small.txt").write_text("hit\n")
    ws = Workspace(root)
    r = ws.search_text("hit", "")
    paths = [x["path"] for x in r["results"]]
    # big.log (300KB) is over the 256KB fallback cap and skipped.
    assert paths == ["small.txt"]


def test_rg_fast_path_used_when_available(tmp_path: Path, monkeypatch):
    root = _make_tree(tmp_path)
    ws = Workspace(root)

    import subprocess as _sp
    from coworker import workspace as ws_module

    fake_json = "\n".join(
        [
            '{"type":"match","data":{"path":{"text":"' + str(root / "a.txt") + '"},'
            '"line_number":1,"lines":{"text":"findme line\\n"}}}',
            '{"type":"summary","data":{}}',
        ]
    )

    def _fake_run(cmd, **kwargs):
        assert "rg" in cmd[0]
        class _P:
            returncode = 0
            stdout = fake_json
            stderr = ""

        return _P()

    monkeypatch.setattr(ws_module.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    monkeypatch.setattr(_sp, "run", _fake_run)

    r = ws.search_text("findme", "")
    assert r["result_count"] == 1
    assert r["results"][0]["path"] == "a.txt"
