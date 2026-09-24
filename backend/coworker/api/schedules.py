# -*- coding: utf-8 -*-
"""Cron schedule HTTP API (independent from workflows)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from coworker.api.state import app, schedule_manager

router = APIRouter()


class SchedulePayload(BaseModel):
    name: str | None = None
    target_type: str | None = None
    workflow: str | None = None
    command: str | None = None
    prompt: str | None = None
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    overlap: str | None = None
    misfire: str | None = None
    grace_seconds: int | None = None
    retry_max: int | None = None
    retry_backoff: int | None = None
    timeout_seconds: int | None = None


class SchedulePreviewPayload(BaseModel):
    cron: str
    timezone: str = "UTC"
    count: int = 5


class ScheduleEnabledPayload(BaseModel):
    enabled: bool = True


def _not_found(schedule_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"schedule not found: {schedule_id}")


@router.get("/schedules")
def list_schedules():
    return {"status": "ok", "schedules": schedule_manager.list()}


@router.post("/schedules/preview")
def preview_schedule(payload: SchedulePreviewPayload):
    return schedule_manager.preview(payload.cron, payload.timezone, payload.count)


@router.post("/schedules/validate")
def validate_schedule(payload: SchedulePayload):
    return schedule_manager.validate_payload(payload.model_dump(exclude_none=True))


@router.post("/schedules")
def create_schedule(payload: SchedulePayload):
    result = schedule_manager.create(payload.model_dump(exclude_none=True))
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message", "create failed"))
    return result


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str):
    data = schedule_manager.get(schedule_id)
    if data is None:
        raise _not_found(schedule_id)
    return {"status": "ok", "schedule": data}


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: SchedulePayload):
    result = schedule_manager.update(schedule_id, payload.model_dump(exclude_none=True))
    if result.get("status") != "ok":
        code = 404 if "not found" in (result.get("message") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("message", "update failed"))
    return result


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    result = schedule_manager.delete(schedule_id)
    if result.get("status") != "ok":
        raise _not_found(schedule_id)
    return result


@router.post("/schedules/{schedule_id}/enable")
def enable_schedule(schedule_id: str):
    result = schedule_manager.set_enabled(schedule_id, True)
    if result.get("status") != "ok":
        raise _not_found(schedule_id)
    return result


@router.post("/schedules/{schedule_id}/disable")
def disable_schedule(schedule_id: str):
    result = schedule_manager.set_enabled(schedule_id, False)
    if result.get("status") != "ok":
        raise _not_found(schedule_id)
    return result


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str):
    result = await schedule_manager.run_now(schedule_id)
    if result.get("status") == "error" and "not found" in (result.get("message") or ""):
        raise _not_found(schedule_id)
    return result


@router.get("/schedules/{schedule_id}/runs")
def list_schedule_runs(schedule_id: str, limit: int = 50):
    if schedule_manager.get(schedule_id) is None:
        raise _not_found(schedule_id)
    return {"status": "ok", "runs": schedule_manager.list_runs(schedule_id, limit)}
