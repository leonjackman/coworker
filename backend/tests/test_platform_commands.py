"""Tests for the platform-aware command/tool policy (coworker.platform)."""

import pytest

from coworker.platform import (
    COMMON_COMMANDS,
    UNIX_COMMANDS,
    WINDOWS_COMMANDS,
    allowed_commands,
    command_hint,
    default_shell,
    force_platform,
    platform_hint,
    platform_tag,
    read_only_commands,
    resolve_command_name,
)
from coworker.workspace import ALLOWED_COMMANDS, READ_ONLY_COMMANDS


@pytest.fixture(autouse=True)
def _reset_platform():
    force_platform(None)
    yield
    force_platform(None)


class TestPlatformTag:
    def test_forced_override(self):
        assert platform_tag("win32") == "win32"
        assert platform_tag("darwin") == "darwin"
        assert platform_tag("linux") == "linux"

    def test_force_platform(self):
        force_platform("win32")
        assert platform_tag() == "win32"


class TestAllowedCommands:
    def test_common_in_all(self):
        for tag in ("darwin", "win32", "linux"):
            cmds = allowed_commands(tag)
            for name in COMMON_COMMANDS:
                assert name in cmds

    def test_unix_tools_not_on_windows(self):
        win = allowed_commands("win32")
        for name in ("ls", "cat", "rg", "sed", "chmod", "find", "python3"):
            assert name in UNIX_COMMANDS
            assert name not in win

    def test_windows_tools_not_on_unix(self):
        unix = allowed_commands("darwin") | allowed_commands("linux")
        for name in ("dir", "type", "findstr", "where", "tasklist", "get-childitem"):
            assert name in WINDOWS_COMMANDS
            assert name not in unix

    def test_linux_and_darwin_share_unix_set(self):
        assert allowed_commands("linux") == allowed_commands("darwin")

    def test_workspace_allowlist_matches_platform(self):
        assert ALLOWED_COMMANDS == allowed_commands()


class TestReadOnlyCommands:
    def test_unix_readonly(self):
        ro = read_only_commands("darwin")
        assert "ls" in ro and "cat" in ro and "rg" in ro
        assert "dir" not in ro

    def test_windows_readonly(self):
        ro = read_only_commands("win32")
        assert "dir" in ro and "type" in ro and "findstr" in ro
        assert "ls" not in ro and "sed" not in ro

    def test_workspace_readonly_matches_platform(self):
        assert READ_ONLY_COMMANDS == read_only_commands()

    def test_readonly_subsets_allowed(self):
        for tag in ("darwin", "win32", "linux"):
            assert read_only_commands(tag) <= allowed_commands(tag)


class TestResolveCommandName:
    def test_unix_returns_bare_name(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.is_windows", lambda platform=None: False)
        assert resolve_command_name("python") == "python"

    def test_windows_pathext_lookup(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.is_windows", lambda platform=None: True)

        def fake_which(candidate):
            return candidate if candidate.lower() in ("python.exe", "npm.cmd") else None

        monkeypatch.setattr("coworker.platform.shutil.which", fake_which)
        monkeypatch.setattr("coworker.platform._pathext", lambda: (".EXE", ".CMD"))
        assert resolve_command_name("python").lower() == "python.exe"
        assert resolve_command_name("npm").lower() == "npm.cmd"

    def test_windows_keeps_existing_suffix(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.is_windows", lambda platform=None: True)
        monkeypatch.setattr("coworker.platform._pathext", lambda: (".EXE", ".CMD"))
        monkeypatch.setattr("coworker.platform.shutil.which", lambda c: None)
        assert resolve_command_name("tool.exe") == "tool.exe"

    def test_windows_unresolvable_returns_bare(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.is_windows", lambda platform=None: True)
        monkeypatch.setattr("coworker.platform._pathext", lambda: (".EXE", ".CMD"))
        monkeypatch.setattr("coworker.platform.shutil.which", lambda c: None)
        assert resolve_command_name("ghost") == "ghost"


class TestDefaultShell:
    def test_unix_uses_shell_env(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.is_windows", lambda platform=None: False)
        monkeypatch.setenv("SHELL", "/bin/fish")
        assert default_shell() == "/bin/fish"

    def test_linux_fallback_bash(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.platform_tag", lambda platform=None: "linux")
        monkeypatch.delenv("SHELL", raising=False)
        assert default_shell() == "/bin/bash"

    def test_darwin_fallback_zsh(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.platform_tag", lambda platform=None: "darwin")
        monkeypatch.delenv("SHELL", raising=False)
        assert default_shell() == "/bin/zsh"

    def test_windows_prefers_powershell(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.platform_tag", lambda platform=None: "win32")
        monkeypatch.setattr("coworker.platform.shutil.which", lambda c: "powershell.exe" if c == "powershell.exe" else None)
        assert default_shell() == "powershell.exe"

    def test_windows_falls_back_to_comspec(self, monkeypatch):
        monkeypatch.setattr("coworker.platform.platform_tag", lambda platform=None: "win32")
        monkeypatch.setattr("coworker.platform.shutil.which", lambda c: None)
        monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
        assert default_shell().lower().endswith("cmd.exe")


class TestHints:
    def test_platform_hint_contains_os_label(self):
        assert "Windows" in platform_hint("win32")
        assert "macOS" in platform_hint("darwin")
        assert "Linux" in platform_hint("linux")

    def test_command_hint_lists_allowlist(self):
        hint = command_hint("win32")
        listed = hint[len("Allowed commands: "):].strip().strip(".").split(", ")
        assert "dir" in listed
        assert "ls" not in listed
        assert hint.startswith("Allowed commands:")
