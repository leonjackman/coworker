"""Headless agent invocations shared by workflows and scheduled agent tasks.

Both the workflow ``agentic`` step and the schedule ``agent`` target need to run
"a real agent turn" without a chat session. This module is the single place that
builds the default LLM and runs a :class:`WorkerAgent`, so the two call sites
never drift apart.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)


def build_default_llm(provider_manager: Any | None, data_dir: Path | None = None) -> Any | None:
    """The default provider's chat model, or ``None`` when none is configured."""
    if provider_manager is None:
        return None
    try:
        provider = provider_manager.default_provider()
    except Exception:  # noqa: BLE001
        return None
    if provider is None:
        return None
    try:
        from coworker.agent.model_defaults import ReasonPreservingChatOpenAI, provider_llm_kwargs

        llm_cls = ReasonPreservingChatOpenAI.create
        return llm_cls(
            **provider_llm_kwargs(provider.model, provider, provider.base_url or None, data_dir=data_dir)
        )
    except Exception as exc:  # noqa: BLE001 - unavailable provider is a failure, not a crash
        logger.warning("headless llm unavailable: %s", exc)
        return None


async def run_agent_task(
    *,
    prompt: str,
    workspace: Any,
    tools: list[Any],
    provider_manager: Any | None = None,
    llm: Any | None = None,
    data_dir: Path | None = None,
    approval_store: Any | None = None,
    skill_manager: Any | None = None,
    session_id: str = "",
    timeout: int = 600,
    readonly: bool = False,
    autonomy: str = "guarded",
    depth: int = 1,
) -> dict[str, Any]:
    """Run a headless agent turn and return ``{status, output|error}``.

    Pass either an explicit ``llm`` or a ``provider_manager`` to resolve the
    default provider.
    """
    from coworker.workers.worker import WorkerAgent
    from coworker.workers.worker_config import TaskBrief, WorkerConfig

    if llm is None:
        llm = build_default_llm(provider_manager, data_dir)
    if llm is None:
        return {"status": "failed", "error": "no enabled provider configured"}
    brief = TaskBrief(task=prompt)
    config = WorkerConfig.for_single_agent(max_concurrent=1, timeout=int(timeout or 600))
    worker = WorkerAgent(
        llm=llm,
        brief=brief,
        config=config,
        workspace=workspace,
        tools=tools,
        approval_store=approval_store,
        change_store=None,
        session_store=None,
        data_dir=data_dir,
        mcp_session_manager=None,
        skill_manager=skill_manager,
        provider_name="",
        session_id=session_id,
        work_mode="build",
        autonomy=autonomy,
        readonly=readonly,
        depth=depth,
    )
    result = await worker.arun()
    if result.success:
        return {"status": "ok", "output": (result.content or "")[:4000]}
    return {"status": "failed", "error": result.error or "agent task failed"}


def run_agent_task_sync(**kwargs: Any) -> dict[str, Any]:
    """Synchronous bridge for the sync workflow executor.

    Runs the coroutine on a fresh loop; when the caller already has a running
    loop (e.g. inside an async tool node) it is executed on a worker thread with
    its own loop so we never nest event loops.
    """
    coro = run_agent_task(**kwargs)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _target() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        finally:
            loop.close()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    return box.get("value", {"status": "failed", "error": "agent task did not return"})
