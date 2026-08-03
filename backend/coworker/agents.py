import json
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Literal, TypedDict

from pydantic import BaseModel, Field

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
MAX_ATTACHMENT_CHARS = 120_000


@dataclass(frozen=True)
class AgentReply:
    content: str
    mode: AgentMode
    provider: str


class SearchFilesArgs(BaseModel):
    query: str = Field(description="Text to search for in UTF-8 workspace files.")
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


class CoworkerExecutionState(TypedDict, total=False):
    messages: list[dict[str, str]]
    language: str
    work_mode: str
    access_mode: str
    plan: str
    draft: str
    verification: str
    final: str


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
        "run_name": "coworker_multi_stage_agent_stream" if streaming else "coworker_multi_stage_agent",
        "tags": [
            "coworker",
            "single-agent",
            "multi-stage",
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
        result = workspace.search_text(
            query,
            path,
            max_results,
        )
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
        result = workspace.replace_text(
            file_path,
            old_text,
            new_text,
            replace_all,
            audit_context,
        )
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
        result = workspace.run_command(
            command,
            cwd,
            timeout_seconds,
            audit_context,
            approval_store,
            approval_store is not None,
        )
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
                current_interrupt_id,
                0,
                "command",
                command,
                cwd,
                timeout_seconds,
                {
                    **context,
                    "source": "agent_langgraph_hitl",
                    "interrupt_id": current_interrupt_id,
                    "action_index": 0,
                    "hitl_request": value,
                },
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
                current_interrupt_id,
                action_index,
                kind,
                command,
                cwd,
                timeout_seconds,
                {
                    **context,
                    "source": "agent_langgraph_hitl",
                    "interrupt_id": current_interrupt_id,
                    "action_index": action_index,
                    "action_count": len(actions),
                    "tool_name": str(action.get("name") or ""),
                    "action_args": args,
                    "hitl_request": value,
                },
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
            **base,
            "type": "question_required",
            "question": str(args.get("question") or ""),
            "header": str(args.get("header") or ""),
            "options": options,
            "multiple": bool(args.get("multiple")),
        }
    return {
        **base,
        "type": "approval_required",
        "command": approval.get("command", []),
        "cwd": approval.get("cwd", ""),
    }


def stage_event(name: str, status: str = "done") -> dict[str, Any]:
    return {"type": "stage", "name": name, "status": status}


def trace_context(
    *,
    session_id: str,
    provider: str,
    provider_id: str,
    model: str,
    language: Language,
    work_mode: WorkMode,
    access_mode: AccessMode,
    streaming: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "provider": provider,
        "provider_id": provider_id,
        "model": model,
        "language": language,
        "work_mode": work_mode,
        "access_mode": access_mode,
        "streaming": streaming,
    }


def coerce_message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


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
            prepared[index] = {
                **prepared[index],
                "content": f"{instruction}\n\n{prepared[index]['content']}",
            }
            return prepared

    return [{"role": "user", "content": instruction}]


async def invoke_stage_model(llm: Any, system_prompt: str, messages: list[dict[str, str]]) -> str:
    response = await llm.ainvoke([{"role": "system", "content": system_prompt}, *messages])
    return coerce_message_content(response)


def invoke_stage_model_sync(llm: Any, system_prompt: str, messages: list[dict[str, str]]) -> str:
    response = llm.invoke([{"role": "system", "content": system_prompt}, *messages])
    return coerce_message_content(response)


def build_langchain_agent(
    llm: Any,
    tools: list[Any],
    checkpointer: Any | None = None,
    access_mode: AccessMode = "default",
    approval_store: CommandApprovalStore | None = None,
) -> Any:
    from langchain.agents import create_agent

    kwargs: dict[str, Any] = {
        "model": llm,
        "tools": tools,
        "system_prompt": SYSTEM_PROMPT,
        "middleware": command_approval_middleware(access_mode, approval_store),
        "name": "coworker_single_agent",
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_agent(**kwargs)


def build_coworker_execution_graph(
    llm: Any,
    tools: list[Any],
    access_mode: AccessMode = "default",
    approval_store: CommandApprovalStore | None = None,
) -> Any:
    from langgraph.graph import END, START, StateGraph

    async def planner(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            "You are the planner stage inside Coworker. Create a concise internal plan for the executor. "
            "Do not use tools. Mention likely files, checks, and risks when relevant."
        )
        plan = await invoke_stage_model(llm, prompt, state.get("messages", []))
        return {"plan": plan}

    async def executor(state: CoworkerExecutionState) -> dict[str, str]:
        plan = state.get("plan", "")
        messages: list[dict[str, str]] = []
        for message in state.get("messages", []):
            role = str(message.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content") or "")
            if role == "user" and not messages:
                content = (
                    "Executor stage. Use this internal plan as guidance, but prioritize the user's request and current tool results.\n\n"
                    f"{plan}\n\n---\n\n{content}"
                )
            messages.append({"role": role, "content": content})
        if not messages:
            messages = [
                {
                    "role": "user",
                    "content": (
                        "Executor stage. Use this internal plan as guidance, but prioritize the user's request and current tool results.\n\n"
                        f"{plan}"
                    ),
                }
            ]
        agent = build_langchain_agent(llm, tools, access_mode=access_mode, approval_store=approval_store)
        result = await agent.ainvoke({"messages": messages})
        agent_messages = result.get("messages", []) if isinstance(result, dict) else []
        draft = coerce_message_content(agent_messages[-1]) if agent_messages else ""
        return {"draft": draft}

    async def verifier(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            "You are the verifier stage inside Coworker. Review the executor draft for correctness, safety, "
            "missing verification, and whether it respected Plan/Build and access limits. Keep this internal and concise."
        )
        messages = [
            *state.get("messages", []),
            {"role": "assistant", "content": state.get("draft", "")},
        ]
        verification = await invoke_stage_model(llm, prompt, messages)
        return {"verification": verification}

    async def summarizer(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            f"You are the summarizer stage inside Coworker. Reply in {language_name(normalize_language(state.get('language')))}. "
            "Produce the final user-facing answer from the executor draft and verifier notes. "
            "Be concise, truthful about validation, and do not expose hidden chain-of-thought.\n\n"
            f"Verifier notes:\n{state.get('verification', '')}"
        )
        messages = [
            *state.get("messages", []),
            {"role": "assistant", "content": f"Executor draft:\n{state.get('draft', '')}"},
        ]
        final = await invoke_stage_model(llm, prompt, messages)
        return {"final": final}

    builder = StateGraph(CoworkerExecutionState)
    builder.add_node("planner", planner)
    builder.add_node("executor", executor)
    builder.add_node("verifier", verifier)
    builder.add_node("summarizer", summarizer)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "executor")
    builder.add_edge("executor", "verifier")
    builder.add_edge("verifier", "summarizer")
    builder.add_edge("summarizer", END)
    return builder


def build_coworker_execution_graph_sync(
    llm: Any,
    tools: list[Any],
    access_mode: AccessMode = "default",
    approval_store: CommandApprovalStore | None = None,
) -> Any:
    from langgraph.graph import END, START, StateGraph

    def planner(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            "You are the planner stage inside Coworker. Create a concise internal plan for the executor. "
            "Do not use tools. Mention likely files, checks, and risks when relevant."
        )
        plan = invoke_stage_model_sync(llm, prompt, state.get("messages", []))
        return {"plan": plan}

    def executor(state: CoworkerExecutionState) -> dict[str, str]:
        plan = state.get("plan", "")
        messages: list[dict[str, str]] = []
        for message in state.get("messages", []):
            role = str(message.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content") or "")
            if role == "user" and not messages:
                content = (
                    "Executor stage. Use this internal plan as guidance, but prioritize the user's request and current tool results.\n\n"
                    f"{plan}\n\n---\n\n{content}"
                )
            messages.append({"role": role, "content": content})
        if not messages:
            messages = [
                {
                    "role": "user",
                    "content": (
                        "Executor stage. Use this internal plan as guidance, but prioritize the user's request and current tool results.\n\n"
                        f"{plan}"
                    ),
                }
            ]
        agent = build_langchain_agent(llm, tools, access_mode=access_mode, approval_store=approval_store)
        result = agent.invoke({"messages": messages})
        agent_messages = result.get("messages", []) if isinstance(result, dict) else []
        draft = coerce_message_content(agent_messages[-1]) if agent_messages else ""
        return {"draft": draft}

    def verifier(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            "You are the verifier stage inside Coworker. Review the executor draft for correctness, safety, "
            "missing verification, and whether it respected Plan/Build and access limits. Keep this internal and concise."
        )
        messages = [
            *state.get("messages", []),
            {"role": "assistant", "content": state.get("draft", "")},
        ]
        verification = invoke_stage_model_sync(llm, prompt, messages)
        return {"verification": verification}

    def summarizer(state: CoworkerExecutionState) -> dict[str, str]:
        prompt = (
            f"You are the summarizer stage inside Coworker. Reply in {language_name(normalize_language(state.get('language')))}. "
            "Produce the final user-facing answer from the executor draft and verifier notes. "
            "Be concise, truthful about validation, and do not expose hidden chain-of-thought.\n\n"
            f"Verifier notes:\n{state.get('verification', '')}"
        )
        messages = [
            *state.get("messages", []),
            {"role": "assistant", "content": f"Executor draft:\n{state.get('draft', '')}"},
        ]
        final = invoke_stage_model_sync(llm, prompt, messages)
        return {"final": final}

    builder = StateGraph(CoworkerExecutionState)
    builder.add_node("planner", planner)
    builder.add_node("executor", executor)
    builder.add_node("verifier", verifier)
    builder.add_node("summarizer", summarizer)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "executor")
    builder.add_edge("executor", "verifier")
    builder.add_edge("verifier", "summarizer")
    builder.add_edge("summarizer", END)
    return builder


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


class SimulatedSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace):
        self.settings = settings
        self.workspace = workspace

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n"
                f"会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n"
                f"你说：{message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\n"
                f"Session: {session_id}\n\n"
                f"Mode: {work_mode} / {access_mode}\n\n"
                f"You said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(
        self,
        workspace: Workspace,
        approval_store: CommandApprovalStore,
        trace_store: AgentTraceStore,
        checkpointer: Any,
        provider: ProviderEntry,
        model_override: str | None = None,
    ):
        from langchain_openai import ChatOpenAI

        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = ChatOpenAI(
            model=self.model_name,
            temperature=0,
            api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
            base_url=self.openai_compatible_base_url(provider),
        )
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpointer = checkpointer

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        audit_context = {
            "session_id": session_id,
            "provider": self.provider_name,
            "provider_id": self.provider_id,
            "model": self.model_name,
            "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id,
            provider=self.provider_name,
            provider_id=self.provider_id,
            model=self.model_name,
            language=language,
            work_mode=work_mode,
            access_mode=access_mode,
            streaming=False,
        )
        self.trace_store.record("run", "start", current_trace_context)
        effective_access = access_mode if work_mode == "build" else "default"
        graph = build_coworker_execution_graph_sync(
            self.llm,
            build_workspace_tools(self.workspace, work_mode == "build", audit_context),
            access_mode=effective_access,
            approval_store=self.approval_store,
        ).compile(checkpointer=self.checkpointer)
        try:
            result = graph.invoke(
                {
                    "messages": prepare_agent_messages([{"role": "user", "content": message}], language, work_mode, access_mode),
                    "language": language,
                    "work_mode": work_mode,
                    "access_mode": access_mode,
                },
                config=agent_run_config(
                    session_id=session_id,
                    provider=self.provider_name,
                    model=self.model_name,
                    language=language,
                    work_mode=work_mode,
                    access_mode=access_mode,
                    streaming=False,
                ),
            )
        except Exception as exc:
            self.trace_store.record("run", "error", current_trace_context, {"error": str(exc)[:400]})
            raise
        if "__interrupt__" in result:
            approvals = record_runtime_interrupts(
                result["__interrupt__"],
                self.approval_store,
                {
                    **audit_context,
                    "language": language,
                    "work_mode": work_mode,
                    "access_mode": access_mode,
                },
            )
            self.trace_store.record("interrupt", "pending", current_trace_context, {"approval_ids": [approval.get("id", "") for approval in approvals]})
            approval_ids = ", ".join(str(approval.get("id", "")) for approval in approvals)
            content = f"Command approval required: {approval_ids}" if language == "en" else f"命令需要审批：{approval_ids}"
            return AgentReply(content=content, mode=self.mode, provider=self.provider_name)
        content = result.get("final", "") if isinstance(result, dict) else ""
        self.trace_store.record("run", "done", current_trace_context, {"content_chars": len(str(content))})
        return AgentReply(content=str(content), mode=self.mode, provider=self.provider_name)

    @staticmethod
    def openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url


class OpenAICompatibleStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(
        self,
        workspace: Workspace,
        approval_store: CommandApprovalStore,
        trace_store: AgentTraceStore,
        checkpoint_path: Path,
        provider: ProviderEntry,
        model_override: str | None = None,
    ):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model_override or provider.model,
            temperature=0,
            api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
            base_url=self.openai_compatible_base_url(provider),
        )
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpoint_path = checkpoint_path

    async def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        access_mode: AccessMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        audit_context = {
            "session_id": session_id,
            "provider": self.provider_name,
            "provider_id": self.provider_id,
            "model": self.model_name,
            "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id,
            provider=self.provider_name,
            provider_id=self.provider_id,
            model=self.model_name,
            language=language,
            work_mode=work_mode,
            access_mode=access_mode,
            streaming=True,
        )
        interrupt_context = {
            **audit_context,
            "language": language,
            "work_mode": work_mode,
            "access_mode": access_mode,
        }
        self.trace_store.record("run", "start", current_trace_context)
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}
        prepared_messages = prepare_agent_messages(messages, language, work_mode, access_mode)
        inputs: CoworkerExecutionState = {
            "messages": prepared_messages,
            "language": language,
            "work_mode": work_mode,
            "access_mode": access_mode,
        }
        content_parts: list[str] = []
        effective_access = access_mode if work_mode == "build" else "default"
        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            graph = build_coworker_execution_graph(
                self.llm,
                build_workspace_tools(self.workspace, work_mode == "build", audit_context),
                access_mode=effective_access,
                approval_store=self.approval_store,
            ).compile(checkpointer=checkpointer)
            try:
                async for chunk in graph.astream(
                    inputs,
                    config=agent_run_config(
                        session_id=session_id,
                        provider=self.provider_name,
                        model=self.model_name,
                        language=language,
                        work_mode=work_mode,
                        access_mode=access_mode,
                        streaming=True,
                    ),
                    stream_mode="updates",
                ):
                    if "__interrupt__" in chunk:
                        approvals = record_runtime_interrupts(
                            chunk["__interrupt__"],
                            self.approval_store,
                            interrupt_context,
                        )
                        self.trace_store.record("interrupt", "pending", current_trace_context, {"approval_ids": [approval.get("id", "") for approval in approvals]})
                        for approval in approvals:
                            yield stream_event_from_interrupt(approval)
                        return
                    for node_name, update in chunk.items():
                        if not isinstance(update, dict):
                            continue
                        self.trace_store.record("stage", "done", current_trace_context, {"stage": node_name})
                        yield stage_event(node_name)
                        if node_name == "summarizer":
                            final = str(update.get("final") or "")
                            if final:
                                yield {"type": "delta", "content": final}
                                content_parts.append(final)
            except Exception as exc:
                self.trace_store.record("run", "error", current_trace_context, {"error": str(exc)[:400]})
                raise
        self.trace_store.record("run", "done", current_trace_context, {"content_chars": len("".join(content_parts))})
        yield {"type": "done", "content": "".join(content_parts), "mode": self.mode, "provider": self.provider_name, "model": self.model_name}

    async def resume_interrupt(
        self,
        approval: dict[str, Any],
        decisions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.types import Command

        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        language = normalize_language(context.get("language"))
        work_mode = normalize_work_mode(str(context.get("work_mode") or "build"))
        access_mode = normalize_access_mode(str(context.get("access_mode") or "default"))
        audit_context = {
            "session_id": session_id,
            "provider": self.provider_name,
            "provider_id": self.provider_id,
            "model": self.model_name,
            "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id,
            provider=self.provider_name,
            provider_id=self.provider_id,
            model=self.model_name,
            language=language,
            work_mode=work_mode,
            access_mode=access_mode,
            streaming=True,
        )
        content_parts: list[str] = []
        events: list[dict[str, Any]] = []
        decision_types = ", ".join(str(item.get("type")) for item in decisions)
        self.trace_store.record("interrupt", "resolved", current_trace_context, {"approval_id": approval.get("id", ""), "decisions": decision_types})
        effective_access = access_mode if work_mode == "build" else "default"
        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
            graph = build_coworker_execution_graph(
                self.llm,
                build_workspace_tools(self.workspace, work_mode == "build", audit_context),
                access_mode=effective_access,
                approval_store=self.approval_store,
            ).compile(checkpointer=checkpointer)
            try:
                async for chunk in graph.astream(
                    Command(resume={"decisions": decisions}),
                    config=agent_run_config(
                        session_id=session_id,
                        provider=self.provider_name,
                        model=self.model_name,
                        language=language,
                        work_mode=work_mode,
                        access_mode=access_mode,
                        streaming=True,
                    ),
                    stream_mode="updates",
                ):
                    if "__interrupt__" in chunk:
                        approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, context)
                        self.trace_store.record("interrupt", "pending", current_trace_context, {"approval_ids": [approval.get("id", "") for approval in approvals]})
                        events.extend(stream_event_from_interrupt(item) for item in approvals)
                        continue
                    for node_name, update in chunk.items():
                        if not isinstance(update, dict):
                            continue
                        self.trace_store.record("stage", "done", current_trace_context, {"stage": node_name, "resumed": True})
                        events.append(stage_event(node_name))
                        if node_name == "summarizer":
                            final = str(update.get("final") or "")
                            if final:
                                content_parts.append(final)
                                events.append({"type": "delta", "content": final})
            except Exception as exc:
                self.trace_store.record("run", "error", current_trace_context, {"error": str(exc)[:400], "resumed": True})
                raise
        self.trace_store.record("run", "done", current_trace_context, {"content_chars": len("".join(content_parts)), "resumed": True})
        events.append({"type": "done", "content": "".join(content_parts), "mode": self.mode, "provider": self.provider_name, "model": self.model_name})
        return events

    @staticmethod
    def openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace):
        self.settings = settings
        self.workspace = workspace

    async def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        access_mode: AccessMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        user_message = messages[-1]["content"] if messages else ""
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n"
                f"会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n"
                f"你说：{user_message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\n"
                f"Session: {session_id}\n\n"
                f"Mode: {work_mode} / {access_mode}\n\n"
                f"You said: {user_message}"
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
            env_provider = ProviderEntry(
                id="env-openai",
                name="Environment OpenAI",
                provider_type="openai",
                base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                model=self.settings.openai_model,
                enabled=True,
            )
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
            provider = ProviderEntry(
                id="env-openai",
                name="Environment OpenAI",
                provider_type="openai",
                base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                model=self.settings.openai_model,
                enabled=True,
            )
        if not provider:
            if self.settings.agent_provider == "simulated":
                return SimulatedStreamRuntime(self.settings, selected_workspace)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            return OpenAICompatibleStreamRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpoint_path, provider, model)
        raise RuntimeError(f"Unsupported agent mode: {mode}")

    def list_agent_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.trace_store.list(limit)

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        if context.get("source") != "agent_langgraph_hitl":
            return []
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        workspace_path = str(context.get("workspace_path") or "")
        workspace = Workspace(Path(workspace_path), self.settings.data_dir / TOOL_AUDIT_FILENAME) if workspace_path else None
        runtime = self.get_stream_runtime("single", provider_id or None, model or None, workspace)
        if not isinstance(runtime, OpenAICompatibleStreamRuntime):
            raise RuntimeError("Only OpenAI-compatible LangGraph sessions can be resumed")
        return await runtime.resume_interrupt(approval, decisions)
