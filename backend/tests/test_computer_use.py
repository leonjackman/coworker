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
    assert names == ["computer_observe", "computer"]


# ---------------------------------------------------------------------------
# Tools: names + bridge error surfacing
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, state=None, screenshot=None, act=None):
        self._state = state if state is not None else {"ok": True, "platform": "darwin", "displays": []}
        self._screenshot = screenshot
        self._act = act if act is not None else {"ok": True, "action": "click"}

    def state(self):
        return self._state

    def displays(self):
        return {"ok": True, "displays": []}

    def screenshot(self, **kw):
        return self._screenshot

    def act(self, payload):
        return self._act


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
    assert [getattr(t, "name", "") for t in tools] == ["computer_observe", "computer"]


def test_observe_state(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(state={"ok": True, "platform": "darwin", "displays": []}))
    (observe, _act) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = observe.invoke({"action": "state"})
    assert json.loads(out)["platform"] == "darwin"


def test_observe_screenshot_vision_block(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    shot = {"image": _data_url(_VALID_JPEG), **_GEOMETRY}
    fake_client_factory(_FakeClient(screenshot=shot))
    (observe, _act) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
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
    (observe, _act) = build_computer_tools(tmp_path, vision=False, session_id="sess-1")
    out = observe.invoke({"action": "screenshot"})
    payload = json.loads(out)
    assert "screenshot" in payload
    assert Path(payload["screenshot"]).exists()
    assert Path(payload["screenshot"]).stat().st_size > 0
    assert payload["shot"]["width"] == 1024


def test_observe_screenshot_rejects_empty_vision(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(screenshot={"image": "data:image/jpeg;base64,", **_GEOMETRY}))
    (observe, _act) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
    out = observe.invoke({"action": "screenshot"})
    assert isinstance(out, str)
    assert json.loads(out)["error_code"] == "screen_permission"


def test_act_paused_hint(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(act={"error": "computer paused by user", "error_code": "computer_paused", "reason": "user"}))
    (_observe, act) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click", "x": 10, "y": 10, "shot_width": 1024, "shot_height": 640})
    payload = json.loads(out)
    assert payload["error_code"] == "computer_paused"
    assert "paused" in payload["hint"]


def test_act_ok(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    fake_client_factory(_FakeClient(act={"ok": True, "action": "click"}))
    (_observe, act) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click", "x": 10, "y": 10, "shot_width": 1024, "shot_height": 640})
    payload = json.loads(out)
    assert payload["ok"] is True


def test_act_passes_shot_geometry(fake_client_factory, monkeypatch):
    from coworker.computer.bridge_client import build_computer_tools

    captured = {}

    class _Recording(_FakeClient):
        def act(self, payload):
            captured.update(payload)
            return {"ok": True, "action": payload.get("action")}

    fake_client_factory(_Recording())
    (_observe, act) = build_computer_tools(Path("/tmp"), session_id="sess")
    act.invoke({"action": "drag", "x": 1, "y": 2, "x2": 3, "y2": 4, "button": "left", "shot_width": 1024, "shot_height": 640, "display": 0})
    assert captured["shot"] == {"width": 1024, "height": 640}
    assert captured["x"] == 1.0
    assert captured["y"] == 2.0
    assert captured["x2"] == 3.0


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
    def __init__(self, request_result, *, act=None, screenshot=None):
        super().__init__(act=act if act is not None else _FakeClient()._act,
                         screenshot=screenshot if screenshot is not None else _FakeClient()._screenshot)
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


def test_act_auto_requests_permission_once_on_denial(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    counting = _install_counting(
        fake_client_factory,
        {"ok": True, "status": "denied", "opened_settings": True},
        act=_PERM_ERR("input_permission", "no accessibility"),
    )
    # First act attempt fails on Accessibility; the OS is denied -> auto-request.
    (_observe, act) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click", "x": 5, "y": 5})
    payload = json.loads(out)
    assert payload["error_code"] == "input_permission"
    assert counting.requested_kinds == ["accessibility"]
    assert "toggle CoWorker OFF and back ON" in payload["hint"]

    # A repeated identical failure must NOT re-request (once-guard).
    out2 = act.invoke({"action": "click", "x": 5, "y": 5})
    assert json.loads(out2)["error_code"] == "input_permission"
    assert counting.requested_kinds == ["accessibility"]


def test_observe_auto_requests_screen_permission(fake_client_factory):
    from coworker.computer.bridge_client import build_computer_tools

    counting = _install_counting(
        fake_client_factory,
        {"ok": True, "status": "not determined", "prompt_shown": True},
        screenshot=_PERM_ERR("screen_permission", "capture empty"),
    )
    (observe, _act) = build_computer_tools(Path("/tmp"), vision=True, session_id="sess")
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
        act=_PERM_ERR("input_permission", "no accessibility"),
    )
    (_observe, act) = build_computer_tools(Path("/tmp"), session_id="sess")
    out = act.invoke({"action": "click", "x": 5, "y": 5})
    payload = json.loads(out)
    assert "granted just now" in payload["hint"]


def test_client_methods_exist():
    from coworker.computer.bridge_client import ComputerClient

    client = ComputerClient(None)
    assert hasattr(client, "request_permission")
    assert hasattr(client, "open_permission_settings")
