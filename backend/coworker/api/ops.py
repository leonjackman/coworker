# -*- coding: utf-8 -*-

import asyncio
import uuid
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from coworker.traces import AGENT_TRACE_FILENAME, MAX_TRACE_LINES
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.api.state import (
    agent_registry,
    app,
    settings,
    tool_audit_path
)

from fastapi import APIRouter

router = APIRouter()


@router.get("/audit/tool")
async def tool_audit(limit: int = 100):
    return {"status": "ok", "events": list_tool_audit_events(tool_audit_path, limit)}
@router.get("/audit/tool/export")
async def export_tool_audit():
    """Export the full tool-audit JSONL as a text download."""
    from fastapi.responses import PlainTextResponse

    text = ""
    try:
        if tool_audit_path.exists():
            text = tool_audit_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return PlainTextResponse(text, media_type="text/plain")
@router.post("/audit/tool/clear")
async def clear_tool_audit():
    """Empty the tool-audit log (retention trim keeps it bounded afterwards)."""
    from coworker.atomicio import atomic_write_text

    try:
        if tool_audit_path.exists():
            atomic_write_text(tool_audit_path, "")
    except OSError:
        pass
    return {"status": "ok"}
@router.get("/traces/agent")
async def agent_traces(limit: int = 100):
    return {"status": "ok", "events": agent_registry.list_agent_traces(limit)}
@router.get("/traces/agent/export")
async def export_agent_traces():
    """Export the full agent-trace JSONL as a text download."""
    from fastapi.responses import PlainTextResponse

    trace_path = settings.data_dir / AGENT_TRACE_FILENAME
    text = ""
    try:
        if trace_path.exists():
            text = trace_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return PlainTextResponse(text, media_type="text/plain")
@router.post("/traces/agent/clear")
async def clear_agent_traces():
    """Empty the agent-trace log (retention trim keeps it bounded afterwards)."""
    from coworker.atomicio import atomic_write_text

    trace_path = settings.data_dir / AGENT_TRACE_FILENAME
    try:
        if trace_path.exists():
            atomic_write_text(trace_path, "")
    except OSError:
        pass
    return {"status": "ok"}
@router.get("/checkpoints/export")
async def export_checkpoints():
    """Download the per-session checkpoint files as a zip (best-effort copy)."""
    from starlette.background import BackgroundTask
    from fastapi import BackgroundTasks
    from fastapi.responses import FileResponse
    import shutil

    ck_dir = agent_registry.checkpoints_dir
    if not ck_dir.is_dir() or not any(ck_dir.glob("*.json")):
        return {"status": "ok", "size": 0, "note": "no checkpoints yet"}
    tmp = ck_dir.with_name(f"checkpoints.export.{uuid.uuid4().hex[:8]}.zip")
    try:
        import zipfile
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(ck_dir.glob("*.json")):
                zf.write(f, arcname=f.name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to snapshot checkpoints: {exc}") from exc

    def _cleanup() -> None:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    return FileResponse(
        str(tmp),
        media_type="application/zip",
        filename="coworker-checkpoints.zip",
        background=BackgroundTasks([BackgroundTask(_cleanup)]),
    )
@router.post("/checkpoints/clear")
async def clear_checkpoints():
    """Delete all runtime checkpoint threads (active streams are skipped)."""
    stats = await asyncio.to_thread(agent_registry.checkpoint_manager.clear_all)
    return {"status": "ok", "stats": stats}
