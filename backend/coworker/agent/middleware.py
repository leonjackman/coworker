"""Agent middleware: phase gating, HITL approval, compaction, loop guards.

Extracted from the former monolithic ``coworker/agents.py``. Contains:

* ``command_approval_middleware`` + interrupt helpers (HITL approval layer);
* ``NormalizeMessagesMiddleware``, ``CoworkerSummarizationMiddleware``,
  ``ToolCallCleanerMiddleware``, ``PhaseToolGateMiddleware``,
  ``StallRetryMiddleware``, ``RepeatedToolCallMiddleware``,
  ``ContextGuardMiddleware`` + ``ContextOverflowError``.

Depends only on ``agent.core`` (shared helpers / types), ``agent.prompts`` and
``agent.model_defaults`` — never on ``agent.runtime`` / ``agent.graph`` — so the
import DAG stays acyclic (runtime → graph → middleware → core).
"""

import json
import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import get_buffer_string
from pydantic import BaseModel

from ..logger import get_logger
from ..steer import steer_inbox
from ..workspace import CommandApprovalStore, READ_ONLY_COMMANDS
from .core import (
    CONTEXT_SAFETY_FACTOR,
    CHARS_PER_TOKEN,
    Language,
    VALID_LANGUAGES,
    MAX_IMAGES_PER_PROMPT,
    AskUserOption,
    CoworkerAgentState,
    _CHANGE_TOOL_NAMES,
    _EXEC_TOOLS,
    _MEMORY_TOOLS,
    _PLAN_TOOLS,
    _READ_ONLY_TOOLS,
    _estimate_tokens,
    _is_external_path_candidate,
    _msg_chars,
    _msg_tokens,
    _truncate_message,
    context_budget_chars,
    context_budget_tokens,
    format_user_message,
    normalize_autonomy,
    normalize_language,
    normalize_phase,
    normalize_work_mode,
)
from .model_defaults import ReasonPreservingChatOpenAI, openai_compatible_base_url
from .prompts import phase_system_prompt

logger = get_logger(__name__)


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
        "install_skill": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to install a new skill. Installing persists across "
            "sessions and injects the skill's instructions into future conversations.",
            "when": _needs_sensitive_approval,
        },
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


def _json_safe(value: Any) -> Any:
    if isinstance(value, AskUserOption):
        return value.model_dump()
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def interrupt_command_details(value: dict[str, Any]) -> tuple[list[str], str, int]:
    action_requests = value.get("action_requests") if isinstance(value, dict) else None
    action = action_requests[0] if isinstance(action_requests, list) and action_requests else {}
    args = action.get("args") if isinstance(action, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    cwd = args.get("cwd") if isinstance(args, dict) else ""
    timeout_seconds = args.get("timeout_seconds") if isinstance(args, dict) else 20
    safe_command = command if isinstance(command, list) and all(isinstance(part, str) for part in command) else []
    return safe_command, str(cwd or ""), int(timeout_seconds or 20)


def _mcp_context(policy: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-safe MCP descriptor stored on the approval record.

    ``digest`` is what "always allow" writes to the approval allowlist, so it
    has to survive the round-trip through the store.
    """
    if not policy:
        return {}
    return {
        "mcp": {
            "server_id": str(policy.get("server_id") or ""),
            "server_name": str(policy.get("server_name") or ""),
            "remote_name": str(policy.get("remote_name") or ""),
            "digest": str(policy.get("digest") or ""),
            "read_only": bool(policy.get("read_only")),
            "trusted": bool(policy.get("trusted")),
            "annotations": _json_safe(policy.get("annotations") or {}),
        }
    }


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
        return {
            **base, "type": "question_required",
            "question": str(args.get("question") or ""),
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
    return {**base, "type": "approval_required", "kind": "command", "command": approval.get("command", []), "cwd": approval.get("cwd", "")}



class NormalizeMessagesMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Ensures no ``system`` message ends up in a non‑first position of the
    message list passed to the model.

    Some providers (e.g. Qwen3.6 / vLLM) reject any request where a system
    message is not the very first message. Historical checkpoints created
    before the plan marker fix can contain a residual ``SystemMessage``
    (``[CW-PLAN]``) in the middle of the conversation, which would trigger a
    400 on resume. This middleware downgrades such misplaced system messages
    to ``human`` (content preserved) right before each model call.
    """

    def _normalize(self, state: CoworkerAgentState) -> list[Any] | None:
        from langchain_core.messages import HumanMessage

        messages = state.get("messages", [])
        if not messages:
            return None

        changed = False
        normalized: list[Any] = []
        for index, msg in enumerate(messages):
            msg_type = getattr(msg, "type", None)
            if msg_type == "system" and index > 0:
                normalized.append(HumanMessage(content=msg.content, id=getattr(msg, "id", None), additional_kwargs=msg.additional_kwargs or {}))
                changed = True
            else:
                normalized.append(msg)

        return normalized if changed else None

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}


class SteerInjectionMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Inject user interjections (插話) into the running turn at model boundaries.

    The interjection feature lets the user pick a queued message and steer the
    agent WHILE it is still streaming — without pausing or aborting the current
    stream (opencode/codex "steer" semantics). The frontend pushes the message
    via ``/chat/interject`` into :data:`coworker.steer.steer_inbox`; this
    middleware drains that inbox at every model-call boundary and folds the
    pending messages into the next model request as ``HumanMessage`` inputs, so
    the agent's next reasoning step incorporates the guidance.

    The current in-flight ``llm.stream`` is never interrupted: steers only take
    effect on the NEXT model call (i.e. after the current response settles and
    any tool round completes). Steers that arrive after the graph already
    finished stay pending for the frontend's auto-continue fallback.

    ``before_model`` state overrides are request-local, so the middleware keeps
    an instance-level ``_injected`` list (the middleware instance is rebuilt
    every turn) to make already-injected steers visible to EVERY subsequent
    model call in the turn, exactly as if they were part of the conversation.
    """

    def __init__(self, steer_emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__()
        # Runtime callback that buffers a ``steer_injected`` frame for ``parts``
        # persistence and publishes it to the session event bus (mirrors the
        # delegation emit wiring in the runtime).
        self._emit = steer_emit
        # Steers already folded into this turn's conversation (persist across
        # every model call of the turn).
        self._injected: list[HumanMessage] = []
        self._injected_ids: set[str] = set()

    def _steer_message(self, entry: Any) -> HumanMessage:
        content = format_user_message(
            entry.content,
            entry.attachments or [],
            entry.references or [],
            max_attachment_bytes=getattr(entry, "max_attachment_bytes", None),
        )
        return HumanMessage(content=content, id=f"steer-{entry.id}")

    def _inject(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        session_id = str(state.get("session_id") or "")
        if not session_id:
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        try:
            fresh = steer_inbox.take_all(session_id)
        except Exception:  # noqa: BLE001 - an inbox hiccup must never break a model call
            fresh = []
        if not fresh and not self._injected:
            return None
        new_messages: list[HumanMessage] = []
        for entry in fresh:
            steer_id = str(getattr(entry, "id", "") or "")
            if steer_id and steer_id in self._injected_ids:
                continue
            new_messages.append(self._steer_message(entry))
            self._injected_ids.add(steer_id)
            if self._emit is not None:
                try:
                    self._emit(
                        {
                            "type": "steer_injected",
                            "session_id": session_id,
                            "steer_id": steer_id,
                            "content": str(getattr(entry, "content", "") or ""),
                        }
                    )
                except Exception:  # noqa: BLE001 - emission is best-effort
                    logger.debug("steer_injected emit failed for %s", steer_id, exc_info=True)
        if not new_messages and not self._injected:
            return None
        injected = list(self._injected)
        injected.extend(new_messages)
        self._injected = injected
        return {"messages": list(messages) + injected}

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._inject(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._inject(state)


KEEP_RECENT_TOKENS = 8_000
# Summary output cap (opencode SUMMARY_OUTPUT_TOKENS=4096): the compacted summary
# must stay small so repeated anchored compactions do not bloat the resident set.
SUMMARY_OUTPUT_TOKENS = 4_096
# Serialized summarizer input budget. Tool results are truncated to
# TOOL_OUTPUT_MAX_CHARS before formatting; if the serialized head still exceeds
# this, the oldest messages are dropped until it fits (opencode feeds the full
# head subject to the summarizer's own context window).
SUMMARY_INPUT_MAX_TOKENS = 32_000
# Tool output truncation length when serializing messages for the summary
# (opencode TOOL_OUTPUT_MAX_CHARS=2000).
TOOL_OUTPUT_MAX_CHARS = 2_000
# Conservative chars/token used when TRUNCATING an oversized message to fit a
# token budget. CJK is ~1.6 chars/token (0.6 tokens/char); truncating to
# ``budget * 1.5`` chars keeps the result under ``budget`` tokens for pure CJK
# (1.5 * 0.6 = 0.9) and well under for Latin — unlike ``budget * CHARS_PER_TOKEN``,
# which leaves a CJK message ~2.2x over its token budget.
TRUNCATE_CHARS_PER_TOKEN = 1.5


def _summary_ok(text: str) -> bool:
    """Reject degenerate summaries before they are injected into the context.

    Guards against the observed failure mode where the summarizer was fed a
    numeric transcript (character counts) and "summarized" it into a wall of
    numbers. A real summary must contain substantive language.
    """
    if not text:
        return False
    t = text.strip()
    if len(t) < 20:
        return False
    if "error generating summary" in t.lower():
        return False
    letters = sum(1 for ch in t if ch.isalpha())
    return letters >= max(10, int(len(t) * 0.2))


# Structured compaction prompt (SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS
# skeleton — same sections LangChain's SummarizationMiddleware uses, localized to
# the session language). The ``<messages>`` marker + ``{messages}`` placeholder
# are part of the framework contract (get_buffer_string feeds the transcript).
COMPACTION_PROMPTS: dict[str, str] = {
    "zh": (
        "你的任务是从下面的会话历史中提炼出最关键的信息，生成一份紧凑的摘要，"
        "用它替换掉这段旧历史，以便在有限上下文窗口内继续当前任务。\n\n"
        "只保留对继续当前目标仍然重要的内容，不要重复已经完成的操作。"
        "请按以下小节组织摘要，每一节都填入相关信息；若无相关内容请写「无」：\n\n"
        "## 会话意图\n"
        "用户的总体目标/诉求是什么？本次会话要完成什么任务？"
        "（简洁但完整到足以理解整个会话的目的）\n\n"
        "## 摘要\n"
        "记录对话中最重要的上下文：关键结论、已做的决策及其理由、"
        "讨论过的被否决方案及否决原因。\n\n"
        "## 产物\n"
        "本次会话创建/修改/访问了哪些文件或资源？对文件修改，列出具体路径并简述改动。"
        "此节用于防止产物信息静默丢失。\n\n"
        "## 后续步骤\n"
        "要达成会话意图还需要完成哪些具体任务？下一步应该做什么？\n\n"
        "只输出提取出的上下文本身，不要输出任何额外说明或前后缀文本。\n\n"
        "<messages>\n需要总结的消息：\n{messages}\n</messages>"
    ),
    "en": (
        "Your task is to extract the most important information from the "
        "conversation history below and produce a compact summary that replaces "
        "it, so work can continue within the context window.\n\n"
        "Keep only what still matters for the current goal; do not repeat work "
        "already completed. Structure the summary with the following sections — "
        "populate each with relevant info or write 'None':\n\n"
        "## SESSION INTENT\n"
        "What is the user's overall goal or request? What task is this session "
        "trying to accomplish? (Concise but complete enough to understand the "
        "purpose of the whole session.)\n\n"
        "## SUMMARY\n"
        "Record the most important context: key conclusions, decisions made and "
        "their rationale, rejected options and why they were not pursued.\n\n"
        "## ARTIFACTS\n"
        "What files or resources were created/modified/accessed in this session? "
        "For file changes, list the specific paths and briefly describe the "
        "changes. This prevents silent loss of artifact information.\n\n"
        "## NEXT STEPS\n"
        "What specific tasks remain to achieve the session intent? What should "
        "be done next?\n\n"
        "Respond ONLY with the extracted context, with no extra text before or "
        "after it.\n\n"
        "<messages>\nMessages to summarize:\n{messages}\n</messages>"
    ),
}


def _compaction_summary_prefix(language: Language) -> str:
    return "先前对话摘要：" if language == "zh" else "[Earlier conversation summary] "


# Anchored-update preamble prepended to the compaction prompt when a previous
# summary exists. Instructs the model to UPDATE (not rewrite) so repeated
# compactions stay small instead of re-summarizing overlapping history (mirrors
# opencode's buildPrompt "Update the anchored summary below ...").
_ANCHORED_PREAMBLES: dict[str, str] = {
    "zh": (
        "以下是本会话上一次压缩时生成的摘要。请基于它更新这份摘要："
        "保留仍然成立的内容，删除已过时的内容，并把下面新对话中出现的新的关键信息并入其中。"
        "保持整体紧凑，不要重复摘要中已有的内容。\n\n"
        "<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    ),
    "en": (
        "Update the anchored summary below using the conversation history that "
        "follows. Preserve still-true details, remove stale details, and merge "
        "in the new facts. Keep it terse; do not repeat what is already in the "
        "anchored summary.\n\n"
        "<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    ),
}


def _anchored_summary_prompt(base_prompt: str, previous_summary: str) -> str:
    """Return the compaction prompt, prefixed with the anchored-update
    instructions when a previous summary exists."""
    if not previous_summary or not previous_summary.strip():
        return base_prompt
    preamble = _ANCHORED_PREAMBLES.get(
        "zh" if "会话意图" in base_prompt else "en",
        _ANCHORED_PREAMBLES["en"],
    )
    return preamble.format(previous_summary=previous_summary.strip()) + base_prompt


def _cap_summary(text: str) -> str:
    """Hard-cap a summary to ``SUMMARY_OUTPUT_TOKENS`` (CJK-aware) so a
    degenerate long output can never bloat the compacted resident set.

    Guarantees the cap even when the summarizer model ignores ``max_tokens``.
    """
    if not text:
        return text
    if _estimate_tokens(text) <= SUMMARY_OUTPUT_TOKENS:
        return text
    marker = "\n[summary truncated by Coworker to fit context]"
    budget = max(1, SUMMARY_OUTPUT_TOKENS - _estimate_tokens(marker))
    # Trim trailing characters until the estimate fits. CJK is dense (~0.6
    # tokens/char), so walk in small steps to avoid over-trimming.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + marker


_COMPACTION_FLUSH: dict[str, str] = {
    "zh": (
        "注意：为保持上下文紧凑，这段对话中最早的部分已被压缩成上面的摘要。"
        "如果其中仍有对未来会话重要的持久事实，请现在通过记忆工具将其保存。"
    ),
    "en": (
        "Note: the oldest part of this conversation was summarized above to keep "
        "the context compact. If any durable fact in it still matters for future "
        "sessions, persist it via the memory tool now."
    ),
}


def _summarizer_candidates(data_dir: Path | None, primary_llm: Any) -> list[Any]:
    """Ordered compaction-model candidates: user default model first, then other
    configured providers, then the primary (per-turn) model.

    The summarizer tries each candidate in turn until one produces a valid
    summary (fallback-until-success), so a broken default model never blocks
    compaction. Falls back to just the primary model with no config present.
    """
    candidates: list[Any] = []
    seen: set[tuple[str, str]] = set()

    def _push(llm: Any) -> None:
        key = (getattr(llm, "model_name", "") or "", getattr(llm, "base_url", "") or "")
        if key in seen:
            return
        seen.add(key)
        candidates.append(llm)

    if data_dir is not None:
        try:
            from ..providers import ProviderManager

            pm = ProviderManager(data_dir / "providers.json", data_dir)
            config = pm.load()
            default = pm.default_provider()
            ordered: list[Any] = []
            if default is not None:
                ordered.append(default)
            for p in config.providers:
                if p.enabled and (default is None or p.id != default.id):
                    ordered.append(p)
            for p in ordered:
                try:
                    _push(
                        ReasonPreservingChatOpenAI.create(
                            model=p.model,
                            temperature=0,
                            api_key=p.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
                            base_url=openai_compatible_base_url(p),
                        )
                    )
                except Exception:  # noqa: BLE001 - one bad provider must not kill the chain
                    continue
        except Exception:  # noqa: BLE001 - config resolution is best-effort
            pass
    if not candidates and primary_llm is not None:
        _push(primary_llm)
    return candidates


def _strip_compaction_echo(content: str, summary: str) -> str:
    """Remove a model's verbatim echo of the injected compaction summary.

    Local models sometimes "continue" the injected summary HumanMessage as part
    of their answer (the observed failure mode). The summary body is exact, so a
    targeted replacement cleans the persisted/displayed reply without touching
    legitimate content.
    """
    if not content or not summary:
        return content
    s = summary.strip()
    if len(s) < 20:
        return content
    if s in content:
        return content.replace(s, "").strip()
    return content




class CoworkerSummarizationMiddleware(SummarizationMiddleware):
    """Framework-backed context compaction with Coworker-specific behavior.

    Subclasses LangChain's :class:`SummarizationMiddleware` to inherit the proven
    mechanics — token/cutoff selection with AI/Tool pair protection, structured
    summary prompt, and HumanMessage summary injection (provider-safe: never a
    system message mid-list, which vLLM/Qwen rejects) — while preserving
    Coworker's product behavior:

    * CJK-aware token counting (``_estimate_tokens``), not the ASCII-only
      ``count_tokens_approximately`` default.
    * ``context_usage`` SSE telemetry on every model call.
    * Mutable per-turn budget (the overflow-retry path halves it).
    * Cheap layer first: stale tool results are cleared (micro-compact) before
      resorting to a model summary.
    * Summary quality validation + fallback to the plain rolling ``_trim``.
    * Dedup so the same segment is never summarized twice (loop guard).
    * Summarizer model fallback chain (user default model first, then other
      configured models) instead of a single fixed LLM.
    """

    def __init__(self, budget_chars: int | None = None, llm: Any | None = None, summarizer_candidates: list[Any] | None = None, language: Language = "zh", context_window_tokens: int = 0, context_window_source: str = "default", context_window_warning: str | None = None, tool_edit: Any | None = None, max_output_tokens: int = 0, calibration_store: Any | None = None, calibration_key: str = ""):
        # The provider reserves ``max_output_tokens`` from the window for the
        # response; budgeting against the RAW window spends that reservation and
        # dies one token past the real input ceiling (the incident 400). Both the
        # char and token budgets are computed on the effective limit.
        self.max_output_tokens = max(0, int(max_output_tokens or 0))
        self.configured_budget = max(20_000, int(budget_chars or context_budget_chars(128_000, self.max_output_tokens)))
        # Mutable per-turn budget (the overflow retry path halves this). The UI
        # always reads ``configured_budget`` so the meter never jumps on a retry
        # — see B9.
        self.budget_chars = self.configured_budget
        # Token-space budget drives trimming/compaction (CJK-aware). Mirrors
        # ``budget_chars`` mutations (overflow retry halves both).
        self.budget_tokens = context_budget_tokens(
            context_window_tokens if context_window_tokens and context_window_tokens > 0 else 128_000,
            self.max_output_tokens,
        )
        self.language = language if language in VALID_LANGUAGES else "zh"
        # Real model context window (tokens) + how it was resolved, surfaced to the
        # UI so the meter shows usage against the ACTUAL window (not just the 75%
        # safety budget) and explains the source — B2/B8.
        self.context_window_tokens = context_window_tokens
        self.context_window_source = context_window_source
        # Human-readable warning about the window (unverified oversized override,
        # or server-reported cap). Surfaced to the UI via context_usage.
        self.context_window_warning = context_window_warning
        # Closed-loop tokenizer calibration (actual usage / raw estimate) shared
        # with the pre-send guard; the meter surfaces the factor + calibrated
        # usage so the topbar shows what the provider will REALLY charge.
        self.calibration_store = calibration_store
        self.calibration_key = calibration_key or ""
        self._summarized_segments: set[str] = set()
        # Cheap layer: ClearToolUsesEdit (Anthropic-style context editing) used
        # BOTH by this middleware (prune-aware trigger, CJK-counted) and by the
        # mounted ContextEditingMiddleware (transient per-call slimming).
        self.tool_edit = tool_edit
        # Summary-model fallback chain: user default model first, then other
        # configured models, then the primary (per-turn) model.
        self.llm = llm
        candidates = list(summarizer_candidates or ())
        if not candidates and llm is not None:
            candidates.append(llm)
        self.summarizer_candidates = candidates
        self.last_summary = ""
        if candidates:
            super().__init__(
                model=candidates[0],
                trigger=("tokens", 1),
                keep=("tokens", 1),
                token_counter=self._cjk_token_counter,
                summary_prompt=COMPACTION_PROMPTS.get(self.language, COMPACTION_PROMPTS["en"]),
                trim_tokens_to_summarize=4000,
            )
        else:
            # No model available at all: the middleware becomes trim-only.
            AgentMiddleware.__init__(self)
            self.model = None
            self.trigger = None
            self.keep = ("tokens", 1)
            self._trigger_clauses: list[Any] = []
            self._trigger_conditions: list[Any] = []
            self.token_counter = self._cjk_token_counter
            self._partial_token_counter = self._cjk_token_counter
            self.summary_prompt = COMPACTION_PROMPTS.get(self.language, COMPACTION_PROMPTS["en"])
            self.trim_tokens_to_summarize = 4000

    @staticmethod
    def _cjk_token_counter(messages: Iterable[Any]) -> int:
        """CJK-aware batch token counter used by trim/cutoff logic."""
        return sum(_msg_tokens(m) for m in messages)

    def _pruned_messages(self, messages: list[Any]) -> list[Any]:
        """Apply the cheap tool-result clear on a copy (CJK-aware decision).

        Uses the SAME CJK/base64-aware counter as every other budget decision —
        the framework default (``count_tokens_approximately``) is ASCII-biased
        and made the prune trigger disagree with the trim trigger on the very
        same message list.
        """
        if self.tool_edit is None:
            return messages
        import copy

        try:
            pruned = copy.deepcopy(list(messages))
            self.tool_edit.apply(pruned, count_tokens=self._cjk_token_counter)
            return pruned
        except Exception:  # noqa: BLE001 - pruning is best-effort
            logger.warning("tool-result pruning failed", exc_info=True)
            return messages

    def _determine_cutoff_index(self, messages: list[Any]) -> int:
        """Token-based cutoff with AI/Tool pairing protection (framework core).

        ``keep_recent`` is a fixed small window (``KEEP_RECENT_TOKENS``, aligned
        with opencode) instead of a fraction of the budget — so after a compact
        the resident set is ``recent + summary`` (~12k), not near the budget
        ceiling. The overflow-retry path that halves the budget keeps this fixed
        too (the summary is already small); trimming still honors the budget.
        """
        keep_recent = max(2_000, KEEP_RECENT_TOKENS)
        self.keep = ("tokens", keep_recent)
        return super()._determine_cutoff_index(messages)

    def _build_new_messages(self, summary: str) -> list[Any]:
        """Inject the summary as a HumanMessage (provider-safe, echo-strippable)."""
        return [
            HumanMessage(
                content=f"{_compaction_summary_prefix(self.language)}{summary}",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]

    def _flush_reminder(self) -> Any:
        """Memory-flush reminder — HumanMessage (never a mid-list system message)."""
        return HumanMessage(
            content=_COMPACTION_FLUSH.get(self.language, _COMPACTION_FLUSH["en"]),
            id="__compaction_flush__",
        )

    def _trim(self, state: CoworkerAgentState) -> list[Any] | None:
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        messages = state.get("messages", [])
        if not messages:
            return None
        total = sum(_msg_tokens(m) for m in messages)
        if total <= self.budget_tokens:
            return None
        # Keep the first message (system prompt) and then the most recent tail
        # (oldest-first drop). Oversized user/system content is truncated instead
        # of dropped so the model still sees the user's current input; oversized
        # tool/AI messages are dropped (cannot be truncated without breaking
        # tool-call pairing).
        head: list[Any] = []
        budget = self.budget_tokens
        for msg in messages[:1]:
            tokens = _msg_tokens(msg)
            if tokens > budget:
                # Convert the token budget to a conservative char cap. CJK is
                # denser than the flat 3.5 chars/token, so use TRUNCATE_CHARS_PER_TOKEN
                # to guarantee the truncated message fits the token budget.
                msg = _truncate_message(msg, max(200, int(budget * TRUNCATE_CHARS_PER_TOKEN)))
            head.append(msg)
            budget -= _msg_tokens(msg)

        kept_tail: list[Any] = []
        for msg in reversed(messages[1:]):
            tokens = _msg_tokens(msg)
            if tokens >= self.budget_tokens:
                # Oversized message: truncate user/system, drop tool/AI.
                if getattr(msg, "type", "") in ("human", "system", "user"):
                    kept_tail.append(_truncate_message(msg, max(200, int(self.budget_tokens * TRUNCATE_CHARS_PER_TOKEN))))
                    budget = 0
                    break
                continue
            if budget - tokens < 0:
                break
            kept_tail.append(msg)
            budget -= tokens

        kept_tail.reverse()
        # Drop any leading ToolMessage whose triggering AIMessage landed in the
        # trimmed gap (a ToolMessage is always preceded by its AIMessage in the
        # list; keeping it alone would 400 the provider).
        while kept_tail and getattr(kept_tail[0], "type", "") == "tool":
            kept_tail.pop(0)

        kept = head + kept_tail
        if len(kept) == len(messages):
            return None
        # Increment the session-level compaction counter. The counter lives in
        # checkpointed state (not on this middleware, which is rebuilt every turn)
        # so it accumulates across turns — see B6.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept], "context_compact_count": 1}

    def _select_compact_plan(self, messages: list[Any]) -> tuple[str, Any, Any] | None:
        """Choose a compaction action: prune tool results, or summarize a segment.

        Returns ``("prune", pruned_messages, None)`` when clearing stale tool
        results alone fits the budget (cheap layer first — Anthropic micro-compact
        semantics), ``("summarize", to_summarize, preserved)`` when a model
        summary is required, or ``None`` when nothing needs to happen.
        """
        if sum(_msg_tokens(m) for m in messages) <= self.budget_tokens:
            return None
        working = messages
        if self.tool_edit is not None:
            working = self._pruned_messages(messages)
            if sum(_msg_tokens(m) for m in working) <= self.budget_tokens:
                return ("prune", working, None)
        cutoff = self._determine_cutoff_index(working)
        if cutoff <= 0:
            return None
        to_summarize, preserved = self._partition_messages(working, cutoff)
        if len(to_summarize) < 2:
            return None
        return ("summarize", to_summarize, preserved)

    def _finish_compact(self, to_summarize: list[Any], preserved: list[Any], summary: str) -> dict[str, Any] | None:
        """Assemble the compacted state from a valid summary (never raises)."""
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        if not summary or not _summary_ok(summary):
            return None
        fingerprint = "|".join(getattr(m, "id", "") or "" for m in to_summarize)
        if fingerprint in self._summarized_segments:
            # Already summarized this exact segment on a prior turn: do not loop.
            return None
        self._summarized_segments.add(fingerprint)
        if len(self._summarized_segments) > 64:
            self._summarized_segments.clear()
        self.last_summary = summary
        kept = [*self._build_new_messages(summary), *preserved]
        # Memory-flush reminder: tell the model the oldest history was compacted
        # and it should persist any still-relevant facts into long-term memory so
        # they survive beyond this session (ties into the auto-memory pipeline).
        kept.append(self._flush_reminder())
        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept],
            "context_compact_count": 1,
            "context_summary": summary,
            # Persist the (capped) fingerprint set so the dedup loop guard
            # survives middleware rebuilds across turns.
            "context_summarized_fingerprints": sorted(self._summarized_segments)[-64:],
        }

    def _compact_sync(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) < 4:
            return None
        plan = self._select_compact_plan(messages)
        if plan is None:
            return None
        kind, a, b = plan
        if kind == "prune":
            from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *a], "context_compact_count": 1}
        return self._finish_compact(a, b, self._create_summary(a, previous_summary=self.last_summary))

    async def _compact_async(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) < 4:
            return None
        plan = self._select_compact_plan(messages)
        if plan is None:
            return None
        kind, a, b = plan
        if kind == "prune":
            from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *a], "context_compact_count": 1}
        summary = await self._acreate_summary(a, previous_summary=self.last_summary)
        return self._finish_compact(a, b, summary)

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self.last_summary = str(state.get("context_summary", "") or "")
        self._summarized_segments = set(state.get("context_summarized_fingerprints") or [])
        if not self.summarizer_candidates:
            return self._trim(state)
        try:
            compacted = self._compact_sync(state)
            if compacted is not None:
                return compacted
        except Exception:  # noqa: BLE001 - compaction must never break a turn
            logger.warning("context compaction failed; falling back to trim", exc_info=True)
        return self._trim(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self.last_summary = str(state.get("context_summary", "") or "")
        self._summarized_segments = set(state.get("context_summarized_fingerprints") or [])
        if not self.summarizer_candidates:
            return self._trim(state)
        try:
            compacted = await self._compact_async(state)
            if compacted is not None:
                return compacted
        except Exception:  # noqa: BLE001 - compaction must never break a turn
            logger.warning("context compaction failed; falling back to trim", exc_info=True)
        return self._trim(state)

    def _serialize_for_summary(self, messages: list[Any]) -> str:
        """Serialize messages for the summarizer: tool results truncated, input
        bounded to ``SUMMARY_INPUT_MAX_TOKENS`` (oldest dropped until it fits).

        Mirrors opencode's ``select``: the whole segment is visible to the
        summarizer (subject to a token budget) instead of only the last few
        thousand tokens, so first-time summaries are complete. Tool outputs are
        truncated to ``TOOL_OUTPUT_MAX_CHARS`` before formatting because they
        dominate the transcript and rarely carry summary-worthy prose.
        """
        if not messages:
            return ""
        import copy

        serialized = copy.deepcopy(list(messages))
        for msg in serialized:
            if getattr(msg, "type", "") != "tool":
                continue
            try:
                content = msg.content
            except Exception:  # noqa: BLE001
                continue
            if isinstance(content, str) and len(content) > TOOL_OUTPUT_MAX_CHARS:
                msg.content = content[:TOOL_OUTPUT_MAX_CHARS] + "\n[truncated]"
        formatted = get_buffer_string(serialized, format="xml")
        if _estimate_tokens(formatted) <= SUMMARY_INPUT_MAX_TOKENS:
            return formatted
        # Drop the oldest messages until the serialized head fits. Pairing is
        # irrelevant here (plain text summarization input, not a provider call).
        for drop in range(1, len(serialized)):
            candidate = get_buffer_string(serialized[drop:], format="xml")
            if _estimate_tokens(candidate) <= SUMMARY_INPUT_MAX_TOKENS or drop == len(serialized) - 1:
                return candidate
        return formatted

    def _create_summary(self, messages_to_summarize: list[Any], previous_summary: str = "") -> str:
        """Synchronous summarizer with the fallback model chain (anchored)."""
        if not messages_to_summarize:
            return ""
        formatted = self._serialize_for_summary(messages_to_summarize)
        if not formatted:
            return ""
        prompt = _anchored_summary_prompt(self.summary_prompt, previous_summary).format(messages=formatted).rstrip()
        for model in self.summarizer_candidates:
            try:
                try:
                    response = model.invoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                        max_tokens=SUMMARY_OUTPUT_TOKENS,
                    )
                except TypeError:
                    # Model does not accept max_tokens as a generation kwarg;
                    # _cap_summary still enforces the output budget.
                    response = model.invoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                text = str(getattr(response, "content", "") or response or "").strip()
                text = _cap_summary(text)
                if _summary_ok(text):
                    return text
                logger.warning("summarizer output rejected (degenerate): %.120s", text)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                logger.warning("summarizer %s failed; trying next: %s", getattr(model, "model_name", "?"), str(exc)[:200])
        return ""

    async def _acreate_summary(self, messages_to_summarize: list[Any], previous_summary: str = "") -> str:
        """Async summarizer with the fallback model chain (anchored)."""
        if not messages_to_summarize:
            return ""
        formatted = self._serialize_for_summary(messages_to_summarize)
        if not formatted:
            return ""
        prompt = _anchored_summary_prompt(self.summary_prompt, previous_summary).format(messages=formatted).rstrip()
        for model in self.summarizer_candidates:
            try:
                try:
                    response = await model.ainvoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                        max_tokens=SUMMARY_OUTPUT_TOKENS,
                    )
                except TypeError:
                    response = await model.ainvoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                text = str(getattr(response, "content", "") or response or "").strip()
                text = _cap_summary(text)
                if _summary_ok(text):
                    return text
                logger.warning("summarizer output rejected (degenerate): %.120s", text)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                logger.warning("summarizer %s failed; trying next: %s", getattr(model, "model_name", "?"), str(exc)[:200])
        return ""



class ToolCallCleanerMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Removes tool calls that the provider emitted without a tool name.

    Some OpenAI-compatible streaming servers (e.g. vLLM with Qwen3.6) can emit
    a parallel tool call whose delta never carries a ``name``, leaving an empty
    ``{"name": "", "args": {}}`` entry in the assistant message. LangChain keeps
    such entries in ``tool_calls``; executing them fails with an invalid-tool
    error, and the corrupted entry is then replayed to the provider on the next
    model call, producing a 400 (``Extra data``). This middleware strips these
    empty tool calls right after the model call so they never reach the tool
    executor or the provider.
    """

    def _clean(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        replacements: list[Any] = []
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                continue
            invalid = [t for t in tool_calls if not (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            if not invalid:
                continue
            from langchain_core.messages import AIMessage
            valid = [t for t in tool_calls if (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            replacements.append(AIMessage(
                content=getattr(msg, "content", None) or "",
                tool_calls=valid,
                id=getattr(msg, "id", None),
                additional_kwargs=getattr(msg, "additional_kwargs", None) or {},
            ))
        if not replacements:
            return None
        return {"messages": replacements}

    def after_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)

    async def aafter_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)


class PhaseToolGateMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Dynamic tool selection based on the agent's phase and autonomy.

    Uses the official ``wrap_model_call`` + ``request.override`` pattern so the
    model only ever sees the tools the current phase allows:

    * ``discuss`` (plan mode before approval): read-only + ``ask_user`` +
      the agent cannot touch the filesystem, but can still write long-term
      memory (the ``memory`` tool) — durable facts belong in planning too.
    * ``execute``: read + write + ``run_command``; ``ask_user`` stays available
      unless autonomy is ``autonomous`` (physical removal — the model cannot
      interrupt the user at all in full-autonomy mode).

    The phase/autonomy-aware system prompt is also injected here so there is a
    single prompt source (fixing the previous double-injection).

    MCP tools are treated as execute-phase tools: they are allowed (and only
    visible) while ``phase == "execute"``. A provider callable supplies the
    currently-connected MCP tool names so the gate can tell them apart from
    unknown tool calls without coupling this module to the MCP layer.
    """

    def __init__(self, capabilities: str = "", mcp_tool_names_provider: Callable[[], set[str]] | None = None, workspace: Any | None = None):
        self.capabilities = capabilities
        self.mcp_tool_names_provider = mcp_tool_names_provider
        self.workspace = workspace

    def _allowed_tools(self, state: CoworkerAgentState) -> set[str]:
        work_mode = normalize_work_mode(state.get("work_mode"))
        phase = normalize_phase(state.get("phase"), work_mode)
        autonomy = normalize_autonomy(state.get("autonomy"))
        allowed = set(_READ_ONLY_TOOLS) | _MEMORY_TOOLS
        if phase == "discuss":
            allowed |= _PLAN_TOOLS
            # use_worker 在讨论（只读）阶段也开放：worker 以只读模式运行（与主
            # agent 一致），专注研究/分析，不改动文件系统。
            allowed |= {"use_worker"}
        else:
            allowed |= _CHANGE_TOOL_NAMES | _EXEC_TOOLS
            if autonomy != "autonomous":
                allowed |= {"ask_user"}
            if self.mcp_tool_names_provider is not None:
                try:
                    allowed |= self.mcp_tool_names_provider()
                except Exception:  # noqa: BLE001 - a broken provider must not gate tools
                    pass
        # Task-list management is available in EVERY phase and mode (build/plan/
        # chat): write_todos only writes graph state, never files, so it stays
        # safe in the read-only discuss phase too.
        allowed.add("write_todos")
        return allowed

    def _overrides(self, request: Any) -> dict[str, Any]:
        state = request.state
        allowed = self._allowed_tools(state)
        tools = [tool for tool in request.tools if getattr(tool, "name", "") in allowed]
        language = normalize_language(state.get("language"))
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        autonomy = normalize_autonomy(state.get("autonomy"))
        # 记录当前 phase，供 use_worker 工具在执行时判断 worker 是否只读。
        if self.workspace is not None:
            try:
                setattr(self.workspace, "_current_phase", phase)
            except Exception:  # noqa: BLE001 - phase tracking must never gate tools
                pass
        prompt = phase_system_prompt(language, phase, autonomy)
        if self.capabilities:
            prompt = f"{prompt}\n\n{self.capabilities}"
        return {
            "tools": tools,
            "system_message": SystemMessage(prompt),
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request.override(**self._overrides(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(**self._overrides(request)))

    def _tool_name(self, request: Any) -> str:
        tool_call = getattr(request, "tool_call", None)
        if isinstance(tool_call, dict):
            return str(tool_call.get("name") or "")
        return str(getattr(tool_call, "name", "") or "")

    def _blocked_tool_message(self, request: Any) -> Any:
        from langchain_core.messages import ToolMessage
        tool_name = self._tool_name(request)
        registered: set[str] = set()
        if self.workspace is not None:
            try:
                registered = set(getattr(self.workspace, "_registered_tool_names", set()) or set())
            except Exception:  # noqa: BLE001 - a broken registry must not crash a turn
                registered = set()
        if registered and tool_name and tool_name not in registered:
            # The model hallucinated a tool name (e.g. list_directory). Tell it the
            # tool does not exist and list what IS available so it can self-correct
            # instead of believing the tool is phase-gated and retrying in vain.
            names = sorted(registered)
            sample = ", ".join(names[:12])
            more = f" … +{len(names) - 12} more" if len(names) > 12 else ""
            content = (
                f"Tool '{tool_name}' does not exist. Only these tools are available: "
                f"{sample}{more}. Use the exact tool names above; do not invent tools."
            )
        else:
            content = (
                f"Tool '{tool_name}' is not available in the current phase/autonomy. "
                "It was skipped."
            )
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call.get("id", "unknown"),
            status="error",
        )

    def _outside_scope(self, request: Any) -> bool:
        tool_name = self._tool_name(request)
        if not tool_name:
            return False
        return tool_name not in self._allowed_tools(request.state)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        # Defense in depth: a tool call that the current phase/autonomy does not
        # allow must never run. Resolve it with an error ToolMessage so the call
        # is closed (avoids a dangling tool_call without a ToolMessage in the
        # checkpoint history, which providers reject on the next turn).
        if self._outside_scope(request):
            return self._blocked_tool_message(request)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self._outside_scope(request):
            return self._blocked_tool_message(request)
        return await handler(request)


class StallRetryMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Retry a model generation call once after a stream-chunk stall.

    ``langchain-openai`` aborts the stream when no chunk arrives for
    ``stream_chunk_timeout`` (``StreamChunkTimeoutError``). A flaky / briefly
    overloaded provider may recover immediately, so we retry the SINGLE model
    call once before letting the error propagate to the SSE layer (which would
    otherwise abort the whole turn). Only the model call is retried — tools are
    never re-run, so this is safe to apply at every model step.
    """

    def __init__(self, max_retries: int = 1) -> None:
        self.max_retries = max(1, int(max_retries))

    @staticmethod
    def _is_stall(exc: BaseException) -> bool:
        # Match by message as well as type: the exception class name/symbol can
        # shift between langchain-openai releases; the message is stable.
        if "stream_chunk_timeout" in str(exc) or "No streaming chunk received" in str(exc):
            return True
        try:
            from langchain_openai.chat_models._client_utils import StreamChunkTimeoutError
        except Exception:  # noqa: BLE001 - version drift must not crash a turn
            return False
        return isinstance(exc, StreamChunkTimeoutError)

    @staticmethod
    def _prompt_tokens(request: Any) -> int:
        """CJK-aware token estimate of the prompt this model call will send.

        Diagnostic only: lets a stall be attributed to an over-sized prompt
        (which some servers, e.g. vLLM, hang on silently instead of erroring).
        """
        messages = list(getattr(request, "messages", None) or [])
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            messages = [system_message, *messages]
        return sum(_msg_tokens(m) for m in messages)

    @staticmethod
    def _model_name(request: Any) -> str:
        model = getattr(request, "model", None)
        if model is not None:
            name = getattr(model, "model_name", None) or getattr(model, "name", None)
            if name:
                return str(name)
        return "?"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        attempt = 0
        while True:
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001 - retry only genuine stalls
                if not self._is_stall(exc):
                    raise
                attempt += 1
                prompt_tokens = self._prompt_tokens(request)
                model = self._model_name(request)
                if attempt > self.max_retries:
                    logger.error(
                        "model stream stalled repeatedly (chunk timeout); giving up "
                        "(model=%s, prompt_tokens≈%s): %s",
                        model,
                        prompt_tokens,
                        str(exc)[:400],
                    )
                    raise
                logger.warning(
                    "model stream stalled (chunk timeout); retrying call %d/%d "
                    "(model=%s, prompt_tokens≈%s)",
                    attempt,
                    self.max_retries,
                    model,
                    prompt_tokens,
                )




class RepeatedToolCallMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Stop models from blindly repeating the same failing tool call — or from
    degenerating into an endless text loop.

    ``create_agent`` hard-codes ``recursion_limit: 9_999``, so a model that
    re-emits one failing call (e.g. a ``find`` blocked by permissions) loops
    effectively forever. This middleware guards the trailing message history
    before every model call and, when a loop is detected:

    * warns the model to change approach, then
    * on the hard cap strips every tool so the model MUST reply with a
      text-only final answer (the same "last step" mechanism opencode uses).

    It detects four loop shapes, all purely from message history:

    1. CONSECUTIVE identical tool calls (name + canonicalized args).
    2. A trailing run of tool-call turns regardless of args (a model that
       varies its command each iteration is still looping).
    3. Consecutive assistant messages with identical text content.
    4. A single assistant message that has already degenerated into repeated
       text (the qwen3-on-vLLM failure mode: "讓我做X：" repeated ~40× in one
       reply, which the old tool-only guard could never catch).

    Only trailing runs count, so ordinary long tasks are unaffected. Mounted
    last (innermost) so its overrides are applied after PhaseToolGateMiddleware
    / SkillMiddleware / MemoryMiddleware.
    """

    def __init__(self, warn_after: int = 2, stop_after: int = 4, text_warn_after: int = 3, text_stop_after: int = 5, max_tool_turns: int = 12, max_total_tool_calls: int = 50) -> None:
        self.warn_after = max(1, int(warn_after))
        self.stop_after = max(self.warn_after + 1, int(stop_after))
        self.text_warn_after = max(1, int(text_warn_after))
        self.text_stop_after = max(self.text_warn_after + 1, int(text_stop_after))
        self.max_tool_turns = max(2, int(max_tool_turns))
        self.max_total_tool_calls = max(10, int(max_total_tool_calls))

    @staticmethod
    def _call_key(tool_call: Any) -> tuple[str, str]:
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
        try:
            canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            canonical = str(args)
        return str(name), canonical

    def _consecutive_repeats(self, messages: list[Any]) -> tuple[int, str, str]:
        """Return (count, name, last_result) for the trailing run of identical
        tool calls. ``count`` is how many identical calls are already in the
        history (0 = none)."""
        count = 0
        name = ""
        prev_key: tuple[str, str] | None = None
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                i -= 1
                continue
            if isinstance(msg, AIMessage):
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    break
                key = self._call_key(calls[-1])
                if prev_key is None:
                    prev_key = key
                    count = 1
                elif key == prev_key:
                    count += 1
                else:
                    break
            else:
                break
            i -= 1
        if prev_key is not None:
            name = prev_key[0]
        last_result = ""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage) and (getattr(msg, "name", "") or "") == name:
                last_result = str(getattr(msg, "content", ""))[:200]
                break
        return count, name, last_result

    def _consecutive_tool_turns(self, messages: list[Any]) -> int:
        """Trailing run of model turns that each emitted at least one tool call,
        regardless of whether the calls are identical (a varying-args loop)."""
        count = 0
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                i -= 1
                continue
            if isinstance(msg, AIMessage):
                if not (getattr(msg, "tool_calls", None) or []):
                    break
                count += 1
            else:
                break
            i -= 1
        return count

    def _text_repeats(self, messages: list[Any]) -> int:
        """How many times the latest text-only assistant reply has ALREADY
        appeared in the history. A model that answers the same text every turn
        (user messages interleaved) is looping just as surely as one repeating a
        tool call — this catches that shape."""
        target: str | None = None
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None) or []:
                    return 0
                target = str(getattr(msg, "content", None) or "").strip()
                break
            return 0  # a user message is latest → fresh question, N/A
        if not target:
            return 0
        count = 0
        for msg in messages:
            if isinstance(msg, AIMessage) and not (getattr(msg, "tool_calls", None) or []):
                if str(getattr(msg, "content", None) or "").strip() == target:
                    count += 1
        return count

    _TEXT_UNIT_SPLIT = re.compile(r"[\n。！？!?；;]+")

    @classmethod
    def _is_degenerate_text(cls, content: str) -> bool:
        """True when a single message repeats one unit several times — the
        qwen3 greedy-decoding collapse (e.g. '讓我搜索一下...' × 40)."""
        text = (content or "").strip()
        if len(text) < 40:
            return False
        units = [u.strip() for u in cls._TEXT_UNIT_SPLIT.split(text) if len(u.strip()) >= 8]
        if len(units) < 5:
            return False
        from collections import Counter
        top = Counter(units).most_common(1)[0][1]
        return top >= 5

    def _last_message_degenerate(self, messages: list[Any]) -> bool:
        """True when the most recent non-tool assistant message is already
        degenerate repetition AND nothing newer than it demands a fresh answer
        (a new user message resets the condition so a normal follow-up question
        is never hijacked)."""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None) or []:
                    return False
                return self._is_degenerate_text(getattr(msg, "content", None) or "")
            return False
        return False

    def _total_tool_calls(self, messages: list[Any]) -> int:
        return sum(len(getattr(m, "tool_calls", None) or []) for m in messages if isinstance(m, AIMessage))

    def _overrides(self, request: Any) -> dict[str, Any]:
        messages = list(request.messages or [])
        tool_count, tool_name, last_result = self._consecutive_repeats(messages)
        tool_turns = self._consecutive_tool_turns(messages)
        text_count = self._text_repeats(messages)
        degenerate = self._last_message_degenerate(messages)
        total_tools = self._total_tool_calls(messages)

        hard_stop = degenerate
        hard_reasons: list[str] = []
        if degenerate:
            hard_reasons.append("your previous reply degenerated into endless repetition")
        if tool_count >= self.stop_after:
            hard_stop = True
            hard_reasons.append(f"you already ran '{tool_name}' {tool_count} times")
        if text_count >= self.text_stop_after:
            hard_stop = True
            hard_reasons.append(f"you have already given the identical reply {text_count} times")
        if tool_turns >= self.max_tool_turns:
            hard_stop = True
            hard_reasons.append(f"you have made {tool_turns} consecutive tool calls without finishing")
        if total_tools >= self.max_total_tool_calls:
            hard_stop = True
            hard_reasons.append(f"this turn already used {total_tools} tool calls")

        if hard_stop:
            msg = (
                "STOP. " + "；".join(hard_reasons) + ". "
                "Do NOT make any more tool calls and do NOT repeat yourself. "
                "Provide your final answer as plain text now and explain what went wrong."
            )
            return {"tools": [], "messages": [*messages, HumanMessage(content=msg)]}

        warn_reasons: list[str] = []
        if tool_count >= self.warn_after:
            last_line = f" Last result: {last_result}" if last_result else ""
            warn_reasons.append(
                f"you already ran '{tool_name}' {tool_count} times in a row and it has "
                f"not succeeded.{last_line}"
            )
        if text_count >= self.text_warn_after:
            warn_reasons.append(f"you have already given the identical reply {text_count} times")
        if tool_turns >= max(2, self.max_tool_turns // 2):
            warn_reasons.append(f"you have made {tool_turns} consecutive tool calls; finish soon")
        if total_tools >= self.max_total_tool_calls // 2:
            warn_reasons.append(f"this turn already used {total_tools} tool calls; wrap up soon")
        if not warn_reasons:
            return {}

        msg = (
            "WARNING: " + "；".join(warn_reasons) + ". "
            "Do NOT repeat the same action or the same text. Change approach "
            "(different path, different tool, narrower scope) or answer directly."
        )
        return {"messages": [*messages, HumanMessage(content=msg)]}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return handler(request)
        return handler(request.override(**overrides))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return await handler(request)
        return await handler(request.override(**overrides))



class ContextOverflowError(RuntimeError):
    """The final request still exceeds the effective window after every staged
    reduction. Carries the measured size / limit so callers can surface a
    precise, friendly error instead of the provider's raw 400."""

    def __init__(self, message: str, *, measured_tokens: int = 0, limit_tokens: int = 0, steps: list[str] | None = None):
        super().__init__(message)
        self.measured_tokens = measured_tokens
        self.limit_tokens = limit_tokens
        self.steps = list(steps or [])


class ContextGuardMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Last line of defense before a request hits the provider.

    Every other context mechanism (trim, compaction, tool-result clearing)
    measures ``state["messages"]`` with an estimate; the provider measures the
    REAL request — system prompt + tool schemas + messages + template overhead,
    with its own tokenizer, and reserves ``max_output_tokens`` from the window.
    When estimate and reality disagree (dense code, base64 blobs, CJK, vision
    blocks) the state-side mechanisms can stay comfortably "under budget"
    while the real request sails past ``window − max_output`` — exactly the
    incident this guard exists to kill.

    Mounted INNERMOST (last in the middleware chain) so it measures the request
    after every other middleware has applied its system-prompt / tool / message
    overrides. When the calibrated measurement exceeds the effective input
    limit it applies staged reductions — cheapest and least-destructive first,
    all on request-local copies (checkpointed state and the UI transcript are
    untouched, same contract as ``ContextEditingMiddleware``):

      S1 externalize binary blobs (base64/data-URL runs are corrupted-on-
         arrival text: pure token waste) and degrade stale image blocks;
      S2 clear old tool results (keep=3, then keep=1);
      S3 truncate the oldest oversized tool results;
      S4 drop optional tool schemas (MCP tools);
      S5 emergency-drop the oldest messages (AI/Tool pairing preserved).

    If the request STILL does not fit, raises :class:`ContextOverflowError` so
    the runtime can emit a friendly terminal event + one-click compacted retry
    instead of leaking the provider's 400 mid-turn.

    The guard also publishes the calibrated measurement + raw estimate for the
    closed-loop calibration (the streaming loop pairs ``last_raw_estimate``
    with the provider-reported ``usage_metadata.input_tokens``).
    """

    # Image blocks kept intact during S1 degradation (the most recent ones are
    # the only ones still relevant to the model's current decision).
    KEEP_RECENT_IMAGES = 2
    # S3 truncation target per old tool result (opencode TOOL_OUTPUT_MAX_CHARS).
    TOOL_RESULT_KEEP_CHARS = 2_000
    # Stop reducing once comfortably under the limit (calibrated headroom).
    TARGET_RATIO = 0.95

    def __init__(
        self,
        *,
        window_tokens: int,
        max_output_tokens: int = 0,
        calibration_store: Any | None = None,
        calibration_key: str = "",
        mcp_tool_names_provider: Callable[[], set[str]] | None = None,
        window_source: str = "default",
        window_warning: str | None = None,
    ) -> None:
        from ..context import effective_input_limit

        self.window_tokens = int(window_tokens or 0)
        self.max_output_tokens = max(0, int(max_output_tokens or 0))
        self.limit_tokens = effective_input_limit(self.window_tokens or 128_000, self.max_output_tokens)
        self.calibration_store = calibration_store
        self.calibration_key = calibration_key or ""
        self.mcp_tool_names_provider = mcp_tool_names_provider
        self.window_source = window_source
        self.window_warning = window_warning
        # Calibration pairing: the streaming loop reads these right after each
        # successful model call and folds actual/estimate into the store.
        self.last_raw_estimate = 0
        self.last_measured = 0
        self.last_steps: list[str] = []

    # -- measurement --------------------------------------------------------

    def _factor(self) -> float:
        if self.calibration_store is not None and self.calibration_key:
            try:
                return float(self.calibration_store.get(self.calibration_key))
            except Exception:  # noqa: BLE001 - fall back to uncalibrated
                return 1.0
        return 1.0

    def _measure(self, request: Any) -> tuple[int, int, float]:
        """Return ``(raw_estimate, calibratedMeasurement, factor)`` for the FULL
        final request (messages + system + tools + per-message overhead)."""
        from ..context import (
            PER_MESSAGE_OVERHEAD_TOKENS,
            estimate_text_tokens,
            messages_tokens,
            tool_schema_tokens,
        )

        system_text = ""
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            content = getattr(system_message, "content", "")
            if isinstance(content, str):
                system_text = content
            elif isinstance(content, list):
                system_text = "\n".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
        messages = list(request.messages or [])
        raw = messages_tokens(messages)
        raw += tool_schema_tokens(getattr(request, "tools", None) or [])
        if system_text:
            raw += estimate_text_tokens(system_text)
        raw += PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
        factor = self._factor()
        return raw, int(round(raw * factor)), factor

    # -- staged reductions (all request-local) --------------------------------

    @staticmethod
    def _with_content(msg: Any, content: Any) -> Any:
        """Copy of ``msg`` with replaced content (keeps ids/pairing intact)."""
        try:
            return msg.model_copy(update={"content": content})
        except Exception:  # noqa: BLE001 - older pydantic / message classes
            try:
                return msg.copy(update={"content": content})
            except Exception:  # noqa: BLE001
                return msg

    def _strip_blobs_and_degrade_images(
        self,
        messages: list[Any],
        *,
        keep_images: int | None = None,
        scrub: bool = True,
    ) -> tuple[list[Any], int]:
        """S1: scrub base64/data-URL text runs and degrade stale image blocks.

        A truncated base64 blob is a CORRUPTED binary — the model can never use
        it, it only burns tokens (~36k per 50k chars). Old images are replaced
        with a text placeholder; the most recent ``keep_images`` image blocks
        survive (they are the ones the model is currently reasoning about).
        Returns ``(new_messages, changes)``.
        """
        from ..context import contains_binary_blob, scrub_text

        keep_budget = self.KEEP_RECENT_IMAGES if keep_images is None else max(0, int(keep_images))

        # Pass 1 (backwards): decide which image blocks are recent enough to keep.
        keep_marks: list[set[int]] = []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            marks: set[int] = set()
            if isinstance(content, list):
                for idx in range(len(content) - 1, -1, -1):
                    part = content[idx]
                    if isinstance(part, dict) and part.get("type") in ("image", "image_url", "audio", "video", "file"):
                        if keep_budget > 0:
                            marks.add(idx)
                            keep_budget -= 1
            keep_marks.append(marks)
        keep_marks.reverse()

        changed = 0
        new_messages: list[Any] = []
        for msg, marks in zip(messages, keep_marks):
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                new_content: list[Any] = []
                msg_changed = False
                for idx, part in enumerate(content):
                    if isinstance(part, dict):
                        ptype = part.get("type")
                        if ptype in ("image", "image_url", "audio", "video", "file"):
                            if idx in marks:
                                new_content.append(part)
                            else:
                                new_content.append(
                                    {"type": "text", "text": f"[{ptype} removed from context to save space]"}
                                )
                                msg_changed = True
                            continue
                        if ptype == "text":
                            if scrub:
                                text = part.get("text") or ""
                                if contains_binary_blob(text):
                                    scrubbed, n = scrub_text(text)
                                    if n:
                                        part = {**part, "text": scrubbed}
                                        msg_changed = True
                    new_content.append(part)
                if msg_changed:
                    changed += 1
                    msg = self._with_content(msg, new_content)
            elif isinstance(content, str) and scrub and contains_binary_blob(content):
                scrubbed, n = scrub_text(content)
                if n:
                    changed += 1
                    msg = self._with_content(msg, scrubbed)
            new_messages.append(msg)
        return new_messages, changed

    def _clear_tool_results(self, messages: list[Any], keep: int) -> list[Any]:
        """S2: forced ClearToolUsesEdit pass (trigger=0 ⇒ always applies)."""
        try:
            from langchain.agents.middleware import ClearToolUsesEdit

            edit = ClearToolUsesEdit(
                trigger=0,
                keep=keep,
                placeholder="[cleared]",
                exclude_tools=("write_todos", "memory", "memory_read", "ask_user"),
            )
            working = [m.model_copy() if hasattr(m, "model_copy") else m for m in messages]
            edit.apply(working, count_tokens=self._cjk_counter)
            return working
        except Exception:  # noqa: BLE001 - reduction step must never break a turn
            logger.warning("guard: tool-result clearing failed", exc_info=True)
            return messages

    @staticmethod
    def _cjk_counter(messages: Iterable[Any]) -> int:
        return sum(_msg_tokens(m) for m in messages)

    @staticmethod
    def _image_count(messages: Iterable[Any]) -> int:
        """Total image/audio/video/file blocks across the request's messages."""
        from ..context import message_media_count

        return sum(message_media_count(m) for m in messages)

    def _truncate_old_tool_results(self, messages: list[Any]) -> tuple[list[Any], int]:
        """S3: truncate oversized tool results, oldest first."""
        changed = 0
        new_messages = list(messages)
        for idx, msg in enumerate(new_messages):
            if not isinstance(msg, ToolMessage):
                continue
            content = getattr(msg, "content", None)
            if not isinstance(content, str) or len(content) <= self.TOOL_RESULT_KEEP_CHARS:
                continue
            new_messages[idx] = self._with_content(
                msg, content[: self.TOOL_RESULT_KEEP_CHARS] + "\n…[truncated by context guard]"
            )
            changed += 1
        return new_messages, changed

    def _drop_mcp_tools(self, tools: list[Any] | None) -> tuple[list[Any] | None, int]:
        """S4: drop optional MCP tool schemas (they ride on EVERY request).

        Handles both ``BaseTool`` instances and raw schema dicts (ModelRequest
        accepts either, and the phase gate passes dicts through untouched).
        """
        if not tools or self.mcp_tool_names_provider is None:
            return tools, 0
        try:
            mcp_names = self.mcp_tool_names_provider()
        except Exception:  # noqa: BLE001 - a broken provider never gates the guard
            return tools, 0
        if not mcp_names:
            return tools, 0

        def _tool_name(tool: Any) -> str:
            name = getattr(tool, "name", "")
            if name:
                return str(name)
            if isinstance(tool, dict):
                fn = tool.get("function")
                if isinstance(fn, dict):
                    return str(fn.get("name") or "")
                return str(tool.get("name") or "")
            return ""

        kept = [t for t in tools if _tool_name(t) not in mcp_names]
        return kept, len(tools) - len(kept)

    def _emergency_drop_oldest(self, messages: list[Any], limit: int, factor: float) -> tuple[list[Any], int]:
        """S5: drop oldest messages until under limit (AI/Tool pairing safe)."""
        from ..context import messages_tokens

        working = list(messages)
        dropped = 0
        while len(working) > 4:
            measured = int(round(messages_tokens(working) * factor))
            if measured <= limit * self.TARGET_RATIO:
                break
            working.pop(0)
            dropped += 1
            while working and isinstance(working[0], ToolMessage):
                working.pop(0)
                dropped += 1
        return working, dropped

    # -- guard core -----------------------------------------------------------

    def _guard(self, request: Any) -> Any:
        """Measure the final request; apply staged reductions when over limit.

        Returns the (possibly overridden) request, or raises
        :class:`ContextOverflowError` when nothing fits.
        """
        raw, measured, factor = self._measure(request)
        self.last_raw_estimate = raw
        self.last_measured = measured
        self.last_steps = []
        # Surface the FULL request size (messages + system prompt + tool schemas
        # + per-message overhead, calibrated) as the `context_usage` telemetry the
        # UI topbar renders — the single source of truth for "how full is the
        # context". The compaction middleware's older message-only estimate
        # undercounted the fixed system/tool overhead (the B-series blind spot),
        # so the guard — which already measures the real request — owns this
        # event now.
        self._emit_context_usage_event(request, raw, measured, factor, measured > self.limit_tokens)
        if measured <= self.limit_tokens:
            # Even when comfortably under the token budget, enforce the per-prompt
            # IMAGE-COUNT ceiling (e.g. vLLM --limit-mm-per-prompt.image=5): 6+
            # in-turn screenshots fit easily inside the window but still 400 the
            # provider. Cheap pre-check; the common path stays untouched.
            messages = list(request.messages or [])
            if self._image_count(messages) > MAX_IMAGES_PER_PROMPT:
                messages, _n = self._strip_blobs_and_degrade_images(
                    messages, keep_images=MAX_IMAGES_PER_PROMPT, scrub=False
                )
                # Re-sync the raw estimate so the closed-loop calibration pairs
                # the ACTUAL sent request (degraded images) with its usage.
                raw_capped, _measured, _ = self._measure(request.override(messages=messages))
                self.last_raw_estimate = raw_capped
                return self._finalize(request, {"messages": messages}, measured)
            return request

        logger.warning(
            "context guard: request %s tokens (calibrated, factor=%.2f) exceeds effective limit %s; reducing",
            measured, factor, self.limit_tokens,
        )
        self._emit_telemetry(request, measured, "reducing")

        overrides: dict[str, Any] = {}
        messages = list(request.messages or [])
        tools = getattr(request, "tools", None)

        # S1 — binary blobs + stale images (cheapest, zero information loss:
        # truncated base64 was already useless).
        messages, n1 = self._strip_blobs_and_degrade_images(messages)
        if n1:
            self.last_steps.append(f"blobs/images:{n1}")
            overrides["messages"] = messages

        def _current() -> tuple[int, int]:
            probe = request.override(**({"messages": messages} | ({"tools": tools} if tools is not None else {})))
            raw_now, measured_now, _ = self._measure(probe)
            self.last_raw_estimate = raw_now
            return raw_now, measured_now

        _, measured = _current()
        if measured <= self.limit_tokens * self.TARGET_RATIO:
            return self._finalize(request, overrides, measured)

        # S2 — clear stale tool results (keep=3, then keep=1).
        for keep in (3, 1):
            messages = self._clear_tool_results(messages, keep)
            overrides["messages"] = messages
            self.last_steps.append(f"clear_tools_keep{keep}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S3 — truncate the oldest oversized tool results.
        messages, n3 = self._truncate_old_tool_results(messages)
        if n3:
            overrides["messages"] = messages
            self.last_steps.append(f"truncate_tools:{n3}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S4 — drop optional MCP tool schemas.
        tools, n4 = self._drop_mcp_tools(tools)
        if n4:
            overrides["tools"] = tools
            self.last_steps.append(f"drop_mcp_tools:{n4}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S5 — emergency drop of the oldest messages.
        messages, n5 = self._emergency_drop_oldest(messages, self.limit_tokens, factor)
        if n5:
            overrides["messages"] = messages
            self.last_steps.append(f"drop_oldest:{n5}")
            _, measured = _current()
            if measured <= self.limit_tokens:
                return self._finalize(request, overrides, measured)

        # S6 — nothing fits: raise so the runtime emits a friendly terminal
        # event + one-click compacted retry instead of the provider's raw 400.
        self._emit_telemetry(request, measured, "overflow")
        raise ContextOverflowError(
            f"request still {measured} tokens after staged reductions (limit {self.limit_tokens})",
            measured_tokens=measured,
            limit_tokens=self.limit_tokens,
            steps=self.last_steps,
        )

    def _finalize(self, request: Any, overrides: dict[str, Any], measured: int) -> Any:
        if not overrides:
            return request
        self._emit_telemetry(request, measured, "reduced")
        return request.override(**overrides)

    def _emit_telemetry(self, request: Any, measured: int, status: str) -> None:
        try:
            runtime = getattr(request, "runtime", None)
            writer = getattr(runtime, "stream_writer", None)
            if writer is None:
                return
            writer(
                {
                    "type": "context_guard",
                    "status": status,
                    "measured_tokens": measured,
                    "limit_tokens": self.limit_tokens,
                    "calibration_factor": round(self._factor(), 3),
                    "steps": list(self.last_steps),
                }
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            logger.debug("context_guard telemetry skipped", exc_info=True)

    def _emit_context_usage_event(
        self, request: Any, raw: int, measured: int, factor: float, over: bool
    ) -> None:
        """Emit the authoritative `context_usage` event for the UI topbar.

        Uses the FULL calibrated request measurement (``raw``/``measured`` from
        :meth:`_measure`, which counts system prompt + tool schemas + messages +
        per-message overhead) so the indicator reflects what is actually sent to
        the model — not just the message history.
        """
        try:
            runtime = getattr(request, "runtime", None)
            writer = getattr(runtime, "stream_writer", None)
            if writer is None:
                return
            messages = list(getattr(request, "messages", []) or [])
            used_chars = sum(_msg_chars(m) for m in messages)
            writer(
                {
                    "type": "context_usage",
                    "used_chars": used_chars,
                    "budget_chars": 0,
                    "used_tokens": raw,
                    "used_tokens_calibrated": measured,
                    "calibration_factor": round(float(factor), 3),
                    "budget_tokens": self.limit_tokens,
                    "active_budget_tokens": self.limit_tokens,
                    "window_tokens": self.window_tokens,
                    "effective_window_tokens": self.limit_tokens,
                    "max_output_tokens": self.max_output_tokens,
                    "compressed": bool(over),
                    "compacted": False,
                    "compact_count": 0,
                    "window_source": self.window_source,
                    "window_warning": self.window_warning,
                }
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            logger.debug("context_usage telemetry skipped", exc_info=True)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._guard(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._guard(request))
