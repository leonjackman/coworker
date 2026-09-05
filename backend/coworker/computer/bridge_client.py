"""OS-level computer-use bridge client + agent tools.

The desktop app exposes a real screen + a real mouse/keyboard through Electron
main (see ``electron/desktop-controller.js``). The AI agent drives it from here
over a loopback HTTP bridge that Electron registers with the backend at startup
(``POST /api/computer/bridge``) — the same contract as the embedded browser,
but a SEPARATE bridge: computer use can click/type anywhere on the user's
desktop, so it must never share a channel with a lower-privilege surface.

Two tools (split so the HITL approval layer can gate *only* the mutating one):

* ``computer_observe`` — read-only: list displays, capture a screenshot, report
  state/permissions. Safe to use from any phase once the capability is on.
* ``computer`` — mutating: click / double_click / right_click / move / drag /
  scroll / type / key / clipboard_set / paste. Gated to the execute phase and
  to per-action human approval under the default (guarded) permission.

Coordinate contract: ``computer_observe``'s screenshot returns its exact pixel
geometry (``shot``) and the display it captured (index + bounds in point
space). The model reads pixel coordinates OFF THE IMAGE IT WAS SHOWN and passes
them back to ``computer`` together with ``display`` / ``shot_width`` /
``shot_height``; Electron maps shot-space to display-point-space (aspect-ratio
preserving, exact across Retina scaling). When no shot geometry is supplied,
coordinates are interpreted as raw display point space.

No new Python dependencies: the bridge is called with ``httpx`` (already a
backend dependency). When the feature is off or the bridge is not registered
(e.g. running the backend headless/web mode) the tools are never built, so the
model never sees them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from coworker.bridge_common import (
    LoopbackBridgeClient,
    looks_like_image_data_url,
    read_bridge_info,
    save_screenshot,
    write_bridge_info,
)
from coworker.computer_feature import computer_feature

logger = logging.getLogger(__name__)

#: Settings key under which Electron registers the computer bridge.
_BRIDGE_KEY = "computer_bridge"

#: Hard cap for any single non-screenshot computer tool output (chars). A state
#: dump with every display + permissions is small; this only guards pathology.
COMPUTER_OUTPUT_MAX_CHARS = 20_000

_TRUNCATION_NOTE = "\n[content truncated by Coworker to fit context]"


def read_computer_bridge(data_dir: Path | str) -> Any:
    """Load the bridge info Electron registered at startup (may be absent)."""
    return read_bridge_info(data_dir, _BRIDGE_KEY)


def write_computer_bridge(data_dir: Path | str, port: int, token: str) -> dict[str, Any]:
    """Persist bridge info (called by Electron main via ``POST /api/computer/bridge``)."""
    return write_bridge_info(data_dir, _BRIDGE_KEY, port, token)


class ComputerClient(LoopbackBridgeClient):
    """Thin httpx client for the Electron computer-use loopback bridge."""

    def __init__(self, data_dir: Path | str | None, *, cache_ttl: float = 5.0):
        super().__init__(data_dir, key=_BRIDGE_KEY, surface="computer", cache_ttl=cache_ttl)

    def state(self) -> dict[str, Any]:
        return self._call("GET", "/state")

    def displays(self) -> dict[str, Any]:
        return self._call("POST", "/displays")

    def screenshot(self, *, display: int = 0, max_width: int = 1024, quality: int = 60) -> dict[str, Any]:
        return self._call("POST", "/screenshot", {"display": int(display), "max_width": int(max_width), "quality": int(quality)})

    def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/act", payload)

    def pause(self, paused: bool = True, reason: str = "user") -> dict[str, Any]:
        return self._call("POST", "/pause", {"paused": bool(paused), "reason": reason})

    def request_permission(self, kind: str) -> dict[str, Any]:
        """Passively trigger the OS permission prompt/pane for a TCC kind.

        ``kind`` is ``"screen"`` (Screen Recording → macOS consent alert appears
        on the real capture attempt) or ``"accessibility"`` (input → opens the
        Accessibility pane). If the user previously denied, macOS will NOT
        re-alert; the bridge opens the exact System Settings pane instead.
        """
        return self._call("POST", "/permissions/request", {"kind": str(kind)})

    def open_permission_settings(self, kind: str) -> dict[str, Any]:
        """Deep-link the user to the exact System Settings pane for a TCC kind."""
        return self._call("POST", "/permissions/open-settings", {"kind": str(kind)})


# ---------------------------------------------------------------------------
# Capability status / system-prompt hint
# ---------------------------------------------------------------------------

def computer_available(data_dir: Path | str | None) -> bool:
    """True when the desktop computer bridge is registered and reachable."""
    return ComputerClient(data_dir).state().get("error_code") is None


def computer_enabled() -> bool:
    """True when the user turned the computer-use master switch on (default OFF)."""
    return computer_feature.is_enabled()


def computer_capability_status(data_dir: Path | str | None) -> str:
    """One of ``ok`` | ``feature_off`` | ``desktop_only``."""
    if not computer_enabled():
        return "feature_off"
    if not computer_available(data_dir):
        return "desktop_only"
    return "ok"


def computer_capability_line(data_dir: Path | str | None) -> str:
    """Capability summary injected into the system prompt (4 states)."""
    status = computer_capability_status(data_dir)
    if status == "ok":
        return (
            "OS Computer Use is ENABLED: use computer_observe to list displays, check "
            "permissions and capture screenshots of the user's real desktop; use computer "
            "to click, type, drag and press keys in ANY app (Finder, dialogs, other IDEs). "
            "Coordinate contract: read pixel coordinates OFF the screenshot you were shown "
            "and pass display + shot_width/shot_height from that screenshot result back to "
            "computer. Workflow: screenshot -> decide exact coordinates -> computer act -> "
            "small wait -> screenshot again to verify. Take at most one screenshot per step "
            "and keep the default max_width; screenshots are token-expensive. If computer "
            "returns a permission error, tell the user to enable the permission and do NOT "
            "retry the same action."
        )
    if status == "feature_off":
        return (
            "OS Computer Use is OFF. computer tools are unavailable. It is a master switch "
            "in Settings (default off). If the task really needs to operate native desktop "
            "apps, tell the user to enable 'Computer Use' in Settings."
        )
    return (
        "OS Computer Use is only available in the desktop app. computer tools are "
        "unavailable here; the task may still be done with the embedded browser tool "
        "or command tools."
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

ObserveAction = Literal["state", "displays", "screenshot"]
ComputerAction = Literal[
    "click", "double_click", "right_click", "move", "drag",
    "scroll", "type", "key", "clipboard_set", "paste",
]


def _json_cap(result: dict[str, Any], limit: int = COMPUTER_OUTPUT_MAX_CHARS) -> str:
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload) > limit:
        return payload[:limit] + _TRUNCATION_NOTE
    return payload


# ── macOS TCC permission auto-request (passive trigger) ─────────────────────
# When a computer tool hits a missing Screen Recording / Accessibility
# permission, we PASSIVELY trigger the OS prompt instead of just returning an
# error: the bridge performs a real capture attempt (Screen Recording → macOS
# shows its consent alert when status is "not determined") or opens the
# Accessibility pane. If the user previously DENIED, macOS will not re-alert —
# the bridge opens the exact System Settings pane so the user can reset the TCC
# entry (toggle OFF/ON). Each kind is requested at most once per mounted toolset
# so repeated tool calls never spam System Settings.

_PERMISSION_REQUESTED = "_permission_requested"
_PERMISSION_PANES = {
    "screen_permission": ("screen", "Screen Recording"),
    "input_permission": ("accessibility", "Accessibility"),
}


def _permission_request_hint(client: Any, error_code: str) -> str:
    """Trigger the OS prompt/pane once and return a user-action hint ('' = no-op)."""
    mapping = _PERMISSION_PANES.get(error_code or "")
    if mapping is None or client is None:
        return ""
    kind, pane = mapping
    marker = getattr(client, _PERMISSION_REQUESTED, None)
    if marker is None:
        marker = set()
        try:
            client._permission_requested = marker  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ""
    if kind in marker:
        return ""
    marker.add(kind)
    try:
        resp = client.request_permission(kind)
    except Exception as exc:  # noqa: BLE001 - never break a turn on a probe
        logger.warning("auto request_permission(%s) failed: %s", kind, exc)
        return ""
    if not isinstance(resp, dict) or resp.get("error_code"):
        return ""
    status = str(resp.get("status") or "")
    if resp.get("granted") or status == "authorized":
        return f" Permission granted just now — retry the action."
    if status == "not determined":
        if kind == "screen":
            return (
                " I triggered the macOS Screen Recording prompt — click Allow for "
                "CoWorker in the system dialog, then reply 'ok' so I can retry."
            )
        return (
            f" macOS asked for Accessibility (input) access for CoWorker — allow it in the "
            f"prompt / System Settings > Privacy & Security > {pane}, then reply 'ok' so I can retry."
        )
    if status == "denied":
        return (
            f" You previously denied {pane} for CoWorker, so macOS will not ask again. "
            f"I opened System Settings: toggle CoWorker OFF and back ON to reset the "
            f"permission, then reply 'ok' so I can retry."
        )
    if status == "restricted":
        return f" {pane} is restricted/managed by the system and cannot be enabled from here."
    return ""


def _render_computer_error(result: dict[str, Any], client: Any = None) -> str:
    """Map bridge error codes to actionable agent-facing guidance.

    ``client`` (when given) enables the passive permission auto-request: on a
    missing screen/input permission the OS prompt/pane is triggered once and the
    returned hint tells the agent exactly what to ask the user to do next.
    """
    code = result.get("error_code") or ""
    error = result.get("error") or "computer error"
    hint = ""
    if code in ("computer_unavailable", "computer_not_found"):
        hint = (
            "Tell the user OS Computer Use is only available in the desktop app "
            "with the feature enabled in Settings."
        )
    elif code == "computer_paused":
        hint = (
            "The user paused computer use (or the screen locked). STOP acting on the "
            "desktop and tell the user you are waiting until they resume it."
        )
    if code in ("screen_permission", "input_permission"):
        hint = ""
    auto_hint = _permission_request_hint(client, code) if client is not None else ""
    if not auto_hint and code in ("screen_permission", "input_permission"):
        _, pane = _PERMISSION_PANES[code]
        auto_hint = (
            f" CoWorker lacks the {pane} permission. If macOS no longer auto-prompts, open "
            f"System Settings > Privacy & Security > {pane}, toggle CoWorker OFF and back ON "
            f"to reset it, then have the user confirm before you retry. Do NOT spam retries."
        )
    hint = (hint + " " + auto_hint).strip() if auto_hint else hint
    return json.dumps({"error": error, "error_code": code, "hint": hint} if hint else {"error": error, "error_code": code}, ensure_ascii=False)


def build_computer_tools(
    data_dir: Path | str | None,
    *,
    vision: bool = False,
    session_id: str = "",
    max_shot_width: int = 1024,
) -> list[Any]:
    """Build the ``computer_observe`` + ``computer`` LangChain tools.

    Mounted only when the master switch is on AND the desktop bridge is up; the
    feature gate keeps the model from ever seeing OS control when the user has
    not explicitly enabled it (Settings, default off).
    """
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    client = ComputerClient(data_dir)

    class ObserveArgs(BaseModel):
        action: ObserveAction = Field(..., description="What to do: state = permissions/displays/platform; screenshot = capture the chosen display; displays = list displays with indices.")
        display: int = Field(0, ge=0, description="For 'screenshot': display index to capture (see displays list; 0 = primary).")
        max_width: int = Field(max_shot_width, ge=320, le=2048, description="Max screenshot width in pixels (higher = clearer but more tokens).")

    class ComputerArgs(BaseModel):
        action: ComputerAction = Field(..., description="What to do on the desktop.")
        display: int = Field(0, ge=0, description="Display index (see the computer_observe displays list; 0 = primary).")
        x: float = Field(0, description="For click/double_click/right_click/move/drag start: X in the screenshot's pixel space (or display point space when no shot given).")
        y: float = Field(0, description="For click/double_click/right_click/move/drag start: Y in the screenshot's pixel space.")
        x2: float = Field(0, description="For 'drag': target X.")
        y2: float = Field(0, description="For 'drag': target Y.")
        text: str = Field("", description="For 'type'/'clipboard_set': text to type or copy.")
        key: str = Field("", description="For 'key': key name (Enter, Tab, Escape, Backspace, Delete, ArrowUp/Down/Left/Right, Home, End, PageUp, PageDown, F1..F12, a-z, 0-9, or a single symbol).")
        modifiers: list[str] = Field(default_factory=list, description="For 'key': modifier list from cmd, ctrl, alt, shift (e.g. [\"cmd\"] for Cmd+C).")
        button: str = Field("left", description="For click/drag: left | right | middle.")
        dx: float = Field(0, description="For 'scroll': horizontal delta (pixels).")
        dy: float = Field(0, description="For 'scroll': vertical delta (pixels; positive scrolls down).")
        shot_width: int = Field(0, ge=0, description="Width of the screenshot the coordinates were read from (from the computer_observe result); 0 = coordinates are display points.")
        shot_height: int = Field(0, ge=0, description="Height of the screenshot the coordinates were read from; 0 = coordinates are display points.")

    def _observe_impl(action: str, display: int, max_width: int) -> str | list:
        try:
            if action == "state":
                result = client.state()
            elif action == "displays":
                result = client.displays()
            elif action == "screenshot":
                result = client.screenshot(display=display, max_width=max_width)
            else:
                return json.dumps({"error": f"unknown observe action: {action}"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("computer_observe failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "computer_error"}, ensure_ascii=False)

        if result.get("error_code"):
            return _render_computer_error(result, client)
        if action == "screenshot":
            return _screenshot_result(result, client, data_dir, session_id, vision, max_width)
        return _json_cap(result)

    def _screenshot_result(result: dict[str, Any], client: Any, data_dir: Path | str | None, session_id: str, vision: bool, max_width: int) -> str | list:
        data_url = str(result.get("image") or "")
        geometry = {
            "shot": result.get("shot") or {},
            "display": result.get("display") or {},
            "note": "Coordinates you output must be read from THIS image (pixel space); pass display, shot_width and shot_height from here back to the computer tool.",
        }
        geo_text = json.dumps(geometry, ensure_ascii=False)
        if data_url:
            if vision:
                if looks_like_image_data_url(data_url):
                    return [
                        {"type": "text", "text": f"Screenshot of the user's desktop.\n{geo_text}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                hint = _permission_request_hint(client, "screen_permission")
                if not hint:
                    hint = (
                        " CoWorker lacks Screen Recording permission. If macOS no longer "
                        "auto-prompts, open System Settings > Privacy & Security > Screen "
                        "Recording, toggle CoWorker OFF and back ON to reset it, then have "
                        "the user confirm before you retry."
                    )
                return json.dumps(
                    {
                        "error": "Screenshot came back empty or invalid — Screen Recording permission is missing.",
                        "error_code": "screen_permission",
                        "hint": hint.strip(),
                    },
                    ensure_ascii=False,
                )
            saved = save_screenshot(data_url, data_dir, session_id)
            if saved:
                return json.dumps(
                    {
                        "screenshot": saved,
                        **geometry,
                        "note": "This model has no vision capability; the screenshot was saved to disk instead of being shown. Use the computer tool carefully or ask the user what is on screen.",
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"error": "screenshot could not be captured or saved", "error_code": "screenshot_failed"}, ensure_ascii=False)
        hint = _permission_request_hint(client, "screen_permission")
        if not hint:
            hint = " CoWorker lacks Screen Recording permission; ask the user to reset/enable it in System Settings > Privacy & Security > Screen Recording."
        return json.dumps({"error": "screenshot came back empty", "error_code": "screen_permission", "hint": hint.strip()}, ensure_ascii=False)

    @tool(args_schema=ObserveArgs)
    def computer_observe(action: str, display: int = 0, max_width: int = max_shot_width) -> str | list:
        """Inspect the user's real desktop: list displays/permissions, or capture a screenshot.

        Read-only. ``state`` returns platform, macOS permissions (input/screen) and the
        display list with bounds. ``screenshot`` captures the chosen display and returns
        it as an image (vision) or a saved path; the result always includes the exact
        ``shot`` pixel geometry and ``display`` info — pass those back to the computer tool
        so its click coordinates land exactly. Take one screenshot at a time and prefer the
        default max_width: each screenshot costs real tokens.
        """
        return _observe_impl(action, display, max_width)

    @tool(args_schema=ComputerArgs)
    def computer(
        action: str,
        display: int = 0,
        x: float = 0,
        y: float = 0,
        x2: float = 0,
        y2: float = 0,
        text: str = "",
        key: str = "",
        modifiers: list[str] | None = None,
        button: str = "left",
        dx: float = 0,
        dy: float = 0,
        shot_width: int = 0,
        shot_height: int = 0,
    ) -> str:
        """Operate the user's real desktop: click, drag, scroll, type or press keys in any app.

        Coordinates are in the pixel space of the screenshot you were shown (read them OFF
        that image); pass display + shot_width + shot_height from the computer_observe
        screenshot result. Never guess coordinates without a fresh screenshot. Prefer
        keyboard shortcuts and the app's own menus; type text only into text fields. After
        an action that changes the UI, take another screenshot to verify. This tool acts on
        the user's whole machine — never operate apps the user is actively using, and stop
        immediately if the user takes back control (pauses) the desktop.
        """
        mods = [str(m) for m in (modifiers or [])]
        payload: dict[str, Any] = {"action": action, "display": int(display)}
        payload["shot"] = (
            {"width": int(shot_width), "height": int(shot_height)}
            if shot_width > 0 and shot_height > 0
            else None
        )
        if action in ("click", "double_click", "right_click", "move", "drag"):
            payload.update({"x": float(x), "y": float(y), "button": str(button) or "left"})
        if action == "drag":
            payload.update({"x2": float(x2), "y2": float(y2)})
        if action in ("type", "clipboard_set"):
            payload["text"] = str(text)
        if action == "key":
            payload["key"] = str(key)
            payload["modifiers"] = mods
        if action == "scroll":
            payload["dx"] = float(dx)
            payload["dy"] = float(dy)
        try:
            result = client.act(payload)
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("computer tool failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "computer_error"}, ensure_ascii=False)
        if result.get("error_code"):
            return _render_computer_error(result, client)
        return json.dumps(
            {
                "ok": True,
                "action": action,
                "note": "Now take another computer_observe screenshot to verify the result before continuing.",
            },
            ensure_ascii=False,
        )

    return [computer_observe, computer]


def resolve_computer_tools(
    data_dir: Path | str | None,
    *,
    vision: bool = False,
    session_id: str = "",
) -> list[Any]:
    """Computer tools for a runtime when the master switch is on AND the desktop
    bridge is up; otherwise ``[]`` (the model never sees the tools)."""
    if data_dir is None:
        return []
    if not computer_enabled():
        return []
    if not computer_available(data_dir):
        return []
    try:
        return build_computer_tools(data_dir, vision=vision, session_id=session_id)
    except Exception:  # noqa: BLE001 - a computer misconfig must never break a turn
        logger.warning("computer tools disabled (config error)", exc_info=True)
        return []
