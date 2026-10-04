"""Capability registry — the single source of truth for the workflow DSL.

Before this module, what a step could *do* was re-declared (and drifted) in six
places: the executor's dispatch branches, ``env``'s locator merge, the recorder's
kind inference, the review prompt, the frontend action catalog, and the parser's
structural validation. A workflow could therefore be saved that no adapter could
run (``tool`` without ``do``, ``skill`` with params, undefined template refs,
locators whose keys nobody read) and, worse, one whose failures were reported as
success.

This registry fixes that at the root: every kind/action/param/locator/success
rule is declared ONCE here. The validator, the executor, the authoring pipeline,
the review prompt and the UI all consume this object, so the DSL can never drift
from the capabilities that actually exist.

Sources:
* DSL-native capabilities (command/wait/set/assert/control/skill/… ) are declared.
* Tool-backed capabilities (browser / computer / arbitrary tools) are DERIVED by
  introspecting the live tool ``args_schema`` via :func:`CapabilityRegistry.from_tools`;
  the declared specs are the offline fallback and a drift guard (see tests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

DSL_VERSION = 2

# Template reference roots allowed anywhere a string is templated.
# `loop` exposes the current item/index inside `foreach` bodies (the executor
# sets run.context["loop"] = {"item", "index"}).
TEMPLATE_ROOTS = ("inputs", "steps", "vars", "env", "loop")


def _action_platforms(name: str) -> list[str]:
    """Platform tags an action is restricted to (empty = runs everywhere)."""
    from .platform_support import PLATFORM_ONLY_TOOLS

    tags = PLATFORM_ONLY_TOOLS.get(str(name or ""))
    return sorted(tags) if tags else []


def _kind_platforms(spec: "KindSpec") -> set[str]:
    """Platform restriction for a whole kind.

    A kind is restricted only when EVERY action is restricted to the same set;
    if any action runs everywhere (empty), the kind runs everywhere. This stops
    the generic `tool` kind from being mislabeled darwin/win32 just because it
    also declares the OS-specific run_applescript/run_powershell actions.
    """
    if not spec.actions:
        return set()
    tags: set[str] = set()
    for action in spec.actions:
        action_tags = set(_action_platforms(action.name))
        if not action_tags:
            return set()
        tags |= action_tags
    return tags

#: Action kinds whose BINDING can be resolved by the agent from an intent-only
#: step (goal set, no ``do``). 絕對遵守 nodes are excluded by callers.
RESOLVABLE_INTENT_KINDS = frozenset(
    {"browser", "computer", "app", "file", "transform", "http", "notify", "tool", "command"}
)

# Top-level YAML document keys (single source for the authoring spec).
DOCUMENT_KEYS: tuple[dict[str, str], ...] = (
    {"name": "required — workflow name (letters/numbers/space/-/_)"},
    {"description": "required — one sentence"},
    {"steps": "required — ordered list of step mappings"},
    {"inputs": "optional — mapping of input name → {type, required, default}"},
    {"outputs": "optional — mapping of output name → template ref"},
    {"triggers": "optional — list, e.g. [manual]"},
    {"platform": "optional"},
    {"status": "optional — active | draft | deprecated"},
)

# Per-step keys (single source for the authoring spec).
STEP_KEYS: tuple[dict[str, str], ...] = (
    {"id": "step id, e.g. \"id:1\""},
    {"kind": "required — one of the capability kinds"},
    {"do": "the action/tool/skill name (see capabilities)"},
    {"params": "mapping of action parameters (see capabilities)"},
    {"locator": "semantic target descriptor: selector / ref / coords (GUI steps)"},
    {"pre": "list of pre-condition specs (forms below)"},
    {"post": "list of post-condition specs (forms below), e.g. \"equals result.return_code 0\""},
    {"success": "list of success specs (same forms as post)"},
    {"goal": "natural-language goal (agentic/skill steps)"},
    {"mode": "auto | agent (agent hands the step to the model)"},
    {"on_error": "{retry, then: abort|skip|human|agent|goto:<id>}"},
    {"timeout": "seconds"},
    {"when": "condition (template or comparison)"},
    {"foreach": "template ref to a list; body runs per item"},
    {"then": "sub-steps (branch)"},
    {"else": "sub-steps (branch)"},
    {"body": "sub-steps (loop/parallel)"},
    {"next": "explicit next step id (linear chain)"},
    {"approval": "bool — require human approval"},
    {"description": "required — short human-readable node label (shown in the Studio)"},
    {"bypass": "list of conformance codes to downgrade to warnings (e.g. [coord_only_locator])"},
    {"bypass_reason": "why the bypass is justified"},
    {"absolute": "user-only HARD constraint (絕對遵守): existing intent/binding may not be changed, "
                 "substituted, self-healed, or taken over; agent writes may not set it"},
)

EXAMPLE_YAML = """name: my-flow
description: What this workflow does, in one sentence.
inputs:
  url:
    type: string
    required: true
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    params:
      url: "{{inputs.url}}"
    description: Open the page
  - id: "id:2"
    kind: command
    do: run
    params:
      command: echo done
    description: Run the command
    post:
      - "equals result.return_code 0"
"""

KIND_FAMILIES = {
    "command": "action",
    "tool": "action",
    "browser": "action",
    "app": "action",
    "computer": "action",
    "http": "action",
    "file": "action",
    "transform": "action",
    "notify": "action",
    "skill": "agent",
    "agentic": "agent",
    "human": "human",
    "set": "verify",
    "assert": "verify",
    "wait": "verify",
    "branch": "control",
    "loop": "control",
    "parallel": "control",
    "subworkflow": "control",
}


# ── specs ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str = "string"  # string|number|boolean|list|object
    required: bool = False
    default: Any = None
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocatorPolicy:
    """Which semantic locator keys are accepted and how they fill params.

    ``maps`` turns a locator descriptor into concrete action params (e.g.
    ``{"coords": ("x", "y")}`` for a browser click, or ``{"selector": ("ref",)}``).
    """

    keys: tuple[str, ...] = ("selector", "ref", "coords")
    require_any: bool = False
    maps: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    name: str
    params: tuple[ParamSpec, ...] = ()
    target: str = ""  # tool/method the executor dispatches to
    arg_map: dict[str, str] = field(default_factory=dict)  # spec param -> target arg
    success: str = "result_ok"  # key into SUCCESS_RULES
    locator: LocatorPolicy | None = None
    aliases: tuple[str, ...] = ()
    description: str = ""
    outputs: tuple[str, ...] = ()  # fields of the result usable as {{steps.<id>.<field>}}

    def param(self, name: str) -> ParamSpec | None:
        for p in self.params:
            if p.name == name or name in p.aliases:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "aliases": list(self.aliases),
            "params": [p.__dict__ for p in self.params],
            "locator": (
                {"keys": list(self.locator.keys), "require_any": self.locator.require_any}
                if self.locator
                else None
            ),
            "success": self.success,
            "outputs": list(self.outputs),
            "platform": _action_platforms(self.name) or ["any"],
            "description": self.description,
        }


@dataclass(frozen=True)
class KindSpec:
    kind: str
    family: str
    requires_do: bool
    actions: tuple[ActionSpec, ...] = ()
    native_params: tuple[ParamSpec, ...] = ()
    nested: tuple[str, ...] = ()
    agentic: bool = False  # executed through the agent (skill/agentic)
    open_actions: bool = False  # action name is not enumerable (e.g. arbitrary tools)
    description: str = ""

    def action(self, name: str) -> ActionSpec | None:
        for a in self.actions:
            if a.name == name or name in a.aliases:
                return a
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "family": self.family,
            "requires_do": self.requires_do,
            "agentic": self.agentic,
            "open_actions": self.open_actions,
            "nested": list(self.nested),
            "native_params": [p.__dict__ for p in self.native_params],
            "actions": [a.to_dict() for a in self.actions],
            "platform": sorted(_kind_platforms(self)) or ["any"],
            "description": self.description,
        }


@dataclass(frozen=True)
class Diagnostic:
    """One structured validation problem (surfaced to UI + authoring repair).

    ``severity`` is "error" (blocks storage) or "warning" (advisory — e.g. an
    agentic goal that looks like it bundles several actions).
    """

    step_id: str
    field: str
    code: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        loc = f"step '{self.step_id}'" if self.step_id else "workflow"
        tag = "" if self.severity == "error" else f"[{self.severity}] "
        return f"{tag}{loc}: {self.message}" + (f" [{self.field}]" if self.field else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ResolvedAction:
    step_id: str
    kind: str
    action: str
    target: str
    args: dict[str, Any]
    success: str
    locator: dict[str, Any] | None = None


# ── success rules (fail-closed) ──────────────────────────────────────────


def _parse(result: Any) -> Any:
    if isinstance(result, str):
        text = result.strip()
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return result
    return result


def rule_command_rc(result: Any, params: dict[str, Any] | None = None) -> bool:
    params = params or {}
    if params.get("allow_failure"):
        return True
    ok_codes = params.get("ok_codes")
    data = result if isinstance(result, dict) else _parse(result)
    if isinstance(data, dict) and "return_code" in data:
        rc = int(data.get("return_code") or 0)
        if isinstance(ok_codes, list):
            return rc in [int(c) for c in ok_codes]
        return rc == 0
    return True


def rule_no_error(result: Any, params: dict[str, Any] | None = None) -> bool:
    """GUI/tool results: any error / error_code / status=error means failure."""
    data = _parse(result)
    if data is None:
        return True
    if isinstance(data, bool):
        return data
    if isinstance(data, dict):
        if data.get("error") or data.get("error_code"):
            return False
        if str(data.get("status") or "").lower() in ("error", "failed"):
            return False
        if data.get("ok") is False:
            return False
    return True


def rule_observable_change(result: Any, params: dict[str, Any] | None = None) -> bool:
    """A state-changing GUI action (click/type) must produce an observable
    effect (DOM change / navigation / download). This is what turns the old
    "clicked but nothing happened" false-success into a real failure.

    Falls back to ``no_error`` when the result carries no ``changed`` signal
    (e.g. an adapter that predates this check), so it never over-fails.
    """
    data = _parse(result)
    if isinstance(data, dict):
        if "changed" in data:
            if data.get("changed") is True:
                return True
            # Explicit "nothing changed" is a verification failure unless the
            # adapter also reported a hard error (which is failure anyway).
            return False
        return rule_no_error(result, params)
    return rule_no_error(result, params)


def rule_result_ok(result: Any, params: dict[str, Any] | None = None) -> bool:
    if result is None:
        return True
    if isinstance(result, bool):
        return result
    if isinstance(result, (str, list, dict)):
        return rule_no_error(result, params) and bool(result) or result == 0
    return True


def rule_http_status(result: Any, params: dict[str, Any] | None = None) -> bool:
    """HTTP success = 2xx by default, or an explicit ``expect_status`` match."""
    params = params or {}
    data = result if isinstance(result, dict) else _parse(result)
    if not isinstance(data, dict):
        return True
    status = data.get("status")
    if status is None:
        return True
    try:
        code = int(status)
    except (TypeError, ValueError):
        return True
    expect = params.get("expect_status")
    if expect is None:
        return 200 <= code < 300
    codes = expect if isinstance(expect, list) else [expect]
    return code in [int(c) for c in codes]


SUCCESS_RULES: dict[str, Callable[[Any, dict[str, Any] | None], bool]] = {
    "command_rc": rule_command_rc,
    "no_error": rule_no_error,
    "result_ok": rule_result_ok,
    "http_status": rule_http_status,
    "observable_change": rule_observable_change,
}


def check_success(rule: str, result: Any, params: dict[str, Any] | None = None) -> bool:
    fn = SUCCESS_RULES.get(rule, rule_result_ok)
    try:
        return bool(fn(result, params))
    except Exception:  # noqa: BLE001 - a broken rule must fail closed
        return False


# ── declared (offline) specs ─────────────────────────────────────────────


def _p(name: str, type_: str = "string", required: bool = False, **kw: Any) -> ParamSpec:
    return ParamSpec(name=name, type=type_, required=required, **kw)


BROWSER_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("browser", "navigate", (_p("url", required=True),), target="browser", success="no_error"),
    ActionSpec("browser", "get_state", (), target="browser", success="no_error"),
    ActionSpec("browser", "get_text", (_p("max_chars", "number"),), target="browser", success="no_error"),
    ActionSpec("browser", "snapshot", (_p("max_items", "number"),), target="browser", success="no_error"),
    ActionSpec("browser", "screenshot", (), target="browser", success="no_error"),
    ActionSpec(
        "browser", "click",
        (_p("x", "number"), _p("y", "number"), _p("selector"), _p("text"), _p("exact", "boolean"), _p("role")),
        target="browser", success="observable_change",
        # Semantic target first: a selector or text (role/name-ish) is preferred;
        # raw coords are the last resort in a fallback ladder.
        locator=LocatorPolicy(keys=("selector", "text", "ref", "coords"), require_any=False,
                              maps={"coords": ("x", "y"), "selector": ("selector",), "text": ("text",),
                                    "ref": ("selector",)}),
    ),
    ActionSpec("browser", "type", (_p("text", required=True),), target="browser", success="no_error"),
    ActionSpec("browser", "press", (_p("key", required=True),), target="browser", success="no_error"),
    ActionSpec("browser", "scroll", (_p("dx", "number"), _p("dy", "number")), target="browser", success="no_error"),
    ActionSpec("browser", "back", (), target="browser", success="no_error"),
    ActionSpec("browser", "forward", (), target="browser", success="no_error"),
    ActionSpec("browser", "reload", (), target="browser", success="no_error"),
    ActionSpec("browser", "evaluate", (_p("expression", required=True),), target="browser", success="no_error"),
    ActionSpec("browser", "click_selector", (_p("selector", required=True),), target="browser", success="observable_change"),
    ActionSpec("browser", "click_text", (_p("text", required=True), _p("exact", "boolean"), _p("role")), target="browser", success="observable_change"),
    ActionSpec("browser", "scroll_to", (_p("selector"), _p("text")), target="browser", success="no_error"),
    ActionSpec("browser", "wait_for", (_p("selector"), _p("text"), _p("timeout_ms", "number")), target="browser", success="no_error"),
    ActionSpec("browser", "upload", (_p("files", "list", required=True), _p("selector")), target="browser", success="no_error"),
    # Account / personal-data actions (agent-facing browser tool).
    ActionSpec("browser", "login", (_p("site"), _p("username"), _p("submit", "boolean")), target="browser", success="no_error"),
    ActionSpec("browser", "list_bookmarks", (), target="browser", success="no_error"),
    ActionSpec("browser", "add_bookmark", (_p("url", required=True), _p("title")), target="browser", success="no_error"),
    ActionSpec("browser", "remove_bookmark", (_p("url", required=True),), target="browser", success="no_error"),
    ActionSpec("browser", "list_history", (_p("query"),), target="browser", success="no_error"),
    ActionSpec("browser", "clear_history", (), target="browser", success="no_error"),
    ActionSpec("browser", "list_downloads", (), target="browser", success="no_error"),
    ActionSpec("browser", "clear_downloads", (), target="browser", success="no_error"),
    ActionSpec("browser", "list_permissions", (), target="browser", success="no_error"),
    ActionSpec("browser", "list_sites", (), target="browser", success="no_error"),
    ActionSpec("browser", "clear_site_data", (_p("origin", required=True),), target="browser", success="no_error"),
)

_COMPUTER_LOCATOR = LocatorPolicy(keys=("ref", "coords"), maps={"ref": ("ref",), "coords": ("x", "y")})
_COMPUTER_COORDS_LOCATOR = LocatorPolicy(keys=("coords",), require_any=False, maps={"coords": ("x", "y")})


def _computer_actions() -> tuple[ActionSpec, ...]:
    """Build the computer action registry from the single action catalog."""
    from coworker.computer.actions import COMPUTER_ACTIONS as definitions

    locators = {"ref": _COMPUTER_LOCATOR, "coords": _COMPUTER_COORDS_LOCATOR}
    out: list[ActionSpec] = []
    for definition in definitions:
        out.append(
            ActionSpec(
                "computer",
                definition.name,
                tuple(
                    ParamSpec(
                        name=p.name,
                        type=p.type,
                        required=p.required,
                        description=p.description,
                        aliases=p.aliases,
                    )
                    for p in definition.params
                ),
                target=definition.target,
                success=definition.success,
                locator=locators.get(definition.locator),
                aliases=definition.aliases,
                description=definition.description,
            )
        )
    return tuple(out)


COMPUTER_ACTIONS: tuple[ActionSpec, ...] = _computer_actions()

# Arbitrary tool steps fall back to these when no live tool_map is available.
# `run_applescript`/`run_powershell` are declared so the catalog (and OFFLINE
# validation) always knows they exist and which OS each pins (see
# PLATFORM_ONLY_TOOLS) — previously they were only visible via live introspection.
TOOL_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("tool", "web_search", (_p("query", required=True), _p("max_results", "number")), target="web_search", success="no_error"),
    ActionSpec("tool", "web_fetch", (_p("url", required=True),), target="web_fetch", success="no_error"),
    ActionSpec(
        "tool", "run_applescript",
        (_p("script", required=True), _p("timeout_seconds", "number")),
        target="run_applescript", success="no_error",
    ),
    ActionSpec(
        "tool", "run_powershell",
        (_p("script", required=True), _p("timeout_seconds", "number")),
        target="run_powershell", success="no_error",
    ),
)

COMMAND_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        "command", "run",
        (_p("command", required=True, aliases=("run", "argv")), _p("cwd"), _p("timeout", "number"),
         _p("shell", "boolean"), _p("allow_failure", "boolean"), _p("ok_codes", "list")),
        target="run_command",
        arg_map={"command": "command", "cwd": "cwd", "timeout": "timeout_seconds"},
        success="command_rc",
    ),
)

HTTP_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        "http", "request",
        (_p("url", required=True), _p("method"), _p("headers", "object"), _p("query", "object"),
         _p("json", "object"), _p("body", "textarea"), _p("auth"), _p("token"), _p("username"),
         _p("password"), _p("expect_status"), _p("timeout", "number")),
        target="http", success="http_status",
        description="Generic HTTP request (GET/POST/PUT/PATCH/DELETE/HEAD).",
    ),
)

FILE_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("file", "read", (_p("path", required=True), _p("encoding")), target="file"),
    ActionSpec("file", "write", (_p("path", required=True), _p("content", "textarea"), _p("encoding"), _p("mkdirs", "boolean")), target="file"),
    ActionSpec("file", "append", (_p("path", required=True), _p("content", "textarea"), _p("encoding"), _p("mkdirs", "boolean")), target="file"),
    ActionSpec("file", "copy", (_p("path", required=True), _p("to", required=True), _p("mkdirs", "boolean")), target="file"),
    ActionSpec("file", "move", (_p("path", required=True), _p("to", required=True), _p("mkdirs", "boolean")), target="file"),
    ActionSpec("file", "delete", (_p("path", required=True),), target="file"),
    ActionSpec("file", "mkdir", (_p("path", required=True),), target="file"),
    ActionSpec("file", "list", (_p("path", required=True), _p("pattern")), target="file"),
    ActionSpec("file", "glob", (_p("path", required=True), _p("pattern"), _p("recursive", "boolean")), target="file"),
    ActionSpec("file", "newest", (_p("path", required=True), _p("pattern"), _p("recursive", "boolean")), target="file", success="no_error"),
    ActionSpec("file", "exists", (_p("path", required=True),), target="file"),
    ActionSpec("file", "stat", (_p("path", required=True),), target="file"),
    ActionSpec("file", "unzip", (_p("path", required=True), _p("to", required=True)), target="file", success="no_error"),
    ActionSpec("file", "zip", (_p("path", required=True), _p("to", required=True)), target="file", success="no_error"),
)

TRANSFORM_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("transform", "json_parse", (_p("text", "textarea", required=True),), target="transform"),
    ActionSpec("transform", "json_path", (_p("data"), _p("path", required=True)), target="transform"),
    ActionSpec("transform", "regex", (_p("text", "textarea"), _p("pattern", required=True), _p("group", "number"), _p("replace", "textarea"), _p("mode")), target="transform"),
    ActionSpec("transform", "template", (_p("text", "textarea", required=True), _p("vars", "object")), target="transform"),
    ActionSpec("transform", "csv_parse", (_p("text", "textarea", required=True), _p("delimiter")), target="transform"),
    ActionSpec("transform", "base64", (_p("op", required=True), _p("text", "textarea", required=True)), target="transform"),
    ActionSpec("transform", "date_format", (_p("value"), _p("from_format"), _p("to_format")), target="transform"),
)

NOTIFY_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("notify", "notification", (_p("title"), _p("body", "textarea")), target="notify"),
    ActionSpec("notify", "webhook", (_p("url", required=True), _p("method"), _p("headers", "object"), _p("json", "object"), _p("body", "textarea"), _p("expect_status")), target="notify", success="http_status"),
)

# Tools an authoring agent may target with a `tool` step (deterministic, no loop).
_TOOL_EXCLUDED = {
    "browser", "computer", "computer_observe", "computer_script",
    "use_worker", "use_workers", "delegate_task", "delegate_parallel",
    "create_team", "create_team_member", "skill_manage", "install_skill",
    "load_skill", "write_todos", "ask_user", "update_goal",
}


# Declared result fields per (kind, action) — used to type-check {{steps.<id>.<field>}}.
_OUTPUTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("command", "run"): ("return_code", "stdout", "stderr", "timed_out"),
    ("http", "request"): ("status", "ok", "headers", "text", "json"),
    ("file", "read"): ("path", "content"),
    ("file", "write"): ("path", "written"),
    ("file", "append"): ("path", "appended"),
    ("file", "copy"): ("path", "to"),
    ("file", "move"): ("path", "to"),
    ("file", "delete"): ("path", "deleted"),
    ("file", "mkdir"): ("path", "created"),
    ("file", "list"): ("path", "items", "count"),
    ("file", "glob"): ("path", "items", "count"),
    ("file", "exists"): ("path", "exists"),
    ("file", "stat"): ("path", "exists", "is_dir", "size"),
    ("file", "newest"): ("path", "found", "mtime", "count"),
    ("file", "unzip"): ("path", "to", "names", "items", "count"),
    ("file", "zip"): ("path", "to", "count"),
    ("transform", "json_path"): ("result",),
    ("transform", "json_parse"): ("result",),
    ("transform", "regex"): ("result", "matched", "groups"),
    ("transform", "template"): ("result",),
    ("transform", "csv_parse"): ("rows", "result", "count"),
    ("transform", "base64"): ("result",),
    ("transform", "date_format"): ("result",),
    ("notify", "notification"): ("notified", "title", "body"),
    ("notify", "webhook"): ("status", "ok", "text", "json"),
    ("tool", "web_search"): ("results", "result"),
    ("tool", "web_fetch"): ("text", "result"),
    ("tool", "run_applescript"): ("return_code", "stdout", "stderr", "timed_out"),
    ("tool", "run_powershell"): ("return_code", "stdout", "stderr", "timed_out"),
    # computer/script returns the cell's rendered text (string), so it has no
    # sub-fields; declaring "result"/"text" lets the validator reject
    # `{{steps.<id>.outputs...}}` and unknown fields.
    ("computer", "script"): ("result", "text"),
    ("app", "script"): ("result", "text"),
}


def _with_outputs(actions: tuple[ActionSpec, ...]) -> tuple[ActionSpec, ...]:
    from dataclasses import replace as _replace

    return tuple(
        _replace(a, outputs=_OUTPUTS.get((a.kind, a.name), a.outputs)) for a in actions
    )


def _declared_kinds() -> dict[str, KindSpec]:
    app_actions = _with_outputs(COMPUTER_ACTIONS)
    kinds = [
        KindSpec("browser", "action", True, actions=_with_outputs(BROWSER_ACTIONS), description="Drive the embedded browser."),
        KindSpec("app", "action", True, actions=_with_outputs(app_actions), description="Drive a desktop app (computer tool)."),
        KindSpec("computer", "action", True, actions=_with_outputs(app_actions), description="Drive the desktop (computer tool)."),
        KindSpec("tool", "action", True, actions=_with_outputs(TOOL_ACTIONS), open_actions=True, description="Call a workspace/web tool by name."),
        KindSpec("command", "action", False, actions=_with_outputs(COMMAND_ACTIONS), description="Run a local command (argv; shell requires opt-in)."),
        KindSpec("http", "action", True, actions=_with_outputs(HTTP_ACTIONS), description="Make an HTTP/API request."),
        KindSpec("file", "action", True, actions=_with_outputs(FILE_ACTIONS), description="Read/write/move/delete/list/archive local files (incl. zip/unzip)."),
        KindSpec("transform", "action", True, actions=_with_outputs(TRANSFORM_ACTIONS), description="Transform data (json/regex/template/csv/base64/date)."),
        KindSpec("notify", "action", True, actions=_with_outputs(NOTIFY_ACTIONS), description="Desktop notification or outbound webhook."),
        KindSpec("skill", "agent", True, native_params=(_p("goal"),), agentic=True,
                 description="Hand a reusable skill + goal to the agent to perform."),
        KindSpec("agentic", "agent", True, native_params=(_p("goal"),), agentic=True,
                 description="Hand this step to the agent."),
        KindSpec("human", "human", True, native_params=(_p("options", "list"),), description="Ask the user."),
        KindSpec("set", "verify", False, native_params=(_p("name"), _p("value")), description="Set a run variable."),
        KindSpec("assert", "verify", False, native_params=(_p("post", "list"),), description="Assert a condition."),
        KindSpec("wait", "verify", False, native_params=(_p("seconds", "number"),), description="Wait N seconds."),
        KindSpec("branch", "control", False, nested=("then", "else"), native_params=(_p("when"),), description="Conditional."),
        KindSpec("loop", "control", False, nested=("body",), native_params=(_p("foreach"), _p("as")), description="Loop over items."),
        KindSpec("parallel", "control", False, nested=("body",), native_params=(_p("max_workers", "number"),), description="Run body steps in parallel."),
        KindSpec("subworkflow", "control", True, native_params=(_p("inputs", "object"),), description="Run another workflow."),
    ]
    return {k.kind: k for k in kinds}


# ── registry ─────────────────────────────────────────────────────────────


@dataclass
class CapabilityRegistry:
    kinds: dict[str, KindSpec] = field(default_factory=dict)
    #: True when ``tool`` actions came from a LIVE tool map (introspected). Only
    #: then can "is this a real tool?" be answered; offline declared specs are a
    #: tiny fallback, so the unknown-tool conformance check is skipped there.
    live_tools: bool = False

    @classmethod
    def declared(cls) -> "CapabilityRegistry":
        return cls(kinds=_declared_kinds())

    @classmethod
    def from_tools(cls, tool_map: dict[str, Any] | None) -> "CapabilityRegistry":
        """Declared specs, then OVERRIDE browser/computer/tool with live schemas."""
        base = cls.declared()
        if not tool_map:
            return base
        kinds = dict(base.kinds)
        browser = _introspect_tool(tool_map.get("browser"), "browser")
        if browser:
            kinds["browser"] = browser
        comp = _introspect_tool(tool_map.get("computer"), "computer")
        if comp:
            kinds["computer"] = comp
            kinds["app"] = KindSpec("app", "action", True, actions=comp.actions, description=comp.description)
        tools = _introspect_tools(tool_map)
        if tools:
            kinds["tool"] = KindSpec("tool", "action", True, actions=tools, open_actions=True, description="Call a workspace/web tool by name.")
        return cls(kinds=kinds, live_tools=True)

    def kind(self, kind: str) -> KindSpec | None:
        return self.kinds.get(kind)

    def actions(self, kind: str) -> tuple[ActionSpec, ...]:
        spec = self.kinds.get(kind)
        return spec.actions if spec else ()

    def action(self, kind: str, name: str) -> ActionSpec | None:
        spec = self.kinds.get(kind)
        return spec.action(name) if spec else None

    # ── resolution (shared by validator + executor) ───────────────────────

    def resolve(self, step: Any) -> tuple[ResolvedAction | None, list[Diagnostic]]:
        kind = str(getattr(step, "kind", "") or "")
        step_id = str(getattr(step, "id", "") or "")
        spec = self.kinds.get(kind)
        if spec is None:
            return None, [Diagnostic(step_id, "kind", "unknown_kind", f"unknown step kind '{kind}'")]

        do = str(getattr(step, "do", "") or "")
        params = dict(getattr(step, "params", {}) or {})
        locator = getattr(step, "locator", None) or None

        if spec.family != "action" or spec.agentic:
            return ResolvedAction(step_id, kind, do, kind, params, "result_ok", locator), []

        if spec.requires_do and not do:
            # Intent-only node: the binding is resolved by the agent at run time
            # (P2). Allowed when the kind is resolvable, an intent is stated, and
            # the node is not 絕對遵守 (whose binding must be explicit).
            has_intent = bool(str(getattr(step, "goal", "") or getattr(step, "description", "") or "").strip())
            if kind in RESOLVABLE_INTENT_KINDS and has_intent and not getattr(step, "absolute", False):
                return ResolvedAction(step_id, kind, "", kind, params, "result_ok", locator), []
            return None, [Diagnostic(step_id, "do", "missing_do", f"{kind} step requires 'do'")]

        # `tool` actions are not enumerable offline: accept any name, validate
        # params only against the declared spec when we recognise the tool.
        action = spec.action(do)
        if action is None and spec.open_actions:
            action = ActionSpec(kind, do, target=do, success="no_error")
        # Command steps accept three legacy/canonical forms: do="run" (+params),
        # do=<command string>, or params.command/run with no do.
        if kind == "command" and action is None:
            if do and "command" not in params and "run" not in params:
                action = COMMAND_ACTIONS[0]
                params = {**params, "command": do}
            elif params.get("command") is not None or params.get("run") is not None:
                action = COMMAND_ACTIONS[0]
            else:
                return None, [Diagnostic(step_id, "command", "missing_command", "command step requires a command (do or params.command)")]
        if action is None:
            valid = ", ".join(a.name for a in spec.actions)
            return None, [
                Diagnostic(step_id, "do", "unknown_action", f"{kind} has no action '{do}' (valid: {valid})")
            ]

        diags: list[Diagnostic] = []
        normalized: dict[str, Any] = {}
        locator_out = _normalize_locator(locator)
        for key, value in params.items():
            pspec = action.param(key)
            if pspec is None:
                if action.target == do and spec.open_actions and not spec.action(do):
                    normalized[key] = value  # unknown tool: pass params through
                    continue
                diags.append(Diagnostic(step_id, key, "unknown_param", f"{kind}.{action.name} has no param '{key}'"))
                continue
            if value is None or value == "":
                continue
            normalized[pspec.name] = value
        # locator-filled params count toward required.
        for pspec in action.params:
            if pspec.name in normalized:
                continue
            if locator_out and pspec.name in ("x", "y", "ref", "selector"):
                continue
            if _locator_fills(locator_out, action, pspec.name):
                continue
            if pspec.required:
                diags.append(Diagnostic(step_id, pspec.name, "missing_param", f"{kind}.{action.name} requires '{pspec.name}'"))

        target = action.target or action.name
        if kind == "tool":
            target = do
            args = dict(params)
        elif kind == "command":
            args = {}
            for k, v in params.items():
                mapped = action.arg_map.get(k, k)
                args[mapped] = v
        else:
            args = dict(normalized)
            args = merge_locator_into_args(args, locator_out, action)
            args["action"] = action.name
        if diags:
            return None, diags
        return ResolvedAction(step_id, kind, action.name, target, args, action.success, locator_out), []

    def prompt_catalog(self, *, max_actions: int = 60) -> str:
        """A compact, model-readable catalog of what steps can actually do.

        Fed to the authoring/review LLM so it can only propose valid steps.
        """
        lines = [
            "Only these step kinds/actions/params are valid (anything else is rejected):",
        ]
        for spec in self.kinds.values():
            if spec.agentic:
                lines.append(f"- kind: {spec.kind} — do = the {spec.kind} name (no other params)")
                continue
            if spec.family == "action" and spec.actions:
                names = [a.name for a in spec.actions][:max_actions]
                lines.append(f"- kind: {spec.kind} — do ∈ {{{', '.join(names)}}}")
                example = spec.actions[0]
                param_names = ", ".join(p.name + ("*" if p.required else "") for p in example.params)
                if param_names:
                    lines.append(f"    params for do={example.name}: {param_names}  (* = required)")
            elif spec.native_params:
                names = ", ".join(p.name for p in spec.native_params)
                lines.append(f"- kind: {spec.kind} — params: {names or '(none)'}")
            else:
                lines.append(f"- kind: {spec.kind}")
        lines.append(
            "Templates: use only {{inputs.<name>}} (declared inputs), {{steps.<id>.<field>}} "
            "(existing step ids; <field> = the action's declared output fields, NOT a "
            "'{{...outputs...}}' prefix) and {{vars.<name>}}. A step that returns plain text "
            "is referenced as {{steps.<id>}}. Other references are rejected."
        )
        lines.append(
            "command runs argv WITHOUT a shell by default; add param shell: true only "
            "if you truly need pipes/globs/$(...). Browser click needs x/y (or a "
            "snapshot-derived coords locator); computer click_ref needs a real ref."
        )
        lines.append(
            "Use declared inputs in steps; finish with a step that VERIFIES the goal "
            "(artifact exists and is non-empty / page shows the expected state), not "
            "just a zero exit code."
        )
        lines.append(
            "Every step needs a `description` (short human-readable label). Atomic nodes only: "
            "one action per step — never a shell blob (| ; && $(...)) or a click/submit `evaluate`. "
            "GUI steps use a semantic locator (role/name), not raw coordinates; target files by "
            "explicit path. Violations are rejected at save time."
        )
        return "\n".join(lines)

    def authoring_pointer(self) -> str:
        """Short, always-on pointer injected into the system prompt.

        The full authoring spec (skeleton + rules + example) is long (~1.2k
        tokens) and, when injected on every model call, dominates the fixed
        prompt cost. Conformance is now enforced by the validator at write time,
        so the prompt only needs to point at the on-demand spec/validator.
        """
        return (
            "\n\n## Workflow authoring (YAML)\n"
            "Author or modify workflows ONLY with the `workflow` tool (action create/update, full "
            "YAML `content`). Before writing, call `workflow` action=spec for the exact skeleton/"
            "rules and action=capabilities (a tiny overview; then action=capabilities kinds=[...] "
            "for the actions/params of just the kinds you need — never fetch every kind). A workflow "
            "must be atomic, user-readable nodes: one action per node, a `description` on every node, "
            "a semantic locator for GUI steps, and a verification step at the end. create/update are "
            "validated at write time and return diagnostics — fix them and resubmit until status ok."
        )

    def authoring_text(self) -> str:
        """Full authoring spec (YAML skeleton + rules + example), fetched on demand.

        No longer injected on every model call (that was a fixed ~1.2k-token cost);
        it is returned by ``workflow`` action=spec when the agent is about to
        author, and enforced by the validator at write time.
        """
        doc = "\n".join(f"  - {k}: {v}" for d in DOCUMENT_KEYS for k, v in d.items())
        step = "\n".join(f"  - {k}: {v}" for d in STEP_KEYS for k, v in d.items())
        return (
            "\n\n## Workflow authoring (YAML)\n"
            "Author a workflow ONLY with the `workflow` tool (action create/update, full YAML "
            "`content`). Never write a markdown file as a workflow.\n\n"
            f"Top-level keys:\n{doc}\n\n"
            f"Step keys:\n{step}\n\n"
            "## Decompose: ONE ACTION PER NODE (most important rule)\n"
            "A workflow must be a graph of ATOMIC action nodes (like Dify/ComfyUI): every "
            "node does exactly ONE action. Never bundle a sequence into one step.\n"
            "- NEVER write a step whose goal/params describe several actions (open app AND "
            "navigate AND click AND wait). Split each into its own node.\n"
            "- `agentic`/`skill` are a LAST RESORT for a single operation with no deterministic "
            "capability. If you can express it with command/browser/computer/tool, do NOT use "
            "agentic. If the goal text contains 然后/接著/并且/再/；/「→」 or multiple verbs, SPLIT it.\n\n"
            "Bad (bundled) vs Good (decomposed):\n"
            "```yaml\n"
            "# BAD — one node describing a whole sequence in natural language\n"
            "- id: \"id:1\"\n"
            "  kind: agentic\n"
            "  do: browser-desktop-action\n"
            "  goal: 打開 Safari，導航到平台，點擊導出，點允許，等待下載完成\n"
            "```\n"
            "```yaml\n"
            "# GOOD — one action per node, each with a description and a semantic locator\n"
            "- id: \"id:1\"\n  kind: computer\n  do: launch_app\n  params: {app: Safari}\n  description: 打开 Safari\n"
            "- id: \"id:2\"\n  kind: browser\n  do: navigate\n  params: {url: \"{{inputs.url}}\"}\n  description: 打开用量页面\n"
            "- id: \"id:3\"\n  kind: browser\n  do: click_text\n  params: {text: 导出}\n  description: 点击导出\n"
            "- id: \"id:4\"\n  kind: wait\n  params: {seconds: 8}\n  description: 等待下载完成\n"
            "- id: \"id:5\"\n  kind: file\n  do: exists\n  params: {path: \"{{steps.id4.path}}\"}\n  description: 确认导出文件已生成\n  post: [\"result.exists\"]\n"
            "```\n"
            "Notes: `click_ref`/`double_click_ref`/`right_click_ref`/`show`/`type_into` accept a "
            "semantic `locator: {role, name}` (resolved from a live AX snapshot — no runtime ref "
            "needed). Use `kind: computer do: script` (params.code, the cw-automa helper API) for "
            "intent-level steps, each script its OWN node.\n\n"
            "Rules (violations are REJECTED at create/update time):\n"
            "- Every step needs an INTENT: `goal` (mirrors `description`), a short human-readable label.\n"
            "- An intent-only node (`goal` set, no `do`) is allowed: the agent resolves and persists the "
            "binding (`do`/`params`/`locator`) at run time.\n"
            "- A step may be marked `absolute: true` (絕對遵守) ONLY by the user; it freezes the existing "
            "intent/binding (no substitution / self-heal / agent takeover).\n"
            "- Atomic nodes: exactly one action per step. NEVER bundle a sequence into one `command` "
            "shell blob (| ; && $(...)); split into command/file/transform nodes.\n"
            "- Valid kinds/actions/params come from the capability catalog: call the "
            "`workflow` tool with action=capabilities before authoring anything non-trivial.\n"
            "- Prefer a real action node over `browser do: evaluate`; never use `evaluate` to click/"
            "submit/mutate the DOM (use click_text/click_selector/click).\n"
            "- GUI steps must use a semantic locator (role/name), not raw coordinates. Target files by "
            "explicit path or file.glob/file.exists — never find/-mmin/…|head/~/Downloads guesses.\n"
            "- command runs argv WITHOUT a shell; add params.shell: true only for pipes/globs/$(...). "
            "The command MUST be an allowlisted program (git, node, python, tar, unzip, get-childitem, …); "
            "do NOT paste a shell one-liner. For OS automation prefer the native-script tools: `tool` kind "
            "`run_powershell` (Windows) / `run_applescript` (macOS) — one script per node, with its own "
            "`platform`. Use `kind: computer do: script` (params.code, cw-automa helper API) for intent-level "
            "GUI automation.\n"
            "- Assertions (`pre`/`post`/`success`) are short declarative specs — valid forms: `ok`, "
            "`not_error`, `contains <text>`, `not_contains <text>`, `equals <ref> <value>`, "
            "`not_equals <ref> <value>`, `matches <ref> <regex>`, `exists <ref>`, `file_exists <path>`, "
            "`not_file_exists <path>`, `file_contains <path> <text>`, `exit_code <n>`, or a bare `<ref>` "
            "(truthiness). A ref is `result.<field>`, `context.<x>`, `{{steps.<id>.<field>}}` or "
            "`{{inputs.<name>}}`. There is NO expression syntax (`!=`, `==`, `&&`); write "
            "`equals result.return_code 0`, not `result.return_code != 1`.\n"
            "- Inside a `computer` script's `code`, step outputs are auto-escaped: a reference in "
            "quotes (`'{{steps.id:1.stdout}}'`) has quotes/newlines escaped, and an unquoted reference "
            "(`{{steps.id:1.count}}`) becomes a JS literal. So you can interpolate step outputs directly "
            "— do NOT hand-roll escaping or a `JSON.parse` workaround.\n"
            "- The current date/time is provided in the system prompt (do not call `computer_observe` just "
            "to read the clock). For a RUNTIME timestamp inside a workflow use a `command`/native-script "
            "step (`date`, or `run_powershell` with `Get-Date`) — do not read the clock via the GUI, and "
            "do not hardcode a timestamp you intend to be dynamic.\n"
            "- computer launch_app 'app' accepts a display name (Calculator), a bundle id "
            "(com.apple.calculator) or an .app path; localized names resolve automatically.\n"
            "- Templates may only reference {{inputs.<declared>}}, {{steps.<id>.<field>}} "
            "(field = the action's declared outputs; no '.outputs.' prefix), {{vars.<name>}}, "
            "{{env.NAME}} and {{secret:name}}. A plain-text step result is {{steps.<id>}}.\n"
            "- Use the declared inputs in the steps (do not hardcode values that an input documents).\n"
            "- End with a VERIFICATION step that proves the GOAL: assert the produced artifact "
            "exists AND is meaningful (non-empty / expected content), or that the page shows the "
            "expected state — not merely that a command exited 0.\n"
            "- Validate before saving: action=validate (static) then action=simulate (dry-run).\n"
            "- Only say a workflow was created/updated when the tool returned status ok.\n\n"
            f"Minimal example:\n```yaml\n{EXAMPLE_YAML}```"
        )

    def to_schema(self) -> dict[str, Any]:
        from .platform_support import ALL_TAGS, current_tag

        return {
            "dsl_version": DSL_VERSION,
            "platform": current_tag(),
            "platforms": list(ALL_TAGS),
            "template_roots": list(TEMPLATE_ROOTS),
            "document_keys": [dict(d) for d in DOCUMENT_KEYS],
            "step_keys": [dict(d) for d in STEP_KEYS],
            "example": EXAMPLE_YAML,
            "kinds": [k.to_dict() for k in self.kinds.values()],
        }

    # ── compact views (agent tool) ───────────────────────────────────────
    # `to_schema()` is ~50KB and is replayed on every model call once fetched;
    # the agent-facing `workflow action=capabilities` returns one of these
    # instead. The HTTP/Studio shape (`to_schema`) is unchanged.

    def kind_overview(self) -> list[dict[str, Any]]:
        """One tiny row per kind: family, action NAMES, platform."""
        return [
            {
                "kind": k.kind,
                "family": k.family,
                "requires_do": k.requires_do,
                "actions": [a.name for a in k.actions],
                "platform": sorted(_kind_platforms(k)) or ["any"],
            }
            for k in self.kinds.values()
        ]

    def overview(self) -> dict[str, Any]:
        """Tiny always-safe starting point: kinds + action names + a fetch hint."""
        from .platform_support import ALL_TAGS, current_tag

        return {
            "dsl_version": DSL_VERSION,
            "platform": current_tag(),
            "platforms": list(ALL_TAGS),
            "template_roots": list(TEMPLATE_ROOTS),
            "kind_overview": self.kind_overview(),
            "hint": "Call action=capabilities with kinds=[...] for a kind's actions/params; "
            "add verbose=true for its full schema. Do not fetch every kind at once.",
        }

    def compact_schema(self, kinds: list[str] | None = None) -> dict[str, Any]:
        """Compact per-kind schema: action params (name/type/required/default),
        outputs, platform — without description/aliases/locator/success noise."""
        from .platform_support import ALL_TAGS, current_tag

        selected = (
            list(self.kinds.values())
            if not kinds
            else [self.kinds[k] for k in kinds if k in self.kinds]
        )
        return {
            "dsl_version": DSL_VERSION,
            "platform": current_tag(),
            "platforms": list(ALL_TAGS),
            "template_roots": list(TEMPLATE_ROOTS),
            "kinds": [_compact_kind(k) for k in selected],
            "unknown_kinds": [k for k in (kinds or []) if k not in self.kinds],
        }


def _compact_param(p: ParamSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"type": p.type}
    if p.required:
        out["required"] = True
    if p.default is not None:
        out["default"] = p.default
    return out


def _compact_kind(k: KindSpec) -> dict[str, Any]:
    return {
        "kind": k.kind,
        "family": k.family,
        "requires_do": k.requires_do,
        "platform": sorted(_kind_platforms(k)) or ["any"],
        "native_params": [p.name for p in k.native_params],
        "nested": list(k.nested),
        "actions": [
            {
                "action": a.name,
                "params": {p.name: _compact_param(p) for p in a.params},
                "outputs": list(a.outputs),
                "platform": _action_platforms(a.name) or ["any"],
            }
            for a in k.actions
        ],
    }


# ── introspection helpers ────────────────────────────────────────────────


def _enum_values(schema: dict[str, Any], prop: dict[str, Any]) -> list[str]:
    if "enum" in prop:
        return [str(v) for v in prop["enum"]]
    if "const" in prop:
        return [str(prop["const"])]
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in prop.get(key) or []:
            vals = _enum_values(schema, sub)
            if vals:
                return vals
    return []


_JSON_TYPE = {
    "string": "string", "integer": "number", "number": "number", "boolean": "boolean",
    "array": "list", "object": "object",
}


def _prop_type(prop: dict[str, Any]) -> str:
    t = prop.get("type")
    if isinstance(t, str):
        return _JSON_TYPE.get(t, "string")
    for key in ("anyOf", "oneOf"):
        for sub in prop.get(key) or []:
            tt = sub.get("type")
            if isinstance(tt, str) and tt != "null":
                return _JSON_TYPE.get(tt, "string")
    return "string"


def _introspect_schema(schema: dict[str, Any]) -> tuple[list[str], tuple[ParamSpec, ...]]:
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    actions = [str(v) for v in _enum_values(schema, props.get("action", {}))]
    params: list[ParamSpec] = []
    for name, prop in props.items():
        if name == "action":
            continue
        if str(prop.get("type")) in ("", "None") and not _enum_values(schema, prop):
            continue
        params.append(
            ParamSpec(
                name=name,
                type=_prop_type(prop),
                required=name in required,
                default=prop.get("default"),
                description=str(prop.get("description") or "")[:200],
            )
        )
    return actions, tuple(params)


def _success_for_kind(kind: str) -> str:
    return "no_error" if kind in ("browser", "computer", "app") else "no_error"


def _introspect_tool(tool: Any, kind: str) -> KindSpec | None:
    schema = _tool_schema(tool)
    if not schema:
        return None
    names, params = _introspect_schema(schema)
    if not names:
        return None
    decl = _declared_kinds()[kind]
    actions: list[ActionSpec] = []
    seen: set[str] = set()
    for name in names:
        base = decl.action(name)
        seen.add(name)
        if base is not None:
            # Use the action's OWN declared params — never the tool schema's full
            # union (that made every action expose every other action's fields).
            actions.append(
                ActionSpec(
                    kind, name, base.params, target=base.target, success=base.success,
                    locator=base.locator, aliases=base.aliases, description=base.description,
                    outputs=base.outputs,
                )
            )
        else:
            actions.append(ActionSpec(kind, name, params, target=kind, success=_success_for_kind(kind)))
    # Keep declared-only actions (e.g. `script` → computer_script) that the
    # underlying tool schema does not enumerate, so they survive introspection.
    for extra in decl.actions:
        if extra.name not in seen:
            actions.append(extra)
    return KindSpec(kind, "action", True, actions=tuple(actions), description=decl.description)


def _introspect_tools(tool_map: dict[str, Any]) -> tuple[ActionSpec, ...]:
    actions: list[ActionSpec] = []
    for name, tool in tool_map.items():
        if name in _TOOL_EXCLUDED:
            continue
        schema = _tool_schema(tool)
        if not schema:
            continue
        _, params = _introspect_schema(schema)
        actions.append(ActionSpec("tool", name, params, target=name, success="no_error"))
    return tuple(actions)


def _tool_schema(tool: Any) -> dict[str, Any] | None:
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return None
    try:
        if hasattr(schema, "model_json_schema"):
            return schema.model_json_schema()
    except Exception:  # noqa: BLE001
        return None
    return None


def _normalize_locator(locator: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep the primary locator descriptors (fallback handled by ``Locator``).

    Descriptor keys are opaque (role/name/text/identifier/selector/ref/coords…):
    the adapter resolves the ones it understands and the agentic self-heal
    translates the rest, so the validator does not police key names — it only
    requires that a GUI step HAS a target (params or locator).
    """
    if not locator:
        return None
    out = {k: v for k, v in locator.items() if k != "fallback" and v not in (None, "")}
    return out or None


def _locator_fills(locator: dict[str, Any] | None, action: ActionSpec, param: str) -> bool:
    if not locator or not action.locator:
        return False
    for key, targets in action.locator.maps.items():
        if param in targets and key in locator:
            return True
    return False


def merge_locator_into_args(
    args: dict[str, Any], locator: dict[str, Any] | None, action: ActionSpec
) -> dict[str, Any]:
    """Apply the locator→param mapping declared on the action (single source)."""
    if not locator or not action.locator:
        return args
    out = dict(args)
    for key, targets in action.locator.maps.items():
        if key not in locator:
            continue
        value = locator[key]
        if key == "coords" and isinstance(value, (list, tuple)) and len(value) >= 2:
            for target, coord in zip(targets, value):
                out.setdefault(target, coord)
        elif targets:
            out.setdefault(targets[0], value)
    return out
