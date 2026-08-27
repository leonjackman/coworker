"""七、請求 prompt build 不合理 (B1 殘留 / A–D) tests.

- A: the MCP attribution is a SystemAssembler FRAGMENT placed AFTER behaviour /
      capabilities (behaviour core first — codex/opencode), hidden in discuss.
- B: the behaviour prompt carries no stale "listed ones" reference (the tool
      catalogue was removed).
- C: build_cw_system_prompt is behaviour-only; build_tool_context is gone.
- D: the base-system extraction tolerates list-content.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.middleware.system_assembler import (  # noqa: E402
    SystemAssembler,
    _system_text,
)
from coworker.agent.system_prompt import build_cw_system_prompt  # noqa: E402


class _FakeMemoryManager:
    bound_project = None
    bound_agent = None

    def render_prompt(self):
        return ""


class _FakeSkillManager:
    def injection_list(self):
        return []


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


# --- A: MCP attribution placement ----------------------------------------------


def test_system_assembler_places_mcp_after_behaviour_and_capabilities():
    asm = SystemAssembler(
        capabilities="CAP_LINE",
        workspace=None,
        memory_manager=_FakeMemoryManager(),
        skill_manager=_FakeSkillManager(),
        mcp_summary_provider=lambda: "- srv: tool_a, tool_b",
    )
    out = str(asm._overrides(_assembler_request())["system_message"].content or "")
    assert "## MCP" in out
    assert "tool_a" in out
    assert out.index("BASE_BEHAVIOUR") < out.index("## MCP")  # behaviour first
    assert out.index("CAP_LINE") < out.index("## MCP")  # capabilities before MCP


def test_system_assembler_hides_mcp_in_discuss():
    asm = SystemAssembler(
        capabilities="",
        workspace=None,
        memory_manager=_FakeMemoryManager(),
        skill_manager=_FakeSkillManager(),
        mcp_summary_provider=lambda: "- srv: tool_a",
    )
    out = str(asm._overrides(_assembler_request(phase="discuss"))["system_message"].content or "")
    assert "## MCP" not in out
    assert "tool_a" not in out


def test_mcp_middleware_no_longer_prepends_system_message():
    """The MCP middleware must not override system_message (ordering moved to
    the assembler)."""
    from langchain_core.messages import SystemMessage

    from coworker.mcp.mcp_middleware import McpToolMiddleware

    class _Req:
        state = {"phase": "execute", "work_mode": "build"}
        tools = []
        system_message = SystemMessage("BASE")

        def override(self, **kwargs):
            return kwargs

    class _SessMgr:
        def __init__(self):
            self.mcp_manager = self  # list_runtime_configs below
            self._connecting = set()
            self._servers = []

        def list_runtime_configs(self, **kw):
            return []

        def ensure_connected(self, **kw):
            pass

        def all_tools(self):
            return []

        def tool_names(self):
            return set()

        def list_conflicts(self):
            return {}

    mw = McpToolMiddleware(_SessMgr())
    overrides = mw._overrides(_Req())
    assert "system_message" not in overrides


# --- B: no stale "listed ones" reference --------------------------------------


def test_behaviour_prompt_has_no_stale_listed_reference():
    sp = build_cw_system_prompt()
    assert "listed ones" not in sp
    assert "the tools provided to you" in sp or "provided to you" in sp


# --- C: behaviour-only base + removed tool catalogue ---------------------------


def test_build_cw_system_prompt_behaviour_only():
    sp = build_cw_system_prompt()
    assert "## Workspace" not in sp
    assert "## Available tools" not in sp
    assert "## MCP" not in sp
    assert "Tool discipline" in sp


def test_build_tool_context_removed():
    import coworker.agent.system_prompt as sp_mod

    assert not hasattr(sp_mod, "build_tool_context")


# --- D: base extraction tolerates list content ---------------------------------


def test_system_text_handles_list_content():
    from langchain_core.messages import SystemMessage

    msg = SystemMessage(content=[{"type": "text", "text": "partA"}, {"type": "text", "text": "partB"}])
    assert _system_text(msg) == "partA\npartB"
    assert _system_text(SystemMessage(content="plain")) == "plain"
    assert _system_text(None) == ""
