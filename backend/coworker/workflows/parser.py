"""Workflow DSL parsing, validation and rendering (W01/W03).

The on-disk artifact is YAML with a top-level mapping. Steps are a nested list;
``then`` / ``else`` / ``body`` carry control-flow children. Unknown scalar keys
on a step are folded into ``params`` for ergonomic authoring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .model import (
    VALID_INPUT_TYPES,
    VALID_KINDS,
    VALID_STEP_MODES,
    Workflow,
    WorkflowInput,
    WorkflowParseError,
    WorkflowValidationError,
    Step,
)

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

# Keys with dedicated handling on a Step (everything else becomes a param).
_STEP_KEYS = frozenset(
    {
        "id",
        "kind",
        "do",
        "action",
        "params",
        "args",
        "locator",
        "pre",
        "post",
        "goal",
        "success",
        "mode",
        "on_error",
        "timeout",
        "approval",
        "when",
        "if",
        "foreach",
        "as",
        "as_name",
        "description",
        "then",
        "else",
        "body",
        "steps",
    }
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_inputs(raw: Any) -> list[WorkflowInput]:
    specs: list[WorkflowInput] = []
    if raw is None:
        return specs
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            specs.append(
                WorkflowInput(
                    name=str(item["name"]),
                    type=_norm_type(item.get("type")),
                    required=bool(item.get("required", False)),
                    default=item.get("default"),
                    description=str(item.get("description") or ""),
                )
            )
        return specs
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, dict) and any(
                k in value for k in ("type", "default", "required", "description")
            ):
                specs.append(
                    WorkflowInput(
                        name=str(name),
                        type=_norm_type(value.get("type")),
                        required=bool(value.get("required", False)),
                        default=value.get("default"),
                        description=str(value.get("description") or ""),
                    )
                )
            else:
                specs.append(
                    WorkflowInput(name=str(name), type=_infer_type(value), default=value)
                )
    return specs


def _norm_type(value: Any) -> str:
    t = str(value or "string").strip().lower()
    if t == "bool":
        t = "boolean"
    if t == "array":
        t = "list"
    if t == "dict":
        t = "object"
    return t if t in VALID_INPUT_TYPES else "string"


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _parse_step(raw: Any, index: int, diagnostics: list[str], scope: str) -> Step | None:
    if not isinstance(raw, dict):
        diagnostics.append(f"{scope}: step #{index + 1} is not a mapping — skipped")
        return None

    step_id = str(raw.get("id") or "").strip() or f"step{index + 1}"
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        diagnostics.append(f"{scope}: step '{step_id}' has no kind — skipped")
        return None
    if kind not in VALID_KINDS:
        diagnostics.append(f"{scope}: step '{step_id}' has unknown kind '{kind}' — skipped")
        return None

    do = str(raw.get("do") or raw.get("action") or "").strip()
    params: dict[str, Any] = {}
    if isinstance(raw.get("params"), dict):
        params.update(raw["params"])
    if isinstance(raw.get("args"), dict):
        params.update(raw["args"])
    for key, value in raw.items():
        if key not in _STEP_KEYS:
            params[key] = value

    locator = raw.get("locator") if isinstance(raw.get("locator"), dict) else None
    pre = [str(x) for x in _as_list(raw.get("pre"))]
    post = [str(x) for x in _as_list(raw.get("post"))]
    goal = str(raw.get("goal") or "")
    success = [str(x) for x in _as_list(raw.get("success"))]
    mode = str(raw.get("mode") or "auto").strip().lower()
    on_error = dict(raw.get("on_error")) if isinstance(raw.get("on_error"), dict) else {}
    try:
        timeout = int(raw.get("timeout", 30))
    except (TypeError, ValueError):
        timeout = 30

    child_scope = f"{scope}/{step_id}"
    then = _parse_steps(raw.get("then"), diagnostics, child_scope)
    else_ = _parse_steps(raw.get("else"), diagnostics, child_scope)
    body = _parse_steps(raw.get("body") or raw.get("steps"), diagnostics, child_scope)

    return Step(
        id=step_id,
        kind=kind,
        do=do,
        params=params,
        locator=locator,
        pre=pre,
        post=post,
        goal=goal,
        success=success,
        mode=mode,
        on_error=on_error,
        timeout=timeout,
        approval=bool(raw.get("approval", False)),
        when=str(raw.get("when") or raw.get("if") or "").strip(),
        foreach=str(raw.get("foreach") or "").strip(),
        as_name=str(raw.get("as") or raw.get("as_name") or "").strip(),
        then=then,
        else_=else_,
        body=body,
        description=str(raw.get("description") or ""),
    )


def _parse_steps(raw: Any, diagnostics: list[str], scope: str = "steps") -> list[Step]:
    steps: list[Step] = []
    for index, item in enumerate(_as_list(raw)):
        step = _parse_step(item, index, diagnostics, scope)
        if step is not None:
            steps.append(step)
    return steps


def parse_workflow(
    content: str,
    *,
    name_hint: str = "",
    source: str = "user",
    file_path: Path | None = None,
    base_dir: Path | None = None,
) -> tuple[Workflow | None, list[str]]:
    """Parse YAML workflow content into a :class:`Workflow`.

    Returns ``(workflow_or_none, diagnostics)``; malformed YAML yields ``None``
    plus a diagnostic rather than raising, so one bad file never breaks a scan.
    """
    diagnostics: list[str] = []
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return None, [f"invalid YAML: {exc}"]
    if data is None:
        return None, ["empty workflow file"]
    if not isinstance(data, dict):
        return None, ["workflow root must be a mapping"]

    name = str(data.get("name") or "").strip() or name_hint
    description = str(data.get("description") or "").strip()
    steps = _parse_steps(data.get("steps"), diagnostics)

    version_raw = data.get("version", 1)
    try:
        version = int(version_raw)
    except (TypeError, ValueError):
        version = 1

    triggers_raw = data.get("triggers")
    triggers: list[str] = []
    if isinstance(triggers_raw, list):
        triggers = [str(t) for t in triggers_raw if isinstance(t, (str, int, float))]
    elif isinstance(triggers_raw, str) and triggers_raw.strip():
        triggers = [triggers_raw.strip()]

    outputs_raw = data.get("outputs")
    outputs = (
        {str(k): str(v) for k, v in outputs_raw.items()}
        if isinstance(outputs_raw, dict)
        else {}
    )

    status = str(data.get("status") or "active").strip().lower()
    created_at = str(data.get("created_at") or "")
    updated_at = str(data.get("updated_at") or "")

    workflow = Workflow(
        name=name,
        description=description,
        steps=steps,
        version=version,
        platform=str(data.get("platform") or "").strip(),
        inputs=_parse_inputs(data.get("inputs")),
        outputs=outputs,
        triggers=triggers,
        provenance=data.get("provenance") if isinstance(data.get("provenance"), dict) else {},
        fingerprint=str(data.get("fingerprint") or ""),
        status=status,
        source=source,
        file_path=file_path,
        base_dir=base_dir,
        created_at=created_at,
        updated_at=updated_at,
    )

    errors = validate(workflow)
    diagnostics.extend(errors)
    if errors and not name:
        return None, diagnostics
    return workflow, diagnostics


def load_workflow_file(path: Path, source: str = "user") -> tuple[Workflow | None, list[str]]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, [f"unreadable: {exc}"]
    return parse_workflow(
        content,
        name_hint=path.stem,
        source=source,
        file_path=path,
        base_dir=path.parent,
    )


def validate(workflow: Workflow) -> list[str]:
    """Structural validation; returns a list of error strings (empty = valid)."""
    errors: list[str] = []
    if not workflow.name:
        errors.append("name is required")
    elif len(workflow.name) > MAX_NAME_LENGTH:
        errors.append(f"name exceeds {MAX_NAME_LENGTH} characters")
    elif not _NAME_RE.match(workflow.name):
        errors.append("name must be lowercase alphanumeric with single hyphen separators")
    if not workflow.description.strip():
        errors.append("description is required")
    elif len(workflow.description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"description exceeds {MAX_DESCRIPTION_LENGTH} characters")
    if not workflow.steps:
        errors.append("at least one step is required")
    if workflow.status not in {"draft", "active", "deprecated"}:
        errors.append(f"invalid status: {workflow.status}")

    input_names = {i.name for i in workflow.inputs}
    if len(input_names) != len(workflow.inputs):
        errors.append("duplicate input names")
    errors.extend(_validate_steps(workflow.steps, "steps", set()))
    return errors


def _validate_steps(steps: list[Step], scope: str, seen: set[str]) -> list[str]:
    errors: list[str] = []
    for step in steps:
        if step.id in seen:
            errors.append(f"{scope}: duplicate step id '{step.id}'")
        seen.add(step.id)
        if step.kind not in VALID_KINDS:
            errors.append(f"{scope}/{step.id}: unknown kind '{step.kind}'")
        if step.mode not in VALID_STEP_MODES:
            errors.append(f"{scope}/{step.id}: unknown mode '{step.mode}'")
        if step.kind == "branch":
            if not step.when:
                errors.append(f"{scope}/{step.id}: branch requires 'when'")
            if not step.then:
                errors.append(f"{scope}/{step.id}: branch requires 'then' steps")
        if step.kind == "loop" and not step.body:
            errors.append(f"{scope}/{step.id}: loop requires 'body' steps")
        if step.kind == "parallel" and not step.body:
            errors.append(f"{scope}/{step.id}: parallel requires 'body' steps")
        if step.kind == "assert" and not (step.post or step.do or step.params):
            errors.append(f"{scope}/{step.id}: assert requires a 'do' or 'post' spec")
        errors.extend(_validate_steps(step.then, f"{scope}/{step.id}.then", seen))
        errors.extend(_validate_steps(step.else_, f"{scope}/{step.id}.else", seen))
        errors.extend(_validate_steps(step.body, f"{scope}/{step.id}.body", seen))
    return errors


def raise_on_errors(workflow: Workflow, diagnostics: list[str] | None = None) -> None:
    problems = validate(workflow)
    if problems:
        raise WorkflowValidationError("; ".join(problems))


def render_workflow(workflow: Workflow) -> str:
    """Render a workflow back to YAML (single source of truth)."""
    data: dict[str, Any] = {
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
    }
    if workflow.platform:
        data["platform"] = workflow.platform
    if workflow.inputs:
        data["inputs"] = {i.name: _input_to_yaml(i) for i in workflow.inputs}
    if workflow.outputs:
        data["outputs"] = dict(workflow.outputs)
    if workflow.triggers:
        data["triggers"] = list(workflow.triggers)
    if workflow.provenance:
        data["provenance"] = dict(workflow.provenance)
    if workflow.fingerprint:
        data["fingerprint"] = workflow.fingerprint
    if workflow.status and workflow.status != "active":
        data["status"] = workflow.status
    if workflow.created_at:
        data["created_at"] = workflow.created_at
    data["steps"] = [step.to_dict() for step in workflow.steps]
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _input_to_yaml(spec: WorkflowInput) -> Any:
    if (
        spec.type == "string"
        and not spec.required
        and spec.description == ""
    ):
        return spec.default if spec.default is not None else ""
    out: dict[str, Any] = {"type": spec.type}
    if spec.required:
        out["required"] = True
    if spec.default is not None:
        out["default"] = spec.default
    if spec.description:
        out["description"] = spec.description
    return out


def parse_error_to_exception(content: str) -> None:
    workflow, diagnostics = parse_workflow(content)
    if workflow is None:
        raise WorkflowParseError("; ".join(diagnostics) or "invalid workflow")
    raise_on_errors(workflow)
