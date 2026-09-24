# -*- coding: utf-8 -*-
"""Workflow HTTP API (aligned with the skills routes).

Runs execute deterministically on the server; the run-events endpoint streams
persisted events (poll-replay) so long runs are observable without a live bus.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from coworker.api.state import app, logger, settings, workflow_manager

router = APIRouter()


def _server_environment() -> Any:
    """Server-side execution environment for manual/API runs.

    Mirrors the scheduler environment (command + web/browser/computer tools) so
    a workflow triggered from the panel behaves like one triggered by cron.
    """
    from coworker.schedules.runner import build_server_environment

    workspace_root = settings.data_dir / "workflows_workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return build_server_environment(settings.data_dir, workspace_root)

_TERMINAL = {"ok", "failed", "needs_human", "paused"}


class WorkflowContentPayload(BaseModel):
    content: str = Field(description="Full workflow YAML.")


class WorkflowCreatePayload(BaseModel):
    content: str = Field(description="Full workflow YAML.")
    overwrite: bool = False


class WorkflowStatusPayload(BaseModel):
    status: str = Field(description="active | draft | deprecated")


class WorkflowRollbackPayload(BaseModel):
    version: int = Field(description="Historical version to restore.")


class WorkflowRecordPayload(BaseModel):
    """Captured actions to generalize into a draft workflow (W24/W25).

    ``steps`` is a list of normalized action dicts (kind/do/params/locator...).
    Literal values matching declared inputs are parameterized automatically.
    """

    name: str
    description: str = ""
    steps: list[dict[str, Any]]
    inputs: list[dict[str, Any]] | None = None
    triggers: list[str] | None = None
    sources: list[str] | None = None


class WorkflowRunPayload(BaseModel):
    inputs: dict[str, Any] | None = None
    run_id: str | None = None
    resume: bool = False
    trigger: str = "manual"


def _not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"workflow not found: {name}")


@router.get("/workflows")
def list_workflows():
    """List active workflows (catalog view)."""
    return {"status": "ok", "workflows": workflow_manager.list()}


@router.get("/workflows/pending")
def list_pending_workflows():
    """List workflow drafts awaiting approval."""
    return {"status": "ok", "pending": workflow_manager.list_pending()}


@router.get("/workflows/pending/{name}")
def get_pending_workflow(name: str):
    content = workflow_manager.read_pending(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"no pending draft: {name}")
    return {"status": "ok", "name": name, "content": content}


@router.put("/workflows/pending/{name}")
def update_pending_workflow(name: str, payload: WorkflowContentPayload):
    result = workflow_manager.update_pending(name, payload.content)
    if result.get("status") != "ok":
        code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "update failed"))
    return result


@router.post("/workflows/pending/{name}/approve")
def approve_pending_workflow(name: str):
    result = workflow_manager.approve_pending(name)
    if result.get("status") != "ok":
        code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "approval failed"))
    return result


@router.post("/workflows/pending/{name}/reject")
def reject_pending_workflow(name: str):
    result = workflow_manager.reject_pending(name)
    if result.get("status") != "ok":
        code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "rejection failed"))
    return result


@router.get("/workflows/runs")
def list_all_runs(workflow: str = "", limit: int = 50):
    return {"status": "ok", "runs": workflow_manager.list_runs(workflow, limit)}


@router.get("/workflows/runs/{run_id}")
def get_run(run_id: str):
    run = workflow_manager.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run: {run_id}")
    return {"status": "ok", "run": run}


@router.get("/workflows/runs/{run_id}/events")
async def stream_run_events(run_id: str):
    """Replay persisted run events, then follow until the run is terminal."""

    async def _gen():
        seen = 0
        idle = 0.0
        while True:
            events = workflow_manager.read_events(run_id)
            for event in events[seen:]:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                seen += 1
            run = workflow_manager.get_run(run_id)
            status = (run or {}).get("status", "")
            if run is None or status in _TERMINAL:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle > 1800:
                yield "event: timeout\ndata: {}\n\n"
                return

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/workflows/render")
def render_workflow_route(payload: WorkflowContentPayload):
    return workflow_manager.render(payload.content)


@router.post("/workflows/validate")
def validate_workflow_route(payload: WorkflowContentPayload):
    return workflow_manager.validate(payload.content)


@router.post("/workflows")
def create_workflow(payload: WorkflowCreatePayload):
    try:
        result = workflow_manager.create(payload.content, overwrite=payload.overwrite)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message", "create failed"))
    return result


@router.post("/workflows/import")
def import_workflow(payload: WorkflowCreatePayload):
    """Import a workflow from exported YAML (alias of create)."""
    return create_workflow(payload)


@router.post("/workflows/record")
def record_workflow(payload: WorkflowRecordPayload):
    """Stage a draft from a captured action sequence (agent/recorded authoring)."""
    result = workflow_manager.stage_recorded(
        payload.name,
        payload.steps,
        description=payload.description or payload.name,
        inputs=payload.inputs,
        triggers=payload.triggers,
        sources=payload.sources,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message", "record failed"))
    return result


@router.get("/workflows/{name}/export")
def export_workflow(name: str):
    result = workflow_manager.export(name)
    if result.get("status") != "ok":
        raise _not_found(name)
    return result


@router.get("/workflows/{name}/versions")
def list_workflow_versions(name: str):
    if workflow_manager.get(name) is None:
        raise _not_found(name)
    return {"status": "ok", "versions": workflow_manager.versions(name)}


@router.post("/workflows/{name}/rollback")
def rollback_workflow(name: str, payload: WorkflowRollbackPayload):
    result = workflow_manager.rollback(name, payload.version)
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message", "rollback failed"))
    return result


@router.get("/workflows/{name}")
def get_workflow(name: str):
    data = workflow_manager.get(name)
    if data is None:
        raise _not_found(name)
    return {"status": "ok", "workflow": data}


@router.put("/workflows/{name}")
def update_workflow(name: str, payload: WorkflowContentPayload):
    result = workflow_manager.update(name, payload.content)
    if result.get("status") != "ok":
        code = 404 if "not found" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "update failed"))
    return result


@router.post("/workflows/{name}/status")
def set_workflow_status(name: str, payload: WorkflowStatusPayload):
    result = workflow_manager.set_status(name, payload.status)
    if result.get("status") != "ok":
        raise _not_found(name)
    return result


@router.delete("/workflows/{name}")
def delete_workflow(name: str):
    result = workflow_manager.delete(name)
    if result.get("status") != "ok":
        raise _not_found(name)
    return result


@router.post("/workflows/{name}/run")
def run_workflow(name: str, payload: WorkflowRunPayload):
    try:
        env = _server_environment()
    except Exception as exc:  # noqa: BLE001 - a broken env must not 500 the route
        logger.warning("workflow server environment unavailable: %s", exc)
        env = None
    result = workflow_manager.run(
        name,
        payload.inputs or {},
        env=env,
        run_id=payload.run_id or None,
        resume=bool(payload.resume),
        trigger=payload.trigger or "manual",
    )
    if result.get("status") == "error":
        raise _not_found(name)
    return result


@router.get("/workflows/{name}/runs")
def list_workflow_runs(name: str, limit: int = 50):
    if workflow_manager.get(name) is None:
        raise _not_found(name)
    return {"status": "ok", "runs": workflow_manager.list_runs(name, limit)}
