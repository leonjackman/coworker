"""Workflow capability for CoWorker.

A workflow is a declarative, versioned, parameterized orchestration that the
runtime executes deterministically (with assertions and self-healing) — the
executable counterpart of a Skill.

Public surface:
    Workflow, Step, WorkflowInput, Locator, Run, RunEvent
    WorkflowManager, WorkflowStore, WorkflowRegistry, WorkflowExecutor
    StepEnvironment, CallbackEnvironment
"""

from .env import CallbackEnvironment, StepEnvironment
from .executor import WorkflowExecutor
from .manager import WorkflowManager
from .model import (
    CONTROL_KINDS,
    ACTION_KINDS,
    VALID_KINDS,
    Locator,
    NeedsHuman,
    Run,
    RunEvent,
    Step,
    StepFailed,
    Workflow,
    WorkflowError,
    WorkflowInput,
    WorkflowParseError,
    WorkflowValidationError,
)
from .parser import parse_workflow, render_workflow, validate
from .recorder import extract_steps_from_trace, record_draft
from .registry import WorkflowRegistry
from .store import WorkflowStore

__all__ = [
    "ACTION_KINDS",
    "CONTROL_KINDS",
    "VALID_KINDS",
    "CallbackEnvironment",
    "Locator",
    "NeedsHuman",
    "Run",
    "RunEvent",
    "Step",
    "StepEnvironment",
    "StepFailed",
    "Workflow",
    "WorkflowError",
    "WorkflowExecutor",
    "WorkflowInput",
    "WorkflowManager",
    "WorkflowParseError",
    "WorkflowRegistry",
    "WorkflowStore",
    "WorkflowValidationError",
    "extract_steps_from_trace",
    "parse_workflow",
    "record_draft",
    "render_workflow",
    "validate",
]
