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

from coworker.api.state import (
    app,
    command_approval_store,
    logger,
    provider_manager,
    session_store,
    settings,
    skill_manager,
    workflow_manager,
)

router = APIRouter()


def _server_environment() -> Any:
    """Server-side execution environment for manual/API runs.

    Mirrors the scheduler environment (command + web/browser/computer tools) so
    a workflow triggered from the panel behaves like one triggered by cron.
    """
    from coworker.schedules.runner import build_server_environment

    workspace_root = settings.data_dir / "workflows_workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return build_server_environment(
        settings.data_dir,
        workspace_root,
        provider_manager=provider_manager,
        approval_store=command_approval_store,
        skill_manager=skill_manager,
    )

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


class WorkflowFeedbackPayload(BaseModel):
    feedback: str = Field(description="What was wrong / how it should behave.")
    step_id: str = ""
    run_id: str = ""
    apply: bool = False


class WorkflowRecordSessionPayload(BaseModel):
    session_id: str = Field(description="Session whose turn should be turned into a workflow draft.")


class WorkflowResumePayload(BaseModel):
    decisions: dict[str, Any] = Field(default_factory=dict, description="Map of step_id -> bool (approval) or str (answer).")


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


@router.post("/workflows/runs/{run_id}/resume")
def resume_run(run_id: str, payload: WorkflowResumePayload):
    try:
        env = _server_environment()
    except Exception as exc:  # noqa: BLE001
        logger.warning("workflow server environment unavailable: %s", exc)
        env = None
    result = workflow_manager.resume(run_id, payload.decisions, env=env)
    if result.get("status") == "error" and "no run" in (result.get("message") or ""):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/workflows/runs/{run_id}/events.json")
def get_run_events(run_id: str):
    if workflow_manager.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no run: {run_id}")
    return {"status": "ok", "events": workflow_manager.read_events(run_id)}


@router.get("/workflows/runs/{run_id}/evidence")
def get_run_evidence(run_id: str):
    if workflow_manager.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no run: {run_id}")
    return {"status": "ok", "evidence": workflow_manager.read_evidence(run_id)}


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


class WorkflowStepsPayload(BaseModel):
    name: str
    description: str = ""
    version: int = 1
    platform: str = ""
    inputs: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[str] | None = None
    status: str = "active"


@router.post("/workflows/render/steps")
def render_workflow_steps(payload: WorkflowStepsPayload):
    """Render structured steps (visual editor) into canonical workflow YAML."""
    return workflow_manager.render_steps(payload.model_dump())


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


@router.get("/workflows/templates")
def list_workflow_templates():
    return {"status": "ok", "templates": workflow_manager.list_templates()}


@router.post("/workflows/templates/{template_id}/install")
def install_workflow_template(template_id: str):
    result = workflow_manager.install_template(template_id)
    if result.get("status") != "ok":
        code = 404 if "not found" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "install failed"))
    return result


@router.post("/workflows/import")
def import_workflow(payload: WorkflowCreatePayload):
    """Import a workflow from exported YAML (alias of create)."""
    return create_workflow(payload)


@router.post("/workflows/record/from-session")
async def record_workflow_from_session(payload: WorkflowRecordSessionPayload):
    """Stage a draft by reviewing a session's recent turns (W27)."""
    from coworker.agent.headless import build_default_llm
    from coworker.workflows.review import run_workflow_review

    llm = build_default_llm(provider_manager, settings.data_dir)
    if llm is None:
        raise HTTPException(status_code=400, detail="no enabled provider configured")
    messages: list[dict[str, Any]] = []
    try:
        session = session_store.load(payload.session_id)
        for message in (getattr(session, "messages", []) or [])[-12:]:
            content = getattr(message, "content", "")
            if content:
                messages.append({"type": getattr(message, "role", "") or "human", "content": content})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"session not available: {exc}") from exc
    result = await run_workflow_review(
        llm, workflow_manager, session_id=payload.session_id, messages=messages, parts=[]
    )
    return {"status": "ok", "review": result}


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


@router.post("/workflows/{name}/feedback")
async def workflow_feedback(name: str, payload: WorkflowFeedbackPayload):
    """Revise a workflow from user feedback, then stage/apply it (learning loop)."""
    from coworker.agent.headless import build_default_llm
    from coworker.workflows.feedback import run_workflow_revision

    current = workflow_manager.export(name)
    if current.get("status") != "ok":
        raise _not_found(name)
    llm = build_default_llm(provider_manager, settings.data_dir)
    if llm is None:
        raise HTTPException(status_code=400, detail="no enabled provider configured")

    revision = await run_workflow_revision(
        llm,
        current["yaml"],
        payload.feedback,
        step_id=payload.step_id,
        run_id=payload.run_id,
    )
    if revision.get("status") != "ok":
        raise HTTPException(status_code=400, detail=revision.get("message", "revision failed"))

    validation = workflow_manager.validate(revision["yaml"])
    if not validation.get("valid"):
        raise HTTPException(status_code=400, detail="; ".join(validation.get("errors", [])) or "invalid revision")

    if payload.apply:
        result = workflow_manager.update(name, revision["yaml"])
    else:
        result = workflow_manager.stage_draft(
            name, revision["yaml"], sources=[f"feedback:{name}"], action="update"
        )
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message", "feedback failed"))
    return {"status": "ok", "applied": bool(payload.apply), "result": result, "yaml": revision["yaml"]}


class WorkflowDuplicatePayload(BaseModel):
    new_name: str = ""


@router.post("/workflows/{name}/duplicate")
def duplicate_workflow(name: str, payload: WorkflowDuplicatePayload):
    result = workflow_manager.duplicate(name, payload.new_name)
    if result.get("status") != "ok":
        code = 404 if "not found" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "duplicate failed"))
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
