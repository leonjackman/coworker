import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from .config import BackendSettings
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


class OpenAISingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace):
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required when COWORKER_AGENT_PROVIDER=openai")

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

        llm = ChatOpenAI(model=settings.openai_model, temperature=0)
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


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings):
        self.settings = settings
        self.workspace = Workspace(settings.workspace_dir)
        self.single_agent = self._create_single_agent()

    def _create_single_agent(self) -> AgentRuntime:
        if self.settings.agent_provider == "openai":
            return OpenAISingleAgentRuntime(self.settings, self.workspace)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode) -> AgentRuntime:
        if mode == "single":
            return self.single_agent
        raise RuntimeError(f"Unsupported agent mode: {mode}")
