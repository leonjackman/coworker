"""Per-OS native-app scripting surface (the deterministic alternative to UIA).

One programmatic app-automation tool per platform:
  * macOS   → ``run_applescript`` (AppleScript via ``osascript``)
  * Windows → ``run_powershell`` (PowerShell, incl. COM automation)
  * Linux   → none (``run_command`` + the Unix allowlist covers it)

Both tools are decoupled from the OS Computer Use master switch: they are mounted
whenever the platform supports them, execute-phase only, and HITL-gated like the
other mutating tools. Delegated worker sub-agents never receive them.
"""

from __future__ import annotations

import sys
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)


def resolve_native_script_tools() -> list[Any]:
    """Return the platform's native-app scripting tool (or ``[]``)."""
    try:
        if sys.platform == "darwin":
            from .applescript import resolve_applescript_tools

            return resolve_applescript_tools()
        if sys.platform == "win32":
            from .powershell import resolve_powershell_tools

            return resolve_powershell_tools()
    except Exception:  # noqa: BLE001 - a missing tool must never break a turn
        logger.warning("native-script tool unavailable", exc_info=True)
    return []


def native_script_capability_line() -> str:
    """System-prompt fragment advertising the platform's native-script tool."""
    try:
        if sys.platform == "darwin":
            from .applescript import applescript_capability_line

            return applescript_capability_line()
        if sys.platform == "win32":
            from .powershell import powershell_capability_line

            return powershell_capability_line()
    except Exception:  # noqa: BLE001 - capability line is best-effort
        logger.warning("native-script capability line unavailable", exc_info=True)
    return ""
