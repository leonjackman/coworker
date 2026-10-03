"""Semantic workflow validation (single pass over the capability registry).

The old ``parser.validate`` only checked structure (name/kind/branch), so a
workflow whose ``tool`` step had no ``do``, whose params nobody reads, whose
locator keys nobody handles, or whose ``{{last_command_output}}`` template never
resolves would still be saved. This module validates a workflow AGAINST the
capability registry and the template grammar, returning structured
:class:`Diagnostic`s that the UI and the authoring pipeline can act on.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable

from .assertions import is_assert_condition, validate_spec
from .capabilities import TEMPLATE_ROOTS, CapabilityRegistry, Diagnostic
from .model import Step, Workflow
from .parser import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, is_valid_name

_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# Keys that carry templated text (where a reference may appear).
_TEMPLATED_KEYS = ("when", "foreach")


def extract_refs(value: Any) -> list[str]:
    """All ``{{ … }}`` reference expressions inside a JSON-like value."""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(m.group(1).strip() for m in _REF_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            out.extend(extract_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(extract_refs(item))
    return out


def _step_template_values(step: Step) -> Iterable[Any]:
    yield step.do
    yield step.params
    yield step.locator
    yield step.pre
    yield step.post
    yield step.success
    yield getattr(step, "when", "")
    yield getattr(step, "foreach", "")
    for slot in (step.then, step.else_, step.body):
        for child in slot or []:
            yield from _step_template_values(child)


def _collect_names(steps: list[Step], step_ids: set[str], var_names: set[str]) -> None:
    for step in steps:
        step_ids.add(step.id)
        if getattr(step, "as_name", ""):
            step_ids.add(step.as_name)
        if step.kind == "set":
            params = step.params or {}
            if params.get("name"):
                var_names.add(str(params["name"]))
            else:
                var_names.update(str(k) for k in params.keys())
        for slot in (step.then, step.else_, step.body):
            if slot:
                _collect_names(slot, step_ids, var_names)


def _collect_steps(steps: list[Step], out: dict[str, Step]) -> None:
    for step in steps:
        out[step.id] = step
        for slot in (step.then, step.else_, step.body):
            if slot:
                _collect_steps(slot, out)


def validate_templates(workflow: Workflow, registry: CapabilityRegistry | None = None) -> list[Diagnostic]:
    step_ids: set[str] = set()
    var_names: set[str] = set()
    _collect_names(workflow.steps, step_ids, var_names)
    input_names = {i.name for i in workflow.inputs}
    steps_by_id: dict[str, Step] = {}
    _collect_steps(workflow.steps, steps_by_id)

    diags: list[Diagnostic] = []

    def check(step_id: str, value: Any) -> None:
        for ref in extract_refs(value):
            if ref.startswith("secret:"):
                continue
            parts = [p for p in ref.split(".") if p != ""]
            root = parts[0] if parts else ""
            if root == "env":
                continue
            name = parts[1] if len(parts) > 1 else ""
            if root == "inputs":
                if not name or name not in input_names:
                    diags.append(
                        Diagnostic(step_id, "params", "unknown_input", f"template references undeclared input '{name or ref}'")
                    )
            elif root == "steps":
                if not name or name not in step_ids:
                    diags.append(
                        Diagnostic(step_id, "params", "unknown_step_ref", f"template references unknown step '{name or ref}'")
                    )
                elif len(parts) > 2:
                    field = parts[2]
                    target = steps_by_id.get(name)
                    action = (
                        registry.action(target.kind, target.do)
                        if registry is not None and target is not None
                        else None
                    )
                    if field == "outputs":
                        diags.append(
                            Diagnostic(
                                step_id, "params", "invalid_outputs_layer",
                                f"'{{{{{ref}}}}}' has a '.outputs.' layer that does not exist — reference "
                                f"'{{{{steps.{name}.<field>}}}}' (or '{{{{steps.{name}}}}}' for a text result)",
                            )
                        )
                    elif action is not None and action.outputs and field not in action.outputs:
                        diags.append(
                            Diagnostic(
                                step_id, "params", "unknown_output",
                                f"'{name}' has no output field '{field}' (valid: {', '.join(action.outputs)})",
                            )
                        )
            elif root == "vars":
                if not name or name not in var_names:
                    diags.append(
                        Diagnostic(step_id, "params", "unknown_var", f"template references unknown var '{name or ref}'")
                    )
            else:
                diags.append(
                    Diagnostic(
                        step_id, "params", "unknown_template_root",
                        f"template reference '{{{{{ref}}}}}' has unknown root '{root}' (expected one of {', '.join(TEMPLATE_ROOTS)})",
                    )
                )

    for step in workflow.steps:
        for value in _step_template_values(step):
            check(step.id, value)
    return diags


def validate_workflow(workflow: Workflow, registry: CapabilityRegistry) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    if not workflow.name:
        diags.append(Diagnostic("", "name", "missing_name", "name is required"))
    elif len(workflow.name) > MAX_NAME_LENGTH or not is_valid_name(workflow.name):
        diags.append(Diagnostic("", "name", "invalid_name", "invalid workflow name"))
    if not workflow.description.strip():
        diags.append(Diagnostic("", "description", "missing_description", "description is required"))
    elif len(workflow.description) > MAX_DESCRIPTION_LENGTH:
        diags.append(Diagnostic("", "description", "description_too_long", f"description exceeds {MAX_DESCRIPTION_LENGTH} characters"))
    if not workflow.steps:
        diags.append(Diagnostic("", "steps", "no_steps", "at least one step is required"))
    if workflow.status not in {"draft", "active", "deprecated"}:
        diags.append(Diagnostic("", "status", "invalid_status", f"invalid status: {workflow.status}"))

    # Canvas endpoint wiring must reference top-level steps.
    top_ids = {step.id for step in workflow.steps}
    if workflow.entry and workflow.entry not in top_ids:
        diags.append(Diagnostic("", "entry", "unknown_entry", f"entry references unknown step '{workflow.entry}'"))
    for exit_id in workflow.exits or []:
        if exit_id not in top_ids:
            diags.append(Diagnostic("", "exits", "unknown_exit", f"exits references unknown step '{exit_id}'"))

    diags.extend(_validate_steps(workflow.steps, registry, set()))
    diags.extend(validate_templates(workflow, registry))
    diags.extend(validate_scripts(workflow))
    diags.extend(validate_conformance(workflow, registry))
    diags.extend(validate_assertions(workflow))
    return diags


def validate_assertions(workflow: Workflow) -> list[Diagnostic]:
    """Authoring-time check that every pre/post/success/assert spec is well-formed."""
    diags: list[Diagnostic] = []

    def walk(steps: list[Step]) -> None:
        for step in steps:
            specs = list(step.pre) + list(step.post) + list(step.success)
            if step.kind == "assert" and is_assert_condition(step.do):
                specs = [step.do, *specs]
            for spec in specs:
                ok, message = validate_spec(spec)
                if not ok:
                    diags.append(Diagnostic(step.id, "post", "bad_assertion", message))
            # An `assert` step with no real condition would pass VACUOUSLY (and
            # still satisfy the "has verification" rule) — reject it.
            if step.kind == "assert" and not specs:
                diags.append(Diagnostic(
                    step.id, "post", "bad_assertion",
                    "assert step has no condition — set `post`/`success` (e.g. "
                    "[\"result.exists\"]) or a `do` spec (e.g. \"equals result.return_code 0\")",
                ))
            for slot in (step.then, step.else_, step.body):
                if slot:
                    walk(slot)

    walk(workflow.steps)
    return diags


# ── atomicity / Studio-readability conformance (write-time, all errors) ───
# The workflow must be a graph of ATOMIC, USER-READABLE nodes so the visual
# Studio can render every step as a friendly field form. Opaque authoring (raw
# DOM-clicking JS, one-command-does-everything shell blobs, guessy file targets,
# lone coordinates, missing labels) is rejected at create/update time. A step
# may opt out of a specific check with ``bypass: [<code>]`` (+ a reason); that
# downgrades the diagnostic to a visible warning instead of blocking.

_SHELL_CHAIN = re.compile(r"&&|\|\||[|;]|\$\(|`|>>|<<|\|\s*\w")
_MUTATING_JS = (
    ".click(", ".click ()", "dispatchevent(", ".submit(", ".value =", ".value=",
    ".innerhtml", "insertadjacenthtml", ".appendchild(", ".removechild(", ".setattribute(",
)
_NONDET_CMD = ("-mmin", "-mtime", "| head", "|head", "| tail", "|tail", "find ", "~/downloads", "$home/downloads")
# Platform-only command tokens live in platform_support (single source, also used
# by the live capability catalog).
from .platform_support import MACOS_ONLY_TOKENS as _MACOS_ONLY  # noqa: E402
from .platform_support import PLATFORM_ONLY_TOOLS as _PLATFORM_ONLY_TOOLS  # noqa: E402
from .platform_support import WINDOWS_ONLY_TOKENS as _WINDOWS_ONLY  # noqa: E402
from .platform_support import explicit_tags as _explicit_platform_tags  # noqa: E402
from .platform_support import infer_platforms as _infer_platforms  # noqa: E402
_GUI_KINDS = ("browser", "computer", "app")
_SEMANTIC_LOCATOR_KEYS = ("role", "name", "selector", "ref", "identifier", "text")

#: All conformance codes. They are hard ERRORS on the authoring path
#: (create/update) but the engine can still hold/run grandfathered workflows, so
#: the manager can be told to skip exactly these (never structural/capability
#: errors) when seeding non-authoring fixtures.
CONFORMANCE_CODES: frozenset[str] = frozenset({
    "missing_verification",
    "missing_description",
    "mutating_evaluate",
    "opaque_command",
    "unrunnable_command",
    "non_deterministic_target",
    "missing_platform",
    "coord_only_locator",
    "unknown_tool",
    "missing_target",
    "unverified_state_change",
    "guessy_source",
    "bad_assertion",
})


def _has_verification(steps: list[Step]) -> bool:
    for step in steps:
        if step.kind == "assert" or step.post or step.success:
            return True
        for slot in (step.then, step.else_, step.body):
            if slot and _has_verification(slot):
                return True
    return False


def _conformance_diag(
    step: Step, code: str, field: str, message: str, diags: list[Diagnostic], severity: str = "error"
) -> None:
    if severity == "error" and code in (getattr(step, "bypass", None) or []):
        severity = "warning"
    diags.append(Diagnostic(step.id, field, code, message, severity=severity))


def validate_conformance(workflow: Workflow, registry: CapabilityRegistry) -> list[Diagnostic]:
    """Atomic-node / Studio-readability rules. Errors block create/update."""
    diags: list[Diagnostic] = []

    if workflow.steps and not _has_verification(workflow.steps):
        diags.append(
            Diagnostic(
                "", "steps", "missing_verification",
                "the workflow has no verification step — add an `assert` node or a `post`/`success` "
                "condition on a step that proves the goal (artifact exists / page shows the expected state)",
            )
        )

    explicit_platform = _explicit_platform_tags(workflow.platform)
    live_tools = bool(getattr(registry, "live_tools", False))

    # Auto-inference: when the steps clearly imply exactly one OS but the author
    # declared none, suggest setting it once at the top level (advisory).
    inferred = _infer_platforms(workflow)
    if len(inferred) == 1 and not explicit_platform:
        tag = next(iter(inferred))
        diags.append(Diagnostic(
            "", "platform", "platform_suggestion",
            f"steps use {tag}-only tools/commands — declare `platform: {tag}` at the top level "
            "so the workflow is rejected up-front on other systems instead of failing at run time",
            severity="warning",
        ))

    def walk(steps: list[Step]) -> None:
        for step in steps:
            if not ((step.description or getattr(step, "goal", "") or "").strip()):
                _conformance_diag(
                    step, "missing_description", "description",
                    "step has no intent (goal/description) — every node needs a short human-readable "
                    "label so it renders meaningfully in the Studio", diags,
                )

            if step.kind == "browser" and step.do == "click":
                params = step.params or {}
                loc = step.locator or {}
                has_target = (
                    ("x" in params and "y" in params)
                    or params.get("selector") or params.get("text")
                    or loc.get("selector") or loc.get("text") or loc.get("coords") or loc.get("ref")
                )
                if not has_target:
                    _conformance_diag(
                        step, "missing_target", "params",
                        "browser click needs a target: a selector, text, or x/y coordinates "
                        "(directly or via a locator)", diags,
                    )

            if step.kind == "browser" and step.do == "evaluate":
                expr = str((step.params or {}).get("expression") or "").lower()
                if any(token in expr for token in _MUTATING_JS):
                    _conformance_diag(
                        step, "mutating_evaluate", "expression",
                        "`evaluate` is being used to drive the page (click/submit/DOM mutation). Use a "
                        "semantic interface action instead (click_text / click_selector / click with a "
                        "role+name locator) so the node is readable and robust", diags,
                    )

            if step.kind == "command":
                params = step.params or {}
                raw = params.get("command") or params.get("run") or step.do
                joined = raw if isinstance(raw, str) else " ".join(str(x) for x in raw or [])
                low = joined.lower()
                # `shell: true` is an EXPLICIT opt-in for pipes/redirects/globs —
                # do not then reject the same metacharacters as "opaque".
                shell_opt_in = bool(params.get("shell"))
                # Shell text: a raw string command, or the script after a
                # ``bash/sh -c`` wrapper (which is just as opaque as a blob).
                shell_text = raw if isinstance(raw, str) else ""
                if not shell_text and isinstance(raw, list) and len(raw) >= 3 and "-c" in [str(x) for x in raw[1:2]]:
                    shell_text = " ".join(str(x) for x in raw[2:])
                if shell_text and not shell_opt_in and _SHELL_CHAIN.search(shell_text):
                    _conformance_diag(
                        step, "opaque_command", "command",
                        "command bundles several actions with shell operators (| ; && $(…)) — split it "
                        "into atomic nodes (command / file / transform) so each step is readable and "
                        "independently verifiable", diags,
                    )
                _check_command_runnable(step, raw, shell_opt_in, diags)
                if any(token in low for token in _NONDET_CMD):
                    _conformance_diag(
                        step, "non_deterministic_target", "command",
                        "command targets files by a guessy search (find/-mmin/…|head/~/Downloads) — use "
                        "file.glob/file.exists on an explicit path (or a declared input) instead", diags,
                    )
                if any(token in low for token in _MACOS_ONLY) and "darwin" not in explicit_platform:
                    _conformance_diag(
                        step, "missing_platform", "command",
                        "command uses macOS-only utilities — declare `platform: darwin` (or a per-OS step)",
                        diags,
                    )
                if any(token in low for token in _WINDOWS_ONLY) and "win32" not in explicit_platform:
                    _conformance_diag(
                        step, "missing_platform", "command",
                        "command uses Windows-only utilities — declare `platform: win32` (or a per-OS step)",
                        diags,
                    )

            # A platform-only tool (run_applescript / run_powershell) pins the OS.
            if step.kind == "tool" and step.do in _PLATFORM_ONLY_TOOLS:
                needed = _PLATFORM_ONLY_TOOLS[str(step.do)]
                missing = needed - explicit_platform
                if missing:
                    _conformance_diag(
                        step, "missing_platform", "do",
                        f"tool '{step.do}' is only available on {'/'.join(sorted(needed))} — "
                        f"declare `platform: {','.join(sorted(needed))}`",
                        diags,
                    )

            if step.kind in _GUI_KINDS:
                locator = step.locator or {}
                has_semantic = any(k in locator for k in _SEMANTIC_LOCATOR_KEYS)
                has_coords = any(k in locator for k in ("coords", "x", "y", "pixel"))
                if has_coords and not has_semantic:
                    _conformance_diag(
                        step, "coord_only_locator", "locator",
                        "GUI step targets raw coordinates with no semantic identity (role/name/selector) — "
                        "use a semantic locator so it survives UI changes, or set `bypass: [coord_only_locator]` "
                        "with a reason when coordinates are genuinely required", diags,
                    )

            # Verification is the contract. A state-changing action whose default
            # success rule is weak (bare result/no_error) SHOULD declare an
            # explicit post/success; advisory (warning) so it never blocks a run.
            if step.kind in ("command", "http", "file") and not (step.post or step.success):
                spec = registry.action(step.kind, step.do) if step.do else None
                rule = getattr(spec, "success", "result_ok") if spec is not None else "result_ok"
                if rule not in ("command_rc", "observable_change"):
                    _conformance_diag(
                        step, "unverified_state_change", "params",
                        f"{step.kind} step '{step.id}' changes state but declares no success condition "
                        "(add `post`/`success`, e.g. the output file exists)",
                        diags, severity="warning",
                    )
            # Guessy file sources (non-deterministic across runs) are advisory.
            if step.kind == "file" and step.do in ("glob", "newest"):
                target = str((step.params or {}).get("path") or "")
                if "downloads" in target.lower():
                    _conformance_diag(
                        step, "guessy_source", "params",
                        f"file.{step.do} targets a Downloads folder — prefer an explicit input/path so the "
                        "result is deterministic across runs",
                        diags, severity="warning",
                    )

            if step.kind == "tool" and live_tools:
                do = (step.do or "").strip()
                if do and registry.action("tool", do) is None:
                    _conformance_diag(
                        step, "unknown_tool", "do",
                        f"tool '{do}' is not a registered tool — only real tools render in the Studio "
                        "(use action=capabilities to see the valid set)", diags,
                    )

            for slot in (step.then, step.else_, step.body):
                if slot:
                    walk(slot)

    walk(workflow.steps)
    return diags


# ── computer/script validation (author-time) ─────────────────────────────
# `computer do: script` runs a constrained JS cell (the cw-automa helper API).
# Catch the common authoring mistakes BEFORE a run: sandbox globals that don't
# exist, unknown app.* methods, and JS syntax errors.

_SCRIPT_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("setTimeout", "timers are not available — use a `wait` node or app.settle()"),
    ("setInterval", "timers are not available — use a `wait` node or app.settle()"),
    ("setImmediate", "timers are not available — use a `wait` node or app.settle()"),
    ("requestAnimationFrame", "requestAnimationFrame is not available"),
    ("require(", "modules cannot be required in the script sandbox"),
    ("process.", "the `process` object is not available"),
    ("eval(", "eval() is not allowed"),
    ("new Function", "dynamic Function() is not allowed"),
)

# The app.* surface exposed by the cw-automa script sandbox (no evaluate()).
_SCRIPT_APP_METHODS: frozenset[str] = frozenset(
    {
        "focus", "isRunning", "activate", "close",
        "getAXState", "getWindowState", "windowState",
        "getScreenshot", "screenshot",
        "click", "clickByIndex", "typeText", "type", "paste", "setValue", "getValue",
        "pressKey", "press", "scroll", "drag", "settle", "waitFor",
    }
)

_APP_METHOD_RE = re.compile(r"\bapp\.([A-Za-z_$][\w$]*)")


def _script_code(step: Step) -> str | None:
    if step.kind not in ("computer", "app") or (step.do or "") != "script":
        return None
    code = (step.params or {}).get("code")
    return code if isinstance(code, str) and code.strip() else None


def _node_syntax_error(code: str) -> str | None:
    """Best-effort JS syntax check via ``node --check`` (skipped if node absent)."""
    node = shutil.which("node")
    if not node:
        return None
    path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            # The sandbox runs the cell inside an async function, so top-level
            # await/return are legal; wrap it the same way for the check.
            handle.write("(async () => {\n" + code + "\n})();\n")
            path = handle.name
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001 - never fail validation on a checker problem
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    if proc.returncode != 0:
        text = (proc.stderr or "").strip()
        return text.splitlines()[-1][:200] if text else "syntax error"
    return None


def validate_scripts(workflow: Workflow) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    def walk(steps: list[Step]) -> None:
        for step in steps:
            code = _script_code(step)
            if code is not None:
                for token, why in _SCRIPT_FORBIDDEN:
                    if token in code:
                        diags.append(
                            Diagnostic(
                                step.id, "code", "invalid_script_api",
                                f"script uses '{token}' which is not available: {why}",
                            )
                        )
                seen_methods: set[str] = set()
                for match in _APP_METHOD_RE.finditer(code):
                    method = match.group(1)
                    if method in _SCRIPT_APP_METHODS or method in seen_methods:
                        continue
                    seen_methods.add(method)
                    diags.append(
                        Diagnostic(
                            step.id, "code", "unknown_script_method",
                            f"script calls 'app.{method}', not part of the computer-script API "
                            f"(allowed: {', '.join(sorted(_SCRIPT_APP_METHODS))})",
                        )
                    )
                syntax = _node_syntax_error(code)
                if syntax:
                    diags.append(Diagnostic(step.id, "code", "script_syntax_error", f"script syntax error: {syntax}"))
            for slot in (step.then, step.else_, step.body):
                if slot:
                    walk(slot)

    walk(workflow.steps)
    return diags


_SHELL_META = re.compile(r"[|&;<>]|\$\(|`")
_SEQ_MARKERS = ("然后", "然後", "接着", "接著", "并且", "並且", "同时", "同時", "再", "；", ";", "→", "->", "and then")
_GOAL_VERBS = (
    "打开", "打開", "开启", "開啟", "启动", "啟動", "导航", "導航", "进入", "進入",
    "点击", "點擊", "输入", "輸入", "等待", "下载", "下載", "保存", "复制", "複製",
    "解压", "解壓", "切换", "切換", "确认", "確認", "允许", "允許", "关闭", "關閉",
)


def _check_shell_need(step: Step, diags: list[Diagnostic]) -> None:
    params = step.params or {}
    if params.get("shell"):
        return
    command = params.get("command") or params.get("run") or step.do
    # Only a STRING command is shlex-split; a list is already argv, so shell
    # metacharacters inside its arguments are literal and need no shell.
    if not isinstance(command, str):
        return
    if _SHELL_META.search(command):
        diags.append(
            Diagnostic(
                step.id, "command", "shell_required",
                "command contains shell metacharacters (| & ; < > $( `) — add params.shell: true to run it through a shell",
            )
        )


def _check_command_runnable(step: Step, raw: Any, shell_opt_in: bool, diags: list[Diagnostic]) -> None:
    """Fail at AUTHORING when the command could never run at RUN time.

    ``workspace.run_command`` validates every program a shell/wrapper invokes
    against the per-platform allowlist. A command that is an unwrappable shell
    (interactive/script) OR names a program unknown to EVERY supported OS can
    never run — reject it here with a hint to the sanctioned alternatives,
    instead of letting a "validated" workflow fail mid-run.
    """
    try:
        import shlex

        from coworker.platform import (
            SHELL_UNVALIDATABLE,
            allowed_commands,
            shell_wrap_command,
            wrapped_program_names,
        )

        if isinstance(raw, str):
            try:
                argv = shlex.split(raw)
            except ValueError:
                argv = [raw]
        elif isinstance(raw, list):
            argv = [str(x) for x in raw]
        else:
            argv = [str(raw)]
        if not argv:
            return
        if shell_opt_in:
            argv = shell_wrap_command(" ".join(argv))
        universal = (
            set(allowed_commands("darwin"))
            | set(allowed_commands("win32"))
            | set(allowed_commands("linux"))
        )
        programs = wrapped_program_names(argv)
        if not programs:  # a direct command: validate the program itself
            name = os.path.basename(str(argv[0]).replace("\\", "/")).strip("\"'`$")
            if name.lower().endswith(".exe"):
                name = name[:-4]
            programs = [name]
        for program in programs:
            if program == SHELL_UNVALIDATABLE or program.lower() not in universal:
                _conformance_diag(
                    step, "unrunnable_command", "command",
                    f"command program '{program}' cannot run on any supported OS (or is an "
                    "unvalidatable shell blob) — use a supported program, or the native-script route: "
                    "a `tool` step (`run_powershell` on Windows / `run_applescript` on macOS), or a "
                    "`computer`/`browser` step",
                    diags,
                )
                return
    except Exception:  # noqa: BLE001 - a validator hiccup must never block authoring
        return


def _check_bundled_goal(step: Step, diags: list[Diagnostic]) -> None:
    goal = str(getattr(step, "goal", "") or step.do or "")
    if not goal:
        return
    low = goal.lower()
    markers = [m for m in _SEQ_MARKERS if m in goal or m in low]
    verbs = {v for v in _GOAL_VERBS if v in goal}
    if markers or len(verbs) >= 2 or len(goal) > 80:
        diags.append(
            Diagnostic(
                step.id, "goal", "might_bundle_actions",
                "this step looks like it bundles multiple actions — split it into one action per node "
                "(open/navigate/click/wait/... each its own step)",
                severity="warning",
            )
        )


def _validate_steps(steps: list[Step], registry: CapabilityRegistry, seen: set[str]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for step in steps:
        if step.id in seen:
            diags.append(Diagnostic(step.id, "id", "duplicate_step_id", f"duplicate step id '{step.id}'"))
        seen.add(step.id)
        spec = registry.kind(step.kind)
        if step.kind == "command":
            _check_shell_need(step, diags)
        if step.kind in ("agentic", "skill"):
            _check_bundled_goal(step, diags)
        if spec is None:
            diags.append(Diagnostic(step.id, "kind", "unknown_kind", f"unknown step kind '{step.kind}'"))
        else:
            # mode=agent hands the step to the agent, so the deterministic
            # action/params are not required — only the kind must be known.
            if getattr(step, "mode", "auto") != "agent":
                _, action_diags = registry.resolve(step)
                diags.extend(action_diags)
            if step.kind == "branch" and not step.when:
                diags.append(Diagnostic(step.id, "when", "missing_when", "branch requires 'when'"))
            if step.kind == "branch" and not step.then:
                diags.append(Diagnostic(step.id, "then", "missing_then", "branch requires 'then' steps"))
            if step.kind == "loop" and not step.body:
                diags.append(Diagnostic(step.id, "body", "missing_body", "loop requires 'body' steps"))
            if step.kind == "parallel" and not step.body:
                diags.append(Diagnostic(step.id, "body", "missing_body", "parallel requires 'body' steps"))
        diags.extend(_validate_steps(step.then, registry, seen))
        diags.extend(_validate_steps(step.else_, registry, seen))
        diags.extend(_validate_steps(step.body, registry, seen))
    return diags


def format_diagnostics(diags: list[Diagnostic]) -> str:
    return "; ".join(str(d) for d in diags)
