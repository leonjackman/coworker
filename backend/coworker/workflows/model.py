"""Workflow domain model (W01–W04).

A workflow is a declarative, parameterized, versioned orchestration artifact:
a list of typed :class:`Step` nodes over typed :class:`WorkflowInput` variables.
It is the "executable" counterpart of a natural-language Skill — the runtime can
replay it deterministically, verify every step, and self-heal on drift.

The YAML on disk is the single source of truth (see ``parser.py``); these
dataclasses are its in-memory view.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Step kinds ---------------------------------------------------------------
# Action kinds are dispatched to a StepEnvironment; control kinds are handled
# by the executor itself.
ACTION_KINDS = frozenset(
    {"command", "tool", "browser", "app", "computer", "skill", "human", "agentic"}
)
CONTROL_KINDS = frozenset(
    {"set", "assert", "wait", "branch", "loop", "parallel", "subworkflow"}
)
VALID_KINDS = ACTION_KINDS | CONTROL_KINDS

# Input value types.
VALID_INPUT_TYPES = frozenset({"string", "number", "boolean", "list", "object", "secret"})

WORKFLOW_STATUSES = frozenset({"draft", "active", "deprecated"})
WORKFLOW_SOURCES = frozenset({"user", "project", "agent", "market"})

DEFAULT_ON_ERROR: dict[str, Any] = {"retry": 0, "then": "abort"}


@dataclass(frozen=True)
class WorkflowInput:
    """A typed workflow parameter."""

    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "description": self.description,
        }


@dataclass(frozen=True)
class Step:
    """One node in a workflow.

    ``kind`` picks the executor branch. ``do`` is the adapter-specific action.
    ``params`` are the adapter arguments (templated at run time). ``locator`` is
    the optional semantic locator for GUI steps. ``pre``/``post`` are assertion
    specs. ``on_error`` is a policy dict (``retry``, ``then``). Control steps
    carry nested ``then``/``else``/``body`` step lists.
    """

    id: str
    kind: str
    do: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    locator: dict[str, Any] | None = None
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    on_error: dict[str, Any] = field(default_factory=DEFAULT_ON_ERROR)
    timeout: int = 30
    approval: bool = False
    when: str = ""
    foreach: str = ""
    as_name: str = ""
    then: list["Step"] = field(default_factory=list)
    else_: list["Step"] = field(default_factory=list)
    body: list["Step"] = field(default_factory=list)
    description: str = ""

    @property
    def bind(self) -> str:
        """The context key this step's result is stored under."""
        return self.as_name or self.id

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.do:
            data["do"] = self.do
        if self.params:
            data["params"] = dict(self.params)
        if self.locator:
            data["locator"] = dict(self.locator)
        if self.pre:
            data["pre"] = list(self.pre)
        if self.post:
            data["post"] = list(self.post)
        if self.on_error and self.on_error != DEFAULT_ON_ERROR:
            data["on_error"] = dict(self.on_error)
        if self.timeout != 30:
            data["timeout"] = self.timeout
        if self.approval:
            data["approval"] = True
        if self.when:
            data["when"] = self.when
        if self.foreach:
            data["foreach"] = self.foreach
        if self.as_name:
            data["as_name"] = self.as_name
        if self.description:
            data["description"] = self.description
        if self.then:
            data["then"] = [s.to_dict() for s in self.then]
        if self.else_:
            data["else"] = [s.to_dict() for s in self.else_]
        if self.body:
            data["body"] = [s.to_dict() for s in self.body]
        return data


@dataclass(frozen=True)
class Workflow:
    """A discovered workflow (catalog view)."""

    name: str
    description: str
    steps: list[Step]
    version: int = 1
    platform: str = ""
    inputs: list[WorkflowInput] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    triggers: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    source: str = "user"
    file_path: Path | None = None
    base_dir: Path | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def input_names(self) -> list[str]:
        return [i.name for i in self.inputs]

    def input_spec(self, name: str) -> WorkflowInput | None:
        for spec in self.inputs:
            if spec.name == name:
                return spec
        return None

    def with_version(self, version: int) -> "Workflow":
        return replace(self, version=version)

    def to_dict(self, *, include_steps: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "platform": self.platform,
            "inputs": [i.to_dict() for i in self.inputs],
            "outputs": dict(self.outputs),
            "triggers": list(self.triggers),
            "provenance": dict(self.provenance),
            "status": self.status,
            "source": self.source,
            "step_count": len(self.steps),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "file_path": str(self.file_path) if self.file_path else "",
        }
        if include_steps:
            data["steps"] = [s.to_dict() for s in self.steps]
        return data


@dataclass(frozen=True)
class Locator:
    """Semantic locator with a fallback ladder (W11/W12).

    ``primary`` is the preferred descriptor; ``fallback`` is an ordered list of
    alternative descriptors. A descriptor is a mapping such as
    ``{"role": "button", "name": "上传图文"}`` or ``{"identifier": "title-input"}``
    or, as a last resort, ``{"coords": [x, y]}``.
    """

    primary: dict[str, Any] = field(default_factory=dict)
    fallback: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Locator | None":
        if not data:
            return None
        fallback = data.get("fallback") or []
        if isinstance(fallback, dict):
            fallback = [fallback]
        primary = {k: v for k, v in data.items() if k != "fallback"}
        return cls(primary=primary, fallback=[f for f in fallback if isinstance(f, dict)])

    def candidates(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.primary:
            out.append(self.primary)
        out.extend(self.fallback)
        return out

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.primary)
        if self.fallback:
            data["fallback"] = [dict(f) for f in self.fallback]
        return data


@dataclass(frozen=True)
class RunEvent:
    """One event in a workflow run stream (W32/W33)."""

    run_id: str
    seq: int
    type: str  # run_start | step_start | step_end | self_heal | run_end | error
    step_id: str = ""
    status: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "step_id": self.step_id,
            "status": self.status,
            "message": self.message,
            "data": self.data,
            "at": self.at,
        }


@dataclass
class Run:
    """Mutable run state (persisted for checkpoint/resume, W09)."""

    run_id: str
    workflow: str
    status: str = "running"  # running | ok | failed | paused | needs_human
    context: dict[str, Any] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    ended_at: str = ""
    trigger: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "status": self.status,
            "context": self.context,
            "completed": list(self.completed),
            "outputs": self.outputs,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "trigger": self.trigger,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        return cls(
            run_id=str(data.get("run_id") or ""),
            workflow=str(data.get("workflow") or ""),
            status=str(data.get("status") or "running"),
            context=dict(data.get("context") or {}),
            completed=[str(x) for x in (data.get("completed") or [])],
            outputs=dict(data.get("outputs") or {}),
            error=str(data.get("error") or ""),
            started_at=str(data.get("started_at") or ""),
            ended_at=str(data.get("ended_at") or ""),
            trigger=str(data.get("trigger") or "manual"),
        )


class WorkflowError(Exception):
    """Base error for workflow parse/validate/run failures."""


class WorkflowParseError(WorkflowError):
    """The workflow definition is malformed."""


class WorkflowValidationError(WorkflowError):
    """The workflow definition is structurally invalid."""


class StepFailed(WorkflowError):
    """A step failed after all recovery attempts."""

    def __init__(self, step_id: str, message: str):
        super().__init__(f"step '{step_id}' failed: {message}")
        self.step_id = step_id
        self.message = message


class NeedsHuman(WorkflowError):
    """A human-approval step paused the run (W22/W37)."""

    def __init__(self, step_id: str, message: str = ""):
        super().__init__(message or f"step '{step_id}' needs human input")
        self.step_id = step_id
