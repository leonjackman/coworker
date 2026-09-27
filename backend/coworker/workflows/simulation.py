"""Dry-run simulation: prove a workflow *can* run without side effects.

Where :mod:`validation` checks structure and references statically, simulation
walks the whole workflow with a synthetic context and reports, per step, the
exact action/target/args the executor WOULD pass to the adapter, plus any
template that fails to resolve or required input that is missing. It never
touches the filesystem, browser, desktop or the agent — so it is safe to run
before activating a workflow.
"""

from __future__ import annotations

from typing import Any

from .capabilities import CapabilityRegistry, Diagnostic
from .model import Step, Workflow
from .templating import TemplateError, resolve

_STEP_PLACEHOLDER: dict[str, Any] = {"mocked": True}


def _build_context(workflow: Workflow, inputs: dict[str, Any] | None) -> dict[str, Any]:
    provided = dict(inputs or {})
    resolved_inputs: dict[str, Any] = {}
    for spec in workflow.inputs:
        if spec.name in provided:
            resolved_inputs[spec.name] = provided[spec.name]
        elif spec.default is not None:
            resolved_inputs[spec.name] = spec.default
    resolved_inputs.update(provided)
    return {"inputs": resolved_inputs, "steps": {}, "vars": {}}


def _missing_inputs(workflow: Workflow, inputs: dict[str, Any] | None) -> list[str]:
    provided = inputs or {}
    return [
        i.name
        for i in workflow.inputs
        if i.required and i.name not in provided and i.default is None
    ]


def simulate_workflow(
    workflow: Workflow,
    registry: CapabilityRegistry,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = _build_context(workflow, inputs)
    errors: list[Diagnostic] = []
    missing = _missing_inputs(workflow, inputs)
    for name in missing:
        errors.append(Diagnostic("", name, "missing_input", f"required input '{name}' has no value"))

    report: list[dict[str, Any]] = []

    def walk(steps: list[Step]) -> None:
        for step in steps:
            entry: dict[str, Any] = {"id": step.id, "kind": step.kind, "do": step.do, "status": "ok"}
            if getattr(step, "mode", "auto") == "agent":
                entry["status"] = "agent"
                report.append(entry)
                _recurse(step)
                continue
            spec = registry.kind(step.kind)
            if spec is not None and spec.family == "action":
                resolved, diags = registry.resolve(step)
                errors.extend(diags)
                if resolved is not None:
                    entry["action"] = resolved.action
                    entry["target"] = resolved.target
                    try:
                        entry["args"] = {
                            k: resolve(v, _ctx(context, step), None) for k, v in resolved.args.items()
                        }
                    except TemplateError as exc:
                        errors.append(Diagnostic(step.id, "params", "template_error", str(exc)))
                        entry["status"] = "error"
                else:
                    entry["status"] = "error"
            elif step.kind == "subworkflow":
                entry["target"] = step.do
            try:
                resolve(step.params, _ctx(context, step), None)
                resolve(step.locator, _ctx(context, step), None)
            except TemplateError as exc:
                errors.append(Diagnostic(step.id, "params", "template_error", str(exc)))
                entry["status"] = "error"
            report.append(entry)
            _recurse(step)

    def _recurse(step: Step) -> None:
        for slot in (step.then, step.else_, step.body):
            if slot:
                walk(slot)

    walk(workflow.steps)
    return {
        "status": "ok" if not errors else "error",
        "workflow": workflow.name,
        "steps": report,
        "missing_inputs": missing,
        "errors": [str(d) for d in errors],
        "diagnostics": [d.to_dict() for d in errors],
    }


def _ctx(context: dict[str, Any], step: Step) -> dict[str, Any]:
    """Context for template resolution, with step bindings mocked to {}."""
    steps = dict(context.get("steps") or {})
    steps.setdefault(step.id, _STEP_PLACEHOLDER)
    return {"inputs": context["inputs"], "steps": steps, "vars": context["vars"]}
