"""Embedded browser bridge client + agent tool.

The desktop app renders a real Chromium view (Electron ``<webview>``) in the
right-side panel. The user drives it manually; the AI agent drives it from
here over a loopback HTTP bridge that Electron registers with the backend at
startup (``POST /api/browser/bridge``).

No new Python dependencies: the bridge is called with ``httpx`` (already a
backend dependency). When the bridge is not registered (e.g. running the
backend headless/web mode) the tool reports a clean ``browser_unavailable``
result and the agent tells the user the browser only exists in the desktop app.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

_SETTINGS_FILENAME = ".coworker_settings.json"
_BRIDGE_KEY = "browser_bridge"

_BASE_URL = "http://127.0.0.1"

_TIMEOUT = 15.0
#: How long a bridge info discovery is cached before being re-read.
_CACHE_TTL = 5.0


@dataclass(frozen=True)
class BridgeInfo:
    port: int
    token: str

    @property
    def base_url(self) -> str:
        return f"{_BASE_URL}:{self.port}"

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


def _settings_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / _SETTINGS_FILENAME


def _read_settings_file(data_dir: Path | str) -> dict[str, Any]:
    try:
        raw = _settings_path(data_dir).read_text(encoding="utf-8") or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupt/missing file falls back to empty
        return {}


def _write_settings_file(data_dir: Path | str, data: dict[str, Any]) -> None:
    from coworker.atomicio import atomic_write_json

    atomic_write_json(_settings_path(data_dir), data)


def read_browser_bridge(data_dir: Path | str) -> BridgeInfo | None:
    """Load the bridge info Electron registered at startup (may be absent)."""
    return BridgeInfo.from_dict(_read_settings_file(data_dir).get(_BRIDGE_KEY))


def write_browser_bridge(data_dir: Path | str, port: int, token: str) -> dict[str, Any]:
    """Persist bridge info (called by Electron main via ``POST /api/browser/bridge``)."""
    data = _read_settings_file(data_dir)
    data[_BRIDGE_KEY] = {"port": int(port), "token": str(token)}
    _write_settings_file(data_dir, data)
    return {"ok": True}


class BridgeClient:
    """Thin httpx client for the Electron loopback browser bridge."""

    def __init__(self, data_dir: Path | str | None, *, cache_ttl: float = _CACHE_TTL):
        self.data_dir = data_dir
        self._cache: tuple[float, BridgeInfo | None] | None = None
        self._cache_ttl = cache_ttl

    def _discover(self) -> BridgeInfo | None:
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < self._cache_ttl:
            return self._cache[1]
        info = read_browser_bridge(self.data_dir) if self.data_dir is not None else None
        self._cache = (now, info)
        return info

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        info = self._discover()
        if info is None:
            return {"error": "browser unavailable", "error_code": "browser_unavailable"}
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
                    "error_code": body.get("error") or f"http_{resp.status_code}",
                }
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True}
        except httpx.HTTPError as exc:
            # Bridge may have restarted; drop the cache so the next call rediscovers.
            self._cache = None
            return {"error": f"bridge unreachable: {exc}", "error_code": "browser_unreachable"}

    def state(self) -> dict[str, Any]:
        return self._call("GET", "/state")

    def navigate(self, url: str) -> dict[str, Any]:
        return self._call("POST", "/navigate", {"url": url})

    def reload(self) -> dict[str, Any]:
        return self._call("POST", "/reload")

    def back(self) -> dict[str, Any]:
        return self._call("POST", "/back")

    def forward(self) -> dict[str, Any]:
        return self._call("POST", "/forward")

    def screenshot(self) -> dict[str, Any]:
        return self._call("POST", "/screenshot")

    def act(self, type_: str, **kw: Any) -> dict[str, Any]:
        return self._call("POST", "/act", {"type": type_, **kw})

    def evaluate(self, expression: str) -> dict[str, Any]:
        return self._call("POST", "/evaluate", {"expression": expression})


def browser_available(data_dir: Path | str | None) -> bool:
    """True when the desktop bridge is registered and reachable."""
    return BridgeClient(data_dir).state().get("error_code") is None


def browser_capability_status(data_dir: Path | str | None) -> str:
    return "ok" if browser_available(data_dir) else "disabled"


def browser_capability_line(data_dir: Path | str | None) -> str:
    """One-line capability summary injected into the agent's system prompt."""
    if browser_capability_status(data_dir) == "ok":
        return (
            "The built-in browser is ENABLED: use the browser tool to open pages, take "
            "screenshots, click, type and scroll. The user sees the live page in the "
            "right-side browser panel."
        )
    return (
        "The built-in browser is DISABLED — you have no browser tool. It is only available "
        "in the desktop app (the right-side browser panel). If the task needs a live, "
        "interactive web page, tell the user to use the desktop app."
    )


BrowserAction = Literal[
    "navigate", "get_state", "screenshot", "click", "type", "press",
    "scroll", "back", "forward", "reload", "evaluate",
]


def _render_error(result: dict[str, Any], action: str) -> str:
    code = result.get("error_code")
    if code in ("browser_unavailable", "browser_not_attached"):
        return json.dumps(
            {
                "error": result.get("error", "browser unavailable"),
                "error_code": code,
                "hint": "Tell the user the embedded browser is only available in the desktop app "
                        "and to open the right-side Browser panel (or start the desktop app).",
            },
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


def build_browser_tool(data_dir: Path | str | None) -> Any | None:
    """Build the ``browser`` LangChain tool (``None`` when the bridge is down)."""
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    class BrowserArgs(BaseModel):
        action: BrowserAction = Field(..., description="What to do in the browser.")
        url: str = Field("", description="For 'navigate': the URL to open (http/https; scheme optional).")
        x: float = Field(0, description="For 'click': viewport X coordinate in CSS pixels.")
        y: float = Field(0, description="For 'click': viewport Y coordinate in CSS pixels.")
        text: str = Field("", description="For 'type': text to type into the focused field.")
        key: str = Field("", description="For 'press': a key name (Enter, Backspace, Tab, Escape, ArrowUp...).")
        expression: str = Field("", description="For 'evaluate': a JavaScript expression to run in the page.")
        dx: float = Field(0, description="For 'scroll': horizontal scroll delta.")
        dy: float = Field(0, description="For 'scroll': vertical scroll delta.")

    client = BridgeClient(data_dir)

    @tool(args_schema=BrowserArgs)
    def browser(
        action: str,
        url: str = "",
        x: float = 0,
        y: float = 0,
        text: str = "",
        key: str = "",
        expression: str = "",
        dx: float = 0,
        dy: float = 0,
    ) -> str:
        """Open and drive the user's embedded browser (visible live in the right panel).

        Workflow: ``navigate`` to a page, then ``screenshot`` to see it, then
        ``click``/``type``/``press``/``scroll`` to interact, then ``screenshot``
        again to verify. ``get_state`` returns the current URL/title.
        """
        try:
            if action == "navigate":
                result = client.navigate(url)
            elif action == "get_state":
                result = client.state()
            elif action == "screenshot":
                result = client.screenshot()
            elif action == "click":
                result = client.act("click", x=x, y=y)
            elif action == "type":
                result = client.act("type", text=text)
            elif action == "press":
                result = client.act("press", key=key)
            elif action == "scroll":
                result = client.act("scroll", dx=dx, dy=dy)
            elif action == "back":
                result = client.back()
            elif action == "forward":
                result = client.forward()
            elif action == "reload":
                result = client.reload()
            elif action == "evaluate":
                result = client.evaluate(expression)
            else:
                return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("browser tool failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "browser_error"}, ensure_ascii=False)
        if result.get("error_code"):
            return _render_error(result, action)
        return json.dumps(result, ensure_ascii=False)

    return browser


def resolve_browser_tool(data_dir: Path | str | None) -> Any | None:
    """Browser tool for a runtime when the desktop bridge is up, else ``None``."""
    if data_dir is None:
        return None
    if not browser_available(data_dir):
        return None
    return build_browser_tool(data_dir)
