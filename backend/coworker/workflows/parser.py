"""Workflow DSL parsing, validation and rendering (W01/W03).

The on-disk artifact is YAML with a top-level mapping. Steps are a nested list;
``then`` / ``else`` / ``body`` carry control-flow children. Unknown scalar keys
on a step are folded into ``params`` for ergonomic authoring.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
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

# Names may be any language/script: letters, combining marks (needed for e.g.
# Devanagari/Thai/Arabic vowel signs), numbers, spaces, hyphens and underscores.
# Path separators, punctuation and emoji are rejected. The store maps non-ASCII
# names to a safe filename, so names never touch the filesystem.
_ALLOWED_LITERAL = frozenset(" -_")
MAX_NAME_LENGTH = 64


def is_valid_name(name: str) -> bool:
    candidate = (name or "").strip()
    if not candidate or len(candidate) > MAX_NAME_LENGTH:
        return False
    for char in candidate:
        if char in _ALLOWED_LITERAL:
            continue
        category = unicodedata.category(char)
        if category[0] in ("L", "M", "N"):  # letter, mark, number
            continue
        return False
    return True
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
        "next",
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
        next=str(raw.get("next") or "").strip(),
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
    elif not is_valid_name(workflow.name):
        errors.append("name may only contain letters, numbers, spaces, hyphens and underscores")
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


_STEP_REF_RE = re.compile(r"\{\{\s*steps\.([A-Za-z0-9_:-]+)")


def _execution_order(steps: list[Step]) -> list[Step]:
    if not any(step.next for step in steps):
        return steps
    by_id = {step.id: step for step in steps}
    incoming = {step.next for step in steps if step.next}
    ordered: list[Step] = []
    seen: set[str] = set()
    for step in steps:
        if step.id in incoming:
            continue
        cursor: Step | None = step
        while cursor is not None and cursor.id not in seen:
            ordered.append(cursor)
            seen.add(cursor.id)
            cursor = by_id.get(cursor.next) if cursor.next else None
    for step in steps:
        if step.id not in seen:
            ordered.append(step)
    return ordered


def renumber_steps(steps: list[Step]) -> list[Step]:
    """Assign system ids ``id:1, id:2, …`` (execution order, depth first).

    Rewrites ``next`` links and ``{{steps.<id>}}`` references so wiring stays
    correct. Used to normalise templates/recorded drafts onto the same scheme
    the visual editor uses.
    """
    id_map: dict[str, str] = {}
    counter = 0

    def assign(seq: list[Step]) -> None:
        nonlocal counter
        ordered = _execution_order(seq)
        # Level-first: number all siblings before descending into children.
        for step in ordered:
            counter += 1
            id_map[step.id] = f"id:{counter}"
        for step in ordered:
            for slot in ("then", "else_", "body"):
                children = getattr(step, slot, None)
                if children:
                    assign(children)

    assign(steps)

    def _repl(match: "re.Match[str]") -> str:
        old = match.group(1)
        new = id_map.get(old, old)
        # Replace only the captured id, not substrings that appear in the prefix
        # (e.g. id "s" must not corrupt "{{steps.s").
        prefix = match.group(0)[: len(match.group(0)) - len(old)]
        return prefix + new

    def remap(value: Any) -> Any:
        if isinstance(value, str):
            return _STEP_REF_RE.sub(_repl, value)
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        return value

    def walk(seq: list[Step]) -> list[Step]:
        out: list[Step] = []
        for step in _execution_order(seq):
            fields: dict[str, Any] = {
                "id": id_map.get(step.id, step.id),
                "params": remap(step.params or {}),
                "pre": [remap(x) for x in step.pre],
                "post": [remap(x) for x in step.post],
                "success": [remap(x) for x in step.success],
            }
            if step.next:
                fields["next"] = id_map.get(step.next, step.next)
            if step.when:
                fields["when"] = remap(step.when)
            if step.foreach:
                fields["foreach"] = remap(step.foreach)
            new_step = replace(step, **fields)
            for slot in ("then", "else_", "body"):
                children = getattr(new_step, slot, None)
                if children:
                    new_step = replace(new_step, **{slot: walk(children)})
            out.append(new_step)
        return out

    return walk(steps)


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
    if workflow.updated_at:
        data["updated_at"] = workflow.updated_at
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
