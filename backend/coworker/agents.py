import json
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Literal, TypedDict

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessageChunk, SystemMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, Runtime

from .config import BackendSettings
from .providers import ProviderEntry, ProviderManager
from .traces import AGENT_TRACE_FILENAME, AgentTraceStore
from .workspace import COMMAND_APPROVAL_FILENAME, TOOL_AUDIT_FILENAME, CommandApprovalStore, Workspace

AgentMode = Literal["single"]
Language = Literal["zh", "en"]
WorkMode = Literal["plan", "build"]
AccessMode = Literal["default", "full"]

SYSTEM_PROMPT = (
    "You are Coworker, a local coding assistant. "
    "Use workspace tools only when they are needed and keep answers concise."
)
TITLE_SYSTEM_PROMPT = (
    "You are a thread title generator. Output ONLY the title string. Nothing else. No code fences, no quotes, no explanation."
    "Rules:"
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

PLAN_MARKER = "[CW-PLAN]"


class CoworkerAgentState(AgentState[Any]):
    work_mode: str
    language: str


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


class AgentRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    @abstractmethod
    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        raise NotImplementedError


class AgentStreamRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        access_mode: AccessMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError


def language_name(language: Language) -> str:
    return "Chinese" if language == "zh" else "English"


def normalize_work_mode(work_mode: str | None) -> WorkMode:
    return "plan" if work_mode == "plan" else "build"


def normalize_access_mode(access_mode: str | None) -> AccessMode:
    return "full" if access_mode == "full" else "default"


def normalize_language(language: Any) -> Language:
    return "en" if language == "en" else "zh"


def runtime_instruction(work_mode: WorkMode, access_mode: AccessMode) -> str:
    if work_mode == "plan":
        return "Current mode: plan. Do not modify files. Inspect context if needed, then return a concise implementation plan."
    if access_mode == "full":
        return "Current mode: build with full access. You may read and write workspace files when needed."
    return "Current mode: build with default access. Read workspace files when needed, but do not modify files."


def agent_run_config(
    *,
    session_id: str,
    provider: str,
    model: str,
    language: Language,
    work_mode: WorkMode,
    access_mode: AccessMode,
    streaming: bool,
) -> dict[str, Any]:
    return {
        "run_name": "coworker_agent" + ("_stream" if streaming else ""),
        "tags": [
            "coworker",
            "agent",
            f"work:{work_mode}",
            f"access:{access_mode}",
            "streaming" if streaming else "non-streaming",
        ],
        "metadata": {
            "coworker.session_id": session_id,
            "coworker.provider": provider,
            "coworker.model": model,
            "coworker.language": language,
            "coworker.work_mode": work_mode,
            "coworker.access_mode": access_mode,
            "coworker.streaming": streaming,
        },
        "configurable": {
            "thread_id": session_id,
        },
    }


def build_workspace_tools(
    workspace: Workspace,
    writable: bool,
    audit_context: dict[str, Any] | None = None,
    approval_store: CommandApprovalStore | None = None,
) -> list[Any]:
    from langchain_core.tools import tool

    @tool(args_schema=SearchFilesArgs)
    def search_files(query: str, path: str = "", max_results: int = 80) -> str:
        """Search UTF-8 workspace text files."""
        result = workspace.search_text(query, path, max_results)
        return json.dumps(result, ensure_ascii=False)

    @tool(args_schema=ReadFileArgs)
    def read_file(file_path: str) -> str:
        """Read a UTF-8 text file from the configured workspace."""
        return workspace.read_text(file_path)

    @tool(args_schema=WriteFileArgs)
    def write_file(file_path: str, content: str) -> str:
        """Write a full UTF-8 text file."""
        workspace.write_text(file_path, content, audit_context)
        return f"Wrote {file_path}"

    @tool(args_schema=ReplaceInFileArgs)
    def replace_in_file(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        """Replace exact text in a UTF-8 workspace file."""
        result = workspace.replace_text(file_path, old_text, new_text, replace_all, audit_context)
        return json.dumps(result, ensure_ascii=False)

    @tool(args_schema=ApplyTextEditsArgs)
    def apply_text_edits(file_path: str, edits: list[TextEditArgs]) -> str:
        """Apply multiple exact text edits to one UTF-8 workspace file atomically."""
        result = workspace.apply_text_edits(
            file_path,
            [edit.model_dump() if isinstance(edit, TextEditArgs) else edit for edit in edits],
            audit_context,
        )
        return json.dumps(result, ensure_ascii=False)

    @tool(args_schema=RunCommandArgs)
    def run_command(command: list[str], cwd: str = "", timeout_seconds: int = 20) -> str:
        """Run an allowlisted command in the workspace after runtime policy approval."""
        result = workspace.run_command(command, cwd, timeout_seconds, audit_context, approval_store, approval_store is not None)
        return json.dumps(result, ensure_ascii=False)

    @tool(args_schema=AskUserArgs)
    def ask_user(question: str, options: list[dict[str, str]], multiple: bool = False, header: str = "") -> str:
        """Ask the user a question with selectable options when you need a decision or clarification."""
        result = {
            "question": question,
            "options": options,
            "multiple": multiple,
            "header": header,
            "status": "awaiting_user",
        }
        return json.dumps(result, ensure_ascii=False)

    tools = [search_files, read_file, ask_user]
    if writable:
        tools.extend([replace_in_file, apply_text_edits, write_file, run_command])
    return tools


_WRITE_TOOL_NAMES = {"write_file", "replace_in_file", "apply_text_edits", "run_command"}


def _command_digest_from_tool_call(tool_call: Any) -> str:
    args = tool_call.get("args") if isinstance(tool_call, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    cwd = str(args.get("cwd") or "") if isinstance(args, dict) else ""
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return Workspace.command_digest(command, cwd)
    return ""


def command_approval_middleware(
    access_mode: AccessMode,
    approval_store: CommandApprovalStore | None = None,
) -> list[Any]:
    from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware

    if access_mode == "full":
        return []

    def _needs_approval(req: Any) -> bool:
        if approval_store is None:
            return True
        tool_call = getattr(req, "tool_call", None)
        digest = _command_digest_from_tool_call(tool_call)
        if digest and approval_store.is_always_allowed(digest):
            return False
        return True

    return [
        HumanInTheLoopMiddleware(
            interrupt_on={
                "run_command": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": "Coworker needs approval before running this workspace command.",
                    "when": _needs_approval,
                },
                "ask_user": {
                    "allowed_decisions": ["respond", "reject"],
                    "description": "Coworker asks the user a question that needs an answer.",
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
    return "question" if name == "ask_user" else "command"


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
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": 0, "hitl_request": value},
            )
            approvals.append(approval)
            continue
        for action_index, action in enumerate(actions):
            args = action.get("args") if isinstance(action, dict) else {}
            args = args if isinstance(args, dict) else {}
            kind = interrupt_action_kind(action)
            if kind == "question":
                command, cwd, timeout_seconds = [], "", 20
            else:
                command, cwd, timeout_seconds = interrupt_command_details({"action_requests": [action]})
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, action_index, kind, command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": action_index, "action_count": len(actions), "tool_name": str(action.get("name") or ""), "action_args": args, "hitl_request": value},
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
    return {**base, "type": "approval_required", "command": approval.get("command", []), "cwd": approval.get("cwd", "")}


def trace_context(
    *, session_id: str, provider: str, provider_id: str, model: str,
    language: Language, work_mode: WorkMode, access_mode: AccessMode, streaming: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id, "provider": provider, "provider_id": provider_id, "model": model,
        "language": language, "work_mode": work_mode, "access_mode": access_mode, "streaming": streaming,
    }


def coerce_message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


def _extract_reasoning_from_chunk(chunk: Any) -> str | None:
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    raw = additional_kwargs.get("reasoning")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


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
        else:
            merged.append(part)
    return merged


def generate_title(user_message: str) -> str:
    from langchain_openai import ChatOpenAI
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
                {"role": "user", "content": user_message},
            ])
            title = coerce_message_content(response).strip().strip('"').strip("'")
            if title and 3 <= len(title) <= 50:
                return title
    except Exception:
        pass
    return _default_title_from_message(user_message)


def _default_title_from_message(user_message: str) -> str:
    text = user_message.strip()
    if len(text) <= 40:
        return text
    return text[:40].rstrip()[:40]


def prepare_agent_messages(
    messages: list[dict[str, Any]],
    language: Language,
    work_mode: WorkMode,
    access_mode: AccessMode,
) -> list[dict[str, str]]:
    prepared: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content")
        if role not in {"user", "assistant", "system"} or content is None:
            continue
        prepared.append({"role": role, "content": str(content)})
    instruction = f"Reply in {language_name(language)}.\n{runtime_instruction(work_mode, access_mode)}"
    for index in range(len(prepared) - 1, -1, -1):
        if prepared[index]["role"] == "user":
            prepared[index] = {**prepared[index], "content": f"{instruction}\n\n{prepared[index]['content']}"}
            return prepared
    return [{"role": "user", "content": instruction}]


def format_user_message(message: str, attachments: list[dict[str, Any]] | None = None) -> str:
    if not attachments:
        return message
    parts = [message, "\n\nAttachments:"]
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
# PlanGateMiddleware – enforces plan‑first semantics before the agent acts.
# ---------------------------------------------------------------------------

class PlanGateMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """In plan mode, runs a deterministic planner LLM pass and injects the
    result as a ``SystemMessage`` marked with ``[CW-PLAN]`` into the agent
    state before the first model call of a turn.

    In build mode the middleware is a no‑op (codex‑style).

    Also gates write/execute tools in plan mode via ``wrap_tool_call``.
    """

    def __init__(self, planner_llm: Any, language: Language = "en"):
        self.planner_llm = planner_llm
        self.language = language

    def _is_plan_mode(self, state: CoworkerAgentState) -> bool:
        return str(state.get("work_mode", "build")) == "plan"

    def _already_planned(self, state: CoworkerAgentState) -> bool:
        messages = state.get("messages", [])
        for msg in messages:
            content = getattr(msg, "content", "") or ""
            if isinstance(content, str) and PLAN_MARKER in content:
                return True
        return False

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if not self._is_plan_mode(state):
            return None
        if self._already_planned(state):
            return None

        language = str(state.get("language", "en"))
        lang_name = "Chinese" if language == "zh" else "English"
        prompt = (
            f"You are the planner stage inside Coworker. Create a concise internal plan "
            f"for the upcoming task. Reply in {lang_name}. "
            "Mention likely files, approach, checks, and risks when relevant. "
            "Do not use tools. Output only the plan text."
        )

        user_message = ""
        for msg in state.get("messages", []):
            if getattr(msg, "type", None) == "human":
                user_message = getattr(msg, "content", "") or ""
                break

        plan_text = ""
        try:
            response = await self.planner_llm.ainvoke([
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message or "Analyze the task and create a plan."},
            ])
            plan_text = coerce_message_content(response).strip()
        except Exception:
            plan_text = "Plan could not be generated. Proceed with best-effort analysis."

        if plan_text:
            stream_writer = getattr(runtime, "stream_writer", None)
            if stream_writer is not None:
                stream_writer({"type": "plan_start"})
                stream_writer({"type": "plan_delta", "content": plan_text})
                stream_writer({"type": "plan_end", "content": plan_text})

        marker_msg = SystemMessage(content=f"{PLAN_MARKER}\n{plan_text}")
        return {"messages": [marker_msg]}

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if not self._is_plan_mode(state):
            return None
        if self._already_planned(state):
            return None

        language = str(state.get("language", "en"))
        lang_name = "Chinese" if language == "zh" else "English"
        prompt = (
            f"You are the planner stage inside Coworker. Create a concise internal plan "
            f"for the upcoming task. Reply in {lang_name}. "
            "Mention likely files, approach, checks, and risks when relevant. "
            "Do not use tools. Output only the plan text."
        )

        user_message = ""
        for msg in state.get("messages", []):
            if getattr(msg, "type", None) == "human":
                user_message = getattr(msg, "content", "") or ""
                break

        plan_text = ""
        try:
            response = self.planner_llm.invoke([
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message or "Analyze the task and create a plan."},
            ])
            plan_text = coerce_message_content(response).strip()
        except Exception:
            plan_text = "Plan could not be generated. Proceed with best-effort analysis."

        if plan_text:
            stream_writer = getattr(runtime, "stream_writer", None)
            if stream_writer is not None:
                stream_writer({"type": "plan_start"})
                stream_writer({"type": "plan_delta", "content": plan_text})
                stream_writer({"type": "plan_end", "content": plan_text})

        marker_msg = SystemMessage(content=f"{PLAN_MARKER}\n{plan_text}")
        return {"messages": [marker_msg]}

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = getattr(request, "tool_name", "") or ""
        if tool_name in _WRITE_TOOL_NAMES:
            from langchain_core.messages import ToolMessage
            tc_id = request.tool_call.get("id", "unknown")
            return ToolMessage(
                content=f"Tool '{tool_name}' is not available in plan mode. Use read-only tools only.",
                tool_call_id=tc_id,
            )
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = getattr(request, "tool_name", "") or ""
        if tool_name in _WRITE_TOOL_NAMES:
            from langchain_core.messages import ToolMessage
            tc_id = request.tool_call.get("id", "unknown")
            return ToolMessage(
                content=f"Tool '{tool_name}' is not available in plan mode. Use read-only tools only.",
                tool_call_id=tc_id,
            )
        return await handler(request)


# ---------------------------------------------------------------------------
# Agent builder – single create_agent graph (official langchain idiom).
# ---------------------------------------------------------------------------

def build_coworker_agent_graph(
    llm: Any,
    tools: list[Any],
    work_mode: WorkMode,
    language: Language,
    access_mode: AccessMode = "default",
    checkpointer: Any | None = None,
    approval_store: CommandApprovalStore | None = None,
) -> Any:
    """Compile the Coworker agent as a single ``create_agent`` graph.

    The graph always includes ``HumanInTheLoopMiddleware`` (when
    access_mode != full). In *plan* mode it also includes a
    ``PlanGateMiddleware`` that runs a deterministic planning step.
    """
    from langchain.agents import create_agent

    middleware: list[Any] = []

    if work_mode == "plan":
        middleware.append(PlanGateMiddleware(llm, language))

    middleware.extend(command_approval_middleware(access_mode, approval_store))

    system_prompt = (
        f"Reply in {language_name(language)}.\n"
        f"{runtime_instruction(work_mode, access_mode)}"
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

    def __init__(self, settings: BackendSettings, workspace: Workspace):
        self.settings = settings
        self.workspace = workspace

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n你说：{message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {access_mode}\n\nYou said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpointer: Any, provider: ProviderEntry, model_override: str | None = None):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpointer = checkpointer

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, access_mode=access_mode, streaming=False,
        )
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "run"})
        effective_access = access_mode if work_mode == "build" else "default"
        graph = build_coworker_agent_graph(
            self.llm,
            build_workspace_tools(self.workspace, work_mode == "build", audit_context),
            work_mode=work_mode,
            language=language,
            access_mode=effective_access,
            checkpointer=self.checkpointer,
            approval_store=self.approval_store,
        )
        try:
            result = graph.invoke(
                {"messages": prepare_agent_messages([{"role": "user", "content": message}], language, work_mode, access_mode), "work_mode": work_mode, "language": language},
                config=agent_run_config(
                    session_id=session_id, provider=self.provider_name, model=self.model_name,
                    language=language, work_mode=work_mode, access_mode=access_mode, streaming=False,
                ),
            )
        except Exception as exc:
            self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
            raise
        if "__interrupt__" in result:
            approvals = record_runtime_interrupts(
                result["__interrupt__"], self.approval_store,
                {**audit_context, "language": language, "work_mode": work_mode, "access_mode": access_mode},
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

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpoint_path: Path, provider: ProviderEntry, model_override: str | None = None):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpoint_path = checkpoint_path

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    async def stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, access_mode=access_mode, streaming=True,
        )
        interrupt_context = {**audit_context, "language": language, "work_mode": work_mode, "access_mode": access_mode}
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "stream"})
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}

        prepared_messages = prepare_agent_messages(messages, language, work_mode, access_mode)
        effective_access = access_mode if work_mode == "build" else "default"

        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(self.workspace, work_mode == "build", audit_context),
                work_mode=work_mode, language=language, access_mode=effective_access,
                checkpointer=checkpointer, approval_store=self.approval_store,
            )

            inputs = {"messages": prepared_messages, "work_mode": work_mode, "language": language}
            config = agent_run_config(
                session_id=session_id, provider=self.provider_name, model=self.model_name,
                language=language, work_mode=work_mode, access_mode=access_mode, streaming=True,
            )

            content_parts: list[str] = []
            tool_state: dict[str, dict[str, Any]] = {}
            parts: list[dict[str, Any]] = []

            try:
                async for stream_mode, chunk in graph.astream(inputs, config=config, stream_mode=["messages", "custom", "updates"]):
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts):
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
                                yield stream_event_from_interrupt(approval)
                            return
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
                raise

        final_content = "".join(content_parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content)})
        merged_parts = _merge_event_parts(parts)
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": merged_parts}

    def _handle_message_chunk(
        self, msg: Any, content_parts: list[str], tool_state: dict[str, dict[str, Any]], parts: list[dict[str, Any]],
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

                if tc_id not in tool_state:
                    tool_state[tc_id] = {"name": tc_name or "", "input": "", "status": "running"}
                    parts.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                    events.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                else:
                    tool_state[tc_id]["input"] = tool_state[tc_id].get("input", "") + tc_args
                    if tc_name:
                        tool_state[tc_id]["name"] = tc_name
                    part = {"type": "tool_delta", "id": tc_id, "input": tc_args}
                    parts.append(part)
                    events.append(part)

        elif isinstance(msg, ToolMessage):
            tc_id = getattr(msg, "tool_call_id", "") or ""
            content = getattr(msg, "content", "") or ""
            if tc_id in tool_state:
                tool_state[tc_id]["status"] = "success"
                tool_state[tc_id]["output"] = str(content)[:2000]
                part = {"type": "tool_end", "id": tc_id, "output": str(content)[:2000], "status": "success"}
                parts.append(part)
                events.append(part)
            elif tc_id:
                part = {"type": "tool_end", "id": tc_id, "output": str(content)[:2000], "status": "success"}
                parts.append(part)
                events.append(part)

        return events

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.types import Command

        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        language = normalize_language(context.get("language"))
        work_mode = normalize_work_mode(str(context.get("work_mode") or "build"))
        access_mode = normalize_access_mode(str(context.get("access_mode") or "default"))
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, access_mode=access_mode, streaming=True,
        )
        content_parts: list[str] = []
        events: list[dict[str, Any]] = []
        parts: list[dict[str, Any]] = []
        decision_types = ", ".join(str(item.get("type")) for item in decisions)
        self.trace_store.record("agent_activity", "resolved", current_trace_context, {"approval_id": approval.get("id", ""), "decisions": decision_types})
        effective_access = access_mode if work_mode == "build" else "default"

        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(self.workspace, work_mode == "build", audit_context),
                work_mode=work_mode, language=language, access_mode=effective_access,
                checkpointer=checkpointer, approval_store=self.approval_store,
            )
            config = agent_run_config(
                session_id=session_id, provider=self.provider_name, model=self.model_name,
                language=language, work_mode=work_mode, access_mode=access_mode, streaming=True,
            )
            tool_state: dict[str, dict[str, Any]] = {}
            try:
                async for stream_mode, chunk in graph.astream(Command(resume={"decisions": decisions}), config=config, stream_mode=["messages", "custom", "updates"]):
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts):
                                events.append(event)
                        except GeneratorExit:
                            raise
                        except Exception:
                            pass
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                events.append(chunk)
                    elif stream_mode == "updates":
                        if "__interrupt__" in chunk:
                            approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, context)
                            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals], "resumed": True})
                            events.extend(stream_event_from_interrupt(item) for item in approvals)
                            continue
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400], "resumed": True})
                raise

        final_content = "".join(content_parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content), "resumed": True})
        events.append({"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": _merge_event_parts(parts)})
        return events


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace):
        self.settings = settings
        self.workspace = workspace

    async def stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AsyncGenerator[dict[str, Any], None]:
        user_message = messages[-1]["content"] if messages else ""
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n你说：{user_message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {access_mode}\n\nYou said: {user_message}"
            )
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": "simulated", "model": ""}
        for chunk in content:
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "content": content, "mode": self.mode, "provider": "simulated", "model": ""}


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings):
        from langgraph.checkpoint.sqlite import SqliteSaver

        self.settings = settings
        self.default_workspace = Workspace(settings.workspace_dir, settings.data_dir / TOOL_AUDIT_FILENAME)
        self.approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
        self.trace_store = AgentTraceStore(settings.data_dir / AGENT_TRACE_FILENAME)
        self.provider_manager = ProviderManager(settings.data_dir / "providers.json")
        self.checkpoint_path = settings.data_dir / "runtime_checkpoints.sqlite"
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_conn = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self.checkpoint_conn)

    def has_runtime_checkpoint(self, session_id: str) -> bool:
        return self.checkpointer.get({"configurable": {"thread_id": session_id}}) is not None

    def forget_runtime_checkpoint(self, session_id: str) -> None:
        self.checkpointer.delete_thread(session_id)

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

    def _create_single_agent(self, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None) -> AgentRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if provider:
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, provider)
        if self.settings.agent_provider == "openai":
            env_provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, env_provider)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings, selected_workspace)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None) -> AgentRuntime:
        if mode == "single":
            return self._create_single_agent(provider_id, model, workspace)
        raise RuntimeError(f"Unsupported agent mode: {mode}")

    def get_stream_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None) -> AgentStreamRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if not provider and self.settings.agent_provider == "openai":
            provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
        if not provider:
            if self.settings.agent_provider == "simulated":
                return SimulatedStreamRuntime(self.settings, selected_workspace)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            return OpenAICompatibleStreamRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpoint_path, provider, model)
        raise RuntimeError(f"Unsupported agent mode for streaming: {mode}")
