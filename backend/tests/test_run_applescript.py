"""WS2a: the gated AppleScript tool is macOS-only and validates its input
without shelling out."""

import json

from coworker.computer.applescript import build_applescript_tool


def test_only_built_on_macos():
    tool = build_applescript_tool()
    import sys

    if sys.platform == "darwin":
        assert tool is not None
        assert tool.name == "run_applescript"
    else:
        assert tool is None


def test_empty_script_is_rejected_without_executing():
    tool = build_applescript_tool()
    if tool is None:
        return
    result = json.loads(tool.invoke({"script": "   "}))
    assert result["error_code"] == "param_error"


def test_oversized_script_is_rejected():
    tool = build_applescript_tool()
    if tool is None:
        return
    result = json.loads(tool.invoke({"script": "x" * 20_001}))
    assert result["error_code"] == "param_error"
