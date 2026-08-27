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


def test_build_cw_system_prompt_behaviour_only_mode_omits_workspace_and_tools():
    """Behaviour-only mode (used as the graph-level base prompt) must NOT carry
    the workspace or the tool catalogue — those are injected exactly once by
    PhaseToolGateMiddleware. Repeating them here caused duplicate, contradictory
    `## Workspace` / `## Available tools` sections (~60KB+ per request) that
    degraded tool calling (the 降智 regression)."""
    sp = build_cw_system_prompt(tools=[_FakeTool("read_file")], workspace=None, include_workspace=False, include_tools=False)
    assert "## Workspace" not in sp
    assert "## Available tools" not in sp
    assert "Working method" in sp
    assert "Tool discipline" in sp


def test_phase_gate_visibility_only_no_prompt_override(tmp_path: Path):
    """After the P1/P5 split, PhaseToolGateMiddleware only filters the tool set;
    the system prompt is composed by SystemAssembler (no tool catalogue, no
    workspace section here — mainstream: tool list IS the schema)."""
    from langchain_core.messages import SystemMessage

    from coworker.agent.middleware import PhaseToolGateMiddleware

    base = build_cw_system_prompt(tools=[], workspace=None, include_workspace=False, include_tools=False)
    tools = [_FakeTool("read_file"), _FakeTool("write_file"), _FakeTool("run_command"), _FakeTool("write_todos"), _FakeTool("web_search")]

    class _Request:
        def __init__(self):
            self.state = {"language": "zh", "work_mode": "build", "phase": "execute", "autonomy": "guarded"}
            self.tools = tools
            self.system_message = SystemMessage(base)

        def override(self, **kwargs):
            return kwargs

    gate = PhaseToolGateMiddleware(workspace=Workspace(tmp_path))
    overrides = gate._overrides(_Request())
    # Visibility only: the phase-filtered schemas are what the model sees.
    assert {t.name for t in overrides["tools"]} == {"read_file", "write_file", "run_command", "write_todos", "web_search"}
    # The system message is left untouched here (SystemAssembler owns it).
    assert "system_message" not in overrides


def test_tool_descriptions_bounded():
    """P2 guard: built tool descriptions must stay bounded (no context bombs in
    the schemas the model sees on every request)."""
    from coworker.agent.core import MAX_TOOL_DESCRIPTION_CHARS

    names = ("memory", "memory_read", "update_goal", "install_skill", "run_command", "delegate_task")
    src = (Path(__file__).resolve().parents[1] / "coworker" / "agent" / "graph.py").read_text(encoding="utf-8")
    import re

    for name in names:
        m = re.search(rf"def {name}\([^)]*\)[^\"]*\"\"\"(.+?)\"\"\"", src, re.S)
        assert m, f"tool {name} not found"
        desc = " ".join(m.group(1).split())
        assert len(desc) <= MAX_TOOL_DESCRIPTION_CHARS, f"{name} desc {len(desc)} chars > {MAX_TOOL_DESCRIPTION_CHARS}"


# --- SystemAssembler (P5/V5/B2) -----------------------------------------------

class _FakeMemoryManager:
    bound_project = None
    bound_agent = None

    def render_prompt(self):
        return "MEMORY_SECTION_TOKEN"


class _FakeSkillEntry:
    def __init__(self, name, desc, path):
        self.name = name
        self.description = desc
        self.file_path = path
        self.version = "1.0.0"
        self.commands = []


class _FakeSkillManager:
    def __init__(self, entries=None):
        self._entries = entries or [_FakeSkillEntry("demo", "does demo things", "/tmp/demo/SKILL.md")]

    def injection_list(self):
        return self._entries

    def read_body(self, name):
        return None  # no activated bodies in these tests

    def read_command_body(self, name, command):
        return None


def _assembler_request(base="BASE_BEHAVIOUR", **state_overrides):
    from langchain_core.messages import SystemMessage

    state = {"language": "zh", "work_mode": "build", "phase": "execute", "autonomy": "guarded", "messages": []}
    state.update(state_overrides)

    class _Request:
        tools = []

        def __init__(self):
            self.state = dict(state)
            self.system_message = SystemMessage(base)

        def override(self, **kwargs):
            return kwargs

    return _Request()


def test_system_assembler_composes_fragments_in_order(tmp_path: Path):
    from coworker.agent.middleware.system_assembler import SystemAssembler

    asm = SystemAssembler(
        capabilities="CAP_LINE",
        workspace=Workspace(tmp_path),
        memory_manager=_FakeMemoryManager(),
        skill_manager=_FakeSkillManager(),
    )
    req = _assembler_request()
    out = str(asm._overrides(req)["system_message"].content or "")
    # Behaviour core first, then phase, capabilities, workspace, memory, skills.
    assert "You are executing" in out  # phase block present
    assert out.index("BASE_BEHAVIOUR") < out.index("You are executing")
    assert out.index("CAP_LINE") > out.index("BASE_BEHAVIOUR")
    assert "## Workspace" in out
    assert "MEMORY_SECTION_TOKEN" in out
    assert "demo" in out  # skill catalog
    assert "Available tools" not in out  # P1: no tool catalogue


def test_system_assembler_hides_skills_in_discuss(tmp_path: Path):
    from coworker.agent.middleware.system_assembler import SystemAssembler

    asm = SystemAssembler(
        capabilities="",
        workspace=Workspace(tmp_path),
        memory_manager=_FakeMemoryManager(),
        skill_manager=_FakeSkillManager(),
    )
    req = _assembler_request(phase="discuss")
    out = str(asm._overrides(req)["system_message"].content or "")
    assert "demo" not in out  # skills hidden in discuss
    assert "MEMORY_SECTION_TOKEN" in out  # memory still injected


def test_system_assembler_budget_guard_drops_skills_then_workspace(tmp_path: Path, monkeypatch):
    import coworker.agent.middleware.system_assembler as sa_module

    monkeypatch.setattr(sa_module, "SYSTEM_FIXED_BUDGET_TOKENS", 20)
    asm = sa_module.SystemAssembler(
        capabilities="CAP_LINE",
        workspace=Workspace(tmp_path),
        memory_manager=_FakeMemoryManager(),
        skill_manager=_FakeSkillManager(),
    )
    req = _assembler_request()
    out = str(asm._overrides(req)["system_message"].content or "")
    # Lowest-priority fragments dropped first: skills gone, then workspace gone,
    # but behaviour + phase never dropped.
    assert "demo" not in out
    assert "MEMORY_SECTION_TOKEN" not in out
    assert "BASE_BEHAVIOUR" in out


def test_system_assembler_workspace_turn_cache(tmp_path: Path):
    from coworker.agent.middleware.system_assembler import SystemAssembler

    asm = SystemAssembler(
        capabilities="",
        workspace=Workspace(tmp_path),
        memory_manager=_FakeMemoryManager(),
        skill_manager=None,
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x")
    req = _assembler_request()
    o1 = str(asm._overrides(req)["system_message"].content or "")
    # Second call with no FS change reuses the cached workspace tree (P3).
    o2 = str(asm._overrides(req)["system_message"].content or "")
    assert o1 == o2
    assert len(asm._ws_cache) >= 1
