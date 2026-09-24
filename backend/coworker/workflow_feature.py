"""Feature flag for the workflow scheduler (cron) capability.

Cron-triggered workflows run unattended, so the capability is gated behind a
master switch that defaults to OFF:

* user-facing: toggled in Settings, persisted in ``.coworker_settings.json``
  under ``workflow_scheduler_enabled`` (default OFF);
* code-level bypass: ``COWORKER_WORKFLOW_SCHEDULER`` env var wins over the
  persisted toggle (``1``/``true`` forces on; see also the legacy
  ``scheduler_enabled`` helper).

The scheduler loop always runs (cheap, one tick/minute); it simply does nothing
while this flag is off, so toggling in Settings takes effect within a minute
without a restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from coworker.atomicio import atomic_write_text

_ENV_VAR = "COWORKER_WORKFLOW_SCHEDULER"
_SETTING_KEY = "workflow_scheduler_enabled"
_FALSY = {"0", "false", "no", "off"}


def _default_settings_file() -> str:
    default_data_dir = Path.home() / "Library" / "Application Support" / "Coworker"
    data_dir = Path(os.getenv("COWORKER_DATA_DIR", str(default_data_dir))).expanduser().resolve()
    return str(data_dir / ".coworker_settings.json")


class WorkflowFeature:
    """Encapsulates whether cron-triggered workflow runs are enabled."""

    def __init__(self, settings_file: str | None = None, default_enabled: bool = False):
        self._settings_file = Path(settings_file or _default_settings_file())
        self._default_enabled = default_enabled

    def _env_override(self) -> bool | None:
        raw = os.getenv(_ENV_VAR, "").strip()
        if not raw:
            return None
        return raw.lower() not in _FALSY

    def is_enabled(self) -> bool:
        env = self._env_override()
        if env is not None:
            return env
        try:
            data = json.loads(self._settings_file.read_text() or "{}")
            return bool(data.get(_SETTING_KEY, self._default_enabled))
        except Exception:
            return self._default_enabled

    def set_enabled(self, enabled: bool) -> bool:
        try:
            data = json.loads(self._settings_file.read_text() or "{}")
        except Exception:
            data = {}
        data[_SETTING_KEY] = bool(enabled)
        atomic_write_text(self._settings_file, json.dumps(data, ensure_ascii=False))
        return self.is_enabled()


workflow_feature = WorkflowFeature()
