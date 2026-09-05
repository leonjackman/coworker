"""Feature-flag component for the OS-level Computer Use capability.

Computer use lets the agent see and drive the user's real desktop (screen
capture + global mouse/keyboard injection via the Electron desktop bridge).
Because it can act OUTSIDE the workspace on any app on the machine, it is
gated behind a master switch that defaults to OFF — the user must explicitly
enable it in Settings:

* user-facing: toggled in the Settings page, persisted in
  ``.coworker_settings.json`` under ``computer_use_enabled`` (default OFF);
* code-level bypass: ``COWORKER_COMPUTER_ENABLED`` env var wins over the
  persisted toggle — set ``1``/``true`` to force-enable regardless of the UI
  (useful for tests and power users who cannot reach the Settings page).

When disabled (or when the Electron desktop bridge is not registered, e.g.
backend-only / web mode) the computer tools are never mounted, so the model
never sees them and cannot attempt to operate the OS.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from coworker.atomicio import atomic_write_text

_ENV_VAR = "COWORKER_COMPUTER_ENABLED"
_SETTING_KEY = "computer_use_enabled"

# Values that mean "off". Anything else (incl. empty/unset) means on.
_FALSY = {"0", "false", "no", "off"}


def _default_settings_file() -> str:
    default_data_dir = Path.home() / "Library" / "Application Support" / "Coworker"
    data_dir = Path(os.getenv("COWORKER_DATA_DIR", str(default_data_dir))).expanduser().resolve()
    return str(data_dir / ".coworker_settings.json")


class ComputerFeature:
    """Encapsulates whether the OS-level computer-use capability is enabled."""

    def __init__(self, settings_file: str | None = None, default_enabled: bool = False):
        self._settings_file = Path(settings_file or _default_settings_file())
        self._default_enabled = default_enabled

    def _env_override(self) -> bool | None:
        raw = os.getenv(_ENV_VAR, "").strip()
        if not raw:
            return None
        return raw.lower() not in _FALSY

    def is_enabled(self) -> bool:
        """True when computer use is active.

        Precedence: ``COWORKER_COMPUTER_ENABLED`` env (code-level bypass) >
        persisted user setting > product default (off).
        """
        env = self._env_override()
        if env is not None:
            return env
        try:
            data = json.loads(self._settings_file.read_text() or "{}")
            return bool(data.get(_SETTING_KEY, self._default_enabled))
        except Exception:
            return self._default_enabled

    def set_enabled(self, enabled: bool) -> bool:
        """Persist the user toggle and return the effective state."""
        try:
            data = json.loads(self._settings_file.read_text() or "{}")
        except Exception:
            data = {}
        data[_SETTING_KEY] = bool(enabled)
        atomic_write_text(self._settings_file, json.dumps(data, ensure_ascii=False))
        return self.is_enabled()


# Module-level singleton — the single instance main.py and graph.py gate on.
computer_feature = ComputerFeature()
