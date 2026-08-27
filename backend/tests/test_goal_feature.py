"""Tests for the GoalFeature component (goal capability toggle).

Covers the component's own precedence rules (env bypass > persisted setting >
default) and the two integration gates: /goal/set rejected when disabled, and
/settings round-trips the goal_enabled flag.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as _main_module  # noqa: E402
from coworker.goal_feature import GoalFeature  # noqa: E402


class _FakeSessionStore:
    """Minimal session store so /goal/set passes _require_goal and we can
    assert set_goal is never reached when the capability is disabled."""

    def require(self, session_id):
        return type("S", (), {"work_mode": "build", "autonomy": "autonomous"})()

    def set_goal(self, *args, **kwargs):
        raise AssertionError("set_goal must not be reached while disabled")


@pytest.fixture
def feature(tmp_path, monkeypatch):
    settings_file = str(tmp_path / ".coworker_settings.json")
    feat = GoalFeature(settings_file, default_enabled=True)
    monkeypatch.setattr(_main_module, "goal_feature", feat)
    # /settings GET/POST persist to the module-level SETTING_FILE constant —
    # point it at the same temp file so the round-trip is observable.
    monkeypatch.setattr(_main_module, "SETTING_FILE", settings_file)
    return feat


def test_defaults_to_enabled(tmp_path):
    feat = GoalFeature(str(tmp_path / ".coworker_settings.json"))
    assert feat.is_enabled() is True


def test_missing_file_falls_back_to_default(tmp_path):
    feat = GoalFeature(str(tmp_path / "does-not-exist.json"))
    assert feat.is_enabled() is True


def test_persisted_toggle(tmp_path):
    feat = GoalFeature(str(tmp_path / ".coworker_settings.json"))
    feat.set_enabled(False)
    assert feat.is_enabled() is False
    feat.set_enabled(True)
    assert feat.is_enabled() is True


def test_env_bypass_wins_over_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_GOAL_ENABLED", "0")
    feat = GoalFeature(str(tmp_path / ".coworker_settings.json"))
    feat.set_enabled(True)  # persisted on, but code-level bypass forces off
    assert feat.is_enabled() is False
    monkeypatch.setenv("COWORKER_GOAL_ENABLED", "1")
    assert feat.is_enabled() is True


def test_goal_set_rejected_when_disabled(feature):
    feature.set_enabled(False)
    original = _main_module.session_store
    _main_module.session_store = _FakeSessionStore()
    try:
        with TestClient(_main_module.app) as client:
            resp = client.post("/goal/set", json={"session_id": "s1", "objective": "do the thing"})
            assert resp.status_code == 403
            assert "disabled" in resp.json()["detail"]
    finally:
        _main_module.session_store = original


def test_goal_set_ok_when_enabled(feature, monkeypatch):
    feature.set_enabled(True)
    original = _main_module.session_store
    calls = []
    store = _FakeSessionStore()

    def set_goal(session_id, objective, token_budget=None):
        calls.append(objective)
        return type("G", (), {"to_dict": lambda self: {"objective": objective, "status": "active"}})()

    store.set_goal = set_goal
    _main_module.session_store = store
    try:
        with TestClient(_main_module.app) as client:
            resp = client.post("/goal/set", json={"session_id": "s1", "objective": "do the thing"})
            assert resp.status_code == 200
            assert calls == ["do the thing"]
    finally:
        _main_module.session_store = original


def test_settings_round_trip_goal_enabled(feature, tmp_path):
    with TestClient(_main_module.app) as client:
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert resp.json()["goal_enabled"] is True

        resp = client.post("/settings", json={"goal_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["goal_enabled"] is False

        resp = client.get("/settings")
        assert resp.json()["goal_enabled"] is False


def test_session_goal_bypasses_persisted_goal_when_disabled(feature, monkeypatch):
    """A pre-existing (persisted) active goal must be invisible to the streaming
    pipeline when the capability is off — no injection, no loop, no events."""
    class _FakeGoal:
        status = "active"

    class _Store:
        def get_goal(self, session_id):
            return _FakeGoal()

    monkeypatch.setattr(_main_module, "session_store", _Store())

    feature.set_enabled(False)
    assert _main_module._session_goal("s1") is None

    feature.set_enabled(True)
    assert _main_module._session_goal("s1").status == "active"
