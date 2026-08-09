import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Literal, TypedDict
from typing_extensions import NotRequired

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, Runtime

from .config import BackendSettings
from .mcp import McpManager
from .providers import ProviderEntry, ProviderManager
from .traces import AGENT_TRACE_FILENAME, AgentTraceStore
from .changes import ChangeStore
from .sessions import SessionStore
from .workspace import ALLOWED_COMMANDS, COMMAND_APPROVAL_FILENAME, TOOL_AUDIT_FILENAME, CommandApprovalStore, Workspace

AgentMode = Literal["single"]
Language = Literal["zh", "en"]
WorkMode = Literal["plan", "build"]
Phase = Literal["discuss", "execute"]
Autonomy = Literal["supervised", "guarded", "autonomous"]
# Backward-compatible alias: legacy "default"/"full" access maps to
# guarded/autonomous autonomy (see autonomy_from_access).
AccessMode = Literal["default", "full"]

SYSTEM_PROMPT = (
    "You are Coworker, a local coding assistant. "
    "Use workspace tools only when they are needed and keep answers concise."
)
TITLE_SYSTEM_PROMPT = (
    "You are a thread title generator. Output ONLY the title string. Nothing else. No code fences, no quotes, no explanation."
    "Rules:"
    " - The input is the first exchange of a conversation: the user's message and the AI's reply."
    " - Summarize the exchange into a short title that captures the main topic, question, or task."
    " - Use the same language as the user's first message."
    " - Title must be a complete meaningful phrase."
    " - Never include tool names like read tool, bash tool, edit tool."
    " - Focus on the main topic, question, or task."
    " - Keep exact: technical terms, numbers, filenames, HTTP codes."
    " - Remove generic words: the, this, my, a, an."
    " - Never respond to questions—just generate a title for the conversation."
    " - For short or conversational messages (hello, lol, what's up, hey): generate a brief friendly title like 'Quick introduction', 'Brief check-in', 'Light chat', etc."
    " - The title must be a single line, 3-40 characters, no explanations."
)
MAX_ATTACHMENT_CHARS = 120_000
MAX_REFERENCE_SESSION_CHARS = 60_000

PLAN_MARKER = "[CW-PLAN]"


class CoworkerAgentState(AgentState[Any]):
    work_mode: NotRequired[str]
    language: NotRequired[str]
    phase: NotRequired[str]
    autonomy: NotRequired[str]
    goal_mode: NotRequired[bool]
    goal_text: NotRequired[str]
    goal_done: NotRequired[bool]
    goal_nudge: NotRequired[str]
    todos: NotRequired[list[Any]]


@dataclass(frozen=True)
class AgentReply:
    content: str
    mode: AgentMode
    provider: str
    parts: list[dict[str, Any]] | None = None


class SearchFilesArgs(BaseModel):
    query: str = Field(min_length=1, description="Text to search for in UTF-8 workspace files.")
    path: str = Field(default="", description="Optional workspace-relative file or directory to search.")
    max_results: int = Field(default=80, ge=1, le=80, description="Maximum number of matches to return.")


class ReadFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")


class WriteFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to write.")
    content: str = Field(description="Full UTF-8 file content to write.")


class ReplaceInFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence when true; otherwise exactly one occurrence is required.")


class TextEditArgs(BaseModel):
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence of old_text for this edit.")


class ApplyTextEditsArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")
    edits: list[TextEditArgs] = Field(description="Ordered exact text edits. All edits must validate before the file is written.")


class SubmitPlanArgs(BaseModel):
    plan_text: str = Field(description="Your concise implementation plan to present to the user for approval before making changes.")


class RunCommandArgs(BaseModel):
    command: list[str] = Field(description="Command argv array, for example ['npm', 'run', 'build']. Shell strings are not accepted.")
    cwd: str = Field(default="", description="Optional workspace-relative working directory.")
    timeout_seconds: int = Field(default=20, ge=1, le=60, description="Command timeout in seconds.")


class AskUserOption(BaseModel):
    label: str = Field(description="Display text (1-5 words, concise).")
    description: str = Field(default="", description="Optional explanation of the choice.")


class AskUserArgs(BaseModel):
    question: str = Field(description="Complete question for the user.")
    options: list[AskUserOption] = Field(description="Available choices, each with a label and optional description.")
    multiple: bool = Field(default=False, description="Allow selecting multiple choices.")
    header: str = Field(default="", description="Very short label (max 30 chars) for the prompt.")


class ReadSessionArgs(BaseModel):
    session_id: str = Field(description="The id of a session the user explicitly referenced in this conversation (pasted session id).")
    max_messages: int = Field(default=0, ge=0, le=50, description="Optional cap on how many recent messages to read (0 = no cap).")


class AgentRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    def _next_turn_index(self, session_id: str) -> int:
        if getattr(self, "change_store", None) is None:
            return 1
        try:
            return self.change_store.next_turn_index(session_id)
        except Exception:
            return 1

    @abstractmethod
    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        raise NotImplementedError


class AgentStreamRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    def _next_turn_index(self, session_id: str) -> int:
        if self.change_store is None:
            return 1
        try:
            return self.change_store.next_turn_index(session_id)
        except Exception:
            return 1

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError


def language_name(language: Language) -> str:
    return "Chinese" if language == "zh" else "English"


def normalize_work_mode(work_mode: str | None) -> WorkMode:
    return "plan" if work_mode == "plan" else "build"


def normalize_access_mode(access_mode: str | None) -> AccessMode:
    return "full" if access_mode == "full" else "default"


def normalize_autonomy(autonomy: str | None) -> Autonomy:
    if autonomy in ("supervised", "guarded", "autonomous"):
        return autonomy
    return "guarded"


def autonomy_from_access(access_mode: str | None) -> Autonomy:
    """Map the legacy access_mode switch onto the autonomy axis.

    ``default`` -> ``guarded`` (Codex ``on-request``) and ``full`` ->
    ``autonomous`` (Codex ``never``). Kept so older API payloads keep working.
    """
    return "autonomous" if normalize_access_mode(access_mode) == "full" else "guarded"


def normalize_phase(phase: str | None, work_mode: str | None = None) -> Phase:
    if phase in ("discuss", "execute"):
        return phase
    # A fresh task in plan mode starts by discussing; build mode executes.
    return "discuss" if normalize_work_mode(work_mode) == "plan" else "execute"


def normalize_language(language: Any) -> Language:
    return "en" if language == "en" else "zh"


def phase_system_prompt(language: Language, phase: Phase, autonomy: Autonomy) -> str:
    """Phase/autonomy-aware system instruction.

    The active phase decides which tools the model sees (via
    ``PhaseToolGateMiddleware``); this prompt only sets the behavioural
    contract for that phase.
    """
    lang_line = f"Reply in {language_name(language)}."
    if phase == "discuss":
        return (
            f"{lang_line}\n"
            "You are planning. Use the read-only tools to research the workspace, call ask_user "
            "when you need to clarify the user's intent, and finish by calling submit_plan with a "
            "concise implementation plan. Do NOT modify files or run commands until the user "
            "approves the plan."
        )
    if autonomy == "autonomous":
        return (
            f"{lang_line}\n"
            "You are executing with full autonomy. You may read, edit files and run workspace "
            "commands. Do not ask the user anything — make reasonable decisions and complete the "
            "task to the best of your ability."
        )
    return (
        f"{lang_line}\n"
        "You are executing. You may read, edit files and run workspace commands. Only call "
        "ask_user when you are genuinely blocked and need a decision to continue; otherwise make "
        "reasonable assumptions and proceed autonomously."
    )


def agent_run_config(
    *,
    session_id: str,
    provider: str,
    model: str,
    language: Language,
    work_mode: WorkMode,
    autonomy: Autonomy,
    streaming: bool,
) -> dict[str, Any]:
    return {
        "run_name": "coworker_agent" + ("_stream" if streaming else ""),
        "tags": [
            "coworker",
            "agent",
            f"work:{work_mode}",
            f"autonomy:{autonomy}",
            "streaming" if streaming else "non-streaming",
        ],
        "metadata": {
            "coworker.session_id": session_id,
            "coworker.provider": provider,
            "coworker.model": model,
            "coworker.language": language,
            "coworker.work_mode": work_mode,
            "coworker.autonomy": autonomy,
            "coworker.streaming": streaming,
        },
        "configurable": {
            "thread_id": session_id,
        },
    }


_ASYNC_SAVER: "Any" = None
_ASYNC_SAVER_PATH: "Any" = None


def _open_checkpointer(checkpoint_path: Any):
    """Return a process-wide AsyncSqliteSaver connection for the checkpoint.

    LangGraph's ``AsyncSqliteSaver.from_conn_string`` opens a fresh sqlite
    connection per access. Concurrent connections on the same checkpoint file
    contend on the SQLite write lock and intermittently raise ``database is
    locked`` (even with a busy timeout). Reusing one long-lived connection for
    the whole process serializes all checkpoint reads/writes and eliminates the
    lock contention. The connection lives for the process lifetime.
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _open():
        global _ASYNC_SAVER, _ASYNC_SAVER_PATH
        if _ASYNC_SAVER is None or _ASYNC_SAVER_PATH != str(checkpoint_path):
            conn = await aiosqlite.connect(str(checkpoint_path), timeout=30.0)
            _ASYNC_SAVER = AsyncSqliteSaver(conn)
            _ASYNC_SAVER_PATH = str(checkpoint_path)
        yield _ASYNC_SAVER

    return _open()


def build_workspace_tools(
    workspace: Workspace,
    audit_context: dict[str, Any] | None = None,
    approval_store: CommandApprovalStore | None = None,
    change_store: ChangeStore | None = None,
    turn_index: int = 1,
    session_store: SessionStore | None = None,
    referenced_sessions: set[str] | None = None,
) -> list[Any]:
    from langchain_core.tools import tool

    def _error_result(error: Exception, operation: str) -> str:
        details = {"error": str(error)[:500], "operation": operation}
        return json.dumps(details, ensure_ascii=False)

    @tool(args_schema=SearchFilesArgs)
    def search_files(query: str, path: str = "", max_results: int = 80) -> str:
        """Search UTF-8 workspace text files."""
        try:
            result = workspace.search_text(query, path, max_results)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "search_files")

    @tool(args_schema=ReadFileArgs)
    def read_file(file_path: str) -> str:
        """Read a UTF-8 text file from the configured workspace."""
        try:
            return workspace.read_text(file_path)
        except Exception as exc:
            return _error_result(exc, "read_file")

    @tool(args_schema=WriteFileArgs)
    def write_file(file_path: str, content: str) -> str:
        """Write a full UTF-8 text file."""
        try:
            workspace.write_text(file_path, content, audit_context, change_store, turn_index)
            return f"Wrote {file_path}"
        except Exception as exc:
            return _error_result(exc, "write_file")

    @tool(args_schema=ReplaceInFileArgs)
    def replace_in_file(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        """Replace exact text in a UTF-8 workspace file."""
        try:
            result = workspace.replace_text(file_path, old_text, new_text, replace_all, audit_context, change_store, turn_index)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "replace_in_file")

    @tool(args_schema=ApplyTextEditsArgs)
    def apply_text_edits(file_path: str, edits: list[TextEditArgs]) -> str:
        """Apply multiple exact text edits to one UTF-8 workspace file atomically."""
        try:
            result = workspace.apply_text_edits(
                file_path,
                [edit.model_dump() if isinstance(edit, TextEditArgs) else edit for edit in edits],
                audit_context,
                change_store,
                turn_index,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "apply_text_edits")

    @tool(args_schema=RunCommandArgs)
    def run_command(command: list[str], cwd: str = "", timeout_seconds: int = 20) -> str:
        """Run an allowlisted command in the workspace after runtime policy approval."""
        try:
            result = workspace.run_command(command, cwd, timeout_seconds, audit_context, approval_store, approval_store is not None)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "run_command")

    @tool(args_schema=AskUserArgs)
    def ask_user(question: str, options: list[dict[str, str]], multiple: bool = False, header: str = "") -> str:
        """Ask the user a question with selectable options when you need a decision or clarification."""
        normalized_options = [
            item.model_dump() if isinstance(item, AskUserOption) else item
            for item in options
        ]
        result = {
            "question": question,
            "options": normalized_options,
            "multiple": multiple,
            "header": header,
            "status": "awaiting_user",
        }
        return json.dumps(result, ensure_ascii=False)

    @tool(args_schema=SubmitPlanArgs)
    def submit_plan(plan_text: str) -> str:
        """Present your implementation plan for approval before making any changes. Call this AFTER researching the workspace and BEFORE writing or editing files or running commands."""
        return f"Plan:\n{plan_text}"

    allowed_references = referenced_sessions or set()

    @tool(args_schema=ReadSessionArgs)
    def read_session(session_id: str, max_messages: int = 0) -> str:
        """Read a conversation session that the user explicitly referenced in this chat (they pasted its session id). Use it to recall prior decisions, code, or context from another session. Only sessions the user pasted into this conversation can be read; anything else is rejected."""
        if session_store is None:
            return json.dumps({"error": "unavailable", "message": "Session reading is not available in this runtime."}, ensure_ascii=False)
        if session_id not in allowed_references:
            return json.dumps(
                {"error": "not_authorized", "message": f"Session {session_id} was not referenced by the user in this conversation. Ask the user to paste that session's id into the chat."},
                ensure_ascii=False,
            )
        try:
            session = session_store.load(session_id)
        except Exception as exc:
            return _error_result(exc, "read_session")
        if session is None:
            return json.dumps({"error": "not_found", "message": f"Session {session_id} does not exist."}, ensure_ascii=False)
        messages: list[dict[str, str]] = []
        for message in session.messages:
            if message.role not in {"user", "assistant"} or not message.content:
                continue
            messages.append({"role": message.role, "content": message.content})
        if max_messages > 0:
            messages = messages[-max_messages:]
        capped: list[dict[str, str]] = []
        total = 0
        truncated = False
        for message in messages:
            total += len(message["content"])
            if total > MAX_REFERENCE_SESSION_CHARS:
                truncated = True
                break
            capped.append(message)
        return json.dumps(
            {
                "session_id": session.id,
                "title": session.title,
                "message_count": len(session.messages),
                "messages": capped,
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    tools = [search_files, read_file, ask_user, submit_plan, replace_in_file, apply_text_edits, write_file, run_command]
    if session_store is not None:
        tools.append(read_session)
    return tools


_CHANGE_TOOL_NAMES = {"write_file", "replace_in_file", "apply_text_edits"}

# Tool sets for phase-driven tool gating (see PhaseToolGateMiddleware).
_READ_ONLY_TOOLS = {"search_files", "read_file", "read_session"}
_PLAN_TOOLS = {"ask_user", "submit_plan"}
_EXEC_TOOLS = {"run_command"}


def _path_from_tool_input(tool_name: str, input_raw: str) -> str:
    if not input_raw:
        return ""
    try:
        args = json.loads(input_raw)
    except Exception:
        return ""
    if not isinstance(args, dict):
        return ""
    return next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")


def _change_to_public(change: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {
        "path": change.get("file_path", ""),
        "kind": change.get("kind", "edit"),
        "added": int(change.get("added") or 0),
        "removed": int(change.get("removed") or 0),
        "truncated": bool(change.get("truncated")),
        "too_large": bool(change.get("too_large")),
    }
    if change.get("hunks"):
        public["hunks"] = change["hunks"]
    return public


def _command_digest_from_tool_call(tool_call: Any) -> str:
    args = tool_call.get("args") if isinstance(tool_call, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    cwd = str(args.get("cwd") or "") if isinstance(args, dict) else ""
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return Workspace.command_digest(command, cwd)
    return ""


def _command_is_allowed(tool_call: Any) -> bool:
    """Whether this tool call's executable can pass the workspace allowlist.

    Used to avoid prompting the user to approve a command that the tool layer
    will refuse regardless of the decision.
    """
    args = tool_call.get("args") if isinstance(tool_call, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    if not isinstance(command, list) or not command:
        return True
    executable = command[0]
    if not isinstance(executable, str) or not executable:
        return True
    return Path(executable).name in ALLOWED_COMMANDS


def command_approval_middleware(
    approval_store: CommandApprovalStore | None = None,
) -> list[Any]:
    """Always-mounted HITL middleware; approval decisions live in ``when``
    predicates that read phase/autonomy from agent state.

    * ``run_command`` / write tools: interrupt only in ``execute`` phase with
      ``supervised`` autonomy. ``guarded`` runs allowlisted commands inside the
      workspace automatically (Codex ``on-request``); ``autonomous`` never asks.
    * ``ask_user``: always interrupts — the tool is only reachable when the
      phase gate exposes it, so this is decoupled from the permission switch
      (fixes D3: full access no longer kills the question capability).
    """
    from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware

    def needs_command_approval(req: Any) -> bool:
        state = req.state
        if normalize_phase(state.get("phase"), state.get("work_mode")) != "execute":
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy != "supervised":
            # guarded runs allowlisted commands freely; autonomous never asks.
            return False
        # Only ask for commands that can actually run (allowlist members);
        # out-of-allowlist commands are refused by the tool layer regardless.
        if not _command_is_allowed(req.tool_call):
            return False
        if approval_store is not None:
            digest = _command_digest_from_tool_call(req.tool_call)
            if digest and approval_store.is_always_allowed(digest):
                return False
        return True

    def needs_write_approval(req: Any) -> bool:
        state = req.state
        if normalize_phase(state.get("phase"), state.get("work_mode")) != "execute":
            return False
        return normalize_autonomy(state.get("autonomy")) == "supervised"

    def needs_ask_user(req: Any) -> bool:
        # In fully-autonomous execution the agent must not interrupt the user.
        # The tool is still registered so a stray call resolves cleanly, but it
        # must not raise an HITL interrupt here.
        state = req.state
        if normalize_phase(state.get("phase"), state.get("work_mode")) == "execute" and normalize_autonomy(state.get("autonomy")) == "autonomous":
            return False
        return True

    write_configs: dict[str, Any] = {}
    for tool_name in ("write_file", "replace_in_file", "apply_text_edits"):
        write_configs[tool_name] = {
            "allowed_decisions": ["approve", "reject"],
            "when": needs_write_approval,
        }

    return [
        HumanInTheLoopMiddleware(
            interrupt_on={
                "run_command": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": "Coworker needs approval before running this workspace command.",
                    "when": needs_command_approval,
                },
                **write_configs,
                "ask_user": {
                    "allowed_decisions": ["respond", "reject"],
                    "description": "Coworker asks the user a question that needs an answer.",
                    "when": needs_ask_user,
                },
            }
        )
    ]


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


def interrupt_action_kind(action: dict[str, Any]) -> str:
    name = str(action.get("name") or "")
    if name == "ask_user":
        return "question"
    if name == "submit_plan":
        return "plan"
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


def record_runtime_interrupts(
    interrupts: Iterable[Any],
    approval_store: CommandApprovalStore,
    context: dict[str, Any],
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
            kind = interrupt_action_kind(action)
            if kind == "question":
                command, cwd, timeout_seconds = [], "", 20
            else:
                command, cwd, timeout_seconds = interrupt_command_details({"action_requests": [action]})
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, action_index, kind, command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": action_index, "action_count": len(actions), "tool_name": str(action.get("name") or ""), "action_args": args, "hitl_request": _json_safe(value)},
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
    if kind == "plan":
        args = context.get("action_args") if isinstance(context.get("action_args"), dict) else {}
        return {
            **base,
            "type": "plan_required",
            "plan": str(args.get("plan_text") or ""),
            "command": approval.get("command", []),
            "cwd": approval.get("cwd", ""),
        }
    return {**base, "type": "approval_required", "command": approval.get("command", []), "cwd": approval.get("cwd", "")}


def trace_context(
    *, session_id: str, provider: str, provider_id: str, model: str,
    language: Language, work_mode: WorkMode, autonomy: Autonomy, streaming: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id, "provider": provider, "provider_id": provider_id, "model": model,
        "language": language, "work_mode": work_mode, "autonomy": autonomy, "streaming": streaming,
    }


def coerce_message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


def _extract_reasoning_from_chunk(chunk: Any) -> str | None:
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    raw = additional_kwargs.get("reasoning")
    if not isinstance(raw, str) or not raw.strip():
        raw = additional_kwargs.get("reasoning_content")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _reasoning_heading(text: str) -> str:
    """Extract a short summary heading from reasoning text.

    Prefers the first markdown heading, then the first ``**bold**`` segment.
    """
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip()[:80]
    match = re.search(r"\*\*([^*]+)\*\*", text)
    if match:
        return match.group(1).strip()[:80]
    return ""


def _strip_plan_leak(content: str, parts: list[dict[str, Any]]) -> str:
    """Remove a leaked internal plan-marker segment from streamed content.

    ``PlanGateMiddleware`` injects the planner output as an assistant message
    (``[CW-PLAN]`` + plan text) so the model can use it as guidance. Some
    providers/graph modes re-stream that injected message as ordinary content
    deltas, duplicating the plan that was already delivered through the
    ``plan_*`` events. This strips that leading segment when it matches the
    plan text emitted through the plan events.
    """
    if not content:
        return content
    plan_text = ""
    for part in parts:
        if part.get("type") == "plan_end" and part.get("content"):
            plan_text = str(part["content"])
            break
        if part.get("type") == "plan" and part.get("content"):
            plan_text = str(part["content"])
            break
    if not plan_text:
        return content

    if content.startswith(plan_text):
        return content[len(plan_text):].lstrip("\n")
    stripped = content.lstrip("\n")
    if stripped.startswith(PLAN_MARKER):
        stripped = stripped[len(PLAN_MARKER):].lstrip("\n")
        if stripped.startswith(plan_text):
            return stripped[len(plan_text):].lstrip("\n")
    return content


_WRITE_ARG_PATH_KEYS = ("file_path", "path", "target")


def _estimate_file_changes(tool_name: str, input_raw: str) -> list[dict[str, Any]]:
    """Best-effort summary of files touched by a write/edit tool call.

    Returns a list of ``{path, kind, added, removed}`` dicts derived from the
    tool input arguments. Values are line-count estimates, not exact diffs.
    """
    if not input_raw:
        return []
    try:
        args = json.loads(input_raw)
    except Exception:
        return []
    if not isinstance(args, dict):
        return []

    def _count_lines(text: str) -> int:
        if not text:
            return 0
        return max(text.rstrip("\n").count("\n") + 1, 1)

    if tool_name == "write_file":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        content = str(args.get("content") or "")
        if path:
            return [{"path": path, "kind": "write", "added": _count_lines(content), "removed": 0}]

    if tool_name == "replace_in_file":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        occurrences = 1 if not args.get("replace_all") else max(int(args.get("occurrences") or 1), 1)
        if path:
            removed = occurrences * max(_count_lines(old_text) - 1, 0)
            added = occurrences * max(_count_lines(new_text) - 1, 0)
            return [{"path": path, "kind": "edit", "added": added, "removed": removed}]

    if tool_name == "apply_text_edits":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        edits = args.get("edits")
        if path and isinstance(edits, list):
            added = 0
            removed = 0
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                old_lines = _count_lines(str(edit.get("old_text") or ""))
                new_lines = _count_lines(str(edit.get("new_text") or ""))
                removed += max(old_lines - 1, 0)
                added += max(new_lines - 1, 0)
            return [{"path": path, "kind": "edit", "added": added, "removed": removed}]

    return []


def _terminate_stray_tools(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defensive: guarantee every ``tool_start`` has a terminal ``tool_end``
    before the turn's parts are merged/persisted. A tool that never produced a
    ToolMessage (interrupted before execution) would otherwise be persisted as
    ``status: running`` and render an endless spinner in the UI."""
    unresolved: set[str] = set()
    for part in parts:
        ptype = part.get("type")
        if ptype == "tool_start":
            unresolved.add(str(part.get("id", "")))
        elif ptype == "tool_end":
            unresolved.discard(str(part.get("id", "")))
    if not unresolved:
        return parts
    terminated = list(parts)
    for tc_id in unresolved:
        terminated.append({"type": "tool_end", "id": tc_id, "output": "", "status": "error"})
    return terminated


def _merge_event_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "reasoning_delta":
            if merged and merged[-1].get("type") == "reasoning":
                merged[-1]["content"] += part["content"]
            else:
                merged.append({"type": "reasoning", "content": part["content"]})
        elif part.get("type") == "plan_delta":
            if merged and merged[-1].get("type") == "plan":
                merged[-1]["content"] += part["content"]
            elif merged and merged[-1].get("type") == "plan_end":
                merged[-1]["content"] = part["content"]
                merged[-1]["type"] = "plan"
            else:
                merged.append({"type": "plan", "content": part["content"]})
        elif part.get("type") == "plan_start":
            continue
        elif part.get("type") == "plan_end":
            if merged and merged[-1].get("type") == "plan":
                merged[-1]["content"] = part["content"] or merged[-1].get("content", "")
            else:
                merged.append({"type": "plan", "content": part.get("content", "")})
        elif part.get("type") == "tool_delta":
            existing_tool = next((p for p in merged if p.get("type") == "tool" and p.get("id") == part.get("id")), None)
            if existing_tool:
                existing_tool["input"] = (existing_tool.get("input", "") or "") + (part.get("input") or "")
            elif merged and merged[-1].get("type") == "tool_start":
                merged[-1]["type"] = "tool"
                merged[-1]["input"] = (merged[-1].get("input", "") or "") + (part.get("input") or "")
        elif part.get("type") == "tool_start":
            merged.append({"type": "tool", "id": part.get("id", ""), "name": part.get("name", ""), "status": "running", "input": part.get("input", "")})
        elif part.get("type") == "tool_end":
            existing_tool = next((p for p in merged if p.get("type") == "tool" and p.get("id") == part.get("id")), None)
            if existing_tool:
                existing_tool["status"] = "success" if part.get("status") == "success" else "error"
                if part.get("output") is not None:
                    existing_tool["output"] = part["output"]
                if part.get("duration_ms") is not None:
                    existing_tool["duration_ms"] = part["duration_ms"]
                if part.get("files") is not None:
                    existing_tool["files"] = part["files"]
            else:
                merged.append(
                    {
                        "type": "tool",
                        "id": part.get("id", ""),
                        "name": part.get("name", ""),
                        "status": "success" if part.get("status") == "success" else "error",
                        "input": "",
                        "output": part.get("output"),
                        **({"duration_ms": part["duration_ms"]} if part.get("duration_ms") is not None else {}),
                        **({"files": part["files"]} if part.get("files") is not None else {}),
                    }
                )
        else:
            merged.append(part)

    for item in merged:
        if item.get("type") == "reasoning":
            item["heading"] = _reasoning_heading(item.get("content", ""))
            item["done"] = True
    return merged


def generate_title(first_user_message: str, assistant_response: str = "") -> str:
    from langchain_openai import ChatOpenAI
    if assistant_response and assistant_response.strip():
        conversation = f"用户: {first_user_message}\n\nAI: {assistant_response}"
    else:
        conversation = first_user_message
    try:
        from .config import load_settings
        from .providers import ProviderManager
        settings = load_settings()
        provider_manager = ProviderManager(settings.data_dir / "providers.json")
        dp = provider_manager.default_provider()
        if dp and dp.api_key and (dp.base_url or dp.provider_type):
            llm = ChatOpenAI(model=dp.model, temperature=0, api_key=dp.api_key, base_url=dp.base_url or None)
            response = llm.invoke([
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": conversation},
            ])
            title = coerce_message_content(response).strip().strip('"').strip("'")
            if title and 3 <= len(title) <= 50:
                return title
    except Exception:
        pass
    return _default_title_from_message(first_user_message)


def _default_title_from_message(user_message: str) -> str:
    text = user_message.strip()
    if len(text) <= 20:
        return text
    return text[:20].rstrip()[:20]


def prepare_agent_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    prepared: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content")
        if role not in {"user", "assistant", "system"} or content is None:
            continue
        prepared.append({"role": role, "content": str(content)})
    if not prepared:
        prepared.append({"role": "user", "content": ""})
    return prepared


def format_user_message(message: str, attachments: list[dict[str, Any]] | None = None, references: list[dict[str, Any]] | None = None) -> str:
    parts = [message]
    if references:
        parts.append("\n\nReferenced sessions (readable via the read_session tool):")
        for reference in references:
            ref_id = str(reference.get("id") or "")
            ref_title = str(reference.get("title") or ref_id)
            parts.append(f"- {ref_title} (session id: {ref_id})")
    if attachments:
        parts.append("\n\nAttachments:")
        for attachment in attachments:
            name = str(attachment.get("name") or "attachment")
            size = int(attachment.get("size") or 0)
            kind = str(attachment.get("type") or "file")
            content = attachment.get("content")
            if isinstance(content, str) and content:
                safe_content = content[:MAX_ATTACHMENT_CHARS]
                was_truncated = attachment.get("truncated") or len(content) > MAX_ATTACHMENT_CHARS
                truncated = "\n[Attachment truncated by Coworker.]" if was_truncated else ""
                parts.append(f"\n--- {name} ({kind}, {size} bytes) ---\n{safe_content}{truncated}\n--- end {name} ---")
            elif attachment.get("binary"):
                parts.append(f"\n- {name} ({kind}, {size} bytes): binary or unsupported attachment; content not included.")
            else:
                parts.append(f"\n- {name} ({kind}, {size} bytes): no readable content included.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Reasoning-preserving ChatOpenAI adapter
# ---------------------------------------------------------------------------

class ReasonPreservingChatOpenAI:
    """Factory that returns a :class:`ChatOpenAI` subclass which persists
    ``reasoning_content`` in ``additional_kwargs`` for OpenAI-compatible
    providers (DeepSeek, vLLM, Ollama, local proxy).

    Needed because the base ``langchain-openai`` class deliberately discards
    non‑standard delta fields in ``_convert_delta_to_message_chunk``.
    """

    @staticmethod
    def create(model: str, temperature: float, api_key: str, base_url: str | None) -> Any:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import AIMessageChunk

        _original = ChatOpenAI._convert_chunk_to_generation_chunk

        def _patched_convert(self: Any, chunk: dict, default_chunk_class: Any, base_generation_info: Any | None = None) -> Any:
            gen_chunk = _original(self, chunk, default_chunk_class, base_generation_info)
            if gen_chunk is None or gen_chunk.message is None:
                return gen_chunk
            msg = gen_chunk.message
            if isinstance(msg, AIMessageChunk):
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    additional = dict(getattr(msg, "additional_kwargs", {}) or {})
                    existing = additional.get("reasoning", "")
                    additional["reasoning"] = reasoning
                    object.__setattr__(msg, "additional_kwargs", additional)
            return gen_chunk

        # Patch at the class level so bind() / model_copy() clones inherit it
        ChatOpenAI._convert_chunk_to_generation_chunk = _patched_convert

        return ChatOpenAI(
            model=model, temperature=temperature, api_key=api_key, base_url=base_url,
        )


# ---------------------------------------------------------------------------
# NormalizeMessagesMiddleware – keeps provider-safe message ordering.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ToolCallCleanerMiddleware – drops empty/invalid tool calls before execution.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# PhaseToolGateMiddleware – phase-driven tool gating (Codex-style autonomy).
# ---------------------------------------------------------------------------

class PhaseToolGateMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Dynamic tool selection based on the agent's phase and autonomy.

    Uses the official ``wrap_model_call`` + ``request.override`` pattern so the
    model only ever sees the tools the current phase allows:

    * ``discuss`` (plan mode before approval): read-only + ``ask_user`` +
      ``submit_plan`` — the agent cannot touch the filesystem.
    * ``execute``: read + write + ``run_command``; ``ask_user`` stays available
      unless autonomy is ``autonomous`` (physical removal — the model cannot
      interrupt the user at all in full-autonomy mode).

    The phase/autonomy-aware system prompt is also injected here so there is a
    single prompt source (fixing the previous double-injection).
    """

    def _allowed_tools(self, state: CoworkerAgentState) -> set[str]:
        work_mode = normalize_work_mode(state.get("work_mode"))
        phase = normalize_phase(state.get("phase"), work_mode)
        autonomy = normalize_autonomy(state.get("autonomy"))
        allowed = set(_READ_ONLY_TOOLS)
        if phase == "discuss":
            allowed |= _PLAN_TOOLS
        else:
            allowed |= _CHANGE_TOOL_NAMES | _EXEC_TOOLS
            if autonomy != "autonomous":
                allowed |= {"ask_user"}
        if state.get("goal_mode"):
            allowed |= {"finalize_goal", "write_todos"}
        return allowed

    def _overrides(self, request: Any) -> dict[str, Any]:
        state = request.state
        allowed = self._allowed_tools(state)
        tools = [tool for tool in request.tools if getattr(tool, "name", "") in allowed]
        language = normalize_language(state.get("language"))
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        autonomy = normalize_autonomy(state.get("autonomy"))
        return {
            "tools": tools,
            "system_message": SystemMessage(phase_system_prompt(language, phase, autonomy)),
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
        return ToolMessage(
            content=f"Tool '{tool_name}' is not available in the current phase/autonomy. It was skipped.",
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


# ---------------------------------------------------------------------------
# GoalModeMiddleware – autonomous /goal loop (Codex Goal mode).
# ---------------------------------------------------------------------------

GOAL_MARKER = "[CW-GOAL]"

# Max forced-continuation nudges before declaring a goal stalled. Shared by
# GoalModeMiddleware (docstring reference) and the goal loop in
# OpenAICompatibleStreamRuntime, which previously referenced `self.MAX_FORCE`
# on a class that never defined it (AttributeError on every stall).
GOAL_MAX_FORCE = 3


class FinalizeGoalArgs(BaseModel):
    achieved: bool = Field(description="True only when the goal is fully achieved and verified.")
    progress: str = Field(default="", description="1-2 sentence status of the current round.")
    verification: str = Field(default="", description="Evidence (tests/commands that passed) when achieved=True.")


def _goal_tools() -> list[Any]:
    from langchain_core.tools import tool

    @tool(args_schema=FinalizeGoalArgs)
    def finalize_goal(achieved: bool, progress: str = "", verification: str = "") -> str:
        """Declare the current goal round complete.

        Call ``finalize_goal(achieved=True, verification=...)`` ONLY when the goal
        is fully achieved and verified (tests/checks passed). Call
        ``finalize_goal(achieved=False, progress=...)`` after finishing a work chunk
        when more work remains — the system continues the goal automatically.
        Do NOT give a final answer without calling this tool first.
        """
        return json.dumps(
            {"achieved": bool(achieved), "progress": str(progress)[:500], "verification": str(verification)[:500]},
            ensure_ascii=False,
        )

    return [finalize_goal]


def goal_system_prompt(language: Language, goal_text: str, autonomy: Autonomy = "autonomous") -> str:
    lang_line = f"Reply in {language_name(language)}."
    if autonomy == "autonomous":
        mode_line = (
            "You are working toward a persistent goal in fully autonomous mode: do not "
            "ask the user anything, make reasonable decisions and keep working."
        )
    elif autonomy == "supervised":
        mode_line = (
            "You are working toward a persistent goal in supervised mode: each write or "
            "command may require the user's approval before it runs. Ask for approval only "
            "when needed."
        )
    else:
        mode_line = (
            "You are working toward a persistent goal in guarded mode: work freely inside "
            "the workspace. You may call ask_user only when you are genuinely blocked and "
            "need a decision; otherwise proceed autonomously."
        )
    return (
        f"{lang_line}\n"
        f"{mode_line}\n"
        f"GOAL: {goal_text}\n\n"
        "Work continuously toward the goal.\n"
        "1. Break the work into concrete todos with `write_todos`, marking each "
        "in_progress/completed as you go.\n"
        "2. Research, edit files and run workspace commands. Use `run_command` to run "
        "tests/checks that prove real progress.\n"
        "3. After a work chunk, if more remains, call "
        "`finalize_goal(achieved=false, progress=<1-2 sentence status>)` — the system "
        "starts the next round automatically.\n"
        "4. When the goal is FULLY achieved and verified, you MUST call "
        "`finalize_goal(achieved=true, verification=<evidence: tests/commands that passed>)`.\n"
        "5. CRITICAL: A plain-text answer does NOT complete the goal. The ONLY way to "
        "finish is to call `finalize_goal(achieved=true, ...)`. Do NOT stop with a "
        "text summary before calling it. If you are truly blocked, call "
        "`finalize_goal(achieved=false, progress=<blocker>)`."
    )


class GoalModeMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Autonomous goal loop (Codex ``/goal``).

    * Injects the goal system prompt and exposes ``finalize_goal``.
    * Guards against premature conclusions: ``wrap_model_call`` re-invokes the
      model (up to ``MAX_FORCE`` times) with a continuation nudge whenever it
      returns plain text without having called ``finalize_goal`` this round, so
      the loop keeps working instead of stopping early.
    """

    MAX_FORCE = GOAL_MAX_FORCE

    def __init__(self, language: Language = "en"):
        self.language = language
        self._finalize_tool = _goal_tools()[0]

    def _active(self, state: CoworkerAgentState) -> bool:
        return bool(state.get("goal_mode"))

    def _finalize_called(self, state: CoworkerAgentState) -> bool:
        from langchain_core.messages import AIMessage, ToolMessage
        finalized: set[str] = set()
        for msg in state.get("messages", []):
            if isinstance(msg, AIMessage):
                for tc in getattr(msg, "tool_calls", None) or []:
                    if tc.get("name") == "finalize_goal" and tc.get("id"):
                        finalized.add(tc["id"])
        for msg in state.get("messages", []):
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None) in finalized:
                return True
        return False

    def _overrides(self, request: Any) -> dict[str, Any]:
        tools = list(request.tools)
        if not any(getattr(t, "name", "") == "finalize_goal" for t in tools):
            tools.append(self._finalize_tool)
        goal_text = str(request.state.get("goal_text") or "")
        autonomy = normalize_autonomy(request.state.get("autonomy"))
        nudge = request.state.get("goal_nudge")
        prompt = goal_system_prompt(self.language, goal_text, autonomy)
        if nudge:
            prompt = f"{prompt}\n\n[Coworker] {str(nudge)[:1000]}"
        return {
            "tools": tools,
            "system_message": SystemMessage(prompt),
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._active(request.state):
            return handler(request)
        return handler(request.override(**self._overrides(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._active(request.state):
            return await handler(request)
        return await handler(request.override(**self._overrides(request)))


# ---------------------------------------------------------------------------
# PlanApprovalMiddleware – plan-first approval gate (Claude Code style).
# ---------------------------------------------------------------------------

class PlanApprovalMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Plan-first approval gate, scoped to the ``discuss`` phase.

    The agent calls ``submit_plan`` to present its implementation plan and
    receive user approval before any write/execute tool is allowed to run. The
    gate only fires while ``phase == "discuss"`` (plan mode before approval);
    in ``execute`` phase it is inert, so build-mode sessions run without a plan
    gate (Codex-style boundary autonomy).

    On approval the decision also carries the user-chosen execution autonomy
    (``supervised`` / ``guarded`` / ``autonomous``), which is written to state
    together with ``phase = "execute"`` — the approval action *routes* the
    follow-up execution posture (Claude Code ExitPlanMode semantics).

    Exits:
    * ``approve`` + ``autonomy`` -> execute with that autonomy.
    * ``continue_discuss`` -> stay in discuss, refine the plan.
    * ``reject`` -> stay in discuss, explain/revise.
    """

    def __init__(self, language: Language = "en"):
        self.language = language

    def _last_ai_with_tool_calls(self, state: CoworkerAgentState):
        from langchain_core.messages import AIMessage
        messages = state.get("messages", [])
        # Only the most recent model output is eligible for gating. Scanning
        # further back would re-interrupt on a stale submit_plan after a
        # "continue discussing" decision.
        if messages and isinstance(messages[-1], AIMessage) and getattr(messages[-1], "tool_calls", None):
            return messages[-1]
        return None

    def _active(self, state: CoworkerAgentState) -> bool:
        return normalize_phase(state.get("phase"), state.get("work_mode")) == "discuss"

    def after_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if not self._active(state):
            return None
        return self._handle_submit_plan(state)

    async def aafter_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def _handle_submit_plan(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        from langchain_core.messages import AIMessage, ToolMessage
        from langgraph.types import interrupt

        last_ai = self._last_ai_with_tool_calls(state)
        if not last_ai:
            return None
        plan_calls = [tc for tc in last_ai.tool_calls if tc.get("name") == "submit_plan"]
        if not plan_calls:
            return None
        plan_call = plan_calls[0]
        plan_text = str(plan_call.get("args", {}).get("plan_text", "") or "")

        hitl_request = {
            "action_requests": [{"name": "submit_plan", "args": {"plan_text": plan_text}}],
            "review_configs": [{"action_name": "submit_plan", "allowed_decisions": ["approve", "reject"]}],
        }
        decisions = interrupt(hitl_request)["decisions"]
        decision = decisions[0] if decisions else {"type": "reject"}
        dtype = str(decision.get("type", "reject"))

        # Keep submit_plan in the tool calls: the synthetic ToolMessage below
        # (same tool_call_id) satisfies the tool executor so submit_plan is not
        # re-run, while the agent loop continues to the next model call in the
        # resolved phase (official HITL "respond" semantics).
        autonomy = normalize_autonomy(state.get("autonomy"))
        if dtype == "approve":
            autonomy = normalize_autonomy(decision.get("autonomy") or state.get("autonomy"))
            phase = "execute"
            tool_msg = ToolMessage(
                content="The user approved your plan. Proceed to implement it using the write/execute tools.",
                name="submit_plan",
                tool_call_id=plan_call.get("id", "unknown"),
                status="success",
            )
        elif dtype == "continue_discuss":
            phase = "discuss"
            tool_msg = ToolMessage(
                content="The user wants to keep discussing before you execute. Refine your plan, research more, or ask clarifying questions. Do not modify files or run commands yet.",
                name="submit_plan",
                tool_call_id=plan_call.get("id", "unknown"),
                status="error",
            )
        else:
            phase = "discuss"
            tool_msg = ToolMessage(
                content="The user rejected your plan. Do not modify any files or run commands. Explain your approach to the user instead.",
                name="submit_plan",
                tool_call_id=plan_call.get("id", "unknown"),
                status="error",
            )

        return {"messages": [last_ai, tool_msg], "phase": phase, "autonomy": autonomy}


# ---------------------------------------------------------------------------
# Agent builder – single create_agent graph (official langchain idiom).
# ---------------------------------------------------------------------------

def build_coworker_agent_graph(
    llm: Any,
    tools: list[Any],
    work_mode: WorkMode,
    language: Language,
    autonomy: Autonomy = "guarded",
    checkpointer: Any | None = None,
    approval_store: CommandApprovalStore | None = None,
    goal_mode: bool = False,
    data_dir: Path | None = None,
) -> Any:
    """Compile the Coworker agent as a single ``create_agent`` graph.

    The middleware chain implements the Codex-style two-axis model:

    * ``PhaseToolGateMiddleware`` filters the tool set each model call based on
      the current ``phase``/``autonomy`` (physical enforcement, not prompt).
    * ``GoalModeMiddleware`` drives the autonomous /goal loop when ``goal_mode``
      is active (goal prompt + finalize_goal/continue_goal + premature-end guard).
    * ``TodoListMiddleware`` (goal mode) exposes ``write_todos`` so the agent can
      break the goal into a visible task list.
    * ``PlanApprovalMiddleware`` gates ``submit_plan`` while ``phase ==
      "discuss"`` and routes the approved execution autonomy.
    * ``HumanInTheLoopMiddleware`` (always mounted) interrupts commands/writes
      only in ``execute`` + ``supervised``, and ``ask_user`` regardless.
    """
    from langchain.agents import create_agent

    middleware: list[Any] = [
        NormalizeMessagesMiddleware(),
        ToolCallCleanerMiddleware(),
        PhaseToolGateMiddleware(),
        GoalModeMiddleware(language),
    ]
    if goal_mode:
        from langchain.agents.middleware.todo import TodoListMiddleware

        middleware.append(TodoListMiddleware())
        # Register the goal-completion tool in the graph's tool registry so the
        # tool node can execute it (the middleware also exposes it to the model).
        tools = [*tools, _goal_tools()[0]]
    middleware.append(PlanApprovalMiddleware(language))
    middleware.extend(command_approval_middleware(approval_store))

    from .mcp_middleware import McpToolMiddleware

    _mcp_manager = McpManager(
        Path(data_dir if data_dir is not None else Path.cwd()) / "mcp_servers.json",
    )

    middleware.append(McpToolMiddleware(_mcp_manager))

    system_prompt = (
        f"You are Coworker, a local coding assistant. Reply in {language_name(language)}. "
        "Use workspace tools only when they are needed and keep answers concise."
    )

    kwargs: dict[str, Any] = {
        "model": llm,
        "tools": tools,
        "system_prompt": system_prompt,
        "middleware": middleware,
        "state_schema": CoworkerAgentState,
        "name": "coworker_agent",
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_agent(**kwargs)


# ---------------------------------------------------------------------------
# Concrete runtimes
# ---------------------------------------------------------------------------

class SimulatedSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {autonomy}\n\n你说：{message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {autonomy}\n\nYou said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpointer: Any, provider: ProviderEntry, model_override: str | None = None, change_store: ChangeStore | None = None, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None, data_dir: Path | None = None):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpointer = checkpointer
        self.change_store = change_store
        self.session_store = session_store
        self.referenced_sessions = set(referenced_sessions or set())
        self.data_dir = data_dir

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=False,
        )
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "run"})
        turn_index = self._next_turn_index(session_id)
        graph = build_coworker_agent_graph(
            self.llm,
            build_workspace_tools(
                self.workspace, audit_context, change_store=self.change_store, turn_index=turn_index,
                session_store=self.session_store, referenced_sessions=self.referenced_sessions,
            ),
            work_mode=work_mode,
            language=language,
            autonomy=autonomy,
            checkpointer=self.checkpointer,
            approval_store=self.approval_store,
            data_dir=self.data_dir,
        )
        try:
            result = graph.invoke(
                {
                    "messages": prepare_agent_messages([{"role": "user", "content": message}]),
                    "work_mode": work_mode,
                    "language": language,
                    "phase": normalize_phase(None, work_mode),
                    "autonomy": autonomy,
                },
                config=agent_run_config(
                    session_id=session_id, provider=self.provider_name, model=self.model_name,
                    language=language, work_mode=work_mode, autonomy=autonomy, streaming=False,
                ),
            )
        except Exception as exc:
            self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
            raise
        if "__interrupt__" in result:
            approvals = record_runtime_interrupts(
                result["__interrupt__"], self.approval_store,
                {**audit_context, "language": language, "work_mode": work_mode, "autonomy": autonomy, "referenced_sessions": list(self.referenced_sessions)},
            )
            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals]})
            approval_ids = ", ".join(str(a.get("id", "")) for a in approvals)
            content = f"Command approval required: {approval_ids}" if language == "en" else f"命令需要审批：{approval_ids}"
            return AgentReply(content=content, mode=self.mode, provider=self.provider_name)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        content = coerce_message_content(messages[-1]) if messages else ""
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(content)})
        return AgentReply(content=content, mode=self.mode, provider=self.provider_name)


class OpenAICompatibleStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpoint_path: Path, provider: ProviderEntry, model_override: str | None = None, change_store: ChangeStore | None = None, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None, data_dir: Path | None = None):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpoint_path = checkpoint_path
        self.change_store = change_store
        self.session_store = session_store
        self.referenced_sessions = set(referenced_sessions or set())
        self.data_dir = data_dir

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    async def stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=False):
            yield event

    async def stream_rerun(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Re-run the agent from a full message history (rollback/regenerate/edit).

        Unlike ``stream``, this treats the given messages as the complete initial
        state (no checkpoint append). The session checkpoint must already have
        been reset by the caller so the history is rebuilt from scratch.
        """
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=True):
            yield event

    async def _stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy, *, rerun: bool,
        goal_mode: bool = False, goal_text: str = "", goal_continue: bool = False, _nudge: str = "", _cancel_event: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        interrupt_context = {**audit_context, "language": language, "work_mode": work_mode, "autonomy": autonomy, "referenced_sessions": list(self.referenced_sessions)}
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "rerun" if rerun else "stream"})
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}
        yield {"type": "stage", "name": "executing", "status": "running"}

        if goal_continue:
            # Round 2+ of a goal: continue the thread from the checkpoint without
            # adding a new user message.
            prepared_messages = []
        else:
            prepared_messages = prepare_agent_messages(messages)
        turn_index = self._next_turn_index(session_id)

        async with _open_checkpointer(self.checkpoint_path) as checkpointer:
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(
                    self.workspace, audit_context, change_store=self.change_store, turn_index=turn_index,
                    session_store=self.session_store, referenced_sessions=self.referenced_sessions,
                ),
                work_mode=work_mode, language=language, autonomy=autonomy,
                checkpointer=checkpointer, approval_store=self.approval_store,
                goal_mode=goal_mode, data_dir=self.data_dir,
            )

            inputs = {
                "messages": prepared_messages,
                "work_mode": work_mode,
                "language": language,
                "phase": normalize_phase(None, work_mode),
                "autonomy": autonomy,
            }
            if goal_mode:
                inputs["goal_mode"] = True
                inputs["goal_text"] = goal_text
            if _nudge:
                inputs["goal_nudge"] = _nudge
            config = agent_run_config(
                session_id=session_id, provider=self.provider_name, model=self.model_name,
                language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
            )

            content_parts: list[str] = []
            tool_state: dict[str, dict[str, Any]] = {}
            parts: list[dict[str, Any]] = []

            try:
                async for stream_mode, chunk in graph.astream(inputs, config=config, stream_mode=["messages", "custom", "updates"]):
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                yield event
                        except GeneratorExit:
                            raise
                        except Exception:
                            pass
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                yield chunk
                    elif stream_mode == "updates":
                        if "__interrupt__" in chunk:
                            approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, interrupt_context)
                            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals]})
                            for approval in approvals:
                                event = stream_event_from_interrupt(approval)
                                if event.get("type") == "plan_required":
                                    parts.append({"type": "plan", "content": str(event.get("plan") or "")})
                                yield event
                            return
                        # write_todos updates the todo list via a Command state update.
                        for node_update in chunk.values():
                            if isinstance(node_update, dict) and "todos" in node_update:
                                yield {"type": "todos", "todos": node_update.get("todos") or []}
                        if _cancel_event and _cancel_event.is_set():
                            raise asyncio.CancelledError("Goal cancelled mid-stream")
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
                raise

        final_content = "".join(content_parts)
        final_content = _strip_plan_leak(final_content, parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content)})
        merged_parts = _merge_event_parts(_terminate_stray_tools(parts))
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": merged_parts}

    async def goal_stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        autonomy: Autonomy,
        goal_text: str = "",
        goal_continue_first: bool = False,
        _cancel_event: Any = None,
        goal_stream_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Autonomous /goal loop (Codex Goal mode): rounds until the agent calls
        ``finalize_goal(achieved=True)`` or the goal is paused.

        Each round is one agent turn (model → tools → … → finalize_goal or a
        status). Pause is honoured at round boundaries: the current round finishes,
        then the loop stops; ``/goal/resume`` re-enters with ``goal_continue_first``.
        """
        if self.session_store is not None:
            try:
                session_state = self.session_store.require(session_id)
                goal_text = str(session_state.goal_text or goal_text)
                # Preserve 0 (unlimited); only fall back to 50 when unset (None).
                goal_max_rounds = 50 if session_state.goal_max_rounds is None else int(session_state.goal_max_rounds)
                if session_state.goal_interrupted:
                    yield {"type": "goal_done", "round": 0, "goal": goal_text, "content": "", "reason": "interrupted"}
                    return
                if session_state.goal_done:
                    # Already achieved — nothing left to run.
                    yield {"type": "goal_done", "round": 0, "goal": goal_text, "content": "", "already": True}
                    return
            except Exception:
                pass
        last_checkpoint: dict[str, Any] | None = None
        last_todos: list[Any] = []
        round_no = 0
        yield {"type": "goal_start", "goal": goal_text, "session_id": session_id}
        while True:
            round_no += 1
            # Read session state: goal_just_edited flag, goal_text, stop request, max_rounds.
            session_state: Any = None
            if self.session_store is not None:
                try:
                    session_state = self.session_store.require(session_id)
                    goal_text = str(session_state.goal_text or goal_text)
                    if session_state.goal_just_edited:
                        session_state.goal_just_edited = False
                        self.session_store.save(session_state)
                        yield {"type": "goal_edited", "round": round_no, "goal": goal_text, "stream_id": goal_stream_id}
                    if session_state.goal_stopped:
                        yield {
                            "type": "goal_done", "round": round_no, "goal": goal_text,
                            "content": "", "reason": "stopped",
                        }
                        return
                    # Preserve 0 (unlimited); only fall back to 50 when unset (None).
                    goal_max_rounds = 50 if session_state.goal_max_rounds is None else int(session_state.goal_max_rounds)
                except Exception:
                    # Failed to read session state: fail safe to the default cap
                    # rather than 0, which would otherwise mean "unlimited".
                    goal_max_rounds = 50
            else:
                goal_max_rounds = 50
            is_first = round_no == 1
            # Max rounds guard: stop when we exceed goal_max_rounds (0 = no limit).
            if goal_max_rounds > 0 and round_no > goal_max_rounds:
                yield {
                    "type": "goal_done", "round": round_no, "goal": goal_text,
                    "content": "", "reason": "max_rounds_exceeded",
                }
                return
            yield {"type": "goal_round", "round": round_no, "goal": goal_text, "status": "running"}
            round_had_tools = False
            round_had_interrupt = False
            try:
                async with asyncio.timeout(600):  # 5 min per turn
                    async for event in self._stream(
                        messages if (is_first and not goal_continue_first) else [],
                        # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                        goal_mode=True, goal_text=goal_text,
                        goal_continue=(not is_first or goal_continue_first),
                        _cancel_event=_cancel_event,
                    ):
                        etype = event.get("type", "")
                        if etype == "goal_checkpoint":
                            last_checkpoint = event
                        elif etype == "todos":
                            last_todos = event.get("todos") or []
                        elif etype == "tool_start":
                            round_had_tools = True
                        elif etype in ("plan_required", "approval_required"):
                            round_had_interrupt = True
                        yield event
            except asyncio.TimeoutError:
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                return
            except asyncio.CancelledError:
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                return
            # Round boundary decision.
            paused = False
            if self.session_store is not None:
                try:
                    paused = bool(self.session_store.require(session_id).goal_paused)
                except Exception:
                    paused = False
            achieved = bool(last_checkpoint and last_checkpoint.get("achieved"))
            if round_had_interrupt and not achieved:
                # A human-in-the-loop interrupt (plan / command approval) fired
                # during this goal round. Pause the loop cleanly instead of
                # re-entering and re-triggering the same interrupt repeatedly
                # until the round cap — the user approves, then resumes from the
                # checkpoint.
                if self.session_store is not None:
                    try:
                        self.session_store.update_goal(session_id, goal_paused=True)
                    except Exception:
                        pass
                yield {"type": "goal_paused", "round": round_no, "goal": goal_text}
                return
            if self.session_store is not None:
                try:
                    self.session_store.update_goal(
                        session_id, goal_done=achieved, goal_todos=list(last_todos or []),
                    )
                except Exception:
                    pass
            if achieved:
                yield {
                    "type": "goal_done", "round": round_no, "goal": goal_text,
                    "content": str((last_checkpoint or {}).get("progress", "") or ""),
                    "verification": str((last_checkpoint or {}).get("verification", "") or ""),
                }
                return
            if paused:
                yield {"type": "goal_paused", "round": round_no, "goal": goal_text}
                return
            if last_checkpoint is None and not round_had_tools:
                # The agent concluded with plain text without calling finalize_goal
                # and made no tool calls this round — it is stalling, not working.
                # Apply force-loop nudges up to MAX_FORCE times before giving up.
                if self.session_store is not None:
                    try:
                        session_state = self.session_store.require(session_id)
                        goal_force = int(session_state.goal_force_count or 0)
                    except Exception:
                        goal_force = 0
                else:
                    goal_force = 0
                if goal_force < GOAL_MAX_FORCE:
                    # Yield a force_continue event to inform the client, then
                    # re-invoke _stream with a nudge appended to the user message.
                    yield {"type": "goal_force", "round": round_no, "reason": "agent_without_tools", "count": goal_force}
                    goal_force += 1
                    # Persist the incremented force count so it survives at round boundary
                    if self.session_store is not None:
                        try:
                            self.session_store.update_goal(session_id, goal_force_count=goal_force)
                        except Exception:
                            pass
                    # Reset checkpoint state for the forced continuation round.
                    last_checkpoint = None
                    last_todos = []
                    round_had_tools = False
                    # Re-invoke _stream with a nudge appended via state.
                    nudge = (
                        "\n\n[Coworker] You gave a plain-text answer without calling "
                        "`finalize_goal` or using any tools. Keep working toward the goal "
                        "using your tools (read_file, search_files, write_file, run_command, etc.) "
                        "and call finalize_goal when done."
                    )
                    try:
                        async with asyncio.timeout(5 * 60):  # 5 min per turn
                            async for event in self._stream(
                                messages if (is_first and not goal_continue_first) else [],
                                # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                                goal_mode=True, goal_text=goal_text,
                                goal_continue=(not is_first or goal_continue_first),
                                _nudge=nudge,
                                _cancel_event=_cancel_event,
                            ):
                                etype = event.get("type", "")
                                if etype == "goal_checkpoint":
                                    last_checkpoint = event
                                elif etype == "todos":
                                    last_todos = event.get("todos") or []
                                elif etype == "tool_start":
                                    round_had_tools = True
                                yield event
                    except asyncio.TimeoutError:
                        yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                        return
                    except asyncio.CancelledError:
                        yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                        return
                    # Check again after the forced continuation.
                    if last_checkpoint is not None:
                        continue
                    # Second attempt also stalled — check force count again.
                    if self.session_store is not None:
                        try:
                            session_state = self.session_store.require(session_id)
                            goal_force = int(session_state.goal_force_count or 0)
                        except Exception:
                            goal_force = 0
                    if goal_force < GOAL_MAX_FORCE:
                        goal_force += 1
                        yield {"type": "goal_force", "round": round_no, "reason": "agent_without_tools", "count": goal_force}
                        last_checkpoint = None
                        last_todos = []
                        round_had_tools = False
                        if self.session_store is not None:
                            try:
                                self.session_store.update_goal(session_id, goal_force_count=goal_force)
                            except Exception:
                                pass
                        try:
                            async with asyncio.timeout(600):  # 5 min per turn
                                async for event in self._stream(
                                    messages if (is_first and not goal_continue_first) else [],
                                    # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                                    goal_mode=True, goal_text=goal_text,
                                    goal_continue=(not is_first or goal_continue_first),
                                    _nudge=nudge,
                                    _cancel_event=_cancel_event,
                                ):
                                    etype = event.get("type", "")
                                    if etype == "goal_checkpoint":
                                        last_checkpoint = event
                                    elif etype == "todos":
                                        last_todos = event.get("todos") or []
                                    elif etype == "tool_start":
                                        round_had_tools = True
                                    yield event
                        except asyncio.CancelledError:
                            yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                            return
                        except asyncio.TimeoutError:
                            yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                            return
                    if last_checkpoint is not None:
                        continue
                # Force exhausted or goal_force >= MAX_FORCE — stop with stall signal.
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "stalled": True}
                return

    def _handle_message_chunk(
        self, msg: Any, content_parts: list[str], tool_state: dict[str, dict[str, Any]], parts: list[dict[str, Any]], session_id: str = "",
    ) -> list[dict[str, Any]]:
        from langchain_core.messages import AIMessageChunk, ToolMessage

        events: list[dict[str, Any]] = []

        if isinstance(msg, AIMessageChunk):
            reasoning = _extract_reasoning_from_chunk(msg)
            if reasoning:
                parts.append({"type": "reasoning_delta", "content": reasoning})
                events.append({"type": "reasoning_delta", "content": reasoning})

            text = getattr(msg, "content", "") or ""
            if isinstance(text, str) and text:
                content_parts.append(text)
                events.append({"type": "delta", "content": text})

            tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
            for tc in tool_call_chunks:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                tc_name = tc.get("name") or ""
                tc_args = tc.get("args") or ""

                if not tc_id:
                    continue

                if tc_name in ("submit_plan", "finalize_goal", "write_todos"):
                    # submit_plan renders as a Plan block; finalize_goal/write_todos
                    # are goal-loop controls (goal_checkpoint/todos events).
                    continue

                if tc_id not in tool_state:
                    tool_state[tc_id] = {"name": tc_name or "", "input": "", "status": "running", "started_at": time.time()}
                    parts.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                    events.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                else:
                    tool_state[tc_id]["input"] = tool_state[tc_id].get("input", "") + tc_args
                    if tc_name:
                        tool_state[tc_id]["name"] = tc_name
                        # 名字可能在后续流式 chunk 才到达，回填已推送的 tool_start part，
                        # 避免持久化后 tool 名称为空（显示 "Used tool:"）。
                        for existing_part in parts:
                            if existing_part.get("type") == "tool_start" and existing_part.get("id") == tc_id:
                                existing_part["name"] = tc_name
                                break
                    part = {"type": "tool_delta", "id": tc_id, "input": tc_args}
                    parts.append(part)
                    events.append(part)

        elif isinstance(msg, ToolMessage):
            msg_name = getattr(msg, "name", "") or ""
            if msg_name == "submit_plan":
                # submit_plan is rendered as a Plan block, not a tool card.
                return events
            tc_id = getattr(msg, "tool_call_id", "") or ""
            content = getattr(msg, "content", "") or ""
            if msg_name == "finalize_goal":
                # Emit a goal-checkpoint event for the round orchestrator / UI.
                try:
                    checkpoint = json.loads(content) if content else {}
                except (TypeError, ValueError):
                    checkpoint = {}
                events.append({
                    "type": "goal_checkpoint",
                    "achieved": bool(checkpoint.get("achieved", False)),
                    "progress": str(checkpoint.get("progress", "") or "")[:500],
                    "verification": str(checkpoint.get("verification", "") or "")[:500],
                })
                return events
            if msg_name == "write_todos":
                # Todo progress is streamed via the `todos` event; no tool card.
                return events
            # The real outcome: HITL rejections/errors carry status "error",
            # only genuine completions are "success".
            tool_status = "success" if (getattr(msg, "status", "") or "success") == "success" else "error"
            if tc_id in tool_state:
                tool_state[tc_id]["status"] = tool_status
                tool_state[tc_id]["output"] = str(content)[:2000]
                started_at = tool_state[tc_id].get("started_at")
                duration_ms = round((time.time() - started_at) * 1000) if started_at else None
                files = self._real_file_changes(tc_id, tool_state, session_id)
                part: dict[str, Any] = {"type": "tool_end", "id": tc_id, "output": str(content)[:2000], "status": tool_status}
                if duration_ms is not None:
                    part["duration_ms"] = duration_ms
                if files:
                    part["files"] = files
                parts.append(part)
                events.append(part)
            elif tc_id:
                part = {
                    "type": "tool_end",
                    "id": tc_id,
                    "name": getattr(msg, "name", "") or "",
                    "output": str(content)[:2000],
                    "status": tool_status,
                }
                parts.append(part)
                events.append(part)

        return events

    def _real_file_changes(self, tc_id: str, tool_state: dict[str, dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
        state = tool_state.get(tc_id) or {}
        tool_name = str(state.get("name") or "")
        input_raw = str(state.get("input") or "")
        if tool_name in _CHANGE_TOOL_NAMES and self.change_store is not None and session_id:
            raw_path = _path_from_tool_input(tool_name, input_raw)
            if raw_path:
                normalized = self.workspace.normalize_rel_path(raw_path)
                change = self.change_store.match_and_claim(session_id, tool_name, normalized)
                if change is not None:
                    return [_change_to_public(change)]
        return _estimate_file_changes(tool_name, input_raw)

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted turn, yielding progress events in real time.

        Every decision (approve / reject / continue_discuss / respond) resumes
        the SAME graph execution via ``Command(resume=...)`` — the official
        LangGraph HITL contract. The middleware synthesizes the corresponding
        ToolMessage (reject -> error feedback, respond -> human answer, plan
        approve -> execute transition), so the model always gets to continue
        instead of the turn being hard-terminated (fixes D4/D5).
        """
        from langgraph.types import Command

        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        language = normalize_language(context.get("language"))
        work_mode = normalize_work_mode(str(context.get("work_mode") or "build"))
        autonomy = normalize_autonomy(context.get("autonomy"))
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        content_parts: list[str] = []
        parts: list[dict[str, Any]] = []
        decision_types = ", ".join(str(item.get("type")) for item in decisions)
        self.trace_store.record("agent_activity", "resolved", current_trace_context, {"approval_id": approval.get("id", ""), "decisions": decision_types})

        config = agent_run_config(
            session_id=session_id, provider=self.provider_name, model=self.model_name,
            language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )

        async with _open_checkpointer(self.checkpoint_path) as checkpointer:
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(
                        self.workspace, audit_context, change_store=self.change_store,
                        session_store=self.session_store, referenced_sessions=self.referenced_sessions,
                    ),
                work_mode=work_mode, language=language, autonomy=autonomy,
                checkpointer=checkpointer, approval_store=self.approval_store,
                data_dir=self.data_dir,
            )
            interrupt_id = str(context.get("interrupt_id") or "")
            resume_map: dict[str, Any] = {interrupt_id: {"decisions": decisions}} if interrupt_id else {"decisions": decisions}
            tool_state: dict[str, dict[str, Any]] = {}
            try:
                async for stream_mode, chunk in graph.astream(Command(resume=resume_map), config=config, stream_mode=["messages", "custom", "updates"]):
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                yield event
                        except GeneratorExit:
                            raise
                        except Exception:
                            pass
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                yield chunk
                    elif stream_mode == "updates":
                        if "__interrupt__" in chunk:
                            approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, context)
                            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals], "resumed": True})
                            for item in approvals:
                                event = stream_event_from_interrupt(item)
                                if event.get("type") == "plan_required":
                                    parts.append({"type": "plan", "content": str(event.get("plan") or "")})
                                yield event
                            continue
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400], "resumed": True})
                raise

        final_content = "".join(content_parts)
        final_content = _strip_plan_leak(final_content, parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content), "resumed": True})
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": _merge_event_parts(_terminate_stray_tools(parts))}
        return


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace

    async def stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def stream_rerun(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def _stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        user_message = messages[-1]["content"] if messages else ""
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {autonomy}\n\n你说：{user_message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {autonomy}\n\nYou said: {user_message}"
            )
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": "simulated", "model": ""}
        for chunk in content:
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "content": content, "mode": self.mode, "provider": "simulated", "model": ""}


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings, session_store: SessionStore | None = None):
        from langgraph.checkpoint.sqlite import SqliteSaver

        self.settings = settings
        self.session_store = session_store
        self.default_workspace = Workspace(settings.workspace_dir, settings.data_dir / TOOL_AUDIT_FILENAME)
        self.approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
        self.trace_store = AgentTraceStore(settings.data_dir / AGENT_TRACE_FILENAME)
        self.change_store = ChangeStore(settings.data_dir)
        self.provider_manager = ProviderManager(settings.data_dir / "providers.json")
        self.mcp_manager = McpManager(settings.data_dir / "mcp_servers.json")
        self.checkpoint_path = settings.data_dir / "runtime_checkpoints.sqlite"
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_conn = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False, timeout=30.0)
        self.checkpointer = SqliteSaver(self.checkpoint_conn)

    def _open_sync_checkpointer(self):
        # A fresh synchronous connection per call, committed and closed, so it
        # never holds a lingering lock that contends with the async saver used
        # during streaming/resume.
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False, timeout=30.0)
        return SqliteSaver(conn), conn

    def has_runtime_checkpoint(self, session_id: str) -> bool:
        saver, conn = self._open_sync_checkpointer()
        try:
            return saver.get({"configurable": {"thread_id": session_id}}) is not None
        finally:
            conn.commit()
            conn.close()

    def forget_runtime_checkpoint(self, session_id: str) -> None:
        saver, conn = self._open_sync_checkpointer()
        try:
            saver.delete_thread(session_id)
        finally:
            conn.commit()
            conn.close()

    def _provider_for_request(self, provider_id: str | None, model: str | None) -> ProviderEntry | None:
        if provider_id:
            config = self.provider_manager.load()
            provider = config.find_enabled(provider_id)
            if not provider:
                raise RuntimeError(f"Provider {provider_id} is not enabled or not found")
            return replace(provider, model=model or provider.model)
        provider = self.provider_manager.default_provider()
        if provider and model:
            return replace(provider, model=model)
        return provider

    def _workspace_or_default(self, workspace: Workspace | None = None) -> Workspace:
        return workspace or self.default_workspace

    def _create_single_agent(self, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None) -> AgentRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if provider:
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, provider, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir)
        if self.settings.agent_provider == "openai":
            env_provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, env_provider, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings, selected_workspace, session_store=self.session_store, referenced_sessions=referenced_sessions)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None) -> AgentRuntime:
        if mode == "single":
            return self._create_single_agent(provider_id, model, workspace, referenced_sessions)
        raise RuntimeError(f"Unsupported agent mode: {mode}")

    def list_agent_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.trace_store.list(limit)

    def get_stream_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None) -> AgentStreamRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if not provider and self.settings.agent_provider == "openai":
            provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
        if not provider:
            if self.settings.agent_provider == "simulated":
                return SimulatedStreamRuntime(self.settings, selected_workspace, session_store=self.session_store, referenced_sessions=referenced_sessions)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            return OpenAICompatibleStreamRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpoint_path, provider, model, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir)
        raise RuntimeError(f"Unsupported agent mode for streaming: {mode}")

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted agent turn (HITL approval) using the stream runtime.

        The approval context carries the provider id, workspace path, and session
        metadata so the same graph can be rebuilt against the existing checkpoint.
        Events are forwarded in real time from the runtime generator.
        """
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME)
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        runtime = self.get_stream_runtime("single", provider_id or None, model or None, workspace, referenced_sessions=referenced_sessions)
        async for event in runtime.resume_interrupt(approval, decisions):
            yield event

    def _stream_runtime_from_context(self, context: dict[str, Any]) -> AgentStreamRuntime:
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME)
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        return self.get_stream_runtime("single", provider_id or None, model or None, workspace, referenced_sessions=referenced_sessions)

    async def rerun_stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
        provider_id: str | None = None, model: str | None = None, referenced_sessions: set[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Reset the session checkpoint and re-run the agent from full history."""
        self.forget_runtime_checkpoint(session_id)
        context = {"provider_id": provider_id or "", "model": model or "", "referenced_sessions": list(referenced_sessions or [])}
        runtime = self._stream_runtime_from_context(context)
        async for event in runtime.stream_rerun(messages, session_id, language, work_mode, autonomy):
            yield event
