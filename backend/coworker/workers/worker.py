"""WorkerAgent — 瞬时子代理执行引擎。

单 agent 和多 agent 共享此实现。触发方式不同：
- 单 agent：父 agent 的 use_worker tool call
- 多 agent：Lead agent 的 delegate_task tool call
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from coworker.workers.worker_config import TaskBrief, WorkerConfig, WorkerResult


class WorkerAgent:
    """瞬时子代理：独立 graph、独立 memory、受限工具集、结果隔离。

    Args:
        llm: LLM 实例（与父代理相同）
        brief: 任务简报
        config: 运行配置
        workspace: 工作区
        tools: 子代理工具集
        approval_store: 审批存储
        change_store: 变更存储
        session_store: 会话存储
        data_dir: 数据目录
        mcp_session_manager: MCP 会话管理器
        skill_manager: 技能管理器
        provider_name: provider 名称
        memory_manager: 可选 memory（多 agent 模式）
        project_dir: 项目目录
        session_id: 会话 id
        caller_agent: 调用者 agent
        readonly: 是否只读
        work_mode: 工作模式
        autonomy: 自主等级
    """

    def __init__(
        self,
        llm: Any,
        brief: TaskBrief,
        config: WorkerConfig,
        workspace: Any,
        tools: list[Any],
        approval_store: Any,
        change_store: Any | None,
        session_store: Any | None,
        data_dir: Path | None,
        mcp_session_manager: Any | None,
        skill_manager: Any | None,
        provider_name: str,
        memory_manager: Any | None = None,
        project_dir: str = "",
        session_id: str = "",
        caller_agent: str = "",
        readonly: bool = False,
        work_mode: str = "",
        autonomy: str = "guarded",
        emit: Any | None = None,
    ):
        self.llm = llm
        self.brief = brief
        self.config = config
        self.workspace = workspace
        self.tools = tools
        self.approval_store = approval_store
        self.change_store = change_store
        self.session_store = session_store
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.provider_name = provider_name
        self.memory_manager = memory_manager
        self.project_dir = project_dir
        self.session_id = session_id
        self.caller_agent = caller_agent
        self.readonly = readonly
        self.work_mode = work_mode or "build"
        self.autonomy = autonomy
        self.emit = emit or (lambda event: None)
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        return self._semaphore

    async def arun(self) -> WorkerResult:
        """异步执行 Worker，返回 WorkerResult。"""
        semaphore = self._get_semaphore()
        async with semaphore:
            return await self._execute()

    async def _execute(self) -> WorkerResult:
        """执行子代理并处理结果。"""
        # 发射 delegate_start 事件（给前端 UI 展示）
        self.emit({
            "type": "delegate_start",
            "from": self.caller_agent or "Coworker",
            "to": "Worker",
            "task": self.brief.task[:200],
        })

        # 1. 构建独立 graph
        graph = self._build_graph()

        # 2. 构建输入 state
        state = self._build_state()

        # 3. 带超时执行
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: graph.invoke(state, self._run_config)
                ),
                timeout=self.config.timeout,
            )
        except asyncio.TimeoutError:
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": "timeout",
                "parallel": False,
            })
            return WorkerResult(
                content=f"Worker 超时（{self.config.timeout}s）。子代理执行超时。",
                success=False,
                error="timeout",
                raw_length=0,
                was_truncated=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": str(exc)[:200],
                "parallel": False,
            })
            return WorkerResult(
                content=f"Worker 执行失败：{exc}",
                success=False,
                error=str(exc),
                raw_length=0,
                was_truncated=False,
            )

        # 4. 提取结果
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        if "__interrupt__" in result:
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": "interrupted",
                "parallel": False,
            })
            return WorkerResult(
                content="（子代理需要审批，已中止）",
                success=False,
                error="interrupted",
                raw_length=0,
                was_truncated=False,
            )

        raw_text = self._coerce_message_content(msgs[-1]) if msgs else ""

        # 5. 摘要/截断
        content = await self._maybe_summarize(raw_text)

        # 6. 发射 delegate_end 事件
        self.emit({
            "type": "delegate_end",
            "from": self.caller_agent or "Coworker",
            "to": "Worker",
            "ok": True,
            "chars": len(content),
            "parallel": False,
        })

        return WorkerResult(
            content=content,
            raw_length=len(raw_text),
            was_truncated=len(raw_text) > self.config.max_output_chars,
            success=True,
        )

    def _build_graph(self) -> Any:
        """构建子代理的 LangChain agent graph。"""
        from coworker.agents import build_coworker_agent_graph

        return build_coworker_agent_graph(
            self.llm,
            self.tools,
            work_mode=self.work_mode,
            language=self.config.language,
            autonomy="autonomous" if self.readonly else self.autonomy,
            checkpointer=None,  # 子代理无持久 checkpoint
            approval_store=self.approval_store,
            data_dir=self.data_dir,
            mcp_session_manager=self.mcp_session_manager,
            skill_manager=self.skill_manager,
            memory_manager=self.memory_manager,
            workspace=self.workspace,
        )

    def _build_state(self) -> dict[str, Any]:
        """构建子代理的输入 state。"""
        from coworker.agents import prepare_agent_messages

        prompt = f"{self.brief.task}"
        if self.brief.context:
            prompt += f"\n\n{self.brief.context}"
        if self.brief.expected_output:
            prompt += f"\n\n期望输出：{self.brief.expected_output}"
        if self.brief.constraints:
            prompt += "\n\n约束条件：\n" + "\n".join(f"- {c}" for c in self.brief.constraints)

        messages = prepare_agent_messages([{"role": "user", "content": prompt}])
        return {
            "messages": messages,
            "work_mode": self.work_mode,
            "language": self.config.language,
            "phase": "execute",  # 子代理直接执行
            "autonomy": self.autonomy,
        }

    @property
    def _run_config(self) -> dict[str, Any]:
        """子代理的 run config。"""
        from coworker.agents import agent_run_config

        return agent_run_config(
            session_id=f"{self.session_id}::worker::{uuid.uuid4().hex[:8]}",
            provider=self.provider_name,
            model=self.llm.model_name if hasattr(self.llm, "model_name") else "",
            language=self.config.language,
            work_mode=self.work_mode,
            autonomy=self.autonomy,
            streaming=False,
        )

    def _coerce_message_content(self, msg: Any) -> str:
        """提取 AIMessage 的文本内容。"""
        from coworker.agents import coerce_message_content

        return coerce_message_content(msg)

    async def _maybe_summarize(self, content: str) -> str:
        """对超长输出做轻量摘要。"""
        from coworker.workers.worker_summarize import summarize_result

        return await summarize_result(
            content=content,
            max_chars=self.config.max_output_chars,
            language=self.config.language,
            data_dir=self.data_dir,
            primary_llm=self.llm,
        )
