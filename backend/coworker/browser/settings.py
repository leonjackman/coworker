"""Non-secret browser preferences, merged into ``.coworker_settings.json``.

These are read/written through the browser API and mirrored into the Electron
main process at startup so download/permission handlers can act synchronously.
Passwords are never persisted here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from coworker.atomicio import atomic_write_json

SETTINGS_FILENAME = ".coworker_settings.json"
BLOCK_KEY = "browser"

DEFAULTS: dict[str, Any] = {
    # Default OFF: closing the app drops all browser tabs and a fresh launch
    # does not reopen the embedded browser. Users can opt in via settings.
    "restore_tabs": False,
    "download_dir": "",
    "ask_where_to_save": False,
    "password_manager_enabled": False,
    "password_autofill": True,
    "permissions_prompt": True,
}

_BOOL_KEYS = {
    "restore_tabs",
    "ask_where_to_save",
    "password_manager_enabled",
    "password_autofill",
    "permissions_prompt",
}


def _settings_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / SETTINGS_FILENAME


def _read_file(data_dir: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(_settings_path(data_dir).read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - missing/corrupt file falls back to defaults
        return {}


def read_browser_settings(data_dir: Path | str) -> dict[str, Any]:
    """Merged browser settings (defaults overlaid with any persisted values)."""
    raw = _read_file(data_dir).get(BLOCK_KEY)
    stored = raw if isinstance(raw, dict) else {}
    merged = dict(DEFAULTS)
    for key in _BOOL_KEYS:
        if isinstance(stored.get(key), bool):
            merged[key] = stored[key]
    if isinstance(stored.get("download_dir"), str):
        merged["download_dir"] = stored["download_dir"]
    return merged


def write_browser_settings(data_dir: Path | str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial update and persist it, returning the merged result."""
    data = _read_file(data_dir)
    current = data.get(BLOCK_KEY)
    merged = dict(current) if isinstance(current, dict) else {}
    for key in _BOOL_KEYS:
        if key in patch and isinstance(patch[key], bool):
            merged[key] = patch[key]
    if "download_dir" in patch and isinstance(patch["download_dir"], str):
        merged["download_dir"] = patch["download_dir"]
    data[BLOCK_KEY] = merged
    atomic_write_json(_settings_path(data_dir), data)
    return read_browser_settings(data_dir)
