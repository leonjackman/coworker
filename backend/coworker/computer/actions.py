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

from dataclasses import dataclass


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


_TEXT_DESC = "The literal text to enter. Any characters are allowed (including '+'); use press_hotkey for keyboard shortcuts."
_REF_DESC = "Element ref from the latest computer_observe snapshot."
_MODS_DESC = 'Modifier keys from cmd, ctrl, alt, shift (e.g. ["cmd"] for Cmd+Space).'
_APP_DESC = "Target app to act on (display name or bundle id); empty = the frontmost app. Use this so input does not land in the wrong window."

COMPUTER_ACTIONS: tuple[Action, ...] = (
    Action(
        "launch_app",
        (Param("app", required=True, description="App display name (e.g. Calculator), bundle id (com.apple.calculator), or .app path; localized names resolve automatically."),),
        description="Open an installed app.",
        observation_free=True,
    ),
    Action(
        "press_hotkey",
        (
            Param("key", required=True, description="Key name (space, enter, tab, escape, backspace, delete, arrows, home, end, pageup/pagedown, F1..F12, a-z, 0-9, or a single symbol)."),
            Param("modifiers", "list", description=_MODS_DESC),
            Param("app", description=_APP_DESC),
        ),
        description="Press a keyboard shortcut / key chord.",
        observation_free=True,
    ),
    Action("click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Click an element by ref.", locator="ref"),
    Action("double_click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Double-click an element by ref.", locator="ref"),
    Action("right_click_ref", (Param("ref", required=True, description=_REF_DESC),), description="Right-click an element by ref.", locator="ref"),
    Action(
        "type_into",
        (
            Param("text", required=True, description=_TEXT_DESC),
            Param("ref", description="Element ref of the input field (AXTextField/AXTextArea)."),
            Param("submit", "boolean", description="Press Enter after typing (search/submit fields need it while focused)."),
            Param("app", description=_APP_DESC),
        ),
        description="Type literal text into a focused field.",
        locator="ref",
    ),
    Action(
        "type_text",
        (
            Param("text", required=True, description=_TEXT_DESC),
            Param("app", description=_APP_DESC),
        ),
        description="Type literal text (into the target/frontmost app).",
    ),
    Action(
        "scroll",
        (
            Param("dy", "number", description="Vertical delta (positive scrolls down)."),
            Param("dx", "number", description="Horizontal delta."),
        ),
        description="Scroll the frontmost app.",
    ),
    Action(
        "scroll_to",
        (
            Param("scroll_app", required=True, description="Target app name or bundle id to scroll (required)."),
            Param("dx", "number", description="Horizontal delta."),
            Param("dy", "number", description="Vertical delta (positive scrolls down)."),
            Param("scroll_x", "number", description="X within the target app's window."),
            Param("scroll_y", "number", description="Y within the target app's window."),
        ),
        description="Scroll a specific app at a point.",
    ),
    Action("go_back", (), description="Back (Cmd+[).", observation_free=True),
    Action("show", (Param("ref", description=_REF_DESC),), description="Reveal/scroll to an element.", locator="ref"),
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
    ),
    Action("focus_window", (Param("app", required=True),), description="Focus an app window.", agent_tool=False),
    Action("clipboard", (Param("op", required=True), Param("text", "textarea"), Param("app")), description="Read/write the clipboard.", agent_tool=False),
    Action("file_dialog", (Param("path", required=True), Param("app")), description="Use a native file dialog.", agent_tool=False),
    Action("script", (Param("code", required=True), Param("reset", "boolean")), description="Run cw-automa script code.", target="computer_script", agent_tool=False),
)

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
