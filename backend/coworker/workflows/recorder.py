"""Workflow authoring / generalization (W24/W25).

Turns a captured sequence of concrete actions into a reusable draft:

* literal values that match a declared input are replaced by ``{{inputs.x}}``
  (parameterization);
* coordinate-only targets keep their coords but the caller is expected to add
  semantic descriptors (heuristic generalization is deliberately conservative);
* the resulting YAML carries the provenance chain (source session/run).
"""

from __future__ import annotations

import re
from typing import Any

from .model import Workflow, WorkflowInput, Step
from .parser import render_workflow
from .templating import resolve_string

VALID_ACTION_KINDS = {"command", "tool", "browser", "app", "computer", "skill", "human", "agentic"}


def normalize_capture(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a captured action dict into a step dict."""
    step: dict[str, Any] = {}
    if raw.get("id"):
        step["id"] = str(raw["id"])
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VALID_ACTION_KINDS:
        kind = _infer_kind(raw)
    step["kind"] = kind
    for key in ("do", "params", "locator", "pre", "post", "on_error", "timeout", "approval"):
        if raw.get(key) is not None:
            step[key] = raw[key]
    return step


def _infer_kind(raw: dict[str, Any]) -> str:
    action = str(raw.get("action") or raw.get("do") or "").lower()
    if action in ("command", "run", "shell") or "command" in raw:
        return "command"
    if action in ("navigate", "click", "type", "snapshot", "screenshot", "scroll", "evaluate"):
        return "browser"
    if action in ("click_ref", "type_into", "press_hotkey", "launch_app", "set_value"):
        return "app"
    if raw.get("tool") or raw.get("name"):
        return "tool"
    return "tool"


def generalize(steps: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace literal occurrences of input values with ``{{inputs.<name>}}``.

    Conservative by design: only exact string matches are substituted, and only
    for non-empty scalar inputs. Nested ``params``/``locator`` values are walked.
    """
    if not inputs:
        return steps

    replacements = {
        str(value): f"{{{{inputs.{name}}}}}"
        for name, value in inputs.items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value)
    }
    if not replacements:
        return steps

    def _sub(value: Any) -> Any:
        if isinstance(value, str):
            out = value
            for literal, ref in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
                out = out.replace(literal, ref)
            return out
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return [_sub(step) if isinstance(step, dict) else step for step in steps]


def record_draft(
    name: str,
    steps: list[dict[str, Any]],
    *,
    description: str,
    inputs: list[dict[str, Any]] | None = None,
    triggers: list[str] | None = None,
    sources: list[str] | None = None,
    platform: str = "",
) -> str:
    """Build a draft workflow YAML from captured/normalized steps."""
    normalized = [normalize_capture(s) if isinstance(s, dict) else s for s in steps]
    normalized = generalize(normalized, {i.get("name"): i.get("default") for i in (inputs or []) if i.get("name")})
    # Assign deterministic, unique ids (captured steps often carry none).
    seen: set[str] = set()
    for index, raw in enumerate(normalized):
        if not isinstance(raw, dict):
            continue
        candidate = str(raw.get("id") or "").strip()
        if not candidate or candidate in seen:
            candidate = f"{raw.get('kind') or 'step'}{index + 1}"
        while candidate in seen:
            candidate = f"{candidate}_{index}"
        raw["id"] = candidate
        seen.add(candidate)

    parsed_steps: list[Step] = []
    for index, raw in enumerate(normalized):
        from .parser import _parse_step  # local import to avoid cycle at module load

        step = _parse_step(raw, index, [], "recorded")
        if step is not None:
            parsed_steps.append(step)

    input_specs = [
        WorkflowInput(
            name=str(i["name"]),
            type=str(i.get("type") or "string"),
            required=bool(i.get("required", False)),
            default=i.get("default"),
            description=str(i.get("description") or ""),
        )
        for i in (inputs or [])
        if isinstance(i, dict) and i.get("name")
    ]

    workflow = Workflow(
        name=name,
        description=description,
        steps=parsed_steps,
        platform=platform,
        inputs=input_specs,
        triggers=list(triggers or ["manual"]),
        provenance={"action": "create", "sources": list(sources or []), "kind": "recorded"},
        status="draft",
        source="agent",
    )
    return render_workflow(workflow)


def extract_steps_from_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map an agent tool-audit trace into candidate workflow steps.

    Only read/act tool calls become steps; reasoning/messages are ignored.
    """
    steps: list[dict[str, Any]] = []
    for index, entry in enumerate(trace):
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool") or entry.get("name") or "")
        if not tool:
            continue
        args = entry.get("input") if isinstance(entry.get("input"), dict) else {}
        spec = _tool_to_step(tool, args)
        if spec is None:
            continue
        spec["id"] = f"{spec['kind']}{index + 1}"
        steps.append(spec)
    return steps


def _tool_to_step(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if tool == "run_command":
        return {"kind": "command", "do": "run", "params": {"command": args.get("command")}}
    if tool == "browser":
        action = args.get("action") or "navigate"
        params = {k: v for k, v in args.items() if k != "action"}
        spec: dict[str, Any] = {"kind": "browser", "do": action, "params": params}
        if args.get("selector"):
            spec["locator"] = {"selector": args["selector"]}
        return spec
    if tool in ("computer", "computer_script"):
        params = {k: v for k, v in args.items() if k not in ("action", "ref")}
        spec = {"kind": "app", "do": args.get("action") or "script", "params": params}
        if args.get("ref"):
            spec["locator"] = {"ref": args["ref"]}
        return spec
    if tool == "web_fetch":
        return {"kind": "tool", "do": "web_fetch", "params": {"url": args.get("url")}}
    return None
