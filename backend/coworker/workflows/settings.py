"""Non-secret workflow execution settings, merged into ``.coworker_settings.json``.

Currently a single knob: what a deterministic step does when it fails and the
step declares no ``on_error`` — ``abort`` (default) or hand the step to the agent
(``agent``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from coworker.atomicio import atomic_write_json

SETTINGS_FILENAME = ".coworker_settings.json"
BLOCK_KEY = "workflows"

DEFAULTS: dict[str, Any] = {
    # "abort" = end the run as failed (default, unchanged behaviour);
    # "agent" = hand a failed step to the agent (it may fix and write the
    #           binding back). Only takes effect when an agent is available.
    "default_on_error": "abort",
}

_ALLOWED_ON_ERROR = ("abort", "agent")


def _path(data_dir: Path | str) -> Path:
    return Path(data_dir) / SETTINGS_FILENAME


def _read_file(data_dir: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(_path(data_dir).read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def read_workflow_settings(data_dir: Path | str) -> dict[str, Any]:
    raw = _read_file(data_dir).get(BLOCK_KEY)
    stored = raw if isinstance(raw, dict) else {}
    merged = dict(DEFAULTS)
    if stored.get("default_on_error") in _ALLOWED_ON_ERROR:
        merged["default_on_error"] = stored["default_on_error"]
    return merged


def write_workflow_settings(data_dir: Path | str, patch: dict[str, Any]) -> dict[str, Any]:
    data = _read_file(data_dir)
    current = data.get(BLOCK_KEY)
    merged = dict(current) if isinstance(current, dict) else {}
    if patch.get("default_on_error") in _ALLOWED_ON_ERROR:
        merged["default_on_error"] = patch["default_on_error"]
    data[BLOCK_KEY] = merged
    atomic_write_json(_path(data_dir), data)
    return read_workflow_settings(data_dir)
