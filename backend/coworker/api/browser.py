# -*- coding: utf-8 -*-
"""Browser personal-data API (bookmarks / history / downloads).

The embedded browser runs in Electron; its navigation and download events are
reported here by the Electron main process, and the renderer's bookmark /
history / download panels read and mutate the same data through these routes.
Passwords are deliberately absent — they stay in the Electron main process.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from coworker.api.state import settings
from coworker.browser.settings import read_browser_settings, write_browser_settings
from coworker.browser.store import BrowserStore

logger = logging.getLogger(__name__)

router = APIRouter()


def _store() -> BrowserStore:
    return BrowserStore(settings.data_dir)


class BookmarkCreate(BaseModel):
    url: str
    title: str = ""


class BookmarkUpdate(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None


class BookmarkReorder(BaseModel):
    ids: list[str]


class HistoryCreate(BaseModel):
    url: str
    title: str = ""


class DownloadCreate(BaseModel):
    id: str
    url: str = ""
    filename: str = ""
    path: str = ""
    mime: str = ""
    total_bytes: int = 0


class DownloadUpdate(BaseModel):
    path: Optional[str] = None
    state: Optional[str] = None
    received_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    filename: Optional[str] = None
    ended_at: Optional[str] = None


class BrowserSettingsUpdate(BaseModel):
    restore_tabs: Optional[bool] = None
    download_dir: Optional[str] = None
    ask_where_to_save: Optional[bool] = None
    password_manager_enabled: Optional[bool] = None
    password_autofill: Optional[bool] = None
    permissions_prompt: Optional[bool] = None


@router.get("/api/browser/settings")
async def get_browser_settings() -> dict[str, Any]:
    return read_browser_settings(settings.data_dir)


@router.post("/api/browser/settings")
async def save_browser_settings(request: BrowserSettingsUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in request.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        return read_browser_settings(settings.data_dir)
    try:
        return write_browser_settings(settings.data_dir, patch)
    except OSError as exc:  # noqa: BLE001 - settings persistence must not fail the request
        logger.warning("Failed to persist browser settings: %s", exc)
        return read_browser_settings(settings.data_dir)


@router.get("/api/browser/bookmarks")
async def list_bookmarks() -> dict[str, Any]:
    return {"items": _store().list_bookmarks()}


@router.post("/api/browser/bookmarks")
async def add_bookmark(request: BookmarkCreate) -> dict[str, Any]:
    try:
        return {"item": _store().add_bookmark(request.url, request.title)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/browser/bookmarks/reorder")
async def reorder_bookmarks(request: BookmarkReorder) -> dict[str, Any]:
    return {"items": _store().reorder_bookmarks(request.ids)}


@router.patch("/api/browser/bookmarks/{bookmark_id}")
async def update_bookmark(bookmark_id: str, request: BookmarkUpdate) -> dict[str, Any]:
    updated = _store().update_bookmark(bookmark_id, url=request.url, title=request.title)
    if updated is None:
        raise HTTPException(status_code=404, detail="bookmark not found")
    return {"item": updated}


@router.delete("/api/browser/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str) -> dict[str, Any]:
    return {"ok": _store().remove_bookmark(bookmark_id)}


@router.get("/api/browser/history")
async def list_history(query: str = "", limit: int = 500) -> dict[str, Any]:
    return {"items": _store().list_history(query=query, limit=limit)}


@router.post("/api/browser/history")
async def add_history(request: HistoryCreate) -> dict[str, Any]:
    return {"item": _store().add_history(request.url, request.title)}


@router.delete("/api/browser/history")
async def clear_history() -> dict[str, Any]:
    _store().clear_history()
    return {"ok": True}


@router.delete("/api/browser/history/{entry_id}")
async def delete_history_entry(entry_id: str) -> dict[str, Any]:
    return {"ok": _store().remove_history(entry_id)}


@router.get("/api/browser/downloads")
async def list_downloads(limit: int = 200) -> dict[str, Any]:
    return {"items": _store().list_downloads(limit=limit)}


@router.post("/api/browser/downloads")
async def add_download(request: DownloadCreate) -> dict[str, Any]:
    return {
        "item": _store().add_download(
            download_id=request.id,
            url=request.url,
            filename=request.filename,
            path=request.path,
            mime=request.mime,
            total_bytes=request.total_bytes,
        )
    }


@router.patch("/api/browser/downloads/{download_id}")
async def update_download(download_id: str, request: DownloadUpdate) -> dict[str, Any]:
    updated = _store().update_download(download_id, **request.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="download not found")
    return {"item": updated}


@router.delete("/api/browser/downloads")
async def clear_downloads() -> dict[str, Any]:
    _store().clear_downloads()
    return {"ok": True}


@router.delete("/api/browser/downloads/{download_id}")
async def delete_download(download_id: str) -> dict[str, Any]:
    return {"ok": _store().remove_download(download_id)}
