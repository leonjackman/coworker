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
    {
        "command", "tool", "browser", "app", "computer", "skill", "human", "agentic",
        "http", "file", "transform", "notify",
    }
)
CONTROL_KINDS = frozenset(
    {"set", "assert", "wait", "branch", "loop", "parallel", "subworkflow"}
)
VALID_KINDS = ACTION_KINDS | CONTROL_KINDS

# Input value types.
VALID_INPUT_TYPES = frozenset({"string", "number", "boolean", "list", "object", "secret"})

VALID_STEP_MODES = frozenset({"auto", "agent"})

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
    # Step contract (P0): what the step is for and what "done" means beyond the
    # action's own ok. ``success`` is merged with ``post`` at verification time.
    goal: str = ""
    success: list[str] = field(default_factory=list)
    # Execution mode: "auto" = deterministic first, agent takes over on failure;
    # "agent" = hand this step straight to the agent (which must self-assess).
    mode: str = "auto"
    on_error: dict[str, Any] = field(default_factory=dict)
    timeout: int = 30
    approval: bool = False
    when: str = ""
    foreach: str = ""
    as_name: str = ""
    # Explicit successor (id) for edge-driven canvas wiring. Empty = unset.
    next: str = ""
    then: list["Step"] = field(default_factory=list)
    else_: list["Step"] = field(default_factory=list)
    body: list["Step"] = field(default_factory=list)
    description: str = ""
    # Explicit, auditable opt-out of specific conformance lints (e.g. a GUI step
    # that genuinely can only be targeted by coordinates). Only the listed codes
    # are downgraded from error to warning; everything else still blocks.
    bypass: list[str] = field(default_factory=list)
    bypass_reason: str = ""
    # Intent/Binding two-layer model. The step's INTENT is ``goal`` (kept in
    # sync with ``description`` for labels/compat); the machine success
    # criterion lives in ``post``. ``origin`` records who authored the binding
    # (user/agent) when known.
    origin: str = ""
    # 絕對遵守 (absolute-obey): a HARD, user-only constraint. When set, the
    # agent may not change the existing intent/binding, substitute a method,
    # self-heal, or take over. Empty fields may still be filled once, then
    # frozen. Only user-authored workflows may set this (enforced in the
    # manager's agent-write path).
    absolute: bool = False

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
        if self.goal:
            data["goal"] = self.goal
        if self.success:
            data["success"] = list(self.success)
        if self.mode and self.mode != "auto":
            data["mode"] = self.mode
        if self.on_error:
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
        if self.next:
            data["next"] = self.next
        if self.description:
            data["description"] = self.description
        if self.bypass:
            data["bypass"] = list(self.bypass)
        if self.bypass_reason:
            data["bypass_reason"] = self.bypass_reason
        if self.origin:
            data["origin"] = self.origin
        if self.absolute:
            data["absolute"] = True
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
    schema_version: int = 1
    platform: str = ""
    inputs: list[WorkflowInput] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    triggers: list[str] = field(default_factory=list)
    # Canvas wiring for the trigger/output endpoint nodes. ``None`` means "derive
    # from the step list" (legacy behaviour); an explicit value (including an
    # empty string / empty list) means the author wired the endpoints by hand, so
    # the trigger may be unconnected and any steps may feed the output.
    entry: str | None = None
    exits: list[str] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    status: str = "active"
    source: str = "user"
    file_path: Path | None = None
    base_dir: Path | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def platform_tags(self) -> list[str]:
        """Normalized platform tags this workflow targets (all tags = any)."""
        from .platform_support import ALL_TAGS, parse_platforms

        declared = parse_platforms(self.platform)
        return [t for t in ALL_TAGS if t in declared]

    def supports_current_platform(self) -> bool:
        from .platform_support import workflow_supports

        return workflow_supports(self)[0]

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
            "schema_version": self.schema_version,
            "platform": self.platform,
            "platform_tags": self.platform_tags,
            "inputs": [i.to_dict() for i in self.inputs],
            "outputs": dict(self.outputs),
            "triggers": list(self.triggers),
            "entry": self.entry,
            "exits": list(self.exits) if self.exits is not None else None,
            "provenance": dict(self.provenance),
            "fingerprint": self.fingerprint,
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
    pending_step: str = ""
    # Human-gate details, set when status == "needs_human":
    # kind ∈ approval | question | failure ; retryable = re-run makes sense.
    gate_kind: str = ""
    gate_retryable: bool = False
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
            "pending_step": self.pending_step,
            "gate_kind": self.gate_kind,
            "gate_retryable": self.gate_retryable,
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
            pending_step=str(data.get("pending_step") or ""),
            gate_kind=str(data.get("gate_kind") or ""),
            gate_retryable=bool(data.get("gate_retryable") or False),
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
    """A step failed after all recovery attempts.

    ``result`` optionally carries the raw adapter/tool result that triggered the
    failure (e.g. the ``{error, error_code}`` envelope), so callers can surface
    the real cause instead of a generic message.
    """

    def __init__(self, step_id: str, message: str, result: Any = None):
        super().__init__(f"step '{step_id}' failed: {message}")
        self.step_id = step_id
        self.message = message
        self.result = result


class NeedsHuman(WorkflowError):
    """A run paused for a human gate.

    ``kind`` distinguishes WHY a human is needed (never guess it from the
    message):

    * ``approval`` — an explicit ``approval: true`` step (authorize the action).
    * ``question`` — an explicit ``human`` step (an answer is needed).
    * ``failure``  — a step failed and the policy escalated to a person.

    ``retryable`` says whether re-running the step after the human intervenes is
    meaningful (a failure gate usually is; a question gate resolves with an
    answer, not a retry).
    """

    def __init__(
        self,
        step_id: str,
        message: str = "",
        result: Any = None,
        kind: str = "question",
        retryable: bool = True,
    ):
        super().__init__(message or f"step '{step_id}' needs human input")
        self.step_id = step_id
        self.message = message
        self.result = result
        self.kind = kind
        self.retryable = retryable


class GotoStep(WorkflowError):
    """Control-flow jump requested by an ``on_error.then: goto:<id>`` policy."""

    def __init__(self, step_id: str, target: str):
        super().__init__(f"goto {target} from {step_id}")
        self.step_id = step_id
        self.target = target


class SkippedStep:
    """Sentinel result recorded when an ``on_error.then: skip`` policy fires."""

    def __init__(self, step_id: str, error: str = ""):
        self.step_id = step_id
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {"skipped": True, "step_id": self.step_id, "error": self.error}
