"""Semantic workflow validation (single pass over the capability registry).

The old ``parser.validate`` only checked structure (name/kind/branch), so a
workflow whose ``tool`` step had no ``do``, whose params nobody reads, whose
locator keys nobody handles, or whose ``{{last_command_output}}`` template never
resolves would still be saved. This module validates a workflow AGAINST the
capability registry and the template grammar, returning structured
:class:`Diagnostic`s that the UI and the authoring pipeline can act on.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

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
                elif registry is not None and len(parts) > 2:
                    target = steps_by_id.get(name)
                    action = registry.action(target.kind, target.do) if target is not None else None
                    if action is not None and action.outputs and parts[2] not in action.outputs:
                        diags.append(
                            Diagnostic(
                                step_id, "params", "unknown_output",
                                f"'{name}' has no output field '{parts[2]}' (valid: {', '.join(action.outputs)})",
                                severity="warning",
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

    diags.extend(_validate_steps(workflow.steps, registry, set()))
    diags.extend(validate_templates(workflow, registry))
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
