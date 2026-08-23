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

from coworker.logger import get_logger
from coworker.workers.worker_config import TaskBrief, WorkerConfig, WorkerResult

logger = get_logger(__name__)


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
        worker_bus: Any | None = None,
        worker_run_id: str = "",
        depth: int = 0,
        context_window_tokens: int = 0,
        max_output_tokens: int = 0,
        calibration_key: str = "",
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
        self.worker_bus = worker_bus
        self.worker_run_id = worker_run_id
        # Context accounting inherited from the parent runtime: the worker's
        # budget/trim/guard must run on the SAME window the provider enforces.
        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        self.calibration_key = calibration_key
        # 委派深度：主 agent = 0，use_worker/delegate 每 spawn 一层 +1。
        # 引擎级兜底：超过 config.max_depth 的子代理直接拒绝运行，防止任何
        # spawn 路径（use_worker 或 delegate_task）形成无限嵌套链。
        self.depth = depth
        self._thread_id: str = ""
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
        # 引擎级深度兜底：达到或超过上限的子代理拒绝运行（不再 emit delegate_start，
        # 前端不会出现空转的 worker 块）。正常路径由工具构造期过滤 + 委派端
        # max_depth 前置校验保证，这里是最后的保险。
        if self.depth >= self.config.max_depth:
            return WorkerResult(
                content=f"委派深度已达上限（max_depth={self.config.max_depth}），无法继续 spawn 子代理。",
                success=False,
                error="max_depth",
                raw_length=0,
                was_truncated=False,
            )

        # 唯一 run id：既作为 worker 的 thread_id 一部分，也作为前端订阅
        # /worker-events/{worker_run_id} 的键。delegate_start/end 摘要帧携带它，
        # worker 内部流通过 worker_event_bus 独立发布（不进入主 SSE 流）。
        worker_run_id = self.worker_run_id or uuid.uuid4().hex[:8]
        self.worker_run_id = worker_run_id
        self._thread_id = f"{self.session_id}::worker::{worker_run_id}"

        # 发射 delegate_start 事件（给前端 UI 展示，仅摘要，不含内部流）
        self.emit({
            "type": "delegate_start",
            "from": self.caller_agent or "Coworker",
            "to": "Worker",
            "task": self.brief.task[:200],
            "worker_run_id": worker_run_id,
        })

        # 在 worker 开始发布内部流之前，先向 worker bus 注册这个 run 为"预期中的
        # 真实 run"。这样即使前端在首个事件到达前就订阅（收到 delegate_start 后立即
        # 展开块），也不会被当作未知 run 提前给 worker_stream_end —— 根源性消除
        # subscribe-before-publish 竞态，而不是靠调大超时窗口。
        if self.worker_bus is not None:
            try:
                self.worker_bus.expect(worker_run_id)
            except Exception:  # noqa: BLE001 - expect 失败不能杀死 worker
                logger.warning("worker bus expect failed for %s", worker_run_id, exc_info=True)

        # 1. 构建独立 graph
        graph = self._build_graph()

        # 2. 构建输入 state
        state = self._build_state()

        content_parts: list[str] = []
        tool_state: dict[str, dict[str, Any]] = {}
        parts: list[dict[str, Any]] = []
        run_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

        def _publish(event: dict[str, Any]) -> None:
            if self.worker_bus is None:
                return
            try:
                self.worker_bus.publish(worker_run_id, {**event, "worker_run_id": worker_run_id})
            except Exception:  # noqa: BLE001 - a publish hiccup must never kill the worker
                logger.warning("worker event publish failed for %s", worker_run_id, exc_info=True)

        def _close_stream(error: str | None = None) -> None:
            if error:
                _publish({"type": "error", "error": error})
            if self.worker_bus is not None:
                try:
                    self.worker_bus.close(worker_run_id)
                except Exception:  # noqa: BLE001 - best-effort close
                    pass

        # 3. 带超时执行：与主流一致用 astream 流式捕获内部 delta/tool/reasoning，
        #    并发布到 worker bus；worker 的 ainvoke 等价结果（最终 messages）通过
        #    stream_mode="values" 的最后一块取得。
        try:
            async def _consume() -> dict[str, Any]:
                from coworker.agent.runtime import _aclose_on_exit
                from coworker.agent.core import _message_chunk_events, _normalize_usage

                final_state: dict[str, Any] | None = None
                async for stream_mode, chunk in _aclose_on_exit(graph.astream(
                    state,
                    config=self._run_config,
                    stream_mode=["messages", "custom", "updates", "values"],
                )):
                    if stream_mode == "values":
                        final_state = chunk if isinstance(chunk, dict) else None
                    elif stream_mode == "messages":
                        msg, _meta = chunk
                        for event in _message_chunk_events(
                            msg, content_parts, tool_state, parts,
                            session_id=worker_run_id, real_file_changes=None,
                        ):
                            _publish(event)
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type == "context_usage":
                                _publish(chunk)
                            elif event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                _publish(chunk)
                    elif stream_mode == "updates":
                        for _node_name, node_update in chunk.items():
                            if isinstance(node_update, dict) and "todos" in node_update:
                                _publish({"type": "todos", "todos": node_update.get("todos") or []})
                            if isinstance(node_update, dict):
                                node_messages = node_update.get("messages")
                                if isinstance(node_messages, list) and node_messages:
                                    last_msg = node_messages[-1]
                                    usage = getattr(last_msg, "usage_metadata", None) or {}
                                    if isinstance(usage, dict):
                                        p, c = _normalize_usage(usage)
                                        run_usage["prompt_tokens"] += p
                                        run_usage["completion_tokens"] += c
                return final_state or {}

            result = await asyncio.wait_for(_consume(), timeout=self.config.timeout)
        except asyncio.TimeoutError:
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": "timeout",
                "parallel": False,
                "worker_run_id": worker_run_id,
            })
            _close_stream(error="timeout")
            return WorkerResult(
                content=f"Worker 超时（{self.config.timeout}s）。子代理执行超时。",
                success=False,
                error="timeout",
                raw_length=0,
                was_truncated=False,
            )
        except asyncio.CancelledError:
            # 主回合被停止/客户端断开：取消会经 asyncio.gather / wait_for 传播到
            # 每个 worker。必须在这里发出 delegate_end 并关闭 worker bus，否则
            # 前端 worker 块的「Delegating…」转圈永不结束、/worker-events SSE 也
            # 永不终止（run 一直挂在内存里）。清理后重新抛出，保持取消语义，
            # 让主回合的取消正常向上传播。
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": "cancelled",
                "parallel": False,
                "worker_run_id": worker_run_id,
            })
            _close_stream(error="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            self.emit({
                "type": "delegate_end",
                "from": self.caller_agent or "Coworker",
                "to": "Worker",
                "error": str(exc)[:200],
                "parallel": False,
                "worker_run_id": worker_run_id,
            })
            _close_stream(error=str(exc)[:400])
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
                "worker_run_id": worker_run_id,
            })
            _close_stream(error="interrupted")
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

        # 5.5 向 worker bus 发布最终的 done（含权威 parts），并关闭 run
        try:
            from coworker.agent.core import _merge_event_parts, _terminate_stray_tools

            merged_parts = _merge_event_parts(_terminate_stray_tools(parts))
            _publish({"type": "done", "content": content, "parts": merged_parts, "usage": run_usage})
            _close_stream()
        except Exception:  # noqa: BLE001 - terminal publish is best-effort
            _close_stream()

        # 6. 发射 delegate_end 事件
        self.emit({
            "type": "delegate_end",
            "from": self.caller_agent or "Coworker",
            "to": "Worker",
            "ok": True,
            "chars": len(content),
            "parallel": False,
            "worker_run_id": worker_run_id,
        })

        return WorkerResult(
            content=content,
            raw_length=len(raw_text),
            was_truncated=len(raw_text) > self.config.max_output_chars,
            success=True,
        )

    def _build_graph(self) -> Any:
        """构建子代理的 LangChain agent graph。"""
        from coworker.agent.graph import build_coworker_agent_graph

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
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
            calibration_key=self.calibration_key,
        )

    def _build_state(self) -> dict[str, Any]:
        """构建子代理的输入 state。"""
        from coworker.agent.core import prepare_agent_messages

        prompt = f"{self.brief.task}"
        if self.brief.context:
            prompt += f"\n\n{self.brief.context}"
        if self.brief.expected_output:
            prompt += f"\n\n期望输出：{self.brief.expected_output}"
        if self.brief.constraints:
            prompt += "\n\n约束条件：\n" + "\n".join(f"- {c}" for c in self.brief.constraints)

        messages = prepare_agent_messages([{"role": "user", "content": prompt}])
        # 只读 worker 以 discuss 阶段运行：其自身的 PhaseToolGateMiddleware 会把
        # 工具集过滤成 read-only + memory + plan（与主 agent 的讨论阶段一致），
        # 确保读写的双重保障（工具不挂 + gate 兜底）。
        return {
            "messages": messages,
            "work_mode": self.work_mode,
            "language": self.config.language,
            "phase": "discuss" if self.readonly else "execute",
            "autonomy": self.autonomy,
        }

    @property
    def _run_config(self) -> dict[str, Any]:
        """子代理的 run config。"""
        from coworker.agent.core import agent_run_config

        return agent_run_config(
            session_id=self._thread_id or f"{self.session_id}::worker::{uuid.uuid4().hex[:8]}",
            provider=self.provider_name,
            model=self.llm.model_name if hasattr(self.llm, "model_name") else "",
            language=self.config.language,
            work_mode=self.work_mode,
            autonomy=self.autonomy,
            streaming=True,
        )

    def _coerce_message_content(self, msg: Any) -> str:
        """提取 AIMessage 的文本内容。"""
        from coworker.agent.core import coerce_message_content

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
