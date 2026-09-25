"""Environment fingerprint (W36).

A cheap, stable signature of the machine/runtime a workflow was recorded or
saved against. When it changes (OS upgrade, Python upgrade, different host), the
executor emits a non-fatal ``drift`` event so the UI can suggest re-recording
before trusting the replay.
"""

from __future__ import annotations

import platform
import sys


def current_fingerprint() -> str:
    return (
        f"{platform.system()}-{platform.release()}-"
        f"py{sys.version_info.major}.{sys.version_info.minor}"
    )
