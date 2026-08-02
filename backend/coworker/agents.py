import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Literal

from .config import BackendSettings
from .providers import ProviderEntry, ProviderManager
from .workspace import Workspace

AgentMode = Literal["single"]
Language = Literal["zh", "en"]


@dataclass(frozen=True)
class AgentReply:
    content: str
    mode: AgentMode
    provider: str


class AgentRuntime(ABC):
    mode: AgentMode

    @abstractmethod
    def run(self, message: str, session_id: str, language: Language) -> AgentReply:
        raise NotImplementedError


class SimulatedSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings):
        self.settings = settings

    def run(self, message: str, session_id: str, language: Language) -> AgentReply:
        if language == "zh":
            content = (
                "单 Agent MVP 正在以模拟 provider 模式运行。\n\n"
                f"工作区：{self.settings.workspace_dir}\n"
                f"会话：{session_id}\n\n"
                f"你说：{message}"
            )
        else:
            content = (
                "Single Agent MVP is running in simulated provider mode.\n\n"
                f"Workspace: {self.settings.workspace_dir}\n"
                f"Session: {session_id}\n\n"
                f"You said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, workspace: Workspace, provider: ProviderEntry, model_override: str | None = None):
        from langchain.agents import create_agent
        from langchain_openai import ChatOpenAI

        def read_file(file_path: str) -> str:
            """Read a UTF-8 text file from the configured workspace."""
            return workspace.read_text(file_path)

        def write_file(input_json: str) -> str:
            """Write a UTF-8 text file. Input JSON has file_path and content."""
            payload = json.loads(input_json)
            workspace.write_text(payload["file_path"], payload["content"])
            return f"Wrote {payload['file_path']}"

        llm = ChatOpenAI(
            model=model_override or provider.model,
            temperature=0,
            api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
            base_url=self.openai_compatible_base_url(provider),
        )
        self.agent = create_agent(
            model=llm,
            tools=[read_file, write_file],
            system_prompt=(
                "You are Coworker Single Agent MVP, a local coding assistant. "
                "Use workspace tools only when they are needed and keep answers concise."
            ),
            name="coworker_single_agent",
        )

    def run(self, message: str, session_id: str, language: Language) -> AgentReply:
        language_name = "Chinese" if language == "zh" else "English"
        result = self.agent.invoke({"messages": [{"role": "user", "content": f"Reply in {language_name}.\n\n{message}"}]})
        messages = result.get("messages", [])
        content = messages[-1].content if messages else ""
        return AgentReply(content=str(content), mode=self.mode, provider="openai")

    @staticmethod
    def openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings):
        self.settings = settings
        self.workspace = Workspace(settings.workspace_dir)
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
            return OpenAICompatibleSingleAgentRuntime(self.workspace, provider)
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
            return OpenAICompatibleSingleAgentRuntime(self.workspace, env_provider)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None) -> AgentRuntime:
        if mode == "single":
            return self._create_single_agent(provider_id, model)
        raise RuntimeError(f"Unsupported agent mode: {mode}")
