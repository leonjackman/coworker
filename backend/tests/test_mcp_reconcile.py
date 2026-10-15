"""Reconcile invariants for the persistent MCP session manager.

Root-cause regression coverage: the runtime's connected-session set used to be a
one-shot snapshot taken at startup, so a server added/edited while the app ran
never entered the tool set and the agent could not discover it (any transport,
SSE included). These tests pin the convergence contract:

- ``_reconcile_async`` closes sessions for disabled/deleted servers, reconnects
  sessions whose connection config changed, connects enabled-but-offline servers,
  and treats a ``disabled_tools`` change as a policy-only update (no reconnect).
- ``reconcile_soon`` is a cheap non-blocking no-op when there is nothing to do.
- The middleware self-heals on the hot path by triggering ``reconcile_soon`` when
  an enabled server is offline.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker.mcp.mcp import McpManager  # noqa: E402
from coworker.mcp.mcp_session import (  # noqa: E402
    McpSessionManager,
    _connect_fingerprint,
    _policy_fingerprint,
)


class _FakeCfg:
    """Minimal ``McpManager`` stand-in: just serves the desired config list."""

    def __init__(self, desired):
        self._desired = list(desired)

    def list_runtime_configs(self, enabled_only=True):
        return list(self._desired)


class _FakeRuntime:
    def __init__(self, server_id, config_fp=(), policy_fp=()):
        self.server_id = server_id
        self.config_fingerprint = config_fp
        self.policy_fingerprint = policy_fp
        self.closed = False

    async def close(self):
        self.closed = True


def _make_manager(desired):
    """A manager with all side-effecting collaborators faked out."""
    sm = McpSessionManager.__new__(McpSessionManager)
    sm._closing = False
    sm._servers = {}
    sm._trust_cache = {}
    sm._connect_failures = {}
    sm._pending_status_refresh = set()
    sm.mcp_manager = _FakeCfg(desired)
    sm.ensure_calls = []

    async def _fake_ensure(enable_browser_flow=False):
        sm.ensure_calls.append(enable_browser_flow)

    def _fake_rebuild():
        return set()

    async def _fake_refresh(ids):
        return None

    sm._ensure_connected_async = _fake_ensure
    sm._rebuild_tools = _fake_rebuild
    sm._refresh_statuses = _fake_refresh
    return sm


def _live(server_id, cfg):
    return _FakeRuntime(server_id, _connect_fingerprint(cfg), _policy_fingerprint(cfg))


# --- manager reconciliation ---------------------------------------------------


def test_reconcile_closes_disabled_or_deleted_server():
    keep = {"id": "keep", "transport": "sse", "url": "http://keep"}
    sm = _make_manager([keep])
    gone_rt = _live("gone", {"id": "gone", "transport": "sse", "url": "http://gone"})
    sm._servers = {"keep": _live("keep", keep), "gone": gone_rt}

    asyncio.run(sm._reconcile_async())

    assert gone_rt.closed is True
    assert "gone" not in sm._servers
    assert "keep" in sm._servers


def test_reconcile_reconnects_on_connection_config_change():
    old = {"id": "s", "transport": "sse", "url": "http://old"}
    new = {"id": "s", "transport": "sse", "url": "http://new"}
    sm = _make_manager([new])
    rt = _live("s", old)
    sm._servers = {"s": rt}

    asyncio.run(sm._reconcile_async())

    assert rt.closed is True
    assert "s" not in sm._servers  # popped so the connect step rebuilds it
    assert sm.ensure_calls  # and the connect step actually ran


def test_reconcile_policy_change_does_not_reconnect():
    old = {"id": "s", "transport": "sse", "url": "http://u", "disabled_tools": []}
    new = {"id": "s", "transport": "sse", "url": "http://u", "disabled_tools": ["a"]}
    sm = _make_manager([new])
    rt = _live("s", old)
    sm._servers = {"s": rt}
    sm._trust_cache["s"] = (0.0, True)

    asyncio.run(sm._reconcile_async())

    assert rt.closed is False
    assert "s" in sm._servers
    assert rt.policy_fingerprint == _policy_fingerprint(new)
    assert "s" not in sm._trust_cache  # trust re-read after a policy change


def test_reconcile_connects_when_no_server_is_live():
    new = {"id": "baidu", "transport": "sse", "url": "http://baidu"}
    sm = _make_manager([new])
    sm._servers = {}

    asyncio.run(sm._reconcile_async())

    assert sm.ensure_calls  # enabled-but-offline -> connect step runs


def test_fingerprint_ignores_trusted_and_name():
    left = {"id": "s", "transport": "sse", "url": "u", "trusted": True, "name": "A"}
    right = {"id": "s", "transport": "sse", "url": "u", "trusted": False, "name": "B"}
    assert _connect_fingerprint(left) == _connect_fingerprint(right)


def test_fingerprint_detects_headers_change():
    left = {"id": "s", "transport": "sse", "url": "u", "headers": {"X": "1"}}
    right = {"id": "s", "transport": "sse", "url": "u", "headers": {"X": "2"}}
    assert _connect_fingerprint(left) != _connect_fingerprint(right)


# --- non-blocking scheduling --------------------------------------------------


def test_reconcile_soon_is_noop_without_a_loop():
    sm = McpSessionManager.__new__(McpSessionManager)
    sm._loop = None
    sm._closing = False
    sm._reconcile_lock = threading.Lock()
    sm._reconcile_pending = False
    sm._reconcile_running = False

    sm.reconcile_soon()  # must not raise

    assert sm._reconcile_pending is False
    assert sm._reconcile_running is False


def test_prewarm_delegates_to_reconcile():
    sm = McpSessionManager.__new__(McpSessionManager)
    sm._loop = None
    sm._closing = False
    calls = []
    sm.reconcile_soon = lambda: calls.append(True)  # type: ignore[method-assign]
    sm.prewarm()
    assert calls == [True]


def test_reconcile_sync_converges_on_a_real_loop(tmp_path):
    cfg = McpManager(tmp_path / "mcp_servers.json")
    sm = McpSessionManager(tmp_path, cfg)
    sm.start()
    try:
        # No enabled servers: reconcile must be a fast no-op, not a hang.
        started = time.monotonic()
        sm.reconcile_sync(timeout=5)
        assert time.monotonic() - started < 5
        assert sm._servers == {}
        # reconcile_soon returns immediately and settles in the background.
        sm.reconcile_soon()
        time.sleep(0.3)
        assert sm._servers == {}
    finally:
        sm.shutdown()


# --- middleware self-heal -----------------------------------------------------


def _middleware_for(enabled, connected):
    from coworker.mcp.mcp_middleware import McpToolMiddleware

    class _SessMgr:
        def __init__(self):
            self.mcp_manager = self
            self._connecting = {}
            self._servers = dict(connected)
            self.reconcile_calls = 0

        def list_runtime_configs(self, **kw):
            return list(enabled)

        def all_tools(self):
            return []

        def tool_names(self):
            return set()

        def list_conflicts(self):
            return {}

        def reconcile_soon(self):
            self.reconcile_calls += 1

    class _Req:
        state = {"phase": "execute", "work_mode": "build"}
        tools = []

        def override(self, **kw):
            return kw

    mgr = _SessMgr()
    mw = McpToolMiddleware(mgr)
    return mw, mgr, _Req


def test_middleware_self_heals_when_enabled_server_is_offline():
    mw, mgr, Req = _middleware_for(
        enabled=[{"id": "baidu", "name": "百度云"}],
        connected={},  # enabled but not connected -> drift
    )
    mw._overrides(Req())
    assert mgr.reconcile_calls == 1


def test_middleware_does_not_self_heal_without_drift():
    mw, mgr, Req = _middleware_for(
        enabled=[{"id": "baidu", "name": "百度云"}],
        connected={"baidu": object()},
    )
    mw._overrides(Req())
    assert mgr.reconcile_calls == 0


# --- phase-aware MCP prompt section -------------------------------------------


class _FakeTool:
    def __init__(self, name, server_id):
        self.name = name
        self.metadata = {"coworker_server": server_id}


class _FakeServers:
    def __init__(self, servers):
        self._servers = servers

    def list_servers(self, enabled_only=True):
        return list(self._servers)


def _mcp_middleware_for_section():
    from coworker.mcp.mcp_middleware import McpToolMiddleware

    mw = McpToolMiddleware.__new__(McpToolMiddleware)
    mw._servers = [
        {"id": "s1", "name": "百度云"},
        {"id": "s2", "name": "广垦优品百度云"},
    ]
    mw.tools = [_FakeTool("file_list", "s1")]

    class _SM:
        def __init__(self):
            self.mcp_manager = _FakeServers([
                {"id": "s1", "name": "百度云", "status": "connected", "tool_count": 16},
                {"id": "s2", "name": "广垦优品百度云", "status": "needs_auth"},
            ])

        def list_conflicts(self):
            return {}

    mw.session_manager = _SM()
    return mw


def test_mcp_section_build_shows_tools_and_unavailable_servers():
    mw = _mcp_middleware_for_section()
    text = mw.mcp_section(False)
    assert "file_list" in text          # attribution in build
    assert "需要重新授权" in text          # unavailable server surfaced


def test_mcp_section_discuss_shows_status_not_tools():
    mw = _mcp_middleware_for_section()
    text = mw.mcp_section(True)
    assert "规划/讨论阶段" in text        # explains tools are not callable now
    assert "file_list" not in text       # no tool attribution in discuss
    assert "百度云" in text and "已连接" in text  # real availability, so no guessing
    assert "广垦优品百度云" in text
    assert "需要重新授权" in text
