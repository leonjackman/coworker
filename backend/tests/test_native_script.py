"""Per-OS native scripting tool + cross-platform desktop notification."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_powershell_tool_only_on_windows(monkeypatch):
    import coworker.computer.powershell as ps

    monkeypatch.setattr(ps.sys, "platform", "linux")
    assert ps.build_powershell_tool() is None
    monkeypatch.setattr(ps.sys, "platform", "win32")
    assert ps.build_powershell_tool() is not None


def test_run_powershell_executes_and_reports(monkeypatch):
    import coworker.computer.powershell as ps

    monkeypatch.setattr(ps.sys, "platform", "win32")
    tool = ps.build_powershell_tool()
    captured = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, shell=None, env=None):
        captured["cmd"] = cmd
        captured["input"] = input
        return subprocess.CompletedProcess(cmd, 0, stdout="hi\n", stderr="")

    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    out = json.loads(tool.invoke({"script": "Write-Output hi"}))
    assert out["return_code"] == 0 and "hi" in out["stdout"]
    assert captured["cmd"][0] == "powershell.exe"
    assert captured["input"] == "Write-Output hi"


def test_native_script_resolver_by_platform(monkeypatch):
    import coworker.computer.native_script as ns

    monkeypatch.setattr(ns.sys, "platform", "linux")
    assert ns.resolve_native_script_tools() == []
    monkeypatch.setattr(ns.sys, "platform", "win32")
    assert [t.name for t in ns.resolve_native_script_tools()] == ["run_powershell"]


def test_notify_prefers_electron(monkeypatch):
    import coworker.workflows.native as native

    calls = {"desktop": 0}

    def fake_desktop(title, body):
        calls["desktop"] += 1
        return "osascript"

    monkeypatch.setattr(native, "_desktop_notification", fake_desktop)
    out = native.run_native(
        "notify", "notification", {"title": "T", "body": "B"},
        notifier=lambda t, b: {"ok": True},
    )
    assert out["via"] == "electron" and out["notified"] is True
    assert calls["desktop"] == 0


def test_notify_falls_back_to_native(monkeypatch):
    import coworker.workflows.native as native

    monkeypatch.setattr(native, "_desktop_notification", lambda t, b: "notify-send")
    out = native.run_native(
        "notify", "notification", {"title": "T", "body": "B"},
        notifier=lambda t, b: {"ok": False},
    )
    assert out["via"] == "notify-send" and out["notified"] is True


def test_desktop_notification_windows_toast(monkeypatch):
    import coworker.workflows.native as native

    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    assert native._desktop_notification("t", "b") == "powershell-toast"
