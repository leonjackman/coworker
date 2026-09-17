"""OS-level Computer Use feature tests.

Covers the master-switch gating (computer_feature, default OFF), capability
status resolution, the two tool mounts (computer_observe read-only +
computer mutating), screenshot delivery (vision image block vs disk path),
pause/error surfacing, and the HITL approval wiring (guarded asks per mutating
action, autonomous never asks, observe is never gated).
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# computer_feature master switch (default OFF)
# ---------------------------------------------------------------------------

def test_feature_default_off(monkeypatch, tmp_path: Path):
    from coworker.computer_feature import ComputerFeature

    feat = ComputerFeature(settings_file=str(tmp_path / ".coworker_settings.json"), default_enabled=False)
    assert feat.is_enabled() is False


def test_feature_toggle_persists(monkeypatch, tmp_path: Path):
    from coworker.computer_feature import ComputerFeature

    settings_file = tmp_path / ".coworker_settings.json"
    feat = ComputerFeature(settings_file=str(settings_file), default_enabled=False)
    assert feat.set_enabled(True) is True
    # A fresh instance reads the persisted toggle.
    feat2 = ComputerFeature(settings_file=str(settings_file), default_enabled=False)
    assert feat2.is_enabled() is True
    # A missing file still defaults OFF (never silently on).
    feat3 = ComputerFeature(settings_file=str(tmp_path / "absent.json"), default_enabled=False)
    assert feat3.is_enabled() is False


def test_feature_env_override(monkeypatch, tmp_path: Path):
    from coworker.computer_feature import ComputerFeature

    monkeypatch.setenv("COWORKER_COMPUTER_ENABLED", "1")
    feat = ComputerFeature(settings_file=str(tmp_path / ".coworker_settings.json"), default_enabled=False)
    assert feat.is_enabled() is True
    monkeypatch.setenv("COWORKER_COMPUTER_ENABLED", "0")
    assert feat.is_enabled() is False


# ---------------------------------------------------------------------------
# Capability status
# ---------------------------------------------------------------------------

def test_capability_status_feature_off(monkeypatch, tmp_path: Path):
    from coworker.computer.bridge_client import computer_capability_status

    monkeypatch.setattr("coworker.computer.bridge_client.computer_enabled", lambda: False)
    assert computer_capability_status(tmp_path) == "feature_off"


def test_capability_status_desktop_only(monkeypatch, tmp_path: Path):
    from coworker.computer.bridge_client import computer_capability_status

    monkeypatch.setattr("coworker.computer.bridge_client.computer_enabled", lambda: True)
    monkeypatch.setattr("coworker.computer.bridge_client.computer_available", lambda _d: False)
    assert computer_capability_status(tmp_path) == "desktop_only"


def test_capability_status_ok(monkeypatch, tmp_path: Path):
    from coworker.computer.bridge_client import computer_capability_status

    monkeypatch.setattr("coworker.computer.bridge_client.computer_enabled", lambda: True)
    monkeypatch.setattr("coworker.computer.bridge_client.computer_available", lambda _d: True)
    assert computer_capability_status(tmp_path) == "ok"


def test_resolve_empty_when_off_or_no_bridge(monkeypatch, tmp_path: Path):
    from coworker.computer import bridge_client as cbc

    monkeypatch.setattr(cbc, "computer_enabled", lambda: False)
    monkeypatch.setattr(cbc, "computer_available", lambda _d: True)
    assert cbc.resolve_computer_tools(tmp_path, session_id="s") == []

    monkeypatch.setattr(cbc, "computer_enabled", lambda: True)
    monkeypatch.setattr(cbc, "computer_available", lambda _d: False)
    assert cbc.resolve_computer_tools(tmp_path, session_id="s") == []

    monkeypatch.setattr(cbc, "computer_enabled", lambda: True)
    monkeypatch.setattr(cbc, "computer_available", lambda _d: True)
    names = [getattr(t, "name", "") for t in cbc.resolve_computer_tools(tmp_path, session_id="s")]
    assert names == ["computer_observe", "computer", "computer_script"]


# ---------------------------------------------------------------------------
# Tools: names + bridge error surfacing
# ---------------------------------------------------------------------------

_SNAP_TEXT = "[1] AXApplication \"TestApp\"\n  [2] AXButton \"OK\" at (100,50)\n  [3] AXTextField value=\"\" at (120,80)"


class _FakeClient:
    def __init__(self, state=None, screenshot=None, ax_act=None, snapshot_text: str | None = _SNAP_TEXT, request=None, frontmost="TestApp"):
        self._state = state if state is not None else {"ok": True, "platform": "darwin", "displays": []}
        self._screenshot = screenshot
        self._ax_act = ax_act if ax_act is not None else {"ok": True, "performed": "click"}
        self._snapshot_text = snapshot_text
        self._request_result = request
        self.requested_kinds = []
        self._frontmost = frontmost
        self._script_result = {"blocks": [{"type": "text", "text": "hello"}], "error": None}
        self.reset_called = False

    def state(self):
        return self._state

    def displays(self):
        return {"ok": True, "displays": []}

    def screenshot(self, **kw):
        return self._screenshot

    def act(self, payload):
        return self._ax_act

    # Structure-first surface (native AX)
    def ax_snapshot(self, depth=6):
        if self._snapshot_text is None:
            return {"error": "no accessibility", "error_code": "input_permission"}
        return {"ok": True, "frontmost": self._frontmost, "refs": 3, "text": self._snapshot_text}

    def ax_act(self, ref, op, text=None):
        return self._ax_act

    def ax_app_state(self, app="", depth=6):
        if self._snapshot_text is None:
            return {"error": "no accessibility", "error_code": "input_permission"}
        return {
            "ok": True, "frontmost": self._frontmost, "app": self._frontmost or "TestApp",
            "pid": 1, "refs": 3, "changed": False, "removed": ["axbutton:x#9"],
            "window": {"title": "Test Window", "frame": {"x": 0, "y": 0, "width": 800, "height": 600}},
            "text": self._snapshot_text,
        }

    def ax_press(self, key, modifiers):
        return {"ok": True, "performed": "press_hotkey", "key": key, "modifiers": modifiers}

    def ax_type(self, text):
        return {"ok": True, "performed": "type_text"}

    def ax_launch(self, app):
        return {"ok": True, "launched": app}

    def ax_coords(self, x, y):
        return {"ok": True, "performed": "click_coords"}

    def ax_scroll(self, dx, dy):
        return {"ok": True, "performed": "scroll"}

    def ax_frontmost(self):
        return {"ok": True, "pid": 1, "app": self._frontmost}

    # Persistent JS surface
    def ax_script(self, code, timeout_ms=0):
        return self._script_result

    def ax_script_reset(self):
        self.reset_called = True
        return {"reset": True}

    def ax_list_apps(self, scope="running"):
        return {"apps": [{"bundleId": "com.apple.Safari", "displayName": "Safari", "pid": 42}]}

    def ax_resolve_app(self, app):
        return {"selector": app, "pid": 42, "bundleId": "com.apple.Safari"}

    def ax_input_text(self, ref, text, app="", submit=False):
        return {"ok": True, "performed": "input_text", "strategy": "ax_value", "verified": True}

    def ax_ui_settle(self, app="", quiet_ms=250, timeout_ms=3000):
        return {"settled": True}

    def request_permission(self, kind):
        self.requested_kinds.append(kind)
        return self._request_result or {"ok": True, "status": "not determined", "prompt_shown": True}


def _data_url(body: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(body).decode('ascii')}"


_VALID_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_GEOMETRY = {"shot": {"width": 1024, "height": 640}, "display": {"index": 0, "bounds": {"x": 0, "y": 0, "width": 1800, "height": 1125}}}


@pytest.fixture
def fake_client_factory(monkeypatch):
    from coworker.computer import bridge_client as cbc

    def install(client):
        monkeypatch.setattr(cbc, "ComputerClient", lambda data_dir, **kw: client)
        return client

    return install


def test_build_tools_names(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    tools = build_computer_tools(Path("/tmp"), session_id="sess")
    assert [getattr(t, "name", "") for t in tools] == ["computer_observe", "computer", "computer_script"]


def _script_tool(tools):
    return next(t for t in tools if getattr(t, "name", "") == "computer_script")


def test_script_text_output(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    client = fake_client_factory(_FakeClient())
    client._script_result = {"blocks": [{"type": "text", "text": "apps=63"}, {"type": "text", "text": "refs=95"}], "error": None}
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess"))
    out = tool.invoke({"code": "cua.emitText('x')"})
    assert "apps=63" in out and "refs=95" in out


def test_script_error_is_surfaced(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    client = fake_client_factory(_FakeClient())
    client._script_result = {"blocks": [{"type": "text", "text": "partial"}], "error": "ReferenceError: foo is not defined"}
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess"))
    out = tool.invoke({"code": "foo"})
    assert "ReferenceError" in out and "do NOT repeat" in out


def test_script_reset(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    client = fake_client_factory(_FakeClient())
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess"))
    out = tool.invoke({"reset": True})
    assert client.reset_called is True
    assert '"reset": true' in out.lower()


def test_script_requires_code(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess"))
    out = tool.invoke({"code": "  "})
    assert "param_error" in out


def test_script_image_vision_block(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    client = fake_client_factory(_FakeClient())
    client._script_result = {"blocks": [{"type": "image", "image": _data_url(_VALID_JPEG)}], "error": None}
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess", vision=True))
    out = tool.invoke({"code": "cua.emitImage(await app.getScreenshot())"})
    assert isinstance(out, list)
    assert any(b.get("type") == "image_url" for b in out)


def test_script_image_non_vision_saved(tmp_path: Path, fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    client = fake_client_factory(_FakeClient())
    client._script_result = {"blocks": [{"type": "image", "image": _data_url(_VALID_JPEG)}], "error": None}
    tool = _script_tool(build_computer_tools(tmp_path, session_id="sess", vision=False))
    out = tool.invoke({"code": "cua.emitImage(await app.getScreenshot())"})
    assert isinstance(out, str)
    assert "saved to disk" in out


def test_observe_state(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(state={"ok": True, "platform": "darwin", "displays": []}))
    (observe, _act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = observe.invoke({"action": "state"})
    assert json.loads(out)["platform"] == "darwin"


def test_observe_app_state_reports_window_and_diff(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    (observe, _act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    payload = json.loads(observe.invoke({"action": "app_state"}))
    assert payload["changed"] is False
    assert payload["removed"] == ["axbutton:x#9"]
    assert payload["window"]["title"] == "Test Window"
    assert payload["refs"] == 3
    assert _SNAP_TEXT in payload["snapshot"]


def test_observe_screenshot_vision_block(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    shot = {"image": _data_url(_VALID_JPEG), **_GEOMETRY}
    fake_client_factory(_FakeClient(screenshot=shot))
    (observe, _act, _script) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
    out = observe.invoke({"action": "screenshot"})
    assert isinstance(out, list)
    text_part = [p for p in out if p.get("type") == "text"][0]
    image_part = [p for p in out if p.get("type") == "image_url"][0]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert '"shot"' in text_part["text"]
    assert '"display"' in text_part["text"]


def test_observe_screenshot_non_vision_saved(tmp_path: Path, fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    shot = {"image": _data_url(_VALID_JPEG), **_GEOMETRY}
    fake_client_factory(_FakeClient(screenshot=shot))
    (observe, _act, _script) = build_computer_tools(tmp_path, vision=False, session_id="sess-1")
    out = observe.invoke({"action": "screenshot"})
    payload = json.loads(out)
    assert "screenshot" in payload
    assert Path(payload["screenshot"]).exists()
    assert Path(payload["screenshot"]).stat().st_size > 0
    assert payload["shot"]["width"] == 1024


def test_observe_screenshot_rejects_empty_vision(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(screenshot={"image": "data:image/jpeg;base64,", **_GEOMETRY}))
    (observe, _act, _script) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
    out = observe.invoke({"action": "screenshot"})
    assert isinstance(out, str)
    assert json.loads(out)["error_code"] == "screen_permission"


def test_act_paused_hint(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(ax_act={"error": "computer paused by user", "error_code": "computer_paused", "reason": "user"}))
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "2"})
    payload = json.loads(out)
    assert payload["error_code"] == "computer_paused"
    assert "paused" in payload["hint"]


def test_act_ok(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(ax_act={"ok": True, "performed": "click"}))
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "2"})
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "verified" in payload


def test_act_press_hotkey_passes_key_and_modifiers(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    captured = {}

    class _Recording(_FakeClient):
        def ax_press(self, key, modifiers):
            captured.update({"key": key, "modifiers": modifiers})
            return {"ok": True, "performed": "press_hotkey"}

    fake_client_factory(_Recording())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    act.invoke({"action": "press_hotkey", "key": "space", "modifiers": ["cmd"]})
    assert captured["key"] == "space"
    assert captured["modifiers"] == ["cmd"]


def test_act_launch_app(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(snapshot_text=_SNAP_TEXT))
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "launch_app", "app": "Calculator"})
    payload = json.loads(out)
    # observation-free: launch_app proceeds even without a usable snapshot.
    assert payload["ok"] is True
    assert payload["action"] == "launch_app"
    assert "verified" in payload


def test_act_type_text_refuses_shortcut(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "type_text", "text": "cmd+spaceMusic"})
    payload = json.loads(out)
    assert payload["error_code"] == "shortcut_as_text"
    assert "press_hotkey" in payload["error"]


def test_act_fail_closed_without_observation(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(snapshot_text=None))
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "2"})
    payload = json.loads(out)
    assert payload["error_code"] == "no_observation"


def test_stale_ref_self_heals_with_fresh_snapshot(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(ax_act=_PERM_ERR("computer_error", "no AX element for ref axbutton:搜索#1")))
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "axbutton:搜索#1"})
    payload = json.loads(out)
    assert payload["error_code"] == "computer_error"
    assert "fresh_snapshot" in payload
    assert payload["fresh_snapshot"] == _SNAP_TEXT
    assert "stale" in payload["note"]


def test_type_into_passes_submit(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    captured = {}

    class _Recording(_FakeClient):
        def ax_act(self, ref, op, **kw):
            if op == "type_into":
                captured.update({"ref": ref, "text": kw.get("text"), "submit": kw.get("submit")})
            return {"ok": True, "performed": op}

    fake_client_factory(_Recording())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    act.invoke({"action": "type_into", "ref": "axtextfield:apple music#1", "text": "情歌王", "submit": True})
    assert captured["submit"] is True
    assert captured["text"] == "情歌王"


def test_type_into_unverified_when_focused_value_empty(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    class _NoPaste(_FakeClient):
        def ax_act(self, ref, op, **kw):
            if op == "type_into":
                return {"ok": True, "performed": "type_into", "focused": {"role": "AXTextField", "value": ""}}
            return {"ok": True, "performed": op}

    fake_client_factory(_NoPaste())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "type_into", "ref": "axtextfield:apple music#1", "text": "情歌王", "submit": True})
    payload = json.loads(out)
    assert payload["verified"] is False
    assert payload["focused_value"] == ""
    assert "do NOT retry-loop" in payload["note"]


def test_type_into_verified_by_focused_value(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    class _Pasted(_FakeClient):
        def ax_act(self, ref, op, **kw):
            if op == "type_into":
                return {"ok": True, "performed": "type_into", "focused": {"role": "AXTextField", "value": kw.get("text")}}
            return {"ok": True, "performed": op}

    fake_client_factory(_Pasted())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "type_into", "ref": "axtextfield:apple music#1", "text": "情歌王", "submit": True})
    payload = json.loads(out)
    assert payload["verified"] is True
    assert payload["focused_value"] == "情歌王"


def test_click_coords_passes_shot_geometry(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    captured = {}

    class _Recording(_FakeClient):
        def ax_coords(self, x, y, shot_width=0, shot_height=0, display=0):
            captured.update({"x": x, "y": y, "sw": shot_width, "sh": shot_height, "display": display})
            return {"ok": True, "performed": "click_coords"}

    fake_client_factory(_Recording())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    act.invoke({"action": "click_coords", "x": 512, "y": 300, "shot_width": 1024, "shot_height": 640, "display": 0})
    assert captured["x"] == 512.0
    assert captured["sw"] == 1024
    assert captured["sh"] == 640


# ---------------------------------------------------------------------------
# HITL: guarded asks for the mutating tool, autonomous passes, observe never gated
# ---------------------------------------------------------------------------

class _StubState:
    def __init__(self, **kw):
        self.data = kw

    def get(self, key, default=None):
        return self.data.get(key, default)


class _StubReq:
    def __init__(self, state):
        self.state = state
        self.tool_call = {}


def test_hitl_computer_gating():
    from coworker.agent.middleware.hitl import command_approval_middleware

    [hitl] = command_approval_middleware()
    interrupt_on = hitl.interrupt_on
    assert "computer" in interrupt_on
    # computer_observe (read-only) must never be gated.
    assert "computer_observe" not in interrupt_on

    when = interrupt_on["computer"]["when"]

    def req(phase, autonomy):
        return _StubReq(_StubState(phase=phase, work_mode="build", autonomy=autonomy))

    assert when(req("execute", "guarded")) is True      # 默認權限 -> 逐動作審批
    assert when(req("execute", "autonomous")) is False  # 完整權限 -> 直接放行
    assert when(req("discuss", "guarded")) is False     # 非 execute phase 不審
    assert when(req("execute", "guarded"))


def test_hitl_computer_observe_never_in_interrupt_map():
    from coworker.agent.middleware.hitl import command_approval_middleware

    [hitl] = command_approval_middleware()
    assert "computer_observe" not in hitl.interrupt_on


# ---------------------------------------------------------------------------
# Passive permission auto-request (once per kind per mounted toolset)
# ---------------------------------------------------------------------------

class _CountingClient(_FakeClient):
    def __init__(self, request_result, *, ax_act=None, screenshot=None):
        super().__init__(ax_act=ax_act if ax_act is not None else {"ok": True, "performed": "click"}, screenshot=screenshot)
        self._request_result = request_result
        self.requested_kinds = []

    def request_permission(self, kind):
        self.requested_kinds.append(kind)
        return self._request_result


_PERM_ERR = lambda code, msg: {"error": msg, "error_code": code}  # noqa: E731


def _install_counting(fake_client_factory, request_result, **kw):
    counting = _CountingClient(request_result, **kw)
    fake_client_factory(counting)
    return counting


def test_script_loop_guard(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    tool = _script_tool(build_computer_tools(Path("/tmp"), session_id="sess"))
    code = "await (await cua.getApp('Finder')).getAXState();"
    first = tool.invoke({"code": code})
    second = tool.invoke({"code": code})
    third = tool.invoke({"code": code})
    assert "loop_guard" not in first and "loop_guard" not in second
    assert "loop_guard" in third


def test_computer_loop_guard(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient())
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    for _ in range(2):
        assert "loop_guard" not in act.invoke({"action": "click_ref", "ref": "axbutton:ok#1"})
    assert "loop_guard" in act.invoke({"action": "click_ref", "ref": "axbutton:ok#1"})


def test_act_auto_requests_permission_once_on_denial(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    counting = _install_counting(
        fake_client_factory,
        {"ok": True, "status": "denied", "opened_settings": True},
        ax_act=_PERM_ERR("input_permission", "no accessibility"),
    )
    # First act attempt fails on Accessibility; the OS is denied -> auto-request.
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "2"})
    payload = json.loads(out)
    assert payload["error_code"] == "input_permission"
    assert counting.requested_kinds == ["accessibility"]
    assert "toggle CoWorker OFF and back ON" in payload["hint"]

    # A repeated identical failure must NOT re-request (once-guard).
    out2 = act.invoke({"action": "click_ref", "ref": "2"})
    assert json.loads(out2)["error_code"] == "input_permission"
    assert counting.requested_kinds == ["accessibility"]


def test_observe_auto_requests_screen_permission(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    counting = _install_counting(
        fake_client_factory,
        {"ok": True, "status": "not determined", "prompt_shown": True},
        screenshot=_PERM_ERR("screen_permission", "capture empty"),
    )
    (observe, _act, _script) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
    out = observe.invoke({"action": "screenshot"})
    payload = json.loads(out) if isinstance(out, str) else out
    assert isinstance(payload, dict)
    assert payload["error_code"] == "screen_permission"
    assert counting.requested_kinds == ["screen"]
    # not-determined => macOS consent alert was triggered; tell the user to Allow.
    assert "macOS Screen Recording prompt" in payload["hint"]


def test_request_granted_then_retry_hint(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    counting = _install_counting(
        fake_client_factory,
        {"ok": True, "status": "authorized", "granted": True},
        ax_act=_PERM_ERR("input_permission", "no accessibility"),
    )
    (_observe, act, _script) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click_ref", "ref": "2"})
    payload = json.loads(out)
    assert "granted just now" in payload["hint"]


def test_client_methods_exist():
    from coworker.computer.bridge_client import ComputerClient

    client = ComputerClient(None)
    assert hasattr(client, "request_permission")
    assert hasattr(client, "open_permission_settings")
