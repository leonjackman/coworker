"""Regression tests for the shell-wrapper allowlist bypass (WS1).

Only ``argv[0]`` used to be validated, so ``sh -c 'osascript …'`` ran an
arbitrary program while the error text claimed the allowlist could not be
overridden. ``wrapped_program_names`` now lists every program a shell/wrapper
would actually run so ``run_command`` can reject them.
"""

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker import platform as p  # noqa: E402
from coworker.workspace import Workspace  # noqa: E402


@pytest.mark.parametrize("argv, expected", [
    (["sh", "-c", "osascript -e 'tell application \"Pages\"'"], ["osascript"]),
    (["bash", "-c", "osascript -e x"], ["osascript"]),
    (["sh", "-c", "cd /tmp && osascript -e x"], ["osascript"]),
    (["sh", "-c", "echo $(osascript -e x)"], ["osascript"]),
    (["sh", "-c", "echo `osascript -e x`"], ["osascript"]),
    (["sh", "-c", "sh -c 'osascript -e x'"], ["osascript"]),
    (["sh", "-c", "if true; then osascript -e x; fi"], ["osascript"]),
    (["sh", "-c", "for f in a b; do osascript -e x; done"], ["osascript"]),
    (["sh", "-c", "eval 'osascript -e x'"], ["osascript"]),
    (["env", "FOO=1", "osascript"], ["osascript"]),
    (["env", "-u", "FOO", "osascript"], ["osascript"]),
    (["timeout", "30", "osascript", "x"], ["osascript"]),
    (["nice", "-n", "10", "osascript"], ["osascript"]),
    (["find", ".", "-exec", "osascript", "{}", ";"], ["osascript"]),
])
def test_wrapped_programs_are_detected(argv, expected):
    assert p.wrapped_program_names(argv, "darwin") == expected


@pytest.mark.parametrize("argv", [
    ["git", "status"],
    ["sh", "-c", "echo one; echo two"],
    ["sh", "-c", "cat a | sed s/a/b/ > out"],
    ["find", ".", "-name", "*.txt"],
    ["env", "FOO=1", "git", "status"],
])
def test_allowlisted_programs_are_all_permitted(argv):
    from coworker.workspace import ALLOWED_COMMANDS
    assert all(program in ALLOWED_COMMANDS for program in p.wrapped_program_names(argv, "darwin"))


@pytest.mark.parametrize("argv", [
    ["bash", "script.sh"],
    ["bash", "-i"],
    ["sh", "-c", "bash script.sh"],
])
def test_unvalidatable_shell_invocations_flagged(argv):
    assert p.SHELL_UNVALIDATABLE in p.wrapped_program_names(argv, "darwin")


def test_non_shell_first_program_is_untouched():
    assert p.wrapped_program_names(["git", "log"], "darwin") == []


def test_workspace_rejects_shell_bypass(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="osascript"):
        ws.run_command(["sh", "-c", "osascript -e 'tell application \"Pages\" to activate'"])


def test_workspace_rejects_wrapper_bypass(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="osascript"):
        ws.run_command(["bash", "-c", "env osascript -e x"])


def test_workspace_rejects_shell_running_script_file(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "s.sh").write_text("osascript -e x\n")
    with pytest.raises(ValueError):
        ws.run_command(["bash", "s.sh"])


def test_workspace_allows_allowlisted_compound(tmp_path):
    if p.is_windows():
        pytest.skip("POSIX shell test")
    ws = Workspace(tmp_path)
    result = ws.run_command(p.normalize_command("echo one; echo two", "darwin"))
    assert result["return_code"] == 0, result
    assert "one" in result["stdout"] and "two" in result["stdout"]
