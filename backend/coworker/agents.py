import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, AsyncGenerator, Literal

from .config import BackendSettings
from .providers import ProviderEntry, ProviderManager
from .workspace import COMMAND_APPROVAL_FILENAME, TOOL_AUDIT_FILENAME, CommandApprovalStore, Workspace

AgentMode = Literal["single"]
Language = Literal["zh", "en"]
WorkMode = Literal["plan", "build"]
AccessMode = Literal["default", "full"]

SYSTEM_PROMPT = (
    "You are Coworker Single Agent MVP, a local coding assistant. "
    "Use workspace tools only when they are needed and keep answers concise."
)
MAX_ATTACHMENT_CHARS = 120_000


@dataclass(frozen=True)
class AgentReply:
    content: str
    mode: AgentMode
    provider: str


class AgentRuntime(ABC):
    mode: AgentMode

    @abstractmethod
    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        raise NotImplementedError


class AgentStreamRuntime(ABC):
    mode: AgentMode

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


def runtime_instruction(work_mode: WorkMode, access_mode: AccessMode) -> str:
    if work_mode == "plan":
        return "Current mode: plan. Do not modify files. Inspect context if needed, then return a concise implementation plan."
    if access_mode == "full":
        return "Current mode: build with full access. You may read and write workspace files when needed."
    return "Current mode: build with default access. Read workspace files when needed, but do not modify files."


def build_workspace_tools(
    workspace: Workspace,
    access_mode: AccessMode,
    audit_context: dict[str, Any] | None = None,
    approval_store: CommandApprovalStore | None = None,
) -> list[Any]:
    def search_files(input_json: str) -> str:
        """Search UTF-8 workspace text files. Input JSON has query, optional path, and optional max_results."""
        payload = json.loads(input_json)
        result = workspace.search_text(
            str(payload["query"]),
            str(payload.get("path") or ""),
            int(payload.get("max_results") or 80),
        )
        return json.dumps(result, ensure_ascii=False)

    def read_file(file_path: str) -> str:
        """Read a UTF-8 text file from the configured workspace."""
        return workspace.read_text(file_path)

    def write_file(input_json: str) -> str:
        """Write a UTF-8 text file. Input JSON has file_path and content."""
        payload = json.loads(input_json)
        workspace.write_text(payload["file_path"], payload["content"], audit_context)
        return f"Wrote {payload['file_path']}"

    def replace_in_file(input_json: str) -> str:
        """Replace exact text in a UTF-8 workspace file. Input JSON has file_path, old_text, new_text, optional replace_all."""
        payload = json.loads(input_json)
        result = workspace.replace_text(
            str(payload["file_path"]),
            str(payload["old_text"]),
            str(payload["new_text"]),
            bool(payload.get("replace_all") or False),
            audit_context,
        )
        return json.dumps(result, ensure_ascii=False)

    def apply_text_edits(input_json: str) -> str:
        """Apply multiple exact text edits to one UTF-8 workspace file atomically. Input JSON has file_path and edits array with old_text, new_text, optional replace_all."""
        payload = json.loads(input_json)
        result = workspace.apply_text_edits(str(payload["file_path"]), payload["edits"], audit_context)
        return json.dumps(result, ensure_ascii=False)

    def run_command(input_json: str) -> str:
        """Run an allowlisted command in the workspace. Input JSON has command array, optional cwd, optional timeout_seconds."""
        payload = json.loads(input_json)
        result = workspace.run_command(
            payload["command"],
            str(payload.get("cwd") or ""),
            int(payload.get("timeout_seconds") or 20),
            audit_context,
            approval_store,
            True,
        )
        return json.dumps(result, ensure_ascii=False)

    tools = [search_files, read_file]
    if access_mode == "full":
        tools.extend([replace_in_file, apply_text_edits, write_file, run_command])
    return tools


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


def message_payload(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    return {
        "content": getattr(message, "content", ""),
        "tool_calls": getattr(message, "tool_calls", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


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

    def __init__(self, settings: BackendSettings):
        self.settings = settings

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        if language == "zh":
            content = (
                "单 Agent MVP 正在以模拟 provider 模式运行。\n\n"
                f"工作区：{self.settings.workspace_dir}\n"
                f"会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n"
                f"你说：{message}"
            )
        else:
            content = (
                "Single Agent MVP is running in simulated provider mode.\n\n"
                f"Workspace: {self.settings.workspace_dir}\n"
                f"Session: {session_id}\n\n"
                f"Mode: {work_mode} / {access_mode}\n\n"
                f"You said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, provider: ProviderEntry, model_override: str | None = None):
        from langchain_openai import ChatOpenAI

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

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, access_mode: AccessMode) -> AgentReply:
        from langchain.agents import create_agent

        audit_context = {"session_id": session_id, "provider": self.provider_name, "model": self.model_name}
        agent = create_agent(
            model=self.llm,
            tools=build_workspace_tools(self.workspace, access_mode if work_mode == "build" else "default", audit_context, self.approval_store),
            system_prompt=SYSTEM_PROMPT,
            name="coworker_single_agent",
        )
        result = agent.invoke({"messages": prepare_agent_messages([{"role": "user", "content": message}], language, work_mode, access_mode)})
        messages = result.get("messages", [])
        content = messages[-1].content if messages else ""
        return AgentReply(content=str(content), mode=self.mode, provider=self.provider_name)

    @staticmethod
    def openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url


class OpenAICompatibleStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, provider: ProviderEntry, model_override: str | None = None):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model_override or provider.model,
            temperature=0,
            api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
            base_url=self.openai_compatible_base_url(provider),
        )
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm
        self.workspace = workspace
        self.approval_store = approval_store

    async def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        access_mode: AccessMode,
    ) -> AsyncGenerator[dict[str, Any], None]:
        from langgraph.prebuilt import create_react_agent

        audit_context = {"session_id": session_id, "provider": self.provider_name, "model": self.model_name}
        agent = create_react_agent(
            model=self.llm,
            tools=build_workspace_tools(self.workspace, access_mode if work_mode == "build" else "default", audit_context, self.approval_store),
            prompt=SYSTEM_PROMPT,
            name="coworker_single_agent",
        )
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}
        inputs = {"messages": prepare_agent_messages(messages, language, work_mode, access_mode)}
        content_parts: list[str] = []
        async for chunk in agent.astream(inputs, stream_mode="updates"):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                for message in update.get("messages", []) or []:
                    payload = message_payload(message)
                    if payload.get("tool_calls"):
                        for tool_call in payload["tool_calls"]:
                            name = tool_call.get("name") or (tool_call.get("function") or {}).get("name")
                            yield {"type": "tool_call", "name": name or "tool"}
                    if payload.get("tool_call_id"):
                        yield {"type": "tool_result", "name": node_name}
                    content = payload.get("content")
                    if content:
                        yield {"type": "delta", "content": str(content)}
                        content_parts.append(str(content))
        yield {"type": "done", "content": "".join(content_parts), "mode": self.mode, "provider": self.provider_name, "model": self.model_name}

    @staticmethod
    def openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings):
        self.settings = settings

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
                "单 Agent MVP 正在以模拟 provider 模式运行。\n\n"
                f"工作区：{self.settings.workspace_dir}\n"
                f"会话：{session_id}\n\n"
                f"模式：{work_mode} / {access_mode}\n\n"
                f"你说：{user_message}"
            )
        else:
            content = (
                "Single Agent MVP is running in simulated provider mode.\n\n"
                f"Workspace: {self.settings.workspace_dir}\n"
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
        self.settings = settings
        self.workspace = Workspace(settings.workspace_dir, settings.data_dir / TOOL_AUDIT_FILENAME)
        self.approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
        self.provider_manager = ProviderManager(settings.data_dir / "providers.json")

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

    def _create_single_agent(self, provider_id: str | None = None, model: str | None = None) -> AgentRuntime:
        provider = self._provider_for_request(provider_id, model)
        if provider:
            return OpenAICompatibleSingleAgentRuntime(self.workspace, self.approval_store, provider)
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
            return OpenAICompatibleSingleAgentRuntime(self.workspace, self.approval_store, env_provider)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None) -> AgentRuntime:
        if mode == "single":
            return self._create_single_agent(provider_id, model)
        raise RuntimeError(f"Unsupported agent mode: {mode}")

    def get_stream_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None) -> AgentStreamRuntime:
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
                return SimulatedStreamRuntime(self.settings)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            return OpenAICompatibleStreamRuntime(self.workspace, self.approval_store, provider, model)
        raise RuntimeError(f"Unsupported agent mode: {mode}")
