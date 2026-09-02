"""MCP protocol-correctness invariants.

Covers the owned Coworker MCP layer (no live server required):
- _annotation_flags applies the spec defaults (readOnlyHint=false,
  destructiveHint=true) so an undeclared tool is treated as destructive.
- _retry_safe only allows re-invocation of idempotent/read-only tools.
- _server_disabled_tools / _apply_policy filter by the exposed name.
- flatten_exceptions / friendly_error are importable (the mcp_utils
  extraction must not leave dangling _flatten_exceptions references).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from coworker.mcp.mcp_session import (  # noqa: E402
    McpSessionManager,
    _annotation_flags,
)
from coworker.mcp.mcp_utils import flatten_exceptions, friendly_error  # noqa: E402
from coworker.mcp.mcp_session import flatten_exceptions as _session_flatten  # noqa: E402
from coworker.mcp.mcp_session import friendly_error as _session_friendly  # noqa: E402
from mcp.types import Tool, ToolAnnotations  # noqa: E402


def _tool(name="x", annotations=None):
    return Tool(name=name, description="", inputSchema={}, annotations=annotations)


class TestAnnotationDefaults:
    def test_undeclared_is_destructive(self):
        flags = _annotation_flags(_tool())
        assert flags["destructive"] is True
        assert flags["read_only"] is False

    def test_declared_read_only_and_safe(self):
        flags = _annotation_flags(
            _tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
        )
        assert flags["read_only"] is True
        assert flags["destructive"] is False

    def test_declared_destructive(self):
        flags = _annotation_flags(_tool(annotations=ToolAnnotations(destructiveHint=True)))
        assert flags["destructive"] is True


class TestRetrySafety:
    def _manager(self):
        return McpSessionManager.__new__(McpSessionManager)

    def test_missing_policy_is_not_retryable(self):
        sm = self._manager()
        sm._policies = {}
        assert sm._retry_safe("nope") is False

    def test_idempotent_is_retryable(self):
        sm = self._manager()
        sm._policies = {"t": {"read_only": False, "annotations": {"idempotent": True}}}
        assert sm._retry_safe("t") is True

    def test_read_only_is_retryable(self):
        sm = self._manager()
        sm._policies = {"t": {"read_only": True, "annotations": {}}}
        assert sm._retry_safe("t") is True

    def test_non_idempotent_write_is_not_retryable(self):
        sm = self._manager()
        sm._policies = {"t": {"read_only": False, "annotations": {"idempotent": False}}}
        assert sm._retry_safe("t") is False


class TestDisabledFilter:
    def test_exposed_name_is_stripped(self, tmp_path):
        sm = McpSessionManager.__new__(McpSessionManager)
        from coworker.mcp.mcp import McpManager

        mgr = McpManager(tmp_path / "config.json")
        entry = mgr.add_server(name="S", transport="stdio", command="echo", args="x")
        sid = entry["id"]
        sm.mcp_manager = mgr
        # Disable by the exposed (namespaced) name
        mgr.update_server(sid, disabled_tools=["git__commit"])
        assert sm._server_disabled_tools(sid) == {"git__commit"}


class TestExtractionWiring:
    """The mcp_utils extraction must expose flatten/friendly error helpers."""

    def test_flatten_exceptions_imported(self):
        assert callable(_session_flatten)
        assert _session_flatten is flatten_exceptions

    def test_friendly_error_imported(self):
        assert callable(_session_friendly)
        assert _session_friendly is friendly_error
        assert "timed out" in friendly_error(TimeoutError("x")).lower()

    def test_flatten_unwraps_groups(self):
        group = BaseExceptionGroup("outer", [ValueError("leaf")])
        leaves = flatten_exceptions(group)
        assert leaves and all(isinstance(e, ValueError) for e in leaves)
