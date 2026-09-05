"""Shared plumbing for the loopback Electron bridges (browser + computer use).

The Electron desktop app exposes two independent loopback HTTP bridges to the
Python backend — one for the embedded ``<webview>`` (browser) and one for
OS-level desktop control (computer). Both follow the identical contract:

* Electron binds ``127.0.0.1`` with a random port + bearer token and persists
  them through ``POST /api/<surface>/bridge`` into ``.coworker_settings.json``
  under a per-surface key;
* the Python side reads that info back, then calls the bridge over ``httpx``
  with a ``Bearer`` token.

This module holds the pieces both clients share so the two surfaces never
drift: image data-URL validation + screenshot persistence (binary bytes must
never ride inside tool-result *text* — a truncated data URL is a corrupted
image that only burns ~36k tokens), the ``BridgeInfo`` dataclass and the
settings-file read/write helpers.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_SETTINGS_FILENAME = ".coworker_settings.json"

_DATA_URL_SPLIT_RE = re.compile(r"^data:(?P<mime>[\w.+:-]+/[\w.+:-]+);base64,(?P<body>.*)$", re.S)
_SCREENSHOTS_DIRNAME = "screenshots"

#: Magic bytes of the image formats we accept for screenshots (dependency-free
#: validation — no PIL in the backend). A "data:image/jpeg;base64," URL with an
#: empty body (hidden webview / collapsed panel / missing screen-recording
#: permission) would otherwise be forwarded to a vision provider and 400 with
#: "cannot identify image file".
_IMAGE_MAGIC: list[tuple[str, bytes]] = [
    ("jpeg", b"\xff\xd8\xff"),
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("gif", b"GIF8"),
    ("webp", b"RIFF"),
]


def looks_like_image_data_url(data_url: str) -> bool:
    """True when ``data_url`` carries a non-empty base64 body that decodes to
    bytes with a recognizable image header (JPEG/PNG/GIF/WebP)."""
    match = _DATA_URL_SPLIT_RE.match(data_url or "")
    if not match:
        return False
    body = match.group("body")
    if not body:
        return False
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return False
    if not raw:
        return False
    return any(raw.startswith(magic) for _, magic in _IMAGE_MAGIC)


def screenshots_dir_for(data_dir: Path | str | None, session_id: str, surface: str = "screenshots") -> Path | None:
    """Per-session directory holding externalized screenshots (cleaned up with
    the session). Returns ``None`` when there is no data dir."""
    if data_dir is None:
        return None
    safe_session = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_") or "default"
    return Path(data_dir) / surface / safe_session


def save_screenshot(data_url: str, data_dir: Path | str | None, session_id: str, surface: str = "screenshots") -> str | None:
    """Persist a base64 data URL to disk and return the path (None on failure).

    Screenshots are binary; the model can never use base64 *text* (a truncated
    data URL is a corrupted image), so for non-vision providers the shot is
    externalized and referenced by path instead of burning ~36k tokens.
    """
    match = _DATA_URL_SPLIT_RE.match(data_url or "")
    if not match:
        return None
    try:
        raw = base64.b64decode(match.group("body"), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    target_dir = screenshots_dir_for(data_dir, session_id, surface=surface)
    if target_dir is None:
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        ext = "jpg" if "jpeg" in match.group("mime") else "png"
        target = target_dir / f"shot-{int(time.time() * 1000)}.{ext}"
        target.write_bytes(raw)
        return str(target)
    except (OSError, binascii.Error, ValueError):
        return None


@dataclass(frozen=True)
class BridgeInfo:
    port: int
    token: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_dict(cls, data: Any) -> "BridgeInfo | None":
        if not isinstance(data, dict):
            return None
        try:
            port = int(data.get("port"))
            token = str(data.get("token") or "")
        except (TypeError, ValueError):
            return None
        if port <= 0 or not token:
            return None
        return cls(port=port, token=token)


def settings_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / _SETTINGS_FILENAME


def read_settings_file(data_dir: Path | str) -> dict[str, Any]:
    try:
        raw = settings_path(data_dir).read_text(encoding="utf-8") or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupt/missing file falls back to empty
        return {}


def write_settings_file(data_dir: Path | str, data: dict[str, Any]) -> None:
    from coworker.atomicio import atomic_write_json

    atomic_write_json(settings_path(data_dir), data)


def read_bridge_info(data_dir: Path | str, key: str) -> BridgeInfo | None:
    """Load the bridge info Electron registered at startup for ``key``."""
    return BridgeInfo.from_dict(read_settings_file(data_dir).get(key))


def write_bridge_info(data_dir: Path | str, key: str, port: int, token: str) -> dict[str, Any]:
    """Persist bridge info (called by Electron main via ``POST /api/<s>/bridge``)."""
    data = read_settings_file(data_dir)
    data[key] = {"port": int(port), "token": str(token)}
    write_settings_file(data_dir, data)
    return {"ok": True}


#: How long bridge-info discovery is cached before being re-read.
CACHE_TTL = 5.0

_TIMEOUT = 15.0


class LoopbackBridgeClient:
    """Thin httpx client for an Electron loopback bridge (any surface).

    ``surface`` names the capability (``"browser"`` / ``"computer"``) and feeds
    the ``*_unavailable`` / ``*_unreachable`` error codes the tool layer maps
    into human guidance.
    """

    def __init__(self, data_dir: Path | str | None, *, key: str, surface: str, cache_ttl: float = CACHE_TTL):
        self.data_dir = data_dir
        self.key = key
        self.surface = surface
        self._cache: tuple[float, BridgeInfo | None] | None = None
        self._cache_ttl = cache_ttl

    def _discover(self) -> BridgeInfo | None:
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < self._cache_ttl:
            return self._cache[1]
        info = read_bridge_info(self.data_dir, self.key) if self.data_dir is not None else None
        self._cache = (now, info)
        return info

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        surface = self.surface
        info = self._discover()
        if info is None:
            return {"error": f"{surface} unavailable", "error_code": f"{surface}_unavailable"}
        url = f"{info.base_url}{path}"
        headers = {"Authorization": f"Bearer {info.token}"}
        try:
            if method == "GET":
                resp = httpx.get(url, headers=headers, timeout=_TIMEOUT)
            else:
                resp = httpx.post(url, json=payload or {}, headers=headers, timeout=_TIMEOUT)
            if resp.status_code >= 400:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return {
                    "error": body.get("error") or f"bridge returned {resp.status_code}",
                    "error_code": body.get("error_code") or body.get("error") or f"http_{resp.status_code}",
                    **({"reason": body["reason"]} if isinstance(body, dict) and body.get("reason") else {}),
                }
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True}
        except httpx.HTTPError as exc:
            # Bridge may have restarted; drop the cache so the next call rediscovers.
            self._cache = None
            return {"error": f"bridge unreachable: {exc}", "error_code": f"{surface}_unreachable"}


def bridge_available(data_dir: Path | str | None, *, key: str, surface: str) -> bool:
    """True when the given bridge surface is registered and reachable."""
    return LoopbackBridgeClient(data_dir, key=key, surface=surface)._call("GET", "/state").get("error_code") is None


__all__ = [
    "BridgeInfo",
    "CACHE_TTL",
    "LoopbackBridgeClient",
    "bridge_available",
    "looks_like_image_data_url",
    "read_bridge_info",
    "read_settings_file",
    "save_screenshot",
    "screenshots_dir_for",
    "settings_path",
    "write_bridge_info",
    "write_settings_file",
]
