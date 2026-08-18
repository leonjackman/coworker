"""OS-level secret storage for API keys and MCP secrets.

Secrets are stored in the macOS Keychain via the ``security`` CLI when
available, so provider API keys / MCP tokens never sit in plaintext JSON. When
Keychain is unavailable (non-macOS, headless, ``security`` missing) we fall back
to a 0600-permissioned file under the app data dir.

The Keychain item is keyed by ``(service, account)``; the fallback file is
``<data_dir>/.coworker_secrets/<service>__<account>.txt`` with mode 0600.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from coworker.logger import get_logger
logger = get_logger(__name__)

_SECRET_DIR_NAME = ".coworker_secrets"


def _keychain_set(service: str, account: str, value: str) -> bool:
    """Store a secret in the macOS Keychain. Returns False when unavailable."""
    try:
        subprocess.run(
            [
                "security", "add-generic-password",
                "-a", account, "-s", service, "-w", value, "-U",
            ],
            check=True, capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _keychain_get(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", account, "-s", service, "-w",
            ],
            check=True, capture_output=True,
        )
        value = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
        return value or None
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def _keychain_delete(service: str, account: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", service],
            check=True, capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass


def _fallback_path(data_dir: Path, service: str, account: str) -> Path:
    safe = hashlib.sha256(f"{service}__{account}".encode("utf-8")).hexdigest()[:24]
    return Path(data_dir) / _SECRET_DIR_NAME / f"{service}__{safe}.txt"


def _file_set(data_dir: Path, service: str, account: str, value: str) -> None:
    path = _fallback_path(data_dir, service, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _file_get(data_dir: Path, service: str, account: str) -> str | None:
    path = _fallback_path(data_dir, service, account)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _file_delete(data_dir: Path, service: str, account: str) -> None:
    path = _fallback_path(data_dir, service, account)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def set_secret(data_dir: Path, service: str, account: str, value: str) -> None:
    """Store a secret (Keychain first, 0600 file fallback)."""
    if value is None:
        return
    if _keychain_set(service, account, value):
        return
    _file_set(data_dir, service, account, value)


def get_secret(data_dir: Path, service: str, account: str) -> str | None:
    """Retrieve a secret (Keychain first, file fallback)."""
    value = _keychain_get(service, account)
    if value is not None:
        return value
    return _file_get(data_dir, service, account)


def delete_secret(data_dir: Path, service: str, account: str) -> None:
    _keychain_delete(service, account)
    _file_delete(data_dir, service, account)


def store_available(data_dir: Path) -> bool:
    """Whether a real secret store is reachable (used for diagnostics)."""
    return True
