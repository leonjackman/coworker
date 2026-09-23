"""Regression tests for run_command string → argv normalization.

A crucial bug: a bare compound shell string was ``shlex.split`` and executed
with ``shell=False``, so ``;`` / ``&&`` / pipes became literal argv for the FIRST
program. A real incident sent

    pkill -f "vite.js preview"; sleep 1; sh -c "…"

which became ``pkill -f "vite.js preview;" sleep "1;" sh -c …`` — ``pkill`` then
treated ``sh`` (and other tokens) as extra ``-f`` patterns and killed unrelated
Electron processes system-wide. Compound strings must go through the shell.
"""

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker import platform as p  # noqa: E402


def test_list_is_used_verbatim():
    argv = ["git", "status", "--short"]
    assert p.normalize_command(argv, "darwin") == argv


def test_simple_string_shlex_split():
    assert p.normalize_command("git status --short", "darwin") == ["git", "status", "--short"]
    assert p.normalize_command('git commit -m "hello world"', "darwin") == [
        "git",
        "commit",
        "-m",
        "hello world",
    ]


def test_compound_string_runs_through_shell():
    raw = 'pkill -f "vite.js preview"; sleep 1; sh -c "echo hi"'
    argv = p.normalize_command(raw, "darwin")
    # Must NOT be shlex-split (which would hand pkill the extra patterns
    # `sh`, `sleep`, … and kill unrelated processes).
    assert argv == ["sh", "-c", raw]
    assert argv[0] != "pkill"


@pytest.mark.parametrize("raw", [
    "a && b",
    "a || b",
    "a | b",
    "a > out.txt",
    "echo $(date)",
    "echo `date`",
    "a; b",
    "echo hi\nrm -rf x",
])
def test_shell_operators_trigger_wrap(raw):
    assert p.normalize_command(raw, "darwin") == ["sh", "-c", raw]


def test_windows_uses_powershell():
    raw = "a; b"
    assert p.normalize_command(raw, "win32")[0] == "powershell"


def test_compound_command_executes_via_workspace(tmp_path):
    """End-to-end: a compound string actually runs both commands in the shell."""
    if p.is_windows():
        pytest.skip("POSIX shell test")
    from coworker.workspace import Workspace

    ws = Workspace(tmp_path)
    result = ws.run_command(p.normalize_command("echo one; echo two", "darwin"))
    assert result["return_code"] == 0, result
    assert "one" in result["stdout"] and "two" in result["stdout"]
