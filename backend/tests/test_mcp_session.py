"""Session layer: discovery, execution, persistence, naming conflicts, policy.

These exercise a real stdio MCP subprocess, so they are slower than a unit test
but they cover the exact plumbing (background loop, cross-loop dispatch,
namespacing) that mocks cannot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from coworker.mcp.mcp_session import (
    MAX_TOOL_NAME_LEN,
    McpSessionManager,
    mcp_approval_digest,
    tool_is_read_only,
)

from .conftest import content_text, write_server


# ── discovery + execution ────────────────────────────────────────────────


def test_tools_are_discovered(single_server):
    sessions, _server_id, _marker = single_server
    names = sessions.tool_names()
    assert {"peek", "wipe", "add", "alpha_only"} <= names


@pytest.mark.asyncio
async def test_tool_executes_through_persistent_session(single_server):
    sessions, _server_id, marker = single_server
    add = next(t for t in sessions.all_tools() if t.name == "add")

    assert "5" in content_text(await add.ainvoke({"a": 2, "b": 3}))
    assert "30" in content_text(await add.ainvoke({"a": 10, "b": 20}))

    # Persistence: the subprocess must have started exactly once for both calls.
    assert marker.read_text() == "x"


def test_broken_server_does_not_break_healthy_one(tmp_path, mcp_manager):
    server = write_server(tmp_path, "alpha", "alpha_only")
    mcp_manager.add_server(
        name="Alpha", transport="stdio", command=sys.executable,
        args=f"{server} {tmp_path / 'alpha.marker'}",
    )
    mcp_manager.add_server(name="Broken", transport="stdio", command="/nonexistent/nope", args="")

    sessions = McpSessionManager(tmp_path, mcp_manager, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    try:
        sessions.ensure_connected(enable_browser_flow=False)
        assert "add" in sessions.tool_names()
    finally:
        sessions.shutdown()


# ── R2: cross-server tool name conflicts ─────────────────────────────────


def test_conflicting_tools_are_namespaced_not_dropped(two_servers):
    sessions, ids = two_servers
    names = sessions.tool_names()

    # Every server-unique tool survives untouched.
    assert {"alpha_only", "beta_only"} <= names

    # Colliding names are namespaced for *all* participants (order-independent),
    # and the bare name disappears so the model can never call an ambiguous tool.
    for bare in ("peek", "wipe", "add"):
        assert bare not in names, f"{bare} still exposed bare despite a conflict"
        namespaced = {n for n in names if n.endswith(f"__{bare}")}
        assert len(namespaced) == 2, f"expected 2 namespaced {bare}, got {namespaced}"

    conflicts = sessions.list_conflicts()
    assert set(conflicts) == {"peek", "wipe", "add"}
    for server_ids in conflicts.values():
        assert sorted(server_ids) == sorted(ids)


def test_namespaced_tools_still_execute(two_servers):
    sessions, _ids = two_servers
    tool = next(t for t in sessions.all_tools() if t.name.endswith("__add"))
    assert "9" in content_text(tool.invoke({"a": 4, "b": 5}))


def test_namespaced_names_fit_the_provider_budget(two_servers):
    sessions, _ids = two_servers
    for name in sessions.tool_names():
        assert 1 <= len(name) <= MAX_TOOL_NAME_LEN
        assert all(ch.isalnum() or ch in "_-" for ch in name), name


def test_no_conflicts_means_bare_names(single_server):
    sessions, _server_id, _marker = single_server
    assert sessions.list_conflicts() == {}
    assert "add" in sessions.tool_names()


# ── policy / annotations ─────────────────────────────────────────────────


def test_policy_reflects_tool_annotations(single_server):
    sessions, server_id, _marker = single_server

    peek = sessions.tool_policy("peek")
    assert peek is not None
    assert peek["read_only"] is True
    assert peek["server_id"] == server_id
    assert peek["remote_name"] == "peek"
    assert peek["digest"] == mcp_approval_digest(server_id, "peek")

    wipe = sessions.tool_policy("wipe")
    assert wipe["read_only"] is False
    assert wipe["annotations"]["destructive"] is True

    # Undeclared annotations must NOT be optimistically treated as read-only.
    add = sessions.tool_policy("add")
    assert add["read_only"] is False


def test_policy_returns_none_for_builtin_tools(single_server):
    sessions, _server_id, _marker = single_server
    assert sessions.tool_policy("read_file") is None
    assert sessions.tool_policy("") is None


def test_tool_is_read_only_defaults_to_false():
    assert tool_is_read_only(None) is False
    assert tool_is_read_only({}) is False
    assert tool_is_read_only({"read_only": None}) is False
    assert tool_is_read_only({"read_only": True}) is True


# ── R4: trusted semantics ────────────────────────────────────────────────


def test_trusted_flag_is_read_live(single_server, mcp_manager):
    sessions, server_id, _marker = single_server
    assert sessions.tool_policy("wipe")["trusted"] is False

    mcp_manager.update_server(server_id, trusted=True)
    sessions._trust_cache.clear()  # bypass the 2s TTL in-test
    assert sessions.tool_policy("wipe")["trusted"] is True


def test_trusted_does_not_reenable_disabled_tools():
    """Trust means "no approval prompt", never "ignore my disable switch"."""
    tools = [type("T", (), {"name": n})() for n in ("peek", "wipe")]
    server = {"trusted": True, "disabled_tools": ["wipe"]}
    kept = {t.name for t in McpSessionManager._apply_policy(server, tools)}
    assert kept == {"peek"}


def test_disabled_tools_are_dropped_from_the_session(tmp_path, mcp_manager):
    server = write_server(tmp_path, "alpha", "alpha_only")
    entry = mcp_manager.add_server(
        name="Alpha", transport="stdio", command=sys.executable,
        args=f"{server} {tmp_path / 'alpha.marker'}",
    )
    mcp_manager.update_server(entry["id"], trusted=True, disabled_tools=["wipe"])

    sessions = McpSessionManager(tmp_path, mcp_manager, connect_timeout=25.0, call_timeout=25.0)
    sessions.start()
    try:
        sessions.ensure_connected(enable_browser_flow=False)
        names = sessions.tool_names()
        assert "wipe" not in names
        assert "peek" in names
        assert sessions.tool_policy("wipe") is None
    finally:
        sessions.shutdown()
