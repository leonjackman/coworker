"""Execute a schedule's target: workflow / bare command / agent task."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any, Callable

from coworker.logger import get_logger

from .model import Schedule

logger = get_logger(__name__)

MAX_OUTPUT_PREVIEW = 2000


class ScheduleRunner:
    def __init__(
        self,
        *,
        workflow_manager: Any,
        env_factory: Callable[[], Any],
        provider_manager: Any | None = None,
        data_dir: Path | None = None,
        workspace_root: Path | None = None,
        approval_store: Any | None = None,
        skill_manager: Any | None = None,
    ):
        self.workflow_manager = workflow_manager
        self.env_factory = env_factory
        self.provider_manager = provider_manager
        self.data_dir = data_dir
        self.workspace_root = workspace_root or (data_dir / "scheduled" if data_dir else Path.cwd())
        self.approval_store = approval_store
        self.skill_manager = skill_manager

    async def execute(self, schedule: Schedule) -> dict[str, Any]:
        try:
            if schedule.target_type == "workflow":
                return await asyncio.to_thread(self._run_workflow, schedule)
            if schedule.target_type == "command":
                return await asyncio.to_thread(self._run_command, schedule)
            if schedule.target_type == "agent":
                return await self._run_agent(schedule)
            return {"status": "failed", "error": f"unknown target_type: {schedule.target_type}"}
        except Exception as exc:  # noqa: BLE001 - a target failure must not kill the engine
            logger.warning("schedule %s target failed: %s", schedule.id, exc)
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    # ── targets ─────────────────────────────────────────────────────────

    def _run_workflow(self, schedule: Schedule) -> dict[str, Any]:
        env = self.env_factory()
        result = self.workflow_manager.run(
            schedule.workflow,
            schedule.inputs or {},
            env=env,
            trigger=f"schedule:{schedule.id}",
        )
        status = result.get("status", "failed")
        run = result.get("run") or {}
        if status == "ok":
            return {"status": "ok", "run_id": run.get("run_id", ""), "output": f"workflow {schedule.workflow} ok"}
        return {
            "status": "failed",
            "run_id": run.get("run_id", ""),
            "error": run.get("error") or f"workflow status: {status}",
        }

    def _run_command(self, schedule: Schedule) -> dict[str, Any]:
        env = self.env_factory()
        argv = shlex.split(schedule.command)
        if not argv:
            return {"status": "failed", "error": "empty command"}
        timeout = schedule.timeout_seconds or 3600
        result = env.command(argv, cwd="", timeout=timeout)
        code = result.get("return_code") if isinstance(result, dict) else None
        stdout = str((result or {}).get("stdout", ""))[:MAX_OUTPUT_PREVIEW]
        stderr = str((result or {}).get("stderr", ""))[:MAX_OUTPUT_PREVIEW]
        if code == 0:
            return {"status": "ok", "output": stdout.strip()}
        return {"status": "failed", "error": (stderr or stdout or f"exit {code}").strip()[:MAX_OUTPUT_PREVIEW]}

    async def _run_agent(self, schedule: Schedule) -> dict[str, Any]:
        from coworker.agent.graph import build_workspace_tools
        from coworker.agent.headless import run_agent_task
        from coworker.workspace import Workspace

        workspace_root = Path(self.workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = Workspace(workspace_root)

        tools: list[Any] = []
        try:
            from coworker.web import resolve_web_tools

            tools.extend(resolve_web_tools(self.data_dir))
        except Exception:  # noqa: BLE001
            pass
        tools.extend(
            build_workspace_tools(
                workspace,
                skill_manager=self.skill_manager,
                web_tools=tools.copy(),
                readonly=False,
            )
        )
        return await run_agent_task(
            prompt=schedule.prompt,
            workspace=workspace,
            tools=tools,
            provider_manager=self.provider_manager,
            data_dir=self.data_dir,
            approval_store=self.approval_store,
            skill_manager=self.skill_manager,
            session_id=f"schedule-{schedule.id}",
            timeout=schedule.timeout_seconds or 600,
            readonly=False,
            depth=1,
        )


def build_server_environment(
    data_dir: Path,
    workspace_root: Path,
    *,
    provider_manager: Any | None = None,
    approval_store: Any | None = None,
    skill_manager: Any | None = None,
) -> Any:
    """Server-side StepEnvironment (command + web/browser/computer tools)."""
    from coworker.browser.bridge_client import resolve_browser_tool
    from coworker.computer.bridge_client import resolve_computer_tools
    from coworker.web import resolve_web_tools
    from coworker.workspace import Workspace
    from coworker.workflows.env import build_tool_environment

    tools: list[Any] = []
    try:
        tools.extend(resolve_web_tools(data_dir))
    except Exception:  # noqa: BLE001
        pass
    try:
        browser = resolve_browser_tool(data_dir)
        if browser is not None:
            tools.append(browser)
    except Exception:  # noqa: BLE001
        pass
    try:
        tools.extend(resolve_computer_tools(data_dir))
    except Exception:  # noqa: BLE001
        pass
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(root)
    return build_tool_environment(
        workspace=workspace,
        tools=tools,
        skill_manager=skill_manager,
        provider_manager=provider_manager,
        data_dir=data_dir,
        approval_store=approval_store,
    )
