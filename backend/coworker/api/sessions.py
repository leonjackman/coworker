# -*- coding: utf-8 -*-

import asyncio
import json
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.agent.core import (
    AgentMode,
    Language,
    _merge_event_parts,
    context_budget_chars,
    context_budget_tokens,
    format_user_message,
    is_provider_bad_request,
    normalize_autonomy,
    normalize_work_mode,
    _runtime_context_budget,
)
from coworker.events import WorkerEventBus, session_event_bus, worker_event_bus
from coworker.sessions import SessionStore, _now
from coworker.goal_feature import goal_feature
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from fastapi.responses import StreamingResponse
from coworker.api.state import (
    agent_registry,
    app,
    command_approval_store,
    logger,
    project_store,
    session_store,
    workspace_controller
)
from coworker.api.streaming import (
    _cleanup_session_screenshots,
    _emit_goal_cleared,
    _emit_goal_updated,
    _force_stop_session_stream,
    _guard_session_not_streaming,
    _hard_stop_session_stream,
    _provider_name_for_id,
    _publish_turn,
    _require_goal,
    _resolve_run_provider,
    _revert_turn_changes,
    _session_context_usage_snapshot,
    _session_goal,
    _session_message_history,
    _session_referenced_ids
)

from fastapi import APIRouter

router = APIRouter()


class SessionCreateRequest(BaseModel):
    title: str = ""
    project_id: str = ""
    agent_id: str = ""
class SessionRenameRequest(BaseModel):
    title: str
@router.get("/sessions")
async def list_sessions(project_id: str | None = None):
    return {"status": "ok", "sessions": session_store.list_sessions(project_id)}
@router.get("/sessions/active")
async def list_active_sessions():
    """Return session ids that currently have an in-flight stream (running/active)."""
    return {"status": "ok", "session_ids": sorted(agent_registry.checkpoint_manager.active_sessions())}
@router.post("/sessions")
async def create_session(request: SessionCreateRequest):
    if not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    try:
        project_store.require(request.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = session_store.create(request.title, project_id=request.project_id, agent_id=request.agent_id)
    return {"status": "ok", "session": session.public()}
@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.full()}
@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    # Hard-terminate the session's in-flight stream/task first, so a delete
    # always succeeds even while the session is mid-generation. The streaming
    # guard below then returns promptly once the teardown marks the session idle.
    _hard_stop_session_stream(session_id)
    await _guard_session_not_streaming(session_id)
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    # Checkpoint teardown just unlinks a per-session JSON file; still run it in
    # the background so a huge file never pushes the delete past the caller's
    # timeout (the session record is already gone, so cleanup is safe).
    asyncio.create_task(agent_registry.forget_runtime_checkpoint(session_id))
    agent_registry.evict_runtime(session_id)
    agent_registry.change_store.delete_session(session_id)
    agent_registry.snapshot_manager.delete_session(session_id)
    # Orphaned approvals would otherwise stay pending forever (active records
    # are never pruned) and linger in the global to-do view with no resume
    # target — purge them together with the session.
    command_approval_store.purge_session(session_id)
    _cleanup_session_screenshots(session_id)
    return {"status": "ok"}
@router.post("/sessions/{session_id}/stop")
async def stop_session_stream(session_id: str):
    """Explicitly stop the session's in-flight generation (user pressed Stop).

    A client abort alone is not enough to free the session: uvicorn/Starlette
    can fail to propagate the disconnect promptly, so the stream keeps running
    and the session stays marked "active" — which makes the next edit or
    regenerate fail with ``409 session is still generating``. Force-cancelling
    the stream task (and marking the session idle) makes Stop deterministic and
    idempotent: stopping an idle session is a no-op.
    """
    _force_stop_session_stream(session_id)
    return {"status": "ok", "session_id": session_id}
@router.post("/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: SessionRenameRequest):
    try:
        session = session_store.rename(session_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "session": session.public()}
@router.post("/sessions/{session_id}/read")
async def mark_session_read(session_id: str):
    """Mark all messages in the session as read (set last_read_at to now).

    Called by the frontend when the user opens a session or explicitly clears
    unread state.  Idempotent.

    Opening a session also counts as having seen its error state, so the
    persisted `last_error` marker is cleared here as well — otherwise the error
    badge would linger forever after the user has already viewed it.
    """
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.last_read_at = _now()
    session.last_error = ""
    session_store.save(session)
    return {"status": "ok", "session": session.public()}
class GenerateTitleRequest(BaseModel):
    first_user_message: str = ""
    assistant_response: str = ""
    language: Language = "zh"
@router.post("/sessions/{session_id}/generateTitle")
async def generate_title_endpoint(session_id: str, request: GenerateTitleRequest):
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not session.title_auto:
        return {"status": "ok", "title": session.title}
    user_message = ""
    assistant_message = ""
    for message in session.messages:
        if message.role == "user" and not user_message:
            user_message = message.content
        elif message.role == "assistant" and not assistant_message:
            assistant_message = message.content
        if user_message and assistant_message:
            break
    if not user_message:
        return {"status": "ok", "title": session.title}
    from coworker.agent.core import generate_title
    new_title = await asyncio.to_thread(
        generate_title,
        user_message,
        assistant_message or request.assistant_response or "",
        request.language,
    )
    final_title = new_title or session.title
    session_store.rename(session_id, final_title)
    return {"status": "ok", "title": final_title}
class GoalSetRequest(BaseModel):
    session_id: str
    objective: str
    token_budget: int | None = None
    # 前端乐观渲染的 /goal user 泡泡元数据：持久化进 session 保证重载/重进会话不消失，
    # 且用 user_message_id 保证前后端消息 id 一致（可编辑/回退）。
    user_message_id: str | None = None
    provider: str = ""
    model: str = ""
    work_mode: str = ""
    autonomy: str = ""
class GoalControlRequest(BaseModel):
    session_id: str
class GoalEditRequest(BaseModel):
    session_id: str
    objective: str
@router.post("/goal/set")
async def goal_set(request: GoalSetRequest):
    """设定并激活目标：置 active、计数清零、广播 ``goal_updated``。

    空闲会话下前端拿到返回的 ``active`` 后必须立即发起一次
    ``skip_user_append=True`` 的 /chat/stream 触发续跑（见设计文档 §3.3.2）。
    """
    _require_goal(request.session_id)
    if not goal_feature.is_enabled():
        raise HTTPException(status_code=403, detail="goal capability is disabled (enable it in Settings)")
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")
    goal = session_store.set_goal(request.session_id, objective, request.token_budget)
    # 持久化目标设定 user 消息：前端乐观渲染的 /goal 泡泡在重载/重进会话后不消失，
    # 且沿用前端传入的 id（编辑/回退时按 id 检索不会 404）。
    try:
        session = session_store.require(request.session_id)
        session_store.append_message(
            request.session_id,
            role="user",
            content=objective,
            mode="single",
            provider=request.provider or "",
            model=request.model or "",
            work_mode=request.work_mode or session.work_mode,
            autonomy=request.autonomy or session.autonomy,
            message_id=request.user_message_id or None,
        )
    except Exception:  # noqa: BLE001 - 持久化失败不影响 goal 设定
        logger.warning("goal/set persist user message failed for %s", request.session_id, exc_info=True)
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}
@router.post("/goal/pause")
async def goal_pause(request: GoalControlRequest):
    """暂停目标作用（不中止进行中的流）：仅 active/budget_limited 可暂停。"""
    _require_goal(request.session_id)
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status not in {"active", "budget_limited"}:
        raise HTTPException(status_code=409, detail=f"cannot pause a {current.status} goal")
    goal = session_store.update_goal_status(request.session_id, "paused")
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}
@router.post("/goal/resume")
async def goal_resume(request: GoalControlRequest):
    """恢复目标作用：仅 paused 可恢复。"""
    _require_goal(request.session_id)
    if not goal_feature.is_enabled():
        raise HTTPException(status_code=403, detail="goal capability is disabled (enable it in Settings)")
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status != "paused":
        raise HTTPException(status_code=409, detail="goal is not paused")
    goal = session_store.update_goal_status(request.session_id, "active")
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}
@router.post("/goal/edit")
async def goal_edit(request: GoalEditRequest):
    """修改 objective（仅 active 可编辑，状态保持 active）。"""
    _require_goal(request.session_id)
    objective = request.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")
    current = session_store.get_goal(request.session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="no active goal")
    if current.status != "active":
        raise HTTPException(status_code=409, detail=f"cannot edit a {current.status} goal")
    goal = session_store.update_goal_objective(request.session_id, objective)
    return {"status": "ok", "goal": _emit_goal_updated(request.session_id, goal)}
@router.post("/goal/clear")
async def goal_clear(request: GoalControlRequest):
    """清除目标：终止一切续跑并清状态条。"""
    _require_goal(request.session_id)
    cleared = session_store.clear_goal(request.session_id)
    if cleared:
        _emit_goal_cleared(request.session_id)
    return {"status": "ok", "cleared": cleared}
@router.get("/goal")
async def goal_get(session_id: str):
    _require_goal(session_id)
    # 能力关闭时按无目标处理：前端拿到 null 不会渲染 GoalCard / 不会触发续跑流。
    goal = _session_goal(session_id)
    return {"status": "ok", "goal": goal.to_dict() if goal is not None else None}
class EditMessageRequest(BaseModel):
    content: str
    work_mode: Optional[str] = None
    autonomy: Optional[str] = None
    language: Language = "zh"
    revert_code: bool = True
    assistant_message_id: str = ""
    # Provider/model for the re-run. When empty, falls back to the session's
    # last-run provider/model (legacy clients).
    provider_id: str = ""
    model: str = ""
class RegenerateRequest(BaseModel):
    language: Language = "zh"
    assistant_message_id: str = ""
    # Provider/model for the re-run. When empty, falls back to the session's
    # last-run provider/model (legacy clients).
    provider_id: str = ""
    model: str = ""
class EditBeginRequest(BaseModel):
    revert_code: bool = True
@router.get("/sessions/{session_id}/context-usage")
async def session_context_usage(session_id: str, provider_id: str = "", model: str = ""):
    # Surface the CURRENT context-window usage for a session without running it,
    # so opening an older session shows the live window (e.g. 192k after a
    # context_window change) rather than the stale last-run figure (e.g. 252k).
    try:
        session = session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        snap = _session_context_usage_snapshot(session, provider_id, model)
    except Exception:  # noqa: BLE001 - never let a preview crash the open flow
        snap = None
    if snap is None:
        raise HTTPException(status_code=404, detail="no enabled provider")
    return {"status": "ok", "context_usage": snap}
@router.post("/sessions/{session_id}/messages/{message_id}/redo")
async def redo_message(session_id: str, message_id: str, keep_state: bool = False):
    """Re-apply the file changes that editing the given user message reverted
    (undo-the-undo). Each ``reverted`` change record bound to that edit is
    restored to its recorded ``after`` content, with conflict detection: a
    record whose file no longer matches the recorded ``before`` state (e.g. the
    user hand-edited it) is reported as a conflict and left untouched. Records
    restored successfully are removed from the change store.

    With ``keep_state=true`` (cancelling a pending edit) the records and the
    snapshot pair return to ``active`` instead of being discarded, so the same
    message can be reverted again by a later edit.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session_store.require(session_id)
        workspace = workspace_controller.workspace_for_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records = agent_registry.change_store.reverted_records(session_id, message_id)
    restored: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for record in records:
        result = workspace.redo_change(record)
        if result.get("status") == "restored":
            restored.append(result)
        else:
            conflicts.append(result)
    restored_ids = [r.get("id") for r in restored if r.get("id")]
    if restored_ids:
        if keep_state:
            agent_registry.change_store.mark_active(session_id, restored_ids)
        else:
            agent_registry.change_store.delete_records(session_id, restored_ids)
    # Git-backed redo for the records whose content was too large for the
    # change store (and any shell-driven changes reverted during the edit).
    try:
        git_redo = await asyncio.to_thread(
            agent_registry.snapshot_manager.redo_turn, session_id, message_id, workspace, keep_state=keep_state,
        )
    except Exception:  # noqa: BLE001 - snapshot redo must never break the request
        logger.warning("redo_message: snapshot redo skipped for %s: %s", session_id, exc_info=True)
        git_redo = {}
    restored += git_redo.get("restored") or []
    conflicts += git_redo.get("conflicts") or []
    return {
        "status": "ok",
        "session_id": session_id,
        "message_id": message_id,
        "restored": restored,
        "conflicts": conflicts,
        "restored_count": len(restored),
        "conflict_count": len(conflicts),
    }
@router.post("/sessions/{session_id}/messages/{message_id}/edit-begin")
async def edit_message_begin(session_id: str, message_id: str, request: EditBeginRequest):
    """Enter edit mode for a user message: revert its downstream file changes
    immediately (two-layer revert) so the user sees a clean file state while
    editing. No messages are truncated or re-run yet. If ``revert_code`` is off
    this is a no-op revert (returns zeros). A later ``edit-cancel`` restores the
    files and returns the records to ``active``.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        if session.messages[index].role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="message not found") from exc

    dropped_assistant_ids = [m.id for m in session.messages[index + 1 :] if m.role == "assistant"]
    if request.revert_code and dropped_assistant_ids:
        revert_summary = await _revert_turn_changes(session_id, message_id, dropped_assistant_ids, mark=True)
    else:
        revert_summary = {"reverted_count": 0, "conflict_count": 0, "total": 0, "reverted_paths": []}
    return {"status": "ok", "session_id": session_id, "message_id": message_id, **revert_summary}
@router.post("/sessions/{session_id}/messages/{message_id}/edit-cancel")
async def edit_message_cancel(session_id: str, message_id: str):
    """Cancel a pending edit: restore the files the ``edit-begin`` reverted and
    return the change records and snapshot pair to ``active`` so the message can
    be reverted again. Conflicts (files changed in the meantime) are reported
    and left untouched.
    """
    await _guard_session_not_streaming(session_id)
    try:
        session_store.require(session_id)
        workspace = workspace_controller.workspace_for_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records = agent_registry.change_store.reverted_records(session_id, message_id)
    restored: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for record in records:
        result = workspace.redo_change(record)
        if result.get("status") == "restored":
            restored.append(result)
        else:
            conflicts.append(result)
    restored_ids = [r.get("id") for r in restored if r.get("id")]
    if restored_ids:
        agent_registry.change_store.mark_active(session_id, restored_ids)
    try:
        git_redo = await asyncio.to_thread(
            agent_registry.snapshot_manager.redo_turn, session_id, message_id, workspace, keep_state=True,
        )
    except Exception:  # noqa: BLE001 - snapshot redo must never break the request
        logger.warning("edit_message_cancel: snapshot restore skipped for %s: %s", session_id, exc_info=True)
        git_redo = {}
    restored += git_redo.get("restored") or []
    conflicts += git_redo.get("conflicts") or []
    return {
        "status": "ok",
        "session_id": session_id,
        "message_id": message_id,
        "restored": restored,
        "conflicts": conflicts,
        "restored_count": len(restored),
        "conflict_count": len(conflicts),
    }
@router.post("/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_message(session_id: str, message_id: str, request: RegenerateRequest):
    """Re-run the assistant reply for the user message that precedes the given
    assistant message (or, if given a user message, for that user message).
    Truncates after that user message and streams a fresh reply."""
    await _guard_session_not_streaming(session_id)
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        target_message = session.messages[index]
        # Walk back to the triggering user message.
        user_index = index
        if target_message.role == "assistant":
            user_index = index - 1
        while user_index >= 0 and session.messages[user_index].role != "user":
            user_index -= 1
        if user_index < 0:
            raise HTTPException(status_code=400, detail="No user message to regenerate from")
        user_message = session.messages[user_index]
        # Regeneration drops the assistant messages after this user message and
        # reverts their file changes (two-layer revert, same as edit) so the
        # re-run starts from a clean file state. Reverted records stay redo-able;
        # conflicted ones are hidden.
        dropped_assistant_ids = [m.id for m in session.messages[user_index:] if m.role == "assistant"]
        revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0}
        if dropped_assistant_ids:
            revert_summary = await _revert_turn_changes(session_id, user_message.id, dropped_assistant_ids, mark=True)
        session_store.truncate_from(session_id, user_message.id)
        # Checkpoint delete is routed through the single shared checkpointer
        # (single-writer model) — no thread needed, never contends.
        await agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_id, model = _resolve_run_provider(session, request.provider_id, request.model)
        provider_name = _provider_name_for_id(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(session.work_mode)
    autonomy = normalize_autonomy(session.autonomy)
    language = request.language
    try:
        workspace_path = workspace_controller.workspace_for_session(session_id).root
    except ValueError as exc:
        # The session's project workspace is missing/invalid. Refuse instead of
        # silently re-running in the DEFAULT workspace (which would write files
        # in the wrong place under the project's session).
        raise HTTPException(status_code=400, detail=f"workspace unavailable: {exc}") from exc
    except Exception:  # noqa: BLE001 - genuinely workspace-less sessions use the default
        workspace_path = None

    async def event_stream():
        accumulated_content = ""
        terminal_sent = False
        interrupt_emitted = False

        # Surface the file-revert result to the UI as the first event, so the
        # client can show "reverted N file changes / restore" right away.
        if revert_summary.get("reverted_count", 0) > 0 or revert_summary.get("conflict_count", 0) > 0:
            yield f"data: {json.dumps({'type': 'revert_summary', 'session_id': session_id, **revert_summary}, ensure_ascii=False)}\n\n"

        def _persist_partial():
            nonlocal accumulated_content, terminal_sent
            if not accumulated_content or terminal_sent:
                return
            terminal_sent = True
            try:
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=accumulated_content,
                    mode="single",
                    provider=provider_name,
                    model=model,
                    work_mode=work_mode,
                    autonomy=autonomy,
                    # Generated id (not the client-supplied one): a partial reply
                    # persisted on error must never be adopted as a successful
                    # commit by the frontend's stream-settle reconciliation.
                    message_id=None,
                    parts=[],
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                logger.exception("Failed to persist partial rerun for session %s", session_id)

        def _on_event(event):
            nonlocal accumulated_content, terminal_sent, interrupt_emitted
            event["session_id"] = session_id
            if event.get("type") in ("approval_required", "question_required"):
                # Agent interrupted to ask the user: the turn is NOT complete. No
                # synthetic done will follow, so persist the partial reply on end
                # (message_id=None) to keep the question context after a refresh.
                interrupt_emitted = True
            if event.get("type") == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if event.get("type") == "done":
                terminal_sent = True
                try:
                    session = session_store.append_message(
                        session_id,
                        role="assistant",
                        content=event["content"],
                        mode="single",
                        provider=event.get("provider") or provider_name,
                        model=event.get("model") or model,
                        work_mode=work_mode,
                        autonomy=autonomy,
                        message_id=request.assistant_message_id or None,
                        parts=event.get("parts") or [],
                    )
                    last = session.messages[-1] if session.messages else None
                    if last is not None:
                        agent_registry.change_store.assign_message(session_id, last.id)
                except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                    logger.exception("Failed to persist rerun assistant message for session %s", session_id)

        def _on_end():
            # Normal end after an interrupt (question/approval asked): no done
            # frame was emitted — persist the partial so the turn survives refresh.
            if interrupt_emitted and not terminal_sent:
                _persist_partial()
            return None

        def _on_error(exc):
            # Persist whatever was produced before the failure so tool-change
            # records from this stream stay bound to a message (rollback needs
            # the binding).
            _persist_partial()
            return {"type": "error", "session_id": session_id, "error": str(exc)[:400]}

        snapshot_workspace = None
        try:
            snapshot_workspace = workspace_controller.workspace_for_session(session_id)
        except (ValueError, KeyError, Exception):  # noqa: BLE001 - default/workspace-less sessions skip snapshot
            snapshot_workspace = None
        snapshot_pre = None
        if snapshot_workspace is not None:
            snapshot_pre = await asyncio.to_thread(
                agent_registry.snapshot_manager.begin_turn, session_id, user_message.id, snapshot_workspace,
            )
        # Same bus-fed turn as /chat/stream: live delivery even while the graph
        # is blocked on a long-running tool / worker.
        stream_iter = agent_registry.rerun_stream(
            history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids, workspace_path=workspace_path, agent=session.agent_id or DEFAULT_AGENT, project_id=session.project_id or None,
        )
        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            turn_task.cancel()
            # Safety net (see /chat/stream): the client is gone — force-stop the
            # underlying stream consumer and release the session so the next
            # edit/regenerate isn't rejected with 409 "session is still generating".
            # Idempotent; the interrupted marker is set by _on_error, not here.
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # Turn over: delete the disposable checkpoint thread unless it paused
            # for an approval/question (the interrupt checkpoint must survive resume).
            # Shielded so it completes even if the client disconnects after done.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)
            if snapshot_pre is not None and snapshot_workspace is not None:
                await asyncio.to_thread(
                    agent_registry.snapshot_manager.end_turn, session_id, user_message.id, snapshot_workspace,
                )
            # Persist error if the turn failed (no done event was emitted)
            if not terminal_sent and session_id:
                try:
                    session = session_store.require(session_id)
                    if not session.last_error:
                        session.last_error = "stream terminated without done event"
                    session_store.save(session)
                except KeyError:
                    pass

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
@router.post("/sessions/{session_id}/messages/{message_id}/edit")
async def edit_message(session_id: str, message_id: str, request: EditMessageRequest):
    """Edit a user message and re-run the conversation from that point."""
    await _guard_session_not_streaming(session_id)
    revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0}
    try:
        session = session_store.require(session_id)
        index = session_store.find_message_index(session_id, message_id)
        if session.messages[index].role != "user":
            raise HTTPException(status_code=400, detail="Only user messages can be edited")
        # Editing re-runs from this user message: the assistant messages after
        # it are dropped. When ``revert_code`` is on (default), their file
        # changes are reverted first (safe inverse edits, conflict-detected),
        # mirroring opencode/Codex "edit = redo from a clean file state". Only
        # ``active`` records are reverted: if ``edit-begin`` already reverted
        # this message's turn, the records are now ``reverted`` and this step is
        # idempotently skipped.
        dropped_assistant_ids = [m.id for m in session.messages[index + 1 :] if m.role == "assistant"]
        if request.revert_code and dropped_assistant_ids:
            revert_summary = await _revert_turn_changes(session_id, message_id, dropped_assistant_ids, mark=True)
        session_store.update_message_content(session_id, message_id, request.content)
        session_store.truncate_from(session_id, message_id)
        # Checkpoint delete is routed through the single shared checkpointer
        # (single-writer model) — no thread needed, never contends.
        await agent_registry.forget_runtime_checkpoint(session_id)
        session = session_store.require(session_id)
        history = _session_message_history(session)
        referenced_ids = _session_referenced_ids(session)
        provider_id, model = _resolve_run_provider(session, request.provider_id, request.model)
        provider_name = _provider_name_for_id(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work_mode = normalize_work_mode(request.work_mode or session.work_mode)
    if request.autonomy:
        autonomy = normalize_autonomy(request.autonomy)
    else:
        autonomy = normalize_autonomy(session.autonomy)
    if request.work_mode or request.autonomy:
        try:
            session_store.update_modes(session_id, work_mode, autonomy)
        except KeyError:
            pass
    language = request.language
    try:
        workspace_path = workspace_controller.workspace_for_session(session_id).root
    except ValueError as exc:
        # The session's project workspace is missing/invalid. Refuse instead of
        # silently re-running in the DEFAULT workspace (which would write files
        # in the wrong place under the project's session).
        raise HTTPException(status_code=400, detail=f"workspace unavailable: {exc}") from exc
    except Exception:  # noqa: BLE001 - genuinely workspace-less sessions use the default
        workspace_path = None

    async def event_stream():
        accumulated_content = ""
        terminal_sent = False
        interrupt_emitted = False

        # Surface the file-revert result to the UI as the first event, so the
        # client can show "reverted N file changes / restore" right away.
        if revert_summary.get("reverted_count", 0) > 0 or revert_summary.get("conflict_count", 0) > 0:
            yield f"data: {json.dumps({'type': 'revert_summary', 'session_id': session_id, **revert_summary}, ensure_ascii=False)}\n\n"

        def _persist_partial():
            nonlocal accumulated_content, terminal_sent
            if not accumulated_content or terminal_sent:
                return
            terminal_sent = True
            try:
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=accumulated_content,
                    mode="single",
                    provider=provider_name,
                    model=model,
                    work_mode=work_mode,
                    autonomy=autonomy,
                    # Generated id (not the client-supplied one): a partial reply
                    # persisted on error must never be adopted as a successful
                    # commit by the frontend's stream-settle reconciliation.
                    message_id=None,
                    parts=[],
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                logger.exception("Failed to persist partial rerun for session %s", session_id)

        def _on_event(event):
            nonlocal accumulated_content, terminal_sent, interrupt_emitted
            event["session_id"] = session_id
            if event.get("type") in ("approval_required", "question_required"):
                # Agent interrupted to ask the user: the turn is NOT complete. No
                # synthetic done will follow, so persist the partial reply on end
                # (message_id=None) to keep the question context after a refresh.
                interrupt_emitted = True
            if event.get("type") == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if event.get("type") == "done":
                terminal_sent = True
                try:
                    session = session_store.append_message(
                        session_id,
                        role="assistant",
                        content=event["content"],
                        mode="single",
                        provider=event.get("provider") or provider_name,
                        model=event.get("model") or model,
                        work_mode=work_mode,
                        autonomy=autonomy,
                        message_id=request.assistant_message_id or None,
                        parts=event.get("parts") or [],
                    )
                    last = session.messages[-1] if session.messages else None
                    if last is not None:
                        agent_registry.change_store.assign_message(session_id, last.id)
                except Exception:  # noqa: BLE001 - persistence must never break the terminal event
                    logger.exception("Failed to persist rerun assistant message for session %s", session_id)

        def _on_end():
            # Normal end after an interrupt (question/approval asked): no done
            # frame was emitted — persist the partial so the turn survives refresh.
            if interrupt_emitted and not terminal_sent:
                _persist_partial()
            return None

        def _on_error(exc):
            # Persist whatever was produced before the failure so tool-change
            # records from this stream stay bound to a message (rollback needs
            # the binding).
            _persist_partial()
            return {"type": "error", "session_id": session_id, "error": str(exc)[:400]}

        snapshot_workspace = None
        try:
            snapshot_workspace = workspace_controller.workspace_for_session(session_id)
        except (ValueError, KeyError, Exception):  # noqa: BLE001 - default/workspace-less sessions skip snapshot
            snapshot_workspace = None
        snapshot_pre = None
        if snapshot_workspace is not None:
            snapshot_pre = await asyncio.to_thread(
                agent_registry.snapshot_manager.begin_turn, session_id, message_id, snapshot_workspace,
            )
        # Same bus-fed turn as /chat/stream: live delivery even while the graph
        # is blocked on a long-running tool / worker.
        stream_iter = agent_registry.rerun_stream(
            history, session_id, language, work_mode, autonomy, provider_id=provider_id, model=model, referenced_sessions=referenced_ids, workspace_path=workspace_path, agent=session.agent_id or DEFAULT_AGENT, project_id=session.project_id or None,
        )
        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            turn_task.cancel()
            # Safety net (see /chat/stream): the client is gone — force-stop the
            # underlying stream consumer and release the session so the next
            # edit/regenerate isn't rejected with 409 "session is still generating".
            # Idempotent; the interrupted marker is set by _on_error, not here.
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # Turn over: delete the disposable checkpoint thread unless it paused
            # for an approval/question (the interrupt checkpoint must survive resume).
            # Shielded so it completes even if the client disconnects after done.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)
            if snapshot_pre is not None and snapshot_workspace is not None:
                await asyncio.to_thread(
                    agent_registry.snapshot_manager.end_turn, session_id, message_id, snapshot_workspace,
                )

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
@router.get("/sessions/{session_id}/changes")
async def session_changes(session_id: str):
    """All file changes made by the agent in this session, grouped by turn."""
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    turns = agent_registry.change_store.changes_by_turn(session_id)
    return {"status": "ok", "session_id": session_id, "turns": turns, "count": sum(len(item["changes"]) for item in turns)}
