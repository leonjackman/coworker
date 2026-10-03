"""Gated PowerShell tool for driving Windows apps deterministically.

Windows parity for the macOS-only ``run_applescript``: UI automation is fragile
for document/office apps, but they are scriptable through COM (Excel / Word /
Outlook), the registry, WMI and the shell — the supported, deterministic API.
The script is fed to ``powershell.exe`` over STDIN (never a shell string), so
quoting/encoding cannot break it, and a hard subprocess timeout prevents a
stuck script from hanging the turn. The tool is only built on Windows and, like
the other mutating computer tools, is HITL-gated (see ``agent/middleware/hitl.py``).

Linux has no equivalent (``run_command`` + the Unix allowlist covers it).
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

MAX_SCRIPT_CHARS = 20_000
MAX_OUTPUT_CHARS = 20_000
MAX_TIMEOUT_SECONDS = 300

_DESCRIPTION = """Run a PowerShell script on Windows to drive apps / the system deterministically.

Use this instead of click/type UI automation for Office apps and system tasks.
The script is executed with `powershell.exe -NoProfile -NonInteractive -Command -`
(STDIN), so multi-line scripts and non-ASCII text are safe. Returns JSON with
return_code, stdout, stderr and timed_out.

Verified recipes (adapt the app / values):
- Excel — set a cell value (COM):
    $x = New-Object -ComObject Excel.Application
    $x.Visible = $true; $wb = $x.Workbooks.Add(); $wb.Sheets(1).Cells(1,1) = "hello"
- Word — write text into the active document:
    $w = New-Object -ComObject Word.Application
    $w.Visible = $true; $d = $w.Documents.Add(); $d.Content.Text = "hello" | Out-Null
- List processes / start an app:
    Get-Process | Select-Object -First 5 Name,Id
    Start-Process notepad.exe
- Read/write a file:
    Get-Content -Raw $env:USERPROFILE\\notes.txt
    Set-Content -Path $env:USERPROFILE\\out.txt -Value "hello"

If it fails with an access error, the action likely needs an elevated process.
"""


def build_powershell_tool() -> Any | None:
    """Return the ``run_powershell`` tool on Windows, else ``None``."""
    if sys.platform != "win32":
        return None
    from langchain_core.tools import tool

    @tool
    def run_powershell(script: str, timeout_seconds: int = 60) -> str:
        """Run a PowerShell script on Windows to drive apps / the system."""
        script_text = str(script or "")
        if not script_text.strip():
            return json.dumps({"error": "script is required", "error_code": "param_error"}, ensure_ascii=False)
        if len(script_text) > MAX_SCRIPT_CHARS:
            return json.dumps(
                {"error": f"script exceeds {MAX_SCRIPT_CHARS} chars", "error_code": "param_error"},
                ensure_ascii=False,
            )
        safe_timeout = max(1, min(int(timeout_seconds or 60), MAX_TIMEOUT_SECONDS))
        try:
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-Command", "-",
                ],
                input=script_text,
                capture_output=True,
                text=True,
                timeout=safe_timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "error": f"PowerShell timed out after {safe_timeout}s (a stuck app or an unanswerable "
                    "prompt can cause this).",
                    "error_code": "timeout",
                    "timed_out": True,
                },
                ensure_ascii=False,
            )
        except FileNotFoundError:
            return json.dumps({"error": "powershell.exe not found", "error_code": "unavailable"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - a tool must never break the turn
            logger.warning("run_powershell failed: %s", exc)
            return json.dumps({"error": str(exc)[:400], "error_code": "powershell_error"}, ensure_ascii=False)
        stderr = completed.stderr or ""
        hint = ""
        if completed.returncode != 0 and "access is denied" in stderr.lower():
            hint = " Windows blocked the action — it may require running CoWorker as administrator."
        return json.dumps(
            {
                "return_code": completed.returncode,
                "stdout": (completed.stdout or "")[:MAX_OUTPUT_CHARS],
                "stderr": stderr[:MAX_OUTPUT_CHARS],
                "timed_out": False,
                "hint": hint.strip() or None,
            },
            ensure_ascii=False,
        )

    run_powershell.description = _DESCRIPTION
    return run_powershell


def resolve_powershell_tools() -> list[Any]:
    """Return ``[run_powershell]`` on Windows, else ``[]``.

    Independent of the OS Computer Use master switch: this is the deterministic
    route for scriptable native Windows apps / the system, gated only by the
    execute phase + HITL like other mutating tools.
    """
    tool = build_powershell_tool()
    return [tool] if tool is not None else []


def powershell_capability_line() -> str:
    """System-prompt fragment advertising the PowerShell tool (Windows only)."""
    if sys.platform != "win32":
        return ""
    return (
        " NATIVE APP AUTOMATION: run_powershell is available on Windows for deterministic system/app "
        "automation (Office COM automation, the registry, WMI, the shell) — prefer it over click/type UI "
        "automation for such tasks. Feed it the whole script; do not shell-quote it. It is execute-phase "
        "only and HITL-gated like other mutating tools."
    )
