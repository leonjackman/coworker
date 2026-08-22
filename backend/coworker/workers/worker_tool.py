"""单 agent 模式下的 use_worker / use_workers tool 定义。"""

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from coworker.workers.worker_config import WorkerResult


class UseWorkerArgs(BaseModel):
    """use_worker / use_workers 中单个任务参数。"""

    task: str = Field(description="任务描述。清晰说明 Worker 需要做什么。")
    context: str = Field(
        default="", description="额外上下文。提供背景信息、相关文件路径等。"
    )
    expected_output: str = Field(
        default="",
        description="期望输出格式。例如 'JSON array of findings' 或 '简短的段落总结'。",
    )


class UseWorkersArgs(BaseModel):
    """use_workers 工具的参数：一次扇出多个独立子任务并行执行。"""

    tasks: list[UseWorkerArgs] = Field(
        description=(
            "需要并行委派给独立 worker 的子任务列表。每个子任务应相互独立、"
            "互不依赖；多个 worker 会并发执行，整体耗时取决于最慢的那个。"
        )
    )


class UseWorkerTool:
    """封装 use_worker / use_workers tool 的创建逻辑。"""

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
        worker_bus: Any | None = None,
        depth: int = 0,
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
        self.worker_bus = worker_bus
        # 委派深度：主 agent = 0；spawn 出的 worker = depth+1。子代理自身工具集
        # 由构造方（agents.build_workspace_tools）预先排除委派/spawn 工具，这里
        # 只负责把深度传给引擎做兜底。
        self.depth = depth
        # 全局并发信号量：use_worker 与 use_workers 共用一个信号量，保证同一时刻
        # 运行中的 worker 不超过 max_concurrent（即使模型在一步里批量发出多个
        # spawn 调用，也被限制在预算内）。
        self._worker_semaphore: asyncio.Semaphore | None = None

    def _get_worker_semaphore(self) -> asyncio.Semaphore:
        if self._worker_semaphore is None:
            self._worker_semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._worker_semaphore

    async def _run_single_worker(self, args: UseWorkerArgs) -> str:
        """Spawn one worker, run to completion, return its (summarized) result.

        Runs inside the shared worker semaphore so concurrent spawns (whether from
        one use_workers call or a batch of use_worker calls) stay within the
        configured budget.
        """
        from coworker.workers.worker_config import TaskBrief, WorkerConfig
        from coworker.workers.worker import WorkerAgent

        brief = TaskBrief(
            task=args.task,
            context=args.context,
            expected_output=args.expected_output,
        )
        config = WorkerConfig.for_single_agent(
            language=self.language,
            max_concurrent=self.max_concurrent,
        )

        # Worker 与主 agent 保持一致的读写权限：主 agent 处于 discuss（只读）
        # 阶段时，worker 也以只读模式运行（仅研究/分析，不改文件系统）。
        # phase 由 PhaseToolGateMiddleware 在每次模型调用时记录到 workspace。
        caller_phase = getattr(self.workspace, "_current_phase", "execute")
        worker_readonly = caller_phase != "execute"

        # self.tools 由构造方（agents.build_workspace_tools）在构造期就已排除
        # 委派/spawn 工具（use_worker/use_workers/delegate_*），worker 因此天然
        # 无法再 spawn 子代理；这里把深度传给引擎做兜底（见 WorkerAgent._execute
        # 的 max_depth）。
        worker = WorkerAgent(
            llm=self.llm,
            brief=brief,
            config=config,
            workspace=self.workspace,
            tools=self.tools,
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
            readonly=worker_readonly,
            emit=self.delegation_emit,
            worker_bus=self.worker_bus,
            depth=self.depth + 1,
        )

        # 异步调用：LangGraph 在 async 上下文中 await 此工具
        result: WorkerResult = await worker.arun()

        output = result.content
        if result.was_truncated:
            output += "\n\n[注意：子代理输出已被摘要]"
        if not result.success:
            output += f"\n\n[错误：{result.error}]"
        return output

    def create_tools(self) -> list[Any]:
        """创建 langchain @tool 装饰的函数：use_worker（单个）与 use_workers（扇出）。"""
        semaphore = self._get_worker_semaphore()

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
            IMPORTANT — parallel batching: if you have 2+ INDEPENDENT tasks, emit
            ALL use_worker calls in the SAME single response (they execute
            concurrently). Do NOT call use_worker one at a time and wait for the
            result before spawning the next — that serializes the work. For 2+
            tasks, prefer the use_workers tool which fans out deterministically.
            """
            async with semaphore:
                return await self._run_single_worker(
                    UseWorkerArgs(task=task, context=context, expected_output=expected_output)
                )

        @tool(args_schema=UseWorkersArgs)
        async def use_workers(tasks: list[UseWorkerArgs]) -> str:
            """Spawn MULTIPLE worker agents in parallel, one per sub-task.

            Each sub-task runs in its own isolated worker (its internal tool calls
            are NOT visible to you; you only get the final results). All workers
            execute concurrently up to the concurrency budget, so total wall-clock
            time is bounded by the slowest worker — not the sum.

            Use when you have 2+ independent research/analysis tasks:
            - Investigating several files/modules in parallel
            - Comparing multiple approaches at once
            - Parallel research that would otherwise bloat your context

            The results are returned as a numbered list; each is summarized if too
            long. Prefer this over calling use_worker repeatedly for independent
            tasks — it guarantees the workers actually run in parallel.
            """
            if not tasks:
                return "[use_workers] 未提供任何子任务。"

            async def run_one(index: int, args: UseWorkerArgs) -> tuple[int, str]:
                async with semaphore:
                    output = await self._run_single_worker(args)
                return index, output

            results = await asyncio.gather(*(run_one(i, t) for i, t in enumerate(tasks)))
            sections: list[str] = []
            for index, output in sorted(results):
                args = tasks[index]
                head = args.task.splitlines()[0][:80] if args.task else f"Worker {index + 1}"
                sections.append(f"--- Worker {index + 1}: {head} ---\n{output}")
            return "\n\n".join(sections)

        return [use_worker, use_workers]
