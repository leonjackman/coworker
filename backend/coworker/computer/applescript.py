"""Gated AppleScript tool for driving macOS apps (Pages / Numbers / Keynote).

UI automation (screenshots + coordinate/ref clicking) is fragile for document
apps: their Accessibility tree exposes little, refs go stale while popovers
animate, and clicks can silently miss (the session that motivated this clicked
Pages' Save button, never noticed the file went to iCloud, and reported success).
AppleScript is the supported, deterministic API for these apps.

Safety: the script is fed to ``osascript`` over STDIN (never a shell string), so
quoting/encoding cannot break it, and a hard subprocess timeout prevents a
script (or a stuck Automation permission prompt) from hanging the turn. The
tool is only built on macOS and, like the other mutating computer tools, is
HITL-gated (see ``agent/middleware/hitl.py``).
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

_DESCRIPTION = """Run an AppleScript on macOS to drive a native app deterministically.

Use this instead of click/type UI automation for Pages, Numbers, Keynote and
other scriptable apps. The script is executed with `osascript` (STDIN), so
multi-line scripts and non-ASCII text are safe. Returns JSON with return_code,
stdout, stderr and timed_out.

Verified recipes (adapt the app name / values):
- Pages — read the front document's body text:
    tell application "Pages" to return body text of front document
- Pages — set the body text (write the WHOLE document at once; Pages' body
  text is an object, so `set text of body text 1 to ...` OR `set body text of
  front document to ...` — use `body text of front document`):
    tell application "Pages"
        set body text of front document to "line 1\\nline 2"
    end tell
- Pages — export the front document to a file (avoids the Save-dialog dance):
    tell application "Pages"
        set d to front document
        export d to POSIX file "/Users/me/Desktop/out.pdf" as PDF
    end tell
- Numbers — set a cell value:
    tell application "Numbers" to tell table 1 of sheet 1 of front document to set value of cell "A1" to "hello"
- Keynote — list slide titles:
    tell application "Keynote" to return name of every slide of front document

If it fails with a permissions error, macOS is blocking Automation: the user
must allow CoWorker under System Settings > Privacy & Security > Automation.
"""


def build_applescript_tool() -> Any | None:
    """Return the ``run_applescript`` tool on macOS, else ``None``."""
    if sys.platform != "darwin":
        return None
    from langchain_core.tools import tool

    @tool
    def run_applescript(script: str, timeout_seconds: int = 60) -> str:
        """Run an AppleScript on macOS to drive a native app (Pages/Numbers/Keynote)."""
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
                ["osascript", "-"],
                input=script_text,
                capture_output=True,
                text=True,
                timeout=safe_timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "error": f"AppleScript timed out after {safe_timeout}s (a stuck app or an unanswerable "
                    "Automation permission prompt can cause this).",
                    "error_code": "timeout",
                    "timed_out": True,
                },
                ensure_ascii=False,
            )
        except FileNotFoundError:
            return json.dumps({"error": "osascript not found", "error_code": "unavailable"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - a tool must never break the turn
            logger.warning("run_applescript failed: %s", exc)
            return json.dumps({"error": str(exc)[:400], "error_code": "applescript_error"}, ensure_ascii=False)
        stderr = completed.stderr or ""
        permission_hint = ""
        if completed.returncode != 0 and ("not allowed" in stderr.lower() or "-1743" in stderr or "-600" in stderr):
            permission_hint = (
                " macOS is blocking Automation — allow CoWorker in System Settings > "
                "Privacy & Security > Automation."
            )
        return json.dumps(
            {
                "return_code": completed.returncode,
                "stdout": (completed.stdout or "")[:MAX_OUTPUT_CHARS],
                "stderr": stderr[:MAX_OUTPUT_CHARS],
                "timed_out": False,
                "hint": permission_hint.strip() or None,
            },
            ensure_ascii=False,
        )

    # Attach the (long) usage description to the generated tool schema.
    run_applescript.description = _DESCRIPTION
    return run_applescript


def resolve_applescript_tools() -> list[Any]:
    """Return ``[run_applescript]`` on macOS, else ``[]``.

    Independent of the OS Computer Use master switch: this is the deterministic
    route for scriptable native apps (Pages/Numbers/Keynote/Finder/Music) and is
    gated only by the execute phase + HITL like other mutating tools.
    """
    tool = build_applescript_tool()
    return [tool] if tool is not None else []


def applescript_capability_line() -> str:
    """System-prompt fragment advertising the AppleScript tool (macOS only)."""
    if sys.platform != "darwin":
        return ""
    return (
        " NATIVE APP AUTOMATION: run_applescript is available for scriptable macOS apps (Pages, Numbers, "
        "Keynote, Finder, Music, …). Prefer it over click/type UI automation for these apps — it drives the "
        "app's real API deterministically. Feed it the whole script; do not shell-quote it. It is execute-phase "
        "only and HITL-gated like other mutating tools."
    )
