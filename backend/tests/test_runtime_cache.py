"""P0-A (W1) compile-cache invariants.

- The compiled graph is built ONCE per turn-dependency key and reused across
  turns (S3).
- reset_per_turn() restores overflow-halved budgets, clears steer injection and
  assembler fragment caches (S2).
- The workspace is stable per path and begin_turn() resets per-turn flags (S1).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.middleware.context_compaction import (  # noqa: E402
    CoworkerSummarizationMiddleware,
)
from coworker.agent.middleware.message_processor import (  # noqa: E402
    SteerInjectionMiddleware,
)
from coworker.agent.middleware.system_assembler import SystemAssembler  # noqa: E402


class _FailingLLM:
    def invoke(self, *a, **k):
        raise RuntimeError("n/a")

    async def ainvoke(self, *a, **k):
        raise RuntimeError("n/a")


# --- S2: reset_per_turn --------------------------------------------------------


def test_summarization_reset_restores_halved_budget():
    mw = CoworkerSummarizationMiddleware(
        llm=_FailingLLM(),
        budget_chars=1_000_000,
        context_window_tokens=128_000,
        summarizer_candidates=[_FailingLLM()],
        language="zh",
        max_output_tokens=4096,
    )
    configured = mw.budget_tokens
    mw.budget_tokens = max(5_000, int(mw.budget_tokens * 0.5))
    mw.last_summary = "STALE_SUMMARY"
    mw._summarized_segments.add("fp1")
    mw.reset_per_turn()
    assert mw.budget_tokens == configured  # budget-halving does not leak
    assert mw.last_summary == ""
    assert mw._summarized_segments == set()


def test_steer_reset_clears_injection():
    mw = SteerInjectionMiddleware(steer_emit=lambda ev: None)
    mw._injected = ["x"]
    mw._injected_ids = {"1"}
    mw.reset_per_turn(steer_emit=lambda ev: None)
    assert mw._injected == []
    assert mw._injected_ids == set()
    assert mw._emit is not None


def test_system_assembler_reset_clears_caches():
    asm = SystemAssembler(capabilities="", workspace=None)
    asm._ws_cache[(("execute", 123.0))] = "stale"
    asm._skill_body_cache[("s", "")] = ("body", "/tmp")
    asm.reset_per_turn()
    assert asm._ws_cache == {}
    assert asm._skill_body_cache == {}


# --- S3: compiled-graph cache keyed on turn deps --------------------------------


def test_compiled_graph_cached_per_key(monkeypatch):
    from coworker.agent import runtime as rt_module
    from coworker.agent.runtime import OpenAICompatibleStreamRuntime

    builds = {"n": 0}

    def _fake_build(*args, **kwargs):
        builds["n"] += 1
        return object()

    monkeypatch.setattr(rt_module, "build_coworker_agent_graph", _fake_build)

    rt = object.__new__(OpenAICompatibleStreamRuntime)
    rt._graph_cache = {}
    rt._audit_context = {"turn_index": 1, "session_id": "s1"}
    rt._delegator = None
    rt._delegator_key = None
    rt.llm = object()
    rt.workspace = object()
    rt.change_store = None
    rt.session_store = None
    rt.referenced_sessions = set()
    rt.skill_manager = None
    rt.memory_manager = None
    rt.agent = "default_agent"
    rt.settings = None
    rt.provider_name = "p"
    rt.provider_id = "pid"
    rt.model_name = "m"
    rt.approval_store = None
    rt.data_dir = None
    rt.mcp_session_manager = None
    rt.context_window_tokens = 128000
    rt.max_output_tokens = 4096
    rt.context_budget_chars = 0
    rt.context_window_source = "default"
    rt.context_window_warning = None
    rt._web_tools_for = lambda sid: []
    rt._browser_tool_for = lambda sid: None
    rt._build_delegator = lambda *a, **k: None
    rt._delegation_emit_live = lambda sid: None
    rt._goal_emit_live = lambda sid: None
    rt.project_id = "proj"

    g1 = rt._compiled_graph(session_id="s1", language="zh", work_mode="build", autonomy="guarded", checkpointer=None, memory_view=None, memory_store=None, memory_rel="")
    assert builds["n"] == 1
    # Same key → cache hit, no rebuild.
    g2 = rt._compiled_graph(session_id="s1", language="zh", work_mode="build", autonomy="guarded", checkpointer=None, memory_view=None, memory_store=None, memory_rel="")
    assert g2 is g1
    assert builds["n"] == 1
    # Different autonomy → rebuild (key miss).
    rt._compiled_graph(session_id="s1", language="zh", work_mode="build", autonomy="autonomous", checkpointer=None, memory_view=None, memory_store=None, memory_rel="")
    assert builds["n"] == 2


# --- S1: stable workspace + begin_turn ------------------------------------------


def test_workspace_begin_turn_resets_flags(tmp_path: Path):
    from coworker.workspace import Workspace

    ws = Workspace(tmp_path)
    ws._current_phase = "discuss"
    ws._allow_external_write = True
    ws.begin_turn()
    assert ws._current_phase == "execute"
    assert ws._allow_external_write is False


def test_workspace_controller_stable_per_path(tmp_path: Path):
    from coworker.projects import ProjectStore
    from coworker.sessions import SessionStore
    from coworker.workspace_controller import WorkspaceController

    data = tmp_path / "data"
    data.mkdir()
    controller = WorkspaceController(ProjectStore(data), SessionStore(data / "sessions"), tmp_path, data)
    a = controller.create_workspace(str(tmp_path))
    b = controller.create_workspace(str(tmp_path))
    assert a is b  # stable per path
    controller.evict_workspace(str(tmp_path))
    c = controller.create_workspace(str(tmp_path))
    assert c is not a


# --- S3b: per-session runtime cache invalidation on provider config change -------


class _FakeRuntime:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id


def _make_registry(cache: dict) -> object:
    from coworker.agent.runtime import AgentRuntimeRegistry

    reg = object.__new__(AgentRuntimeRegistry)
    reg._runtime_cache = cache
    return reg


def _key(session: str, provider_id):
    return (session, "single", provider_id, "m", None, None, "a", frozenset())


def test_invalidate_runtimes_for_provider_matches_resolved_provider():
    cache = {
        _key("s1", "p1"): _FakeRuntime("p1"),
        _key("s1", "p2"): _FakeRuntime("p2"),
        # Requested provider_id was None (default-driven), but the runtime was
        # actually built on the edited default provider -> must also be evicted.
        _key("s2", None): _FakeRuntime("p1"),
    }
    reg = _make_registry(cache)
    dropped = reg.invalidate_runtimes_for_provider("p1")
    assert dropped == 2
    assert list(reg._runtime_cache) == [_key("s1", "p2")]
    assert reg.invalidate_runtimes_for_provider("nope") == 0


def test_invalidate_default_runtimes_only_drops_none_keyed():
    cache = {
        _key("s1", "p1"): _FakeRuntime("p1"),
        _key("s2", None): _FakeRuntime("p2"),
        _key("s3", None): _FakeRuntime("p3"),
    }
    reg = _make_registry(cache)
    dropped = reg.invalidate_default_runtimes()
    assert dropped == 2
    assert list(reg._runtime_cache) == [_key("s1", "p1")]
    assert reg.invalidate_default_runtimes() == 0


def test_evict_runtime_by_session_is_unchanged():
    cache = {
        _key("s1", "p1"): _FakeRuntime("p1"),
        _key("s2", "p1"): _FakeRuntime("p1"),
    }
    reg = _make_registry(cache)
    reg.evict_runtime("s1")
    assert list(reg._runtime_cache) == [_key("s2", "p1")]
