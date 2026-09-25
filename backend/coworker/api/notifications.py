# -*- coding: utf-8 -*-
"""Notification log API (unattended run failures / needs-human)."""

from __future__ import annotations

from fastapi import APIRouter

from coworker.api.state import app, settings
from coworker.notifications import clear_notifications, list_notifications

router = APIRouter()


@router.get("/notifications")
def get_notifications(limit: int = 50):
    items = list_notifications(settings.data_dir, limit)
    return {"status": "ok", "notifications": items, "unread": sum(1 for n in items if not n.get("read"))}


@router.post("/notifications/clear")
def clear():
    removed = clear_notifications(settings.data_dir)
    return {"status": "ok", "removed": removed}
