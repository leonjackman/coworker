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

    # ── Structure-first automation surface (native cw-automa AX helper) ──
    def ax_snapshot(self, depth: int = 6) -> dict[str, Any]:
        """Accessibility element tree flattened to text w/ stable refs. Primary
        observation; only needs the Accessibility permission (no Screen Rec)."""
        return self._call("POST", "/ax/snapshot", {"depth": int(depth)})

    def ax_act(self, ref: str, op: str, **params: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"ref": str(ref), "op": str(op)}
        payload.update(params)
        return self._call("POST", "/ax/act", payload)

    def ax_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        return self._call("POST", "/ax/press", {"key": str(key), "modifiers": list(modifiers or [])})

    def ax_type(self, text: str) -> dict[str, Any]:
        return self._call("POST", "/ax/type", {"text": str(text)})

    def ax_launch(self, app: str) -> dict[str, Any]:
        return self._call("POST", "/ax/launch", {"app": str(app)})

    def ax_coords(self, x: float, y: float) -> dict[str, Any]:
        return self._call("POST", "/ax/coords", {"x": float(x), "y": float(y)})

    def ax_scroll(self, dx: float = 0, dy: float = 0) -> dict[str, Any]:
        return self._call("POST", "/ax/scroll", {"dx": float(dx), "dy": float(dy)})

    def ax_frontmost(self) -> dict[str, Any]:
        return self._call("POST", "/ax/frontmost", {})


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
            "OS Computer Use is ENABLED. Observe with computer_observe snapshot (Accessibility "
            "element tree + [ref]s — works with just the Accessibility permission, no Screen "
            "Recording needed). Act BY REF: click_ref/double_click_ref/right_click_ref/type_into/show. "
            "Open apps ONLY via computer(action='launch_app'); press shortcuts ONLY via "
            "computer(action='press_hotkey', key, modifiers) — NEVER type a shortcut as text "
            "(type_text refuses it, e.g. do not type 'cmd+space'). Keep click_coords strictly as a "
            "last resort for canvas/rendered content. Workflow: snapshot -> pick a ref -> act -> "
            "read the returned after_preview -> continue only if verified. NEVER claim an outcome "
            "unless the re-observed snapshot confirms it. If snapshot returns a permission error "
            "you cannot see the desktop: stop and tell the user to grant Accessibilitiy, do NOT act "
            "or pretend."
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

ObserveAction = Literal["state", "displays", "screenshot", "snapshot"]
ComputerAction = Literal[
    "launch_app", "press_hotkey", "click_ref", "double_click_ref", "right_click_ref",
    "type_into", "type_text", "scroll", "go_back", "show", "click_coords",
]

# The macOS Accessibility (AX) element tree is the PRIMARY observation surface:
# it only needs the Accessibility permission (NOT Screen Recording), and gives
# the agent a real searchable element list with stable refs — so it acts by ref
# instead of guessing pixel coordinates. Screen Recording is a secondary, visual
# complement. When the AX tree is unavailable the mutating tools fail closed.

_SHORTCUT_TOKENS = [
    "cmd", "command", "ctrl", "control", "alt", "option", "shift",
    "space", "enter", "return", "escape", "esc", "tab", "backspace",
    "super", "meta", "\u2318", "\u21e7", "\u2325", "\u2303", "\u21a9",
]


def _looks_like_shortcut(text: str) -> bool:
    """True when ``text`` is actually a keyboard shortcut typed as text."""
    t = (text or "").lower()
    if "+" in t:
        return True
    return any(token in t for token in _SHORTCUT_TOKENS)


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
        action: ObserveAction = Field(..., description="Read-only: state = permissions/platform/frontmost; displays = list displays; screenshot = capture the chosen display (visual, needs Screen Recording); snapshot = Accessibility element tree (text + refs) — the reliable observation, needs only Accessibility permission.")
        display: int = Field(0, ge=0, description="For 'screenshot': display index to capture (see displays list; 0 = primary).")
        max_width: int = Field(max_shot_width, ge=320, le=2048, description="For 'screenshot': max screenshot width in pixels (higher = clearer but more tokens).")
        depth: int = Field(6, ge=1, le=10, description="For 'snapshot': Accessibility tree depth.")

    class ComputerArgs(BaseModel):
        action: ComputerAction = Field(..., description="Structure-first desktop action. Prefer ref-based and intent-level actions; coordinates are a last resort for canvas/rendered content.")
        ref: str = Field("", description="For click_ref/double_click_ref/right_click_ref/type_into/show: the element ref from the latest computer_observe snapshot.")
        app: str = Field("", description="For 'launch_app': application name to open via the system launcher (e.g. 'Calculator', 'Safari').")
        key: str = Field("", description="For 'press_hotkey': key name (space, enter, tab, escape, backspace, delete, arrows, home, end, pageup/pagedown, F1..F12, a-z, 0-9, or single symbol).")
        modifiers: list[str] = Field(default_factory=list, description="For 'press_hotkey': from cmd, ctrl, alt, shift (e.g. [\"cmd\"] for Cmd+Space).")
        text: str = Field("", description="For 'type_into'/'type_text': the text to enter. NEVER a keyboard shortcut — shortcuts go through press_hotkey.")
        x: float = Field(0, description="For 'click_coords' (last resort): X in display points.")
        y: float = Field(0, description="For 'click_coords' (last resort): Y in display points.")
        dx: float = Field(0, description="For 'scroll': horizontal delta.")
        dy: float = Field(0, description="For 'scroll': vertical delta (positive scrolls down).")

    def _snapshot_text() -> str | None:
        """Return the current AX snapshot text, or None when observation fails
        (fail-closed: if we cannot see, the agent must not guess)."""
        try:
            res = client.ax_snapshot(6)
        except Exception:
            return None
        if not isinstance(res, dict) or res.get("error_code"):
            return None
        return str(res.get("text") or "")

    def _verify_and_report(before: str, action: str, text: str, app: str, res: dict[str, Any]) -> str:
        after = _snapshot_text()
        b = before or ""
        a = after or ""
        changed = a != b
        if action == "launch_app":
            verified = bool(app) and changed and (app.lower() in a.lower())
        elif action == "type_into":
            verified = bool(text) and (text.lower() in a.lower()) and (a != b)
        else:
            verified = changed
        note = (
            "Verified: the re-observed state changed as expected."
            if verified
            else "The re-observed state did NOT change as expected — re-read computer_observe snapshot before continuing and do not claim success."
        )
        preview = "\n".join(a.split("\n")[:16])
        return json.dumps(
            {
                "ok": True,
                "action": action,
                "verified": verified,
                "changed": changed,
                "note": note,
                "after_preview": preview,
            },
            ensure_ascii=False,
        )

    def _execute_action(action: str, args: Any) -> dict[str, Any]:
        if action == "launch_app":
            return client.ax_launch(str(args.app or ""))
        if action == "press_hotkey":
            return client.ax_press(str(args.key or ""), [str(m) for m in (args.modifiers or [])])
        if action in ("click_ref", "double_click_ref", "right_click_ref"):
            op = {"click_ref": "click", "double_click_ref": "double", "right_click_ref": "right"}[action]
            return client.ax_act(str(args.ref or ""), op)
        if action == "show":
            return client.ax_act(str(args.ref or ""), "show")
        if action == "type_into":
            return client.ax_act(str(args.ref or ""), "type_into", text=str(args.text or ""))
        if action == "type_text":
            return client.ax_type(str(args.text or ""))
        if action == "scroll":
            return client.ax_scroll(float(args.dx or 0), float(args.dy or 0))
        if action == "go_back":
            return client.ax_press("[", ["cmd"])
        if action == "click_coords":
            return client.ax_coords(float(args.x or 0), float(args.y or 0))
        return {"error": f"unknown computer action: {action}", "error_code": "computer_error"}

    def _observe_impl(action: str, display: int, max_width: int, depth: int) -> str | list:
        try:
            if action == "state":
                result = client.state()
            elif action == "displays":
                result = client.displays()
            elif action == "snapshot":
                result = client.ax_snapshot(depth)
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
        if action == "snapshot":
            snap_text = str(result.get("text") or "")
            return json.dumps(
                {
                    "frontmost": result.get("frontmost") or "",
                    "refs": result.get("refs") or 0,
                    "snapshot": snap_text,
                    "note": "Use the [ref] from this snapshot to act on real elements (click_ref, type_into, show). If snapshot is empty/unavailable, you cannot see the desktop — stop and do not claim anything.",
                },
                ensure_ascii=False,
            )
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
                        "note": "This model has no vision capability; the screenshot was saved to disk instead of being shown. Use computer_observe snapshot (accessibility tree) as the reliable observation instead.",
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"error": "screenshot could not be captured or saved", "error_code": "screenshot_failed"}, ensure_ascii=False)
        hint = _permission_request_hint(client, "screen_permission")
        if not hint:
            hint = " CoWorker lacks Screen Recording permission; ask the user to reset/enable it in System Settings > Privacy & Security > Screen Recording."
        return json.dumps({"error": "screenshot came back empty", "error_code": "screen_permission", "hint": hint.strip()}, ensure_ascii=False)

    @tool(args_schema=ObserveArgs)
    def computer_observe(action: str, display: int = 0, max_width: int = max_shot_width, depth: int = 6) -> str | list:
        """Inspect the user's real desktop: Accessibility snapshot, screenshot, or state.

        Read-only. PREFER ``snapshot`` — it returns the Accessibility element tree as
        text with stable [ref]s and only needs the Accessibility permission (works even
        without Screen Recording), so you can act on real elements by ref. ``screenshot``
        is the visual complement (needs Screen Recording). ``state`` returns platform,
        permissions and the current frontmost app. The snapshot is your source of truth:
        never claim an action "worked" unless the re-observed snapshot shows it.
        """
        return _observe_impl(action, display, max_width, depth)

    @tool(args_schema=ComputerArgs)
    def computer(
        action: str,
        ref: str = "",
        app: str = "",
        key: str = "",
        modifiers: list[str] | None = None,
        text: str = "",
        x: float = 0,
        y: float = 0,
        dx: float = 0,
        dy: float = 0,
    ) -> str:
        """Operate the user's real desktop BY ACCESSIBILITY ELEMENT REF.

        Structure-first: use a ref from the latest computer_observe snapshot for
        clicks/typing (click_ref, double_click_ref, right_click_ref, type_into, show).
        Open apps only via launch_app; press keyboard shortcuts only via press_hotkey
        (cmd+space etc.) — NEVER type a shortcut as text (type_text refuses it). Keep
        click_coords strictly as a last resort for canvas/rendered content. After every
        action read the returned after_preview and only claim what it confirms.
        """
        mods = [str(m) for m in (modifiers or [])]

        # Instant shortcut-as-text guard: type_* must never be a hotkey.
        if action in ("type_text", "type_into") and _looks_like_shortcut(str(text or "")):
            return json.dumps(
                {
                    "error": "You passed a keyboard shortcut as text. Use computer(action='press_hotkey', key='<key>', modifiers=['cmd']) instead.",
                    "error_code": "shortcut_as_text",
                },
                ensure_ascii=False,
            )

        # Fail-closed: if we cannot observe the desktop, refuse to guess.
        before = _snapshot_text()
        if before is None:
            return json.dumps(
                {
                    "error": "Cannot see the desktop (Accessibility unavailable). Enable Accessibility for CoWorker in System Settings and retry; do NOT act on a screen you cannot observe.",
                    "error_code": "no_observation",
                },
                ensure_ascii=False,
            )

        # The `action` string also needs to be passed to arg-driven dispatch; build
        # a lightweight namespace from the raw args for _execute_action.
        from types import SimpleNamespace

        ns = SimpleNamespace(
            ref=ref, app=app, key=key, modifiers=mods, text=text, x=x, y=y, dx=dx, dy=dy,
        )
        try:
            result = _execute_action(action, ns)
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("computer tool failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "computer_error"}, ensure_ascii=False)
        if result.get("error_code"):
            return _render_computer_error(result, client)
        return _verify_and_report(before, action, str(text or ""), str(app or ""), result)

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
