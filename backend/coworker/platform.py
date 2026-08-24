"""Platform-aware command/tool policy for Coworker.

Centralizes OS detection and the per-platform command allowlists used by the
agent's ``run_command`` tool and the manual bottom-panel terminal. The backend
behaves identically on macOS / Linux / Windows; only the shell command
vocabulary differs (Unix tools on macOS/Linux, native PowerShell/cmd verbs on
Windows).

This module is a leaf in the import DAG (stdlib only) so it can be imported
from ``workspace``, the agent graph and the terminal handler without cycles.
Tests can force a platform via :func:`force_platform`.
"""

from __future__ import annotations

import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

#: Override for the detected platform (set by tests via :func:`force_platform`).
_ACTIVE_PLATFORM: str | None = None


def platform_tag(platform: str | None = None) -> str:
    """Return a stable platform tag: ``darwin`` | ``win32`` | ``linux``."""
    if platform is not None:
        return platform
    if _ACTIVE_PLATFORM is not None:
        return _ACTIVE_PLATFORM
    sys_platform = sys.platform.lower()
    if sys_platform.startswith("win"):
        return "win32"
    if sys_platform == "darwin":
        return "darwin"
    return "linux"


def force_platform(platform: str | None) -> None:
    """Override the detected platform for tests. Pass ``None`` to reset."""
    global _ACTIVE_PLATFORM
    _ACTIVE_PLATFORM = platform


def is_windows(platform: str | None = None) -> bool:
    return platform_tag(platform) == "win32"


def is_macos(platform: str | None = None) -> bool:
    return platform_tag(platform) == "darwin"


def is_linux(platform: str | None = None) -> bool:
    return platform_tag(platform) == "linux"


# ---------------------------------------------------------------------------
# Command allowlists (per platform)
# ---------------------------------------------------------------------------

#: Commands allowed on every platform (cross-platform toolchains).
COMMON_COMMANDS = frozenset({
    "curl", "git", "node", "npm", "npx", "pytest", "python",
})

#: Commands available on Unix-like systems (macOS + Linux).
UNIX_COMMANDS = frozenset({
    "bash", "cat", "chmod", "date", "df", "du", "echo", "file",
    "find", "grep", "head", "id", "less", "ls", "more", "pwd",
    "rg", "sed", "sh", "stat", "tail", "tar", "uname", "wc", "whoami",
    "python3",
})

#: Commands available on native Windows (cmd / PowerShell vocabulary).
WINDOWS_COMMANDS = frozenset({
    "cd", "cls", "cmd", "date", "dir", "echo", "findstr", "get-childitem",
    "get-content", "hostname", "powershell", "select-string", "systeminfo",
    "tasklist", "time", "type", "ver", "where",
})

#: Read-only commands auto-approved in supervised mode (Unix).
READ_ONLY_UNIX_COMMANDS = frozenset({
    "cat", "date", "df", "du", "echo", "file", "find", "grep", "head",
    "id", "less", "ls", "more", "pwd", "rg", "stat", "tail", "uname",
    "wc", "whoami",
})

#: Read-only commands auto-approved in supervised mode (Windows).
READ_ONLY_WINDOWS_COMMANDS = frozenset({
    "date", "dir", "echo", "findstr", "get-childitem", "get-content",
    "hostname", "select-string", "systeminfo", "tasklist", "time", "type",
    "ver", "where",
})


def allowed_commands(platform: str | None = None) -> frozenset[str]:
    """Command names the ``run_command`` tool may execute on this platform."""
    tag = platform_tag(platform)
    if tag == "win32":
        return COMMON_COMMANDS | WINDOWS_COMMANDS
    return COMMON_COMMANDS | UNIX_COMMANDS


def read_only_commands(platform: str | None = None) -> frozenset[str]:
    """Read-only commands auto-approved in supervised mode on this platform."""
    tag = platform_tag(platform)
    if tag == "win32":
        return READ_ONLY_WINDOWS_COMMANDS
    return READ_ONLY_UNIX_COMMANDS


# ---------------------------------------------------------------------------
# Executable resolution (Windows PATHEXT)
# ---------------------------------------------------------------------------

#: PATHEXT fallback used when the env var is unset.
_WINDOWS_PATHEXT = (".COM", ".EXE", ".BAT", ".CMD")


def _pathext() -> tuple[str, ...]:
    raw = os.environ.get("PATHEXT", "")
    exts = tuple(ext.strip().upper() for ext in raw.split(";") if ext.strip())
    return exts or _WINDOWS_PATHEXT


def resolve_command_name(name: str, platform: str | None = None) -> str:
    """Resolve a bare command name to an executable on this platform.

    On Windows this appends each PATHEXT extension (``.exe`` / ``.cmd`` /
    ``.bat``) and returns the first candidate found on PATH, so ``python``
    becomes ``python.exe`` and ``npm`` becomes ``npm.cmd``. On Unix the bare
    name is returned unchanged (subprocess resolves it via PATH).
    """
    name = name.strip()
    if not name:
        return name
    if not is_windows(platform):
        return name
    if os.path.splitext(name)[1].upper() in _pathext():
        return name
    for ext in _pathext():
        candidate = name + ext
        if shutil.which(candidate) is not None:
            return candidate
    return name


# ---------------------------------------------------------------------------
# Shell selection
# ---------------------------------------------------------------------------

def default_shell(platform: str | None = None) -> str:
    """Pick an interactive shell for the current platform.

    Windows prefers PowerShell (falls back to ``%COMSPEC%`` / cmd.exe). Unix
    honours the ``SHELL`` env var and falls back to bash on Linux and zsh on
    macOS.
    """
    tag = platform_tag(platform)
    if tag == "win32":
        if shutil.which("powershell.exe"):
            return "powershell.exe"
        comspec = os.environ.get("COMSPEC")
        if comspec:
            return comspec
        return "cmd.exe"
    shell = os.environ.get("SHELL")
    if shell:
        return shell
    return "/bin/bash" if tag == "linux" else "/bin/zsh"


# ---------------------------------------------------------------------------
# LLM-facing hints
# ---------------------------------------------------------------------------

def command_hint(platform: str | None = None) -> str:
    """List the allowlisted commands, for the ``run_command`` tool description."""
    tag = platform_tag(platform)
    return "Allowed commands: " + ", ".join(sorted(allowed_commands(tag))) + "."


def platform_hint(platform: str | None = None) -> str:
    """One line describing the OS for the model's system prompt."""
    tag = platform_tag(platform)
    labels = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}
    label = labels.get(tag, tag)
    return (
        f"Current platform: {label} ({tag}). "
        "The run_command tool only executes an allowlisted command set — see its tool description."
    )


__all__ = [
    "COMMON_COMMANDS",
    "UNIX_COMMANDS",
    "WINDOWS_COMMANDS",
    "READ_ONLY_UNIX_COMMANDS",
    "READ_ONLY_WINDOWS_COMMANDS",
    "allowed_commands",
    "command_hint",
    "default_shell",
    "force_platform",
    "is_linux",
    "is_macos",
    "is_windows",
    "platform_hint",
    "platform_tag",
    "read_only_commands",
    "resolve_command_name",
]
