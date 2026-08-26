"""System-prompt construction tests.

Guard the workspace + tool-catalogue injection: the model must always see the
real project root/layout and exactly which tools it can call, so it never
guesses paths or hallucinates tools (the observed "degraded" failure mode).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.system_prompt import (  # noqa: E402
    build_cw_system_prompt,
    build_project_context_md,
    build_tool_context,
    build_workspace_context,
    build_workspace_tree,
)
from coworker.workspace import Workspace  # noqa: E402


class _FakeTool:
    def __init__(self, name, desc=""):
        self.name = name
        self.description = desc


def test_build_workspace_tree_no_duplicate_dirs(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c.txt").write_text("x")
    (tmp_path / "f.txt").write_text("x")
    ws = Workspace(tmp_path)
    tree = build_workspace_tree(ws)
    lines = tree.split("\n")
    # a/ appears exactly once at depth 0, b/ under it once, etc.
    assert sum(l == "a/" for l in lines) == 1
    assert sum("b/" in l for l in lines) == 1
    assert sum("c.txt" in l for l in lines) == 1


def test_build_workspace_tree_ignores_noise(tmp_path: Path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "real.txt").write_text("x")
    ws = Workspace(tmp_path)
    tree = build_workspace_tree(ws)
    assert "node_modules" not in tree
    assert ".git" not in tree
    assert "real.txt" in tree


def test_build_workspace_context_includes_root_and_relative_hint(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    ws = Workspace(tmp_path)
    ctx = build_workspace_context(ws)
    assert str(tmp_path) in ctx
    assert "RELATIVE to this root" in ctx
    assert "src/" in ctx


def test_build_tool_context_groups_by_section():
    tools = [
        _FakeTool("read_file"),
        _FakeTool("write_file"),
        _FakeTool("run_command"),
        _FakeTool("memory"),
    ]
    ctx = build_tool_context(tools)
    assert "Filesystem" in ctx
    assert "Runtime" in ctx
    assert "Memory" in ctx
    # Registered tool names appear; unknown ones render too.
    assert "read_file" in ctx
    assert "run_command" in ctx


def test_build_cw_system_prompt_assembles_all_sections(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    ws = Workspace(tmp_path)
    tools = [_FakeTool("read_file"), _FakeTool("run_command")]
    sp = build_cw_system_prompt(tools=tools, workspace=ws, language="zh")
    assert "## Workspace" in sp
    assert "## Available tools" in sp
    assert "Working method" in sp
    assert "Tool discipline" in sp
    assert "do NOT re-run" in sp


def test_build_project_context_md_carries_identity(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    md = build_project_context_md(tmp_path)
    assert tmp_path.name in md  # project name
    assert str(tmp_path) in md  # root path
    # A static directory tree is deliberately NOT snapshotted (it goes stale as
    # the repo evolves); the live layout comes from the per-request Workspace
    # section instead. The template tells the agent to use live tool listing.
    assert "backend/" not in md
    assert "项目结构" not in md
    assert "以当前实时结构为准" in md
