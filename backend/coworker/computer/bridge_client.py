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
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal

from coworker.bridge_common import (
    LoopbackBridgeClient,
    looks_like_image_data_url,
    read_bridge_info,
    save_screenshot,
    write_bridge_info,
)
from coworker.computer.actions import ACTION_MAP, agent_actions, agent_union_params, param_names
from coworker.computer_feature import computer_feature

logger = logging.getLogger(__name__)

# The computer action catalog is the single source of truth (coworker.computer.actions).
# The agent's single `computer` tool and the workflow registry both derive from it.
_AGENT_ACTIONS = agent_actions()
_AGENT_ACTION_NAMES = tuple(a.name for a in _AGENT_ACTIONS)
_ACTION_BY_NAME = ACTION_MAP

#: Settings key under which Electron registers the computer bridge.
_BRIDGE_KEY = "computer_bridge"

#: Hard cap for any single non-screenshot computer tool output (chars). A state
#: dump with every display + permissions is small; this only guards pathology.
COMPUTER_OUTPUT_MAX_CHARS = 20_000

_TRUNCATION_NOTE = "\n[content truncated by Coworker to fit context]"

# Bracketed element refs in the model-facing tree, e.g. `[button:确定#1]`.
_REF_TOKEN_RE = re.compile(r"\[([^\]\n]+)\]")


def _sample_refs(snapshot_text: str, limit: int = 3) -> str:
    """Comma-joined real refs taken from a snapshot, used as examples.

    Deriving examples from the actual tree keeps them correct across platforms
    (macOS `axbutton:…`, Windows `button:…`) instead of hardcoding one platform's
    vocabulary into a shared note. Returns "" when the tree has no refs.
    """
    seen: list[str] = []
    for match in _REF_TOKEN_RE.finditer(snapshot_text or ""):
        ref = match.group(1).strip()
        if ref and ref not in seen:
            seen.append(ref)
        if len(seen) >= limit:
            break
    return ", ".join(seen)


def _element_note(snapshot_text: str, *, app_state: bool) -> str:
    """Platform-neutral guidance attached to snapshot/app_state results.

    Names BOTH platforms' ref vocabulary but never presents one platform's prefix
    as THE format: the concrete refs are shown verbatim from ``snapshot_text``, so
    the model copies real ids instead of inventing an `ax…` prefix on Windows
    (which was the root cause of the earlier "no UIA element" ref failures).
    """
    samples = _sample_refs(snapshot_text)
    parts = [
        "Refs are opaque element ids of the form role:label#n, copied verbatim from the "
        "square brackets in the tree (macOS looks like `axbutton:搜索#1`, Windows like "
        "`button:确定#1`)."
        + (f" Real refs in this view: {samples}." if samples else ""),
        "ALWAYS act on the LATEST snapshot; if an action returns fresh_snapshot, re-pick a ref "
        "from it. If the tree is empty/unavailable you cannot see the desktop — stop and do not "
        "claim anything.",
        "Only editable fields accept typing (macOS AXTextField/AXTextArea; Windows "
        "Edit/Document/ComboBox). Buttons, labels and groups are for clicking only — clicking a "
        "static label will not open a text field.",
        "This tree covers ONE app window (the frontmost app, or the `app` you passed); use "
        "list_apps / app_state(app=…) to target another app.",
    ]
    if app_state:
        parts.insert(
            1,
            "'changed' false means the tree is identical to the previous read — reuse your prior "
            "understanding and do not re-reason. If removed[] lists refs, they no longer exist.",
        )
    return " ".join(parts)


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

    def notify(self, title: str, body: str = "") -> dict[str, Any]:
        """Show a desktop notification through the Electron app (all OSes)."""
        return self._call("POST", "/notify", {"title": str(title or ""), "body": str(body or "")})

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

    def ax_app_state(self, app: str = "", depth: int = 6) -> dict[str, Any]:
        """get_app_state: key-window AX tree + window info + an incremental diff
        against the previous read. ``app`` empty targets the frontmost app."""
        payload: dict[str, Any] = {"depth": int(depth)}
        if app:
            payload["app"] = str(app)
        return self._call("POST", "/ax/app_state", payload)

    def ax_act(self, ref: str, op: str, **params: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"ref": str(ref), "op": str(op)}
        payload.update(params)
        return self._call("POST", "/ax/act", payload)

    def ax_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        return self._call("POST", "/ax/press", {"key": str(key), "modifiers": list(modifiers or [])})

    def ax_type(self, text: str) -> dict[str, Any]:
        return self._call("POST", "/ax/type", {"text": str(text)})

    def ax_input_text(self, app: str = "", ref: str = "", text: str = "", submit: bool = False) -> dict[str, Any]:
        """App/ref-targeted text input (vs the frontmost-app ``ax_type``)."""
        payload: dict[str, Any] = {"text": str(text or ""), "submit": bool(submit)}
        if app:
            payload["app"] = str(app)
        if ref:
            payload["ref"] = str(ref)
        return self._call("POST", "/ax/input_text", payload)

    def ax_press_to(self, app: str, key: str, modifiers: list[str] | None = None, repeat: int = 1) -> dict[str, Any]:
        """App-targeted key press (vs the frontmost-app ``ax_press``)."""
        payload: dict[str, Any] = {"key": str(key or ""), "modifiers": list(modifiers or []), "repeat": int(repeat or 1)}
        if app:
            payload["app"] = str(app)
        return self._call("POST", "/ax/press_to", payload)

    def ax_launch(self, app: str) -> dict[str, Any]:
        return self._call("POST", "/ax/launch", {"app": str(app)})

    def ax_list_apps(self, scope: str = "installed") -> dict[str, Any]:
        """List installed or running apps: ``{scope, apps:[{bundleId, displayName, path?}]}``."""
        safe = scope if scope in ("installed", "running") else "installed"
        return self._call("POST", "/ax/list_apps", {"scope": safe})

    def ax_coords(self, x: float, y: float, shot_width: int = 0, shot_height: int = 0, display: int = 0) -> dict[str, Any]:
        """Click at display points, or at screenshot-pixel coords mapped to points
        when shot_width/shot_height (from the computer_observe screenshot) are given."""
        return self._call("POST", "/ax/coords", {
            "x": float(x), "y": float(y),
            "shot_width": int(shot_width or 0), "shot_height": int(shot_height or 0),
            "display": int(display or 0),
        })

    def ax_scroll(self, dx: float = 0, dy: float = 0) -> dict[str, Any]:
        return self._call("POST", "/ax/scroll", {"dx": float(dx), "dy": float(dy)})

    def ax_scroll_to(
        self, app: str = "", dx: float = 0, dy: float = 0, x: float = 0, y: float = 0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"dx": float(dx), "dy": float(dy)}
        if app:
            payload["app"] = str(app)
        if x:
            payload["x"] = float(x)
        if y:
            payload["y"] = float(y)
        return self._call("POST", "/ax/scroll_to", payload)

    def ax_frontmost(self) -> dict[str, Any]:
        return self._call("POST", "/ax/frontmost", {})

    # ── Persistent JS surface (Codex-parity) ─────────────────────────────
    def ax_script(self, code: str, timeout_ms: int = 0) -> dict[str, Any]:
        """Run one JavaScript cell in the persistent computer-use worker."""
        return self._call("POST", "/ax/js", {"code": str(code), "timeout_ms": int(timeout_ms or 0)})

    def ax_script_reset(self) -> dict[str, Any]:
        """Discard the persistent worker and its bindings."""
        return self._call("POST", "/ax/js_reset", {})

    def ax_resolve_app(self, app: str) -> dict[str, Any]:
        return self._call("POST", "/ax/resolve_app", {"app": str(app or "")})

    def ax_ui_settle(self, app: str = "", quiet_ms: int = 250, timeout_ms: int = 3000) -> dict[str, Any]:
        return self._call("POST", "/ax/ui_settle", {
            "app": str(app or ""),
            "quiet_ms": int(quiet_ms or 250),
            "timeout_ms": int(timeout_ms or 3000),
        })


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
            "OS Computer Use is ENABLED. PRIMARY surface = computer_script (persistent JavaScript): "
            "`const app = await cua.getApp('Music')` binds an app, then in ONE call observe + act, with "
            "loops/conditions: app.getAXState() (object with `.text` tree + `.refs`; there is no `.treeString`), "
            "app.click(refOrIndex), app.typeText(text,{submit}), app.paste(text), app.setValue(ref,text), "
            "app.pressKey('cmd+f'), app.scroll(target,'down',pages), app.drag([x,y],[x,y]), app.settle(). "
            "Element targets are opaque refs copied verbatim from the tree brackets (strings like "
            "'role:label#n', e.g. macOS 'axbutton:搜索#1' / Windows 'button:确定#1') or INTEGER indices from "
            "the SAME cell's getAXState (call it first). Bindings persist across calls; "
            "computer_script(reset=true, code=…) resets the session and then runs code. "
            "No require/process/fs/network. "
            "INPUT LADDER (AX-first): setValue (most deterministic) -> typeText -> paste. After any input, "
            "re-observe and confirm the field/result changed before claiming success; if AX readback is empty, "
            "trust the paste receipt/field change instead of retrying blindly. "
            "Legacy low-level tools remain: computer_observe (state/snapshot/app_state/screenshot) and computer "
            "(click_ref/type_into/press_hotkey/launch_app/click_coords). Open apps ONLY via launch_app or "
            "cua.getApp; press shortcuts ONLY via press_hotkey/pressKey. type_text/type_into enter literal text — any characters. "
            "If an action fails the SAME way twice, STOP and ask the user instead of retry-looping. "
            "NEVER claim an outcome you did not observe. For the current local date/time call "
            "computer_observe state (it returns `now`); never guess the time. If a permission error is "
            "reported, stop and tell the user."
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

ObserveAction = Literal["state", "displays", "screenshot", "snapshot", "app_state"]
# Derived from the action catalog (coworker.computer.actions) — never hand-listed.
ComputerAction = Literal[*_AGENT_ACTION_NAMES]

# The platform accessibility element tree (macOS Accessibility/AX, Windows UI
# Automation) is the PRIMARY observation surface: it gives the agent a real
# searchable element list with stable refs — so it acts by ref instead of
# guessing pixel coordinates. Screen Recording (macOS) / screenshots are a
# secondary, visual complement. When the tree is unavailable the mutating tools
# fail closed.


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
    elif code == "stale_ref":
        hint = (
            "That element ref no longer exists (the UI changed). Re-read the current "
            "tree (computer_observe snapshot / app.getAXState) and pick a fresh ref; "
            "do NOT reuse the old one."
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
    interactive: bool = True,
) -> list[Any]:
    """Build the ``computer_observe`` + ``computer`` LangChain tools.

    Mounted only when the master switch is on AND the desktop bridge is up; the
    feature gate keeps the model from ever seeing OS control when the user has
    not explicitly enabled it (Settings, default off).
    """
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field, create_model

    client = ComputerClient(data_dir)

    # Loop breaker: the single most common failure mode is retry-looping the
    # same call. The toolset persists for the session, so we can detect N
    # consecutive IDENTICAL calls and refuse the next one, forcing a change of
    # strategy (or a question to the user) instead of an unbounded loop.
    _recent: dict[str, list[str]] = {"computer": [], "computer_script": []}
    _LOOP_LIMIT = 3

    def _loop_guard(kind: str, signature: str) -> bool:
        # Interactive-only guard: it exists to stop the MODEL retry-looping the
        # same call. A deterministic workflow run is not an interactive loop, so
        # the guard is disabled there (each step is intentional and reproducible).
        if not interactive:
            return False
        hist = _recent[kind]
        hist.append(signature)
        if len(hist) > _LOOP_LIMIT:
            hist.pop(0)
        return hist.count(signature) >= _LOOP_LIMIT

    class ObserveArgs(BaseModel):
        action: ObserveAction = Field(..., description="Read-only: state = permissions/platform/frontmost; displays = list displays; screenshot = capture the chosen display (visual); snapshot = accessibility element tree (macOS AX / Windows UIA: text + refs) — the reliable observation.")
        display: int = Field(0, ge=0, description="For 'screenshot': display index to capture (see displays list; 0 = primary).")
        max_width: int = Field(max_shot_width, ge=320, le=2048, description="For 'screenshot': max screenshot width in pixels (higher = clearer but more tokens).")
        depth: int = Field(6, ge=1, le=10, description="For 'snapshot'/'app_state': accessibility tree depth.")
        app: str = Field("", description="For 'app_state': target app (macOS display name or bundle id; Windows process name, exe, or window title); empty = frontmost app.")

    # The tool schema is GENERATED from the action catalog, so a param name can
    # never drift from the workflow registry. LangChain cannot express a
    # discriminated-union args_schema, so per-action required/unknown args are
    # enforced at call time by `_validate_action_args`.
    _PY_TYPES: dict[str, Any] = {"number": float, "boolean": bool, "list": list, "object": dict}
    _computer_fields: dict[str, Any] = {
        "action": (
            ComputerAction,
            Field(..., description="Structure-first desktop action. Prefer ref-based and intent-level actions; coordinates are a last resort."),
        ),
    }
    for _param_name, _param in agent_union_params().items():
        if _param.type == "list":
            _computer_fields[_param_name] = (list[str], Field(default_factory=list, description=_param.description))
        else:
            _computer_fields[_param_name] = (
                _PY_TYPES.get(_param.type, str),
                Field(0.0 if _param.type == "number" else (False if _param.type == "boolean" else ""), description=_param.description),
            )
    ComputerArgs = create_model("ComputerArgs", **_computer_fields)

    def _validate_action_args(action: str, raw: dict[str, Any]) -> str | None:
        """Enforce the per-action contract (discriminated union) at call time.

        Rejects params that belong to a DIFFERENT action, and reports missing
        required params. This is how one `computer` tool still behaves like a
        per-action union schema.
        """
        definition = _ACTION_BY_NAME.get(action)
        if definition is None:
            return f"unknown computer action '{action}' (allowed: {', '.join(_AGENT_ACTION_NAMES)})"
        allowed = param_names(definition)
        provided = [k for k, v in raw.items() if k != "action" and v not in (None, "", [], 0, 0.0, False)]
        unexpected = [k for k in provided if k not in allowed]
        if unexpected:
            return f"{action} got unexpected parameter(s): {', '.join(sorted(unexpected))} (allowed: {', '.join(sorted(allowed)) or 'none'})"
        return None

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

    def _normalize_for_verify(text: str) -> str:
        """Compare content, not churn: drop on-screen positions which change every
        frame (even when nothing meaningful moved)."""
        return re.sub(r"at \(-?\d+,-?\d+\)", "", text or "")

    def _frontmost_pid() -> int | None:
        try:
            fm = client.ax_frontmost()
            if isinstance(fm, dict) and not fm.get("error_code"):
                pid = fm.get("pid")
                return int(pid) if isinstance(pid, int) and pid > 0 else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def _frontmost_name() -> str:
        try:
            fm = client.ax_frontmost()
            if isinstance(fm, dict) and not fm.get("error_code"):
                return str(fm.get("app") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _focus_conflict(target: str, front: str) -> bool:
        """Loose check that input meant for ``target`` may have hit another app.

        Names are localized (target "Pages" vs frontmost "Pages文稿"), so treat
        a containment either way as a match; skip bundle ids (dots) to avoid
        false positives.
        """
        if not target or not front or "." in target:
            return False
        a = target.strip().lower()
        f = front.strip().lower()
        return a not in f and f not in a

    def _verify_and_report(before: str, action: str, text: str, app: str, res: dict[str, Any],
                           front_before_pid: int | None) -> str:
        # Evidence policy (Anthropic-aligned): only a STRONG identity signal may
        # mark an action verified. "The snapshot text changed" is NOT one — an
        # animating app changes constantly, which caused false "success" before.
        # launch/app-switch: the app must actually become frontmost (pid change).
        # type_into: the focused field's value must contain the text.
        # Everything else: verified=null + the fresh observation, so the MODEL
        # decides by looking — exactly Anthropic's "evaluate after each step".
        if action == "launch_app":
            # Strong verification only: "some window became frontmost" is NOT proof
            # of a launch — on Windows a missing program opens a shell error dialog,
            # which used to be counted as success. An app is confirmed launched when
            # we can resolve a running instance for it (helper-reported pid, or our
            # own resolve_app lookup as a fallback for older helpers).
            pid: int | None = None
            if isinstance(res, dict):
                cand = res.get("pid")
                if isinstance(cand, int) and cand > 0:
                    pid = cand
            if pid is None and app:
                for _ in range(6):
                    try:
                        resolved = client.ax_resolve_app(app)
                    except Exception:  # noqa: BLE001
                        resolved = None
                    if isinstance(resolved, dict):
                        cand = resolved.get("pid")
                        if isinstance(cand, int) and cand > 0:
                            pid = cand
                            break
                    time.sleep(0.4)
            verified = pid is not None
            name = _frontmost_name()
            st = _snapshot_text() or ""
            preview = "\n".join(st.split("\n")[:12])
            note = (
                f"Verified: \"{app}\" is running (pid {pid})."
                if verified
                else f"Could NOT confirm \"{app}\" launched — no matching process/window appeared. "
                "It may be a wrong name, or need an executable/path (Windows) rather than a "
                "display name. Re-check with computer_observe state; do NOT assume it opened."
            )
            return json.dumps({
                "ok": True, "action": action, "verified": verified, "pid": pid, "changed": verified,
                "frontmost_before_pid": front_before_pid, "frontmost": name,
                "note": note, "after_preview": preview,
            }, ensure_ascii=False)

        time.sleep(0.35)
        after = _snapshot_text() or ""
        focus_lost = _focus_conflict(app, _frontmost_name())
        focus_note = (
            f" Focus is on another app than '{app}' — the action may have hit the wrong window. "
            "Re-focus the target app before continuing."
            if focus_lost else ""
        )
        focused_val = ""
        if isinstance(res, dict):
            focused = res.get("focused")
            if isinstance(focused, dict):
                focused_val = str(focused.get("value") or "")
        if action == "type_into":
            landed = focused_val.lower() if focused_val else ""
            verified = bool(text) and (text.lower() in landed or text.lower() in _normalize_for_verify(after))
            if not verified:
                note = (
                    "The text did NOT land in the focused field (readback value is empty/different). "
                    "This surface does not accept standard text editing. STOP and ask the user to enter "
                    "it manually (or use another route); do NOT retry-loop."
                )
            else:
                note = "Verified by focused-field readback: the text is in the field."
            return json.dumps({
                "ok": True, "action": action, "verified": verified, "focused_value": focused_val,
                "focus_lost": focus_lost, "note": note + focus_note,
                "after_preview": "\n".join(after.split("\n")[:16]),
            }, ensure_ascii=False)

        # No strong anchor (click/scroll/press_hotkey/show/coords): report
        # verified=null and hand the fresh observation to the model to judge.
        preview = "\n".join(after.split("\n")[:16])
        return json.dumps({
            "ok": True, "action": action, "verified": None, "changed": None,
            "focus_lost": focus_lost,
            "note": (
                "No automatic confirmation for this action. Evaluate the after_preview yourself: "
                "only continue/claim if the observation shows the intended result." + focus_note
            ),
            "after_preview": preview,
        }, ensure_ascii=False)

    def _execute_action(action: str, args: Any) -> dict[str, Any]:
        if action == "launch_app":
            return client.ax_launch(str(args.app or ""))
        if action == "press_hotkey":
            mods = [str(m) for m in (args.modifiers or [])]
            app = str(getattr(args, "app", "") or "")
            if app:
                return client.ax_press_to(app, str(args.key or ""), mods, 1)
            return client.ax_press(str(args.key or ""), mods)
        if action in ("click_ref", "double_click_ref", "right_click_ref"):
            op = {"click_ref": "click", "double_click_ref": "double", "right_click_ref": "right"}[action]
            return client.ax_act(str(args.ref or ""), op)
        if action == "show":
            return client.ax_act(str(args.ref or ""), "show")
        if action == "type_into":
            app = str(getattr(args, "app", "") or "")
            ref = str(args.ref or "")
            submit = bool(getattr(args, "submit", False))
            if ref:
                return client.ax_act(ref, "type_into", text=str(args.text or ""), submit=submit)
            # No ref given: type into the focused field of the target app (or the
            # frontmost app when `app` is empty). The action catalog marks `ref`
            # optional, so this must not hard-fail — it mirrors `type_text`.
            return client.ax_input_text(app=app, text=str(args.text or ""), submit=submit)
        if action == "type_text":
            app = str(getattr(args, "app", "") or "")
            if app:
                return client.ax_input_text(app=app, text=str(args.text or ""))
            return client.ax_type(str(args.text or ""))
        if action == "scroll":
            return client.ax_scroll(float(args.dx or 0), float(args.dy or 0))
        if action == "scroll_to":
            if not str(getattr(args, "scroll_app", "") or ""):
                return {"error": "scroll_to requires 'scroll_app' (target app name or bundle id)", "error_code": "param_error"}
            return client.ax_scroll_to(
                str(getattr(args, "scroll_app", "") or ""),
                float(args.dx or 0), float(args.dy or 0),
                float(args.scroll_x or 0), float(args.scroll_y or 0),
            )
        if action == "go_back":
            if sys.platform == "win32":
                return client.ax_press("left", ["alt"])
            return client.ax_press("[", ["cmd"])
        if action == "click_coords":
            return client.ax_coords(float(args.x or 0), float(args.y or 0),
                                    int(args.shot_width or 0), int(args.shot_height or 0),
                                    int(args.display or 0))
        return {"error": f"unknown computer action: {action}", "error_code": "computer_error"}

    def _observe_impl(action: str, display: int, max_width: int, depth: int, app: str = "") -> str | list:
        try:
            if action == "state":
                result = client.state()
                # Attach the current frontmost app so the agent can confirm what
                # it is actually acting on (launch/switch verification anchor).
                fm = client.ax_frontmost()
                if isinstance(fm, dict) and not fm.get("error_code"):
                    result["frontmost_pid"] = fm.get("pid")
                    result["frontmost"] = fm.get("app")
            elif action == "displays":
                result = client.displays()
            elif action == "snapshot":
                result = client.ax_snapshot(depth)
            elif action == "app_state":
                result = client.ax_app_state(app, depth)
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
        if action == "app_state":
            snap_text = str(result.get("text") or "")
            return json.dumps(
                {
                    "frontmost": result.get("frontmost") or "",
                    "app": result.get("app") or "",
                    "pid": result.get("pid"),
                    "refs": result.get("refs") or 0,
                    "changed": result.get("changed"),
                    "removed": result.get("removed") or [],
                    "window": result.get("window") or {},
                    "snapshot": snap_text,
                    "note": _element_note(snap_text, app_state=True),
                },
                ensure_ascii=False,
            )
        if action == "snapshot":
            snap_text = str(result.get("text") or "")
            return json.dumps(
                {
                    "frontmost": result.get("frontmost") or "",
                    "refs": result.get("refs") or 0,
                    "snapshot": snap_text,
                    "note": _element_note(snap_text, app_state=False),
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
    def computer_observe(action: str, display: int = 0, max_width: int = max_shot_width, depth: int = 6, app: str = "") -> str | list:
        """Inspect the user's real desktop: accessibility snapshot, screenshot, or state.

        Read-only. PREFER ``app_state`` (or ``snapshot``) — it returns the platform
        accessibility element tree (macOS Accessibility/AX, Windows UI Automation) as text
        with stable [ref]s, so you can act on real elements by ref. ``app_state`` adds the
        key window's title/frame and a ``changed`` flag plus the ``removed`` refs
        (incremental diff), so you can skip re-reasoning an unchanged UI. ``screenshot`` is
        the visual complement. ``state`` returns platform, permissions and the current
        frontmost app. The snapshot is your source of truth: never claim an action "worked"
        unless the re-observed snapshot shows it.
        """
        return _observe_impl(action, display, max_width, depth, app)

    @tool(args_schema=ComputerArgs)
    def computer(**kwargs: Any) -> str:
        """Operate the user's real desktop BY ACCESSIBILITY ELEMENT REF.

        Structure-first: use a ref from the latest computer_observe snapshot for
        clicks/typing (click_ref, double_click_ref, right_click_ref, type_into, show).
        Open apps only via launch_app; press keyboard shortcuts only via press_hotkey.
        type_text / type_into enter LITERAL text — every character (including ``+``)
        is just text. Keep click_coords strictly as a last resort for canvas/rendered
        content; its x,y are in the SCREENSHOT's pixel space, so pass shot_width/
        shot_height (and display) from that computer_observe screenshot result. After
        every action read the returned after_preview and only claim what it confirms.

        For typing: ``type_into`` takes an OPTIONAL ref — with a ref it types into that
        text field (macOS AXTextField/AXTextArea; Windows Edit/Document/ComboBox); without a
        ref it types into the focused field of ``app`` (or the frontmost app), so you never
        need to invent a ref to fill a search box you just focused. ``type_text`` always
        types into the frontmost/focused field. Clicking a static label or a button will NOT
        make it editable — those are display elements, not input fields.
        """
        action = str(kwargs.get("action") or "")
        invalid = _validate_action_args(action, kwargs)
        if invalid:
            return json.dumps({"error": invalid, "error_code": "param_error"}, ensure_ascii=False)
        ref = str(kwargs.get("ref") or "")
        app = str(kwargs.get("app") or "")
        key = str(kwargs.get("key") or "")
        mods = [str(m) for m in (kwargs.get("modifiers") or [])]
        text = str(kwargs.get("text") or "")
        submit = bool(kwargs.get("submit") or False)
        x = float(kwargs.get("x") or 0)
        y = float(kwargs.get("y") or 0)
        dx = float(kwargs.get("dx") or 0)
        dy = float(kwargs.get("dy") or 0)
        shot_width = int(kwargs.get("shot_width") or 0)
        shot_height = int(kwargs.get("shot_height") or 0)
        display = int(kwargs.get("display") or 0)
        scroll_app = str(kwargs.get("scroll_app") or "")
        scroll_x = float(kwargs.get("scroll_x") or 0)
        scroll_y = float(kwargs.get("scroll_y") or 0)

        sig = json.dumps(
            [action, ref, app, key, mods, str(text or ""), bool(submit), x, y, dx, dy],
            ensure_ascii=False,
        )
        if _loop_guard("computer", sig):
            return json.dumps(
                {
                    "error": "Loop guard: you have issued this exact computer action 3 times. Stop repeating it.",
                    "error_code": "loop_guard",
                    "note": (
                        "Change strategy: re-observe with computer_observe (or computer_script + app.getAXState), "
                        "pick a DIFFERENT ref/target, or ask the user. Do not repeat the same call."
                    ),
                },
                ensure_ascii=False,
            )

        # Observation-independent actions do NOT need to see the screen — never
        # fail-closed them or the agent will nag the user for a permission it has.
        observation_free = bool(getattr(_ACTION_BY_NAME.get(action), "observation_free", False)) or (
            action in ("type_text", "type_into") and bool(app)
        )

        # Capture the frontmost app identity before the action: the anchor for
        # launch/app-switch verification (a pid CHANGE), never animation churn.
        front_before_pid = _frontmost_pid()

        # Fail-closed for actions that must know the target: if we cannot observe
        # the desktop, refuse to guess (but never blame "permission" — the real
        # cause is usually that the frontmost app exposes an empty/transient AX
        # tree, or a missing grant for THIS process).
        before = _snapshot_text() if not observation_free else None
        if before is None and not observation_free:
            return json.dumps(
                {
                    "error": "Could not read the Accessibility tree just now. This usually means the frontmost app has no readable UI (or is still loading). Retry with computer_observe snapshot; if it stays empty, check that Accessibility is enabled for the RUNNING CoWorker process (not just any CoWorker entry).",
                    "error_code": "no_observation",
                },
                ensure_ascii=False,
            )

        # The `action` string also needs to be passed to arg-driven dispatch; build
        # a lightweight namespace from the raw args for _execute_action.
        from types import SimpleNamespace

        ns = SimpleNamespace(
            ref=ref, app=app, key=key, modifiers=mods, text=text, submit=submit,
            x=x, y=y, dx=dx, dy=dy,
            shot_width=shot_width, shot_height=shot_height, display=display,
            scroll_app=scroll_app, scroll_x=scroll_x, scroll_y=scroll_y,
        )
        try:
            result = _execute_action(action, ns)
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("computer tool failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "computer_error"}, ensure_ascii=False)
        if result.get("error_code"):
            error = str(result.get("error") or "")
            code = str(result.get("error_code") or "")
            # Self-healing for stale refs: the accessibility tree churns when the
            # UI changes (popovers/menus), so a ref from an older snapshot is often
            # gone. Instead of a dead end, re-read the CURRENT tree and hand it
            # back so the model immediately picks a valid ref. Both helpers report
            # the dedicated `stale_ref` code; keep the legacy string match so older
            # helper binaries (macOS AX / generic) still self-heal.
            is_stale = code == "stale_ref" or (
                code == "computer_error"
                and ("no AX element for ref" in error or "no UIA element for ref" in error or "accessibility_not_trusted" in error)
            )
            if is_stale:
                now = _snapshot_text()
                payload = {"error": error, "error_code": code or "stale_ref"}
                if now is not None:
                    payload["fresh_snapshot"] = now[:COMPUTER_OUTPUT_MAX_CHARS]
                    payload["note"] = "The ref from your previous snapshot is stale (the UI changed). Re-pick a ref from fresh_snapshot above and retry."
                return json.dumps(payload, ensure_ascii=False)
            return _render_computer_error(result, client)
        return _verify_and_report(before, action, str(text or ""), str(app or ""), result, front_before_pid)

    class ScriptArgs(BaseModel):
        code: str = Field(
            "",
            description=(
                "JavaScript for the persistent computer-use worker. `cua` is the only global: "
                "await cua.getApp('Safari'|bundleId|path) binds an app; then "
                "app.getAXState({disableDiffing?}) (returns an OBJECT: read `.text` for the tree, `.refs`; there is no `.treeString`), app.getAXStateText() (tree as text), app.getScreenshot(), app.getAXStateAndScreenshot(), "
                "app.click(refOrIndex|[x,y]), app.drag([x,y],[x,y]), app.pressKey('cmd+s'), "
                "app.scroll(target,'down',pages), app.typeText('hi',{submit?}), app.paste('hi'), "
                "app.setValue(ref,'v'), app.focus(ref), app.settle(). "
                "Element targets are observed refs (strings) or integer indices from the SAME cell's "
                "getAXState (call it first). cua.emitText(x)/cua.emitImage(shot) show output. "
                "No require/process/fs/network. Persist across cells via globalThis."
            ),
        )
        reset: bool = Field(False, description="Discard the worker and all bindings before running `code` (with no `code`, just reset the session).")
        timeout_ms: int = Field(0, ge=0, le=60000, description="Optional wall timeout for this cell (default 30s, cap 60s).")

    def _render_script_blocks(blocks: Any, client: Any, data_dir: Any, session_id: str, vision: bool) -> str | list:
        texts: list[str] = []
        images: list[str] = []
        saved: list[str] = []
        for b in (blocks or []):
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "text":
                if b.get("text"):
                    texts.append(str(b["text"]))
            elif kind == "image":
                url = str(b.get("image") or b.get("image_url") or b.get("data") or "")
                if not url:
                    continue
                if (
                    isinstance(url, str)
                    and url.startswith("data:")
                    and looks_like_image_data_url(url)
                ):
                    if vision:
                        images.append(url)
                    else:
                        path = save_screenshot(url, data_dir, session_id)
                        if path:
                            saved.append(path)
                else:
                    texts.append(f"[image block omitted: {url[:80]}]")
        body = "\n".join(t for t in texts if t).strip()
        if images:
            out: list[Any] = []
            if body:
                out.append({"type": "text", "text": body})
            for url in images:
                out.append({"type": "image_url", "image_url": {"url": url}})
            return out
        if saved:
            note = "This model has no vision; screenshots were saved to disk: " + ", ".join(saved)
            return json.dumps({"output": body, "note": note}, ensure_ascii=False)
        # A cell that emits nothing is easy to misread as a failure. Say it ran,
        # and point at the real readback API: `app.getAXState()` returns an OBJECT
        # ({text, refs, ...}) — read `.text` / `app.getAXStateText()`; there is no
        # `.treeString`.
        return body or (
            "The cell ran successfully but emitted no output. To read state use "
            "`await app.getAXState()` (returns an object; read `.text` for the tree or "
            "use `await app.getAXStateText()`), then `cua.emitText(...)` to show it."
        )

    @tool(args_schema=ScriptArgs)
    def computer_script(code: str = "", reset: bool = False, timeout_ms: int = 0) -> str | list:
        """Drive the desktop with a persistent JavaScript session (primary structure-first surface).

        This is the preferred way to control apps: you write JS that binds an app
        (``await cua.getApp('Music')``) and then observes and acts on it in ONE call —
        including loops and conditional logic — so a multi-step task (click a field, type,
        press Return, verify) does not need a tool round-trip per step. Bindings persist
        across calls; ``reset=true`` discards the worker and bindings, then runs ``code``
        (if given) in the fresh session. Native calls are re-validated by
        the app (pause gate, permissions, app identity), so nothing here bypasses safety.

        The input ladder is accessibility-first: prefer ``app.setValue(ref, text)`` (most
        deterministic), then ``app.typeText(text, {submit})``, then ``app.paste(text)``.
        Always re-observe (``app.getAXState()``) and check the result before claiming
        success. If a call fails twice the same way, stop and ask the user.
        """
        try:
            if reset:
                res = client.ax_script_reset()
                if isinstance(res, dict) and res.get("error_code"):
                    return _render_computer_error(res, client)
                _recent["computer_script"].clear()
                # reset with no code is a pure session reset; reset + code resets
                # FIRST and then runs the code in the fresh session (do not silently
                # discard the code).
                if not str(code or "").strip():
                    return json.dumps({"reset": True, "note": "Computer-use session reset."}, ensure_ascii=False)
            if not str(code or "").strip():
                return json.dumps({"error": "computer_script requires code (or reset=true)", "error_code": "param_error"}, ensure_ascii=False)
            if _loop_guard("computer_script", str(code)):
                return json.dumps(
                    {
                        "error": "Loop guard: you have submitted this exact script 3 times. Stop repeating it.",
                        "error_code": "loop_guard",
                        "note": (
                            "Change approach: observe the current state first (app.getAXState()), try the "
                            "next rung of the input ladder (setValue -> typeText -> paste -> pressKey), or ask "
                            "the user. Do not re-run the same script."
                        ),
                    },
                    ensure_ascii=False,
                )
            result = client.ax_script(str(code), int(timeout_ms or 0))
        except Exception as exc:  # noqa: BLE001 - tool must never break a turn
            logger.warning("computer_script failed: %s", exc)
            return json.dumps({"error": str(exc)[:500], "error_code": "computer_error"}, ensure_ascii=False)

        if isinstance(result, dict) and result.get("error_code"):
            return _render_computer_error(result, client)
        blocks = result.get("blocks") if isinstance(result, dict) else None
        rendered = _render_script_blocks(blocks, client, data_dir, session_id, vision)
        err = result.get("error") if isinstance(result, dict) else None
        if err:
            note = (
                "The cell raised an error. Inspect the app state before continuing; "
                "do NOT repeat the same failing call."
            )
            if isinstance(rendered, list):
                rendered = rendered + [{"type": "text", "text": f"error: {err}\n{note}"}]
            else:
                rendered = f"{rendered}\nerror: {err}\n{note}"
        return rendered

    return [computer_observe, computer, computer_script]


def resolve_computer_tools(
    data_dir: Path | str | None,
    *,
    vision: bool = False,
    session_id: str = "",
    interactive: bool = True,
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
        tools = build_computer_tools(data_dir, vision=vision, session_id=session_id, interactive=interactive)
    except Exception:  # noqa: BLE001 - a computer misconfig must never break a turn
        logger.warning("computer tools disabled (config error)", exc_info=True)
        return []
    # NOTE: run_applescript is intentionally NOT mounted here. It is decoupled
    # from the OS Computer Use master switch and mounted on macOS directly by
    # build_workspace_tools (execute-phase + HITL gated), so users who only want
    # deterministic app scripting do not need to enable desktop control.
    return tools
