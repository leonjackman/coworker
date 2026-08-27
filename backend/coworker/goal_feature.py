"""Feature-flag component for the persistent-goal (多輪續跑) capability.

The goal capability — the ``/goal`` command, the multi-round continuation loop
and the ``update_goal`` / ``get_goal`` tools — is encapsulated behind this single
gate so it can be switched off as a unit:

* user-facing: toggled in the Settings page, persisted in
  ``.coworker_settings.json`` under ``goal_enabled`` (default on);
* code-level bypass: ``COWORKER_GOAL_ENABLED`` env var wins over the persisted
  toggle — set ``0``/``false``/``off`` to hard-disable regardless of the UI.

When disabled, new goals cannot be set/resumed, the continuation loop degrades
to a single turn and the goal tools are never mounted, so the model never sees
the goal continuation prompts. This is the A/B switch used to check whether the
goal prompts cause model degradation (降智).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from coworker.atomicio import atomic_write_text

_ENV_VAR = "COWORKER_GOAL_ENABLED"
_SETTING_KEY = "goal_enabled"

# Values that mean "off". Anything else (incl. empty/unset) means on.
_FALSY = {"0", "false", "no", "off"}


def _default_settings_file() -> str:
    default_data_dir = Path.home() / "Library" / "Application Support" / "Coworker"
    data_dir = Path(os.getenv("COWORKER_DATA_DIR", str(default_data_dir))).expanduser().resolve()
    return str(data_dir / ".coworker_settings.json")


class GoalFeature:
    """Encapsulates whether the persistent-goal capability is enabled."""

    def __init__(self, settings_file: str | None = None, default_enabled: bool = True):
        self._settings_file = Path(settings_file or _default_settings_file())
        self._default_enabled = default_enabled

    def _env_override(self) -> bool | None:
        raw = os.getenv(_ENV_VAR, "").strip()
        if not raw:
            return None
        return raw.lower() not in _FALSY

    def is_enabled(self) -> bool:
        """True when the goal capability is active.

        Precedence: ``COWORKER_GOAL_ENABLED`` env (code-level bypass) >
        persisted user setting > product default.
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


# Module-level singleton — the single instance both main.py and graph.py gate on.
goal_feature = GoalFeature()
