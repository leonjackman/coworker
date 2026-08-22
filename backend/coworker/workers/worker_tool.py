"""单 agent 模式下的 use_worker tool 定义。"""

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from coworker.workers.worker_config import WorkerResult


class UseWorkerArgs(BaseModel):
    """use_worker 工具的参数。"""

    task: str = Field(description="任务描述。清晰说明 Worker 需要做什么。")
    context: str = Field(
        default="", description="额外上下文。提供背景信息、相关文件路径等。"
    )
    expected_output: str = Field(
        default="",
        description="期望输出格式。例如 'JSON array of findings' 或 '简短的段落总结'。",
    )


class UseWorkerTool:
    """封装 use_worker tool 的创建逻辑。"""

    def __init__(
        self,
        llm: Any,
        workspace: Any,
        tools: list[Any],
        approval_store: Any,
        change_store: Any | None,
        session_store: Any | None,
        data_dir: Path | None,
        mcp_session_manager: Any | None,
        skill_manager: Any | None,
        provider_name: str,
        session_id: str = "",
        work_mode: str = "build",
        autonomy: str = "guarded",
        language: str = "zh",
        max_concurrent: int = 4,
        delegation_emit: Any | None = None,
    ):
        self.llm = llm
        self.workspace = workspace
        self.tools = tools
        self.approval_store = approval_store
        self.change_store = change_store
        self.session_store = session_store
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.provider_name = provider_name
        self.session_id = session_id
        self.work_mode = work_mode
        self.autonomy = autonomy
        self.language = language
        self.max_concurrent = max_concurrent
        self.delegation_emit = delegation_emit or (lambda event: None)

    def create_tool(self) -> Any:
        """创建 langchain @tool 装饰的函数。"""

        @tool(args_schema=UseWorkerArgs)
        async def use_worker(
            task: str, context: str = "", expected_output: str = ""
        ) -> str:
            """Spawn a short-lived worker agent to do focused research or analysis.

            The worker runs in isolation — its internal tool calls are NOT visible
            to you. You only receive its final result (summarized if needed).

            Use when a focused task benefits from dedicated exploration:
            - Deep codebase investigation
            - Comparing multiple approaches
            - Research that would bloat your context

            The worker's output is automatically truncated/summarized if too long.
            You can spawn multiple workers in parallel by calling this tool multiple
            times in a single turn — they will execute concurrently.
            """
            from coworker.workers.worker_config import TaskBrief, WorkerConfig
            from coworker.workers.worker import WorkerAgent

            brief = TaskBrief(
                task=task,
                context=context,
                expected_output=expected_output,
            )
            config = WorkerConfig.for_single_agent(
                language=self.language,
                max_concurrent=self.max_concurrent,
            )

            worker = WorkerAgent(
                llm=self.llm,
                brief=brief,
                config=config,
                workspace=self.workspace,
                tools=self.tools,  # 继承父 agent 的工具集
                approval_store=self.approval_store,
                change_store=self.change_store,
                session_store=self.session_store,
                data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                provider_name=self.provider_name,
                session_id=self.session_id,
                work_mode=self.work_mode,
                autonomy=self.autonomy,
                emit=self.delegation_emit,
            )

            # 异步调用：LangGraph 在 async 上下文中 await 此工具
            result: WorkerResult = await worker.arun()

            output = result.content
            if result.was_truncated:
                output += "\n\n[注意：子代理输出已被摘要]"
            if not result.success:
                output += f"\n\n[错误：{result.error}]"
            return output

        return use_worker
