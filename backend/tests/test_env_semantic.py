"""Semantic GUI locators: resolve {role, name} to an AX ref by observing."""

from __future__ import annotations

from coworker.workflows.env import _resolve_semantic_ref, _semantic_ref_from_text, build_tool_environment

SNAPSHOT = """
[axwindow:DeepSeek#0]
  [axbutton:导出#1]
  [axbutton:允许#2]
  [axtextfield:搜索#3]
"""


class _FakeTool:
    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        return self._result


def test_semantic_ref_from_text_by_role_and_name():
    assert _semantic_ref_from_text(SNAPSHOT, "button", "导出") == "axbutton:导出#1"
    assert _semantic_ref_from_text(SNAPSHOT, "button", "允许") == "axbutton:允许#2"
    assert _semantic_ref_from_text(SNAPSHOT, "", "搜索") == "axtextfield:搜索#3"
    assert _semantic_ref_from_text(SNAPSHOT, "button", "不存在") is None


def test_resolve_semantic_ref_observes():
    obs = _FakeTool("computer_observe", SNAPSHOT)
    ref = _resolve_semantic_ref({"computer_observe": obs}, {"role": "button", "name": "导出"})
    assert ref == "axbutton:导出#1"
    assert obs.calls and obs.calls[0]["action"] == "snapshot"


def test_app_click_ref_resolves_semantic_locator():
    comp = _FakeTool("computer", {"ok": True})
    obs = _FakeTool("computer_observe", SNAPSHOT)
    env = build_tool_environment(workspace=None, tools=[comp, obs])
    env.app("click_ref", {}, {"role": "button", "name": "导出"})
    assert comp.calls[-1]["action"] == "click_ref"
    assert comp.calls[-1]["ref"] == "axbutton:导出#1"


def test_app_click_ref_without_target_fails_closed():
    comp = _FakeTool("computer", {"ok": True})
    env = build_tool_environment(workspace=None, tools=[comp])
    try:
        env.app("click_ref", {}, None)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected a fail-closed RuntimeError")


def test_drag_and_clipboard_generate_script():
    script = _FakeTool("computer_script", {"ok": True})
    env = build_tool_environment(workspace=None, tools=[script])
    env.app("drag", {"app": "Safari", "x1": 1, "y1": 2, "x2": 3, "y2": 4}, None)
    assert "app.drag([1, 2], [3, 4])" in script.calls[-1]["code"]
    env.app("clipboard", {"op": "paste", "text": "hi", "app": "Safari"}, None)
    assert "app.paste" in script.calls[-1]["code"]
    env.app("file_dialog", {"path": "/tmp/x", "app": "Safari"}, None)
    assert "cmd+shift+g" in script.calls[-1]["code"]


def test_focus_window_launches_app():
    comp = _FakeTool("computer", {"ok": True})
    env = build_tool_environment(workspace=None, tools=[comp])
    env.app("focus_window", {"app": "Safari"}, None)
    assert comp.calls[-1] == {"action": "launch_app", "app": "Safari"}


def test_script_action_dispatches_to_computer_script():
    script = _FakeTool("computer_script", {"ok": True})
    env = build_tool_environment(workspace=None, tools=[script])
    env.app("script", {"code": "app.open('x')", "reset": True}, None)
    assert script.calls[-1]["code"] == "app.open('x')"
    assert script.calls[-1]["reset"] is True
