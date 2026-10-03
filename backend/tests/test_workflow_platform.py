"""Workflow platform support: normalization, inference, run-time gate."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.workflows import platform_support as ps  # noqa: E402


def _step(kind, do="", params=None, then=None, else_=None, body=None):
    return SimpleNamespace(kind=kind, do=do, params=params or {}, then=then, else_=else_, body=body)


def _wf(platform, *steps):
    return SimpleNamespace(platform=platform, steps=list(steps))


def test_parse_platforms_and_canonical():
    assert ps.parse_platforms("") == set(ps.ALL_TAGS)
    assert ps.parse_platforms("any") == set(ps.ALL_TAGS)
    assert ps.parse_platforms("darwin") == {"darwin"}
    assert ps.parse_platforms("mac, win") == {"darwin", "win32"}
    assert ps.parse_platforms(["win32", "linux"]) == {"win32", "linux"}
    assert ps.canonical("macos") == "darwin"
    assert ps.canonical("") == "any"
    assert ps.canonical("win, linux") == "win32,linux"


def test_explicit_tags_distinguishes_any_from_undeclared():
    assert ps.explicit_tags("") == set()
    assert ps.explicit_tags("any") == set()
    assert ps.explicit_tags("darwin") == {"darwin"}
    assert ps.explicit_tags(["mac", "linux"]) == {"darwin", "linux"}


def test_modifier_for():
    assert ps.modifier_for("darwin") == "cmd"
    assert ps.modifier_for("win32") == "ctrl"
    assert ps.modifier_for("linux") == "ctrl"


def test_infer_platforms():
    assert ps.infer_platforms(_wf("", _step("command", params={"command": ["open", "-a", "Safari"]}))) == {"darwin"}
    assert ps.infer_platforms(_wf("", _step("command", params={"command": "powershell -Command Get-Process"}))) == {"win32"}
    assert ps.infer_platforms(_wf("", _step("tool", do="run_applescript"))) == {"darwin"}
    assert ps.infer_platforms(_wf("", _step("tool", do="run_powershell"))) == {"win32"}
    assert ps.infer_platforms(_wf("", _step("browser", do="navigate", params={"url": "https://x"}))) == set()


def test_native_script_tools_are_declared_in_catalog():
    # Declared offline (not only via live introspection) and pinned to their OS.
    from coworker.workflows.capabilities import CapabilityRegistry

    names = {a.name for a in CapabilityRegistry.declared().actions("tool")}
    assert "run_applescript" in names and "run_powershell" in names
    assert ps.PLATFORM_ONLY_TOOLS["run_applescript"] == {"darwin"}
    assert ps.PLATFORM_ONLY_TOOLS["run_powershell"] == {"win32"}


def test_workflow_supports():
    other = "win32" if ps.current_tag() == "darwin" else "darwin"
    ok, declared, reason = ps.workflow_supports(_wf(other, _step("command", params={"command": ["echo"]})))
    assert ok is False and declared == {other} and "targets" in reason
    ok2, _, _ = ps.workflow_supports(_wf("any"))
    assert ok2 is True
    ok3, _, _ = ps.workflow_supports(_wf(""))
    assert ok3 is True


def test_executor_refuses_platform_mismatch(tmp_path: Path):
    from coworker.workflows import WorkflowManager

    other = "win32" if ps.current_tag() == "darwin" else "darwin"
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(
        f"""
name: platform-gated
description: d
platform: {other}
steps:
- id: id:1
  kind: command
  do: run
  params: {{command: ["echo", "hi"]}}
  description: run
  post: [ok]
"""
    )
    result = mgr.run("platform-gated")
    assert result["status"] == "failed"
    assert "platform_mismatch" in result["run"]["error"]


def test_scheduled_platform_mismatch_does_not_alert(tmp_path: Path, monkeypatch):
    """A platform mismatch on a scheduled run is a SKIP, so it must not raise a
    failure alert (the runner reports 'skipped')."""
    import coworker.notifications as notifications

    alerts: list = []
    monkeypatch.setattr(notifications, "notify", lambda *a, **k: alerts.append((a, k)))

    from coworker.workflows import WorkflowManager

    other = "win32" if ps.current_tag() == "darwin" else "darwin"
    mgr = WorkflowManager(tmp_path)
    mgr.enforce_conformance = False
    mgr.create(
        f"""
name: platform-alert
description: d
platform: {other}
steps:
- id: id:1
  kind: command
  do: run
  params: {{command: ["python", "-c", "print('hi')"]}}
  description: run
  post: [ok]
"""
    )
    result = mgr.run("platform-alert", trigger="schedule:s1")
    assert result["status"] == "failed"
    assert alerts == []


def test_schedule_runner_reports_skipped_on_platform_mismatch(tmp_path: Path):
    from types import SimpleNamespace

    from coworker.schedules.runner import ScheduleRunner

    class _FakeManager:
        def run(self, name, inputs, **kwargs):  # noqa: ANN001
            return {"status": "failed", "run": {"run_id": "r1", "error": "platform_mismatch: nope"}}

    runner = ScheduleRunner(workflow_manager=_FakeManager(), env_factory=lambda: None, data_dir=tmp_path)
    schedule = SimpleNamespace(workflow="w", inputs={}, id="s1")
    out = runner._run_workflow(schedule)
    assert out["status"] == "skipped"
