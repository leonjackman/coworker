"""HumanInTheLoop middleware: command approval, MCP approval, interrupt helpers.

This module provides the HITL approval layer that gates write commands, file
modifications, MCP tool calls, and user questions based on phase and autonomy.
"""

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import Runtime
from langchain_core.messages import HumanMessage

from ...logger import get_logger
from ...steer import steer_inbox
from ...workspace import CommandApprovalStore, READ_ONLY_COMMANDS
from .base import _json_safe, _mcp_context
from ..core import (
    AskUserOption,
    CoworkerAgentState,
    _is_external_path_candidate,
    format_user_message,
    normalize_autonomy,
    normalize_phase,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dynamic interrupt_on mapping for MCP tool names
# ---------------------------------------------------------------------------

class _DynamicInterruptOn(dict):
    """``interrupt_on`` mapping that resolves MCP tool names on demand.

    ``HumanInTheLoopMiddleware`` looks its config up by tool name at interrupt
    time (``self.interrupt_on.get(name)`` / ``[name]``), so tool names that are
    only known once an MCP server connects can be resolved lazily here instead
    of being frozen into a static dict at graph-build time.
    """

    def __init__(self, static: dict[str, Any], resolver: Callable[[str], Any | None]):
        super().__init__(static)
        self._resolver = resolver

    def _resolve(self, key: Any) -> Any | None:
        if not isinstance(key, str) or not key:
            return None
        try:
            return self._resolver(key)
        except Exception:  # noqa: BLE001 - approval lookup must never break a run
            return None

    def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        resolved = self._resolve(key)
        return default if resolved is None else resolved

    def __missing__(self, key: Any) -> Any:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        return resolved

    def __contains__(self, key: Any) -> bool:  # type: ignore[override]
        return dict.__contains__(self, key) or self._resolve(key) is not None


# ---------------------------------------------------------------------------
# MCP interrupt description
# ---------------------------------------------------------------------------

def _mcp_interrupt_description(tool_call: Any, state: Any, runtime: Any) -> str:
    """Human-readable description for an MCP tool approval request."""
    name = str((tool_call or {}).get("name") or "")
    args = (tool_call or {}).get("args")
    try:
        rendered = json.dumps(args, ensure_ascii=False, default=str)[:600]
    except Exception:  # noqa: BLE001
        rendered = str(args)[:600]
    return (
        "Coworker needs approval before calling an external MCP tool.\n\n"
        f"Tool: {name}\nArgs: {rendered}"
    )


# ---------------------------------------------------------------------------
# command_approval_middleware factory
# ---------------------------------------------------------------------------

def command_approval_middleware(
    approval_store: CommandApprovalStore | None = None,
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
    workspace: Any | None = None,  # NEW: for external write detection in guarded mode
) -> list[Any]:
    """Always-mounted HITL middleware; approval decisions live in ``when``
    predicates that read phase/autonomy from agent state.

    * ``run_command`` / write tools: interrupt only in ``execute`` phase with
      ``supervised`` autonomy. ``guarded`` runs allowlisted commands inside the
      workspace automatically (Codex ``on-request``); ``autonomous`` never asks.
    * ``ask_user``: always interrupts — the tool is only reachable when the
      phase gate exposes it, so this is decoupled from the permission switch
      (fixes D3: full access no longer kills the question capability).
    * MCP tools (resolved dynamically through ``mcp_policy``): MCP calls leave
      the workspace sandbox entirely, so they get their own risk ladder derived
      from the server's ``ToolAnnotations``:

      =============  ==========  ==================  ================
      autonomy       read-only   write / undeclared  destructive
      =============  ==========  ==================  ================
      supervised     auto        ask                 ask
      guarded        auto        auto                ask
      autonomous     auto        auto                auto
      =============  ==========  ==================  ================

      A server the user marked ``trusted`` is exempt at every level (that is
      the entire meaning of the trust toggle), and "always allow" adds the
      individual tool to the approval allowlist.
    """
    workspace_root = workspace.root if workspace is not None else None
    from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware

    def _is_read_only_command(command_list: list[str]) -> bool:
        if not command_list:
            return False
        return Path(command_list[0]).name in READ_ONLY_COMMANDS

    def _needs_command_approval(req: Any) -> bool:
        state = req.state
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        if phase != "execute":
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy in ("guarded", "autonomous"):
            return False
        # read-only commands in supervised → direct pass (ls, cat, head, etc.)
        tool_input = req.tool_call.get("args", {}) if isinstance(req.tool_call, dict) else {}
        command_val = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if isinstance(command_val, list):
            _parts = command_val if command_val else []
        elif isinstance(command_val, str):
            _parts = command_val.split() if command_val else []
        else:
            _parts = []
        return not _is_read_only_command(_parts)

    def _needs_write_approval(req: Any) -> bool:
        state = req.state
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        if phase != "execute":
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy == "autonomous":
            return False
        if autonomy == "supervised":
            return True
        if autonomy == "guarded" and workspace_root is not None:
            tool_args = req.tool_call.get("args", {}) if isinstance(req.tool_call, dict) else {}
            file_path = str(tool_args.get("file_path", "") or "") if isinstance(tool_args, dict) else ""
            return file_path and _is_external_path_candidate(file_path, workspace_root)
        return False

    def _needs_mcp_approval(req: Any) -> bool:
        state = req.state
        if normalize_phase(state.get("phase"), state.get("work_mode")) != "execute":
            return False
        policy = _mcp_policy_for(req.tool_call)
        if policy is None:
            return False
        if policy.get("trusted") or policy.get("read_only"):
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy == "autonomous":
            return False
        if autonomy == "guarded":
            annotations = policy.get("annotations") or {}
            if annotations.get("destructive") is not True:
                return False
        return not (policy.get("digest") and approval_store is not None and approval_store.is_always_allowed(policy["digest"]))

    def _needs_sensitive_approval(req: Any) -> bool:
        state = req.state
        autonomy = normalize_autonomy(state.get("autonomy"))
        # memory + install_skill: HITL for supervised + guarded, direct pass for
        # autonomous. No phase gate here: memory may be written from any phase
        # (planning included); install_skill stays execute-only via the phase gate.
        return autonomy != "autonomous"

    def _needs_ask_user(req: Any) -> bool:
        state = req.state
        return not (normalize_phase(state.get("phase"), state.get("work_mode")) == "execute" and normalize_autonomy(state.get("autonomy")) == "autonomous")

    write_configs: dict[str, Any] = {}
    for tool_name in ("write_file", "replace_in_file", "apply_text_edits"):
        write_configs[tool_name] = {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to modify a file.",
            "when": _needs_write_approval,
        }

    static_configs: dict[str, Any] = {**write_configs,
        "run_command": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker needs approval before running this workspace command.",
            "when": _needs_command_approval,
        },
        "memory": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to update its long-term memory for this project.",
            "when": _needs_sensitive_approval,
        },
        # Skill writes (install_skill / skill_manage) are NOT HITL-gated: they
        # stage a DRAFT that waits in the review queue (per the auto-skills
        # "require approval" setting). A second immediate card would double-gate
        # the same write — the pending panel is the single approval surface.
        "ask_user": {
            "allowed_decisions": ["respond", "reject"],
            "description": "Coworker asks the user a question that needs an answer.",
            "when": _needs_ask_user,
        },
    }

    def _mcp_policy_for(tool_call: Any) -> dict[str, Any] | None:
        if mcp_policy is None:
            return None
        name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
        if not name:
            return None
        try:
            return mcp_policy(str(name))
        except Exception:  # noqa: BLE001 - a broken policy lookup must not break the run
            return None

    def resolve_mcp_config(name: str) -> dict[str, Any] | None:
        if mcp_policy is None:
            return None
        try:
            if mcp_policy(name) is None:
                return None
        except Exception:  # noqa: BLE001
            return None
        return {
            "allowed_decisions": ["approve", "reject"],
            "description": _mcp_interrupt_description,
            "when": _needs_mcp_approval,
        }

    hitl = HumanInTheLoopMiddleware(interrupt_on=static_configs)
    if mcp_policy is not None:
        hitl.interrupt_on = _DynamicInterruptOn(hitl.interrupt_on, resolve_mcp_config)
    return [hitl]


# ---------------------------------------------------------------------------
# Interrupt helpers (used by runtime.py)
# ---------------------------------------------------------------------------

def interrupt_payload(interrupt: Any) -> dict[str, Any]:
    value = getattr(interrupt, "value", None)
    return value if isinstance(value, dict) else {"value": value}


def interrupt_id(interrupt: Any) -> str:
    return str(getattr(interrupt, "id", "") or "")


def interrupt_action_requests(value: dict[str, Any]) -> list[dict[str, Any]]:
    action_requests = value.get("action_requests") if isinstance(value, dict) else None
    if not isinstance(action_requests, list):
        return []
    return [action for action in action_requests if isinstance(action, dict)]


def interrupt_action_kind(
    action: dict[str, Any],
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
) -> str:
    name = str(action.get("name") or "")
    if name == "ask_user":
        return "question"
    if mcp_policy is not None and name:
        try:
            if mcp_policy(name) is not None:
                return "mcp"
        except Exception:
            logger.exception("mcp_policy lookup failed for name=%r", name)
    return "command"


def interrupt_command_details(value: dict[str, Any]) -> tuple[list[str], str, int]:
    action_requests = value.get("action_requests") if isinstance(value, dict) else None
    action = action_requests[0] if isinstance(action_requests, list) and action_requests else {}
    args = action.get("args") if isinstance(action, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    cwd = args.get("cwd") if isinstance(args, dict) else ""
    timeout_seconds = args.get("timeout_seconds") if isinstance(args, dict) else 20
    safe_command = command if isinstance(command, list) and all(isinstance(part, str) for part in command) else []
    return safe_command, str(cwd or ""), int(timeout_seconds or 20)


def mcp_policy_resolver(session_manager: Any | None) -> Callable[[str], dict[str, Any] | None] | None:
    """``tool_policy`` accessor for a session manager (``None`` when absent)."""
    if session_manager is None:
        return None
    resolver = getattr(session_manager, "tool_policy", None)
    return resolver if callable(resolver) else None


def record_runtime_interrupts(
    interrupts: Iterable[Any],
    approval_store: CommandApprovalStore,
    context: dict[str, Any],
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:

    approvals: list[dict[str, Any]] = []
    for interrupt in interrupts:
        value = interrupt_payload(interrupt)
        current_interrupt_id = interrupt_id(interrupt)
        actions = interrupt_action_requests(value)
        if not actions:
            command, cwd, timeout_seconds = interrupt_command_details(value)
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, 0, "command", command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": 0, "hitl_request": _json_safe(value)},
            )
            approvals.append(approval)
            continue
        for action_index, action in enumerate(actions):
            args = action.get("args") if isinstance(action, dict) else {}
            args = args if isinstance(args, dict) else {}
            args = {
                key: ([item.model_dump() if isinstance(item, AskUserOption) else item for item in value] if key == "options" and isinstance(value, list) else value)
                for key, value in args.items()
            }
            kind = interrupt_action_kind(action, mcp_policy)
            policy: dict[str, Any] | None = None
            if kind in ("question", "mcp"):
                command, cwd, timeout_seconds = [], "", 20
                if kind == "mcp" and mcp_policy is not None:
                    try:
                        policy = mcp_policy(str(action.get("name") or ""))
                    except Exception:  # noqa: BLE001
                        policy = None
            else:
                command, cwd, timeout_seconds = interrupt_command_details({"action_requests": [action]})
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, action_index, kind, command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": action_index, "action_count": len(actions), "tool_name": str(action.get("name") or ""), "action_args": args, **_mcp_context(policy), "hitl_request": _json_safe(value)},
            )
            approvals.append(approval)
    return approvals


def stream_event_from_interrupt(approval: dict[str, Any]) -> dict[str, Any]:

    context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
    kind = str(context.get("kind") or "command")
    base = {
        "approval_id": approval.get("id", ""),
        "approval_status": approval.get("status", "pending"),
        "session_id": str(context.get("session_id") or ""),
    }
    if kind == "question":
        args = context.get("action_args") if isinstance(context.get("action_args"), dict) else {}
        options = args.get("options") if isinstance(args.get("options"), list) else []
        question = str(args.get("question") or "").strip()
        if not question:
            # 防御：模型偶发以空 question 调 ask_user → 前端会渲染空提问卡。
            # 兜底给一句可读文案（前端另有本地化兜底），绝不把空串发出去。
            question = "The agent needs your input to continue — please reply with guidance."
        return {
            **base, "type": "question_required",
            "question": question,
            "header": str(args.get("header") or ""),
            "options": options,
            "multiple": bool(args.get("multiple")),
        }
    if kind == "mcp":
        args = context.get("action_args") if isinstance(context.get("action_args"), dict) else {}
        mcp = context.get("mcp") if isinstance(context.get("mcp"), dict) else {}
        annotations = mcp.get("annotations") if isinstance(mcp.get("annotations"), dict) else {}
        return {
            **base,
            "type": "approval_required",
            "kind": "mcp",
            "command": [],
            "cwd": "",
            "tool_name": str(context.get("tool_name") or ""),
            "tool_args": _json_safe(args),
            "server_name": str(mcp.get("server_name") or ""),
            "server_id": str(mcp.get("server_id") or ""),
            "remote_name": str(mcp.get("remote_name") or ""),
            "read_only": bool(mcp.get("read_only")),
            "destructive": annotations.get("destructive") is True,
        }
    return {
        **base, "type": "approval_required", "kind": "command",
        "command": approval.get("command", []),
        "cwd": approval.get("cwd", ""),
        # Non-command tools (write_file, memory, …) are classified as "command"
        # with an empty argv — carry the tool identity so the UI can still show
        # what the agent wants to do instead of a blank card.
        "tool_name": str(context.get("tool_name") or ""),
        "tool_args": _json_safe(context.get("action_args") or {}),
    }
