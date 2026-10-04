"""Single source of truth for OS-level *computer* actions.

Both consumers derive from this catalog:

* ``coworker.computer.bridge_client`` — the agent's single ``computer`` tool
  (its parameter schema is generated from here) and the runtime dispatch.
* ``coworker.workflows.capabilities`` — the workflow action registry used for
  validation, authoring and the Studio UI.

Keeping one definition removes the drift that previously produced a union
tool-schema (every action exposing every other action's params) and content
sniffing (guessing "text vs shortcut" from a string).

Semantics: an action owns its meaning. ``type_text`` / ``type_into`` take an
opaque literal string — every character (including ``+``) is just text.
Keyboard shortcuts belong exclusively to ``press_hotkey``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "string"  # string | number | boolean | list | textarea | object
    required: bool = False
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Action:
    name: str
    params: tuple[Param, ...] = ()
    description: str = ""
    target: str = "computer"  # executor target: computer | computer_script
    locator: str = ""  # "" | "ref" | "coords"
    observation_free: bool = False
    success: str = "no_error"
    agent_tool: bool = True  # exposed on the agent's single `computer` tool
    aliases: tuple[str, ...] = ()
    # ── Declarative wiring (single source; consumed by the JS host kernel, the
    #    driver and the contract test — never re-declared elsewhere):
    rpc: str = ""  # host/helper JSON-RPC method this action maps to
    script: str = ""  # JS sandbox binding method name ("" = not exposed to scripts)
    op: str = ""  # sub-op when rpc is "act" (click/double/right/show)
    shortcut: str = ""  # implicit semantic shortcut (go_back -> "back")
    param_map: dict[str, str] = field(default_factory=dict)  # catalog param -> rpc/script arg
    requires_any: tuple[str, ...] = ()  # at least one of these params must be present


#: Semantic shortcut names (platform-NEUTRAL). The concrete key+modifiers for
#: each name live in the per-platform driver (mac/win), so the shared layer and
#: the model never hardcode a platform's modifier or key. Used by both the
#: `computer` action `press_hotkey` (via the `shortcut` param) and the script API
#: `app.shortcut(name)`.
SEMANTIC_SHORTCUTS: tuple[str, ...] = (
    "copy", "cut", "paste", "save", "save_as", "find", "select_all",
    "undo", "redo", "new", "open", "close", "quit", "print",
    "back", "forward", "zoom_in", "zoom_out", "fullscreen", "refresh",
    "delete",
)

#: The COMPLETE computer-script sandbox surface: `app.<binding>()` methods and
#: `cua.<method>()` calls that the kernel actually implements. This is the single
#: source for the author-time script validator (`validate_scripts`) — the earlier
#: hardcoded copy drifted and falsely rejected real methods. A contract test
#: asserts this equals the kernel's surface (electron/computer-repl-kernel.js).
SCRIPT_APP_METHODS: frozenset[str] = frozenset({
    "getAXState", "getAXStateText", "getScreenshot", "getAXStateAndScreenshot",
    "click", "doubleClick", "rightClick", "clickPoint", "focus", "show",
    "setValue", "performSecondaryAction", "typeText", "typeInto", "paste",
    "selectText", "pressKey", "shortcut", "scroll", "scrollTo", "drag", "settle",
})

SCRIPT_CUA_METHODS: frozenset[str] = frozenset({
    "listApps", "getApp", "launchApp", "focusApp", "getState",
    "emitText", "emitImage", "sleep",
})


_TEXT_DESC = "The literal text to enter. Any characters are allowed (including '+'); use press_hotkey for keyboard shortcuts."
_REF_DESC = "Element ref from the latest computer_observe snapshot."
_MODS_DESC = 'Modifier keys from cmd, ctrl, alt, shift (e.g. ["cmd"] for Cmd+Space; use "ctrl"/"alt" on Windows).'
_APP_DESC = "Target app to act on (macOS display name or bundle id; Windows process name, exe, or window title); empty = the frontmost app. Use this so input does not land in the wrong window."

COMPUTER_ACTIONS: tuple[Action, ...] = (
    Action(
        "launch_app",
        (Param("app", required=True, description="macOS: app display name (e.g. Calculator), bundle id (com.apple.calculator), or .app path. Windows: executable / App Paths name (e.g. notepad, mspaint, calc.exe), a full path, or an installed Start Menu name."),),
        description="Open an installed app.",
        observation_free=True,
        rpc="launch",
        script="launchApp",
    ),
    Action(
        "press_hotkey",
        (
            Param("key", description="Key name (space, enter, tab, escape, backspace, delete, arrows, home, end, pageup/pagedown, F1..F12, a-z, 0-9, or a single symbol)."),
            Param("modifiers", "list", description=_MODS_DESC),
            Param("shortcut", description=f"Semantic shortcut name (preferred; platform-neutral): one of {', '.join(SEMANTIC_SHORTCUTS)}."),
            Param("app", description=_APP_DESC),
        ),
        description="Press a keyboard shortcut / key chord (or a semantic `shortcut`).",
        observation_free=True,
        rpc="press_hotkey",
        script="pressKey",
        requires_any=("key", "shortcut"),
    ),
    Action("click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Click an element by ref.", locator="ref", rpc="act", op="click", script="click"),
    Action("double_click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Double-click an element by ref.", locator="ref", rpc="act", op="double", script="doubleClick"),
    Action("right_click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Right-click an element by ref.", locator="ref", rpc="act", op="right", script="rightClick"),
    Action(
        "type_into",
        (
            Param("text", required=True, description=_TEXT_DESC),
            Param("ref", description="Element ref of the input field (macOS AXTextField/AXTextArea; Windows Edit/Document/ComboBox). Omit to type into the currently focused field of `app` (or the frontmost app)."),
            Param("submit", "boolean", description="Press Enter after typing (search/submit fields need this while focused)."),
            Param("app", description=_APP_DESC),
        ),
        description="Type literal text into a text field (by ref) or the focused field.",
        locator="ref",
        rpc="input_text",
        script="typeInto",
    ),
    Action(
        "type_text",
        (
            Param("text", required=True, description=_TEXT_DESC),
            Param("app", description=_APP_DESC),
        ),
        description="Type literal text (into the target/frontmost app).",
        rpc="type_text",
        script="typeText",
    ),
    Action(
        "scroll",
        (
            Param("dy", "number", description="Vertical delta (positive scrolls down)."),
            Param("dx", "number", description="Horizontal delta."),
        ),
        description="Scroll the frontmost app.",
        rpc="scroll",
        script="scroll",
    ),
    Action(
        "scroll_to",
        (
            Param("app", required=True, description=_APP_DESC + " (required for scroll_to)."),
            Param("dx", "number", description="Horizontal delta."),
            Param("dy", "number", description="Vertical delta (positive scrolls down)."),
            Param("x", "number", description="X within the target app's window."),
            Param("y", "number", description="Y within the target app's window."),
        ),
        description="Scroll a specific app at a point.",
        rpc="scroll_to",
        script="scrollTo",
        param_map={"app": "app", "x": "x", "y": "y"},
    ),
    Action("go_back", (), description="Back (semantic `back` shortcut; Cmd+[ on macOS, Alt+Left on Windows).", observation_free=True, rpc="shortcut", script="shortcut", shortcut="back"),
    Action("show", (Param("ref", description=_REF_DESC),), description="Reveal/scroll to an element.", locator="ref", rpc="act", op="show", script="show"),
    Action(
        "click_coords",
        (
            Param("x", "number", required=True, description="X in the SCREENSHOT's pixel space (read it off the computer_observe screenshot)."),
            Param("y", "number", required=True, description="Y in the SCREENSHOT's pixel space."),
            Param("shot_width", "number", description="Screenshot width from computer_observe; 0 = coordinates are display points."),
            Param("shot_height", "number", description="Screenshot height from computer_observe; 0 = coordinates are display points."),
            Param("display", "number", description="Display index the screenshot was taken from."),
        ),
        description="Click raw coordinates (last resort).",
        locator="coords",
        rpc="click_point",
        script="clickPoint",
    ),
    # Workflow-only actions (not exposed on the agent tool): the executor runs
    # them through the scripting surface.
    Action(
        "drag",
        (
            Param("app"),
            Param("x1", "number", required=True),
            Param("y1", "number", required=True),
            Param("x2", "number", required=True),
            Param("y2", "number", required=True),
        ),
        description="Drag between two points.",
        agent_tool=False,
        rpc="drag",
        script="drag",
    ),
    Action("focus_window", (Param("app", required=True),), description="Focus (bring to front) an already-running app window.", rpc="focus_app", script="focusApp"),
    # clipboard/file_dialog/script are executed by the executor via generated
    # script code (see workflows/env.py `_computer_script_for`), not as direct
    # script-sandbox methods — so they declare no `script` binding.
    Action("clipboard", (Param("op", required=True), Param("text", "textarea"), Param("app")), description="Read/write the clipboard.", agent_tool=False),
    Action("file_dialog", (Param("path", required=True), Param("app")), description="Use a native file dialog.", agent_tool=False),
    Action("script", (Param("code", required=True), Param("reset", "boolean")), description="Run cw-automa script code.", target="computer_script", agent_tool=False),
)


def contract_dump() -> dict[str, Any]:
    """The computer-action contract as plain JSON — the SINGLE source consumed
    by the JS host kernel / driver and by the contract test (scripts/contract-check.js).
    Nothing else may re-declare this surface."""
    return {
        "shortcuts": list(SEMANTIC_SHORTCUTS),
        "actions": [
            {
                "name": a.name,
                "rpc": a.rpc,
                "script": a.script,
                "op": a.op,
                "shortcut": a.shortcut,
                "param_map": dict(a.param_map),
                "target": a.target,
                "locator": a.locator,
                "success": a.success,
                "agent_tool": a.agent_tool,
                "requires_any": list(a.requires_any),
                "params": [
                    {"name": p.name, "type": p.type, "required": p.required, "aliases": list(p.aliases)}
                    for p in a.params
                ],
            }
            for a in COMPUTER_ACTIONS
        ],
    }

ACTION_MAP: dict[str, Action] = {a.name: a for a in COMPUTER_ACTIONS}


def agent_actions() -> tuple[Action, ...]:
    """Actions exposed on the agent's single ``computer`` tool."""
    return tuple(a for a in COMPUTER_ACTIONS if a.agent_tool)


def agent_union_params() -> dict[str, Param]:
    """Union of params across agent actions, keyed by name (for the flat tool schema)."""
    out: dict[str, Param] = {}
    for action in agent_actions():
        for param in action.params:
            if param.name in out:
                continue
            out[param.name] = param
    return out


def param_names(action: Action) -> set[str]:
    return {p.name for p in action.params}
