# -*- coding: utf-8 -*-

import asyncio
import json
from collections import defaultdict
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
from fastapi.responses import StreamingResponse
from coworker.api.state import (
    agent_registry,
    app,
    command_approval_store,
    logger,
    memory_manager,
    session_store
)
from coworker.api.streaming import (
    SSE_HEARTBEAT_SECONDS,
    _merge_message_parts
)

from fastapi import APIRouter

router = APIRouter()


class ApprovalEventBus:
    """In-memory pub/sub that streams resume progress events to SSE subscribers.

    A small ring buffer per resume_id ensures subscribers that attach after the
    background resume task started still receive the events already published.
    """

    def __init__(self, buffer_size: int = 64) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._closed: set[str] = set()
        self._buffer_size = buffer_size

    def subscribe(self, resume_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        if resume_id in self._closed:
            # The resume already finished before this subscriber attached: hand
            # it an immediate stream_end so the SSE connection terminates instead
            # of hanging on heartbeats forever.
            queue.put_nowait({"type": "stream_end"})
            return queue
        self._subscribers[resume_id].append(queue)
        for event in list(self._buffer.get(resume_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        return queue

    def unsubscribe(self, resume_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(resume_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(resume_id, None)
        # Drain the queue to release queued events and prevent memory leak.
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def publish(self, resume_id: str, event: dict[str, Any]) -> None:
        """Publish an event to all subscribers for a given resume_id.

        Non-blocking: if any subscriber queue is full the OLDEST queued event
        is dropped to make room (latest-wins — the newest event, e.g. a
        terminal ``stream_end`` or ``approval_resolved``, must not be lost).
        This prevents a single slow subscriber from back-pressuring and
        stalling the entire HITL resume pipeline.
        """
        buffer = self._buffer[resume_id]
        buffer.append(event)
        if len(buffer) > self._buffer_size:
            del buffer[: len(buffer) - self._buffer_size]
        queues = list(self._subscribers.get(resume_id, []))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest queued event to make room for the newest
                # (latest-wins). If it is still full (e.g. a concurrent
                # drainer took the slot), drop the incoming event itself
                # rather than ever blocking the pipeline.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def close(self, resume_id: str) -> None:
        queues = self._subscribers.pop(resume_id, [])
        for queue in queues:
            queue.put_nowait({"type": "stream_end"})
        # Drop the ring buffer and mark the resume finished so late subscribers
        # terminate immediately (see subscribe) instead of hanging.
        self._buffer.pop(resume_id, None)
        self._closed.add(resume_id)
        # The closed set is small (one entry per HITL resume); bound it so it
        # cannot grow unboundedly for a long-running process.
        if len(self._closed) > 512:
            self._closed = set(list(self._closed)[-256:])
approval_event_bus = ApprovalEventBus()
class ApprovalDecisionPayload(BaseModel):
    # The resolve endpoint keys off the TOP-LEVEL `request.approval_id`;
    # this nested copy is legacy/optional and must not fail validation when the
    # frontend sends a decision without duplicating the id (fixes dead buttons
    # on question/approval/plan cards → HTTP 422).
    approval_id: str = ""
    type: str = ""
    decision: str = ""
    reason: str = ""
    provider_id: str = ""
    model: str = ""
    workspace: str = ""
    message: str = ""
    session_id: str = ""
    always_allow: bool = False
    respond_text: str = ""
    plan_text: str = ""
    # The plan card's "approve with autonomy" buttons send the autonomy they
    # should approve under; must be declared or pydantic silently drops it and
    # main.py:2416 reads .autonomy -> AttributeError -> 500.
    autonomy: str = ""
class CommandApprovalResolve(BaseModel):
    approval_id: str
    decision: ApprovalDecisionPayload
@router.get("/command-approvals")
async def list_command_approvals():
    return {"status": "ok", "approvals": command_approval_store.list()}
@router.post("/command-approvals/resolve")
async def resolve_command_approval(request: CommandApprovalResolve):
    try:
        approval = command_approval_store.require(request.approval_id)
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        decision_type = request.decision.type

        if decision_type == "always":
            # MCP approvals carry their own allowlist key (server + remote tool
            # name); workspace commands are keyed by argv + cwd.
            mcp = context.get("mcp") if isinstance(context.get("mcp"), dict) else {}
            mcp_digest = str(mcp.get("digest") or "")
            if mcp_digest:
                command_approval_store.always_allow(mcp_digest)
            else:
                command = approval.get("command") if isinstance(approval.get("command"), list) else []
                cwd = str(approval.get("cwd") or "")
                if command:
                    from coworker.workspace import Workspace

                    command_approval_store.always_allow(Workspace.command_digest(command, cwd))
            decision = {"type": "approve"}
            status = "approved"
        elif decision_type == "approve":
            # For plan approvals the decision also carries the chosen execution
            # autonomy (supervised / guarded / autonomous) that routes the
            # follow-up execution posture.
            from coworker.agent.core import normalize_autonomy

            autonomy = normalize_autonomy(request.decision.autonomy) if request.decision.autonomy else None
            decision = {"type": "approve", **({"autonomy": autonomy} if autonomy else {})}
            status = "approved"
        elif decision_type == "continue_discuss":
            decision = {"type": "continue_discuss"}
            status = "answered"
        elif decision_type == "respond":
            decision = {"type": "respond", "message": request.decision.message}
            status = "answered"
        elif decision_type == "regenerate":
            # Legacy regenerate semantics: keep planning (stay in discuss phase).
            decision = {"type": "continue_discuss"}
            status = "denied"
        else:
            decision = {
                "type": "reject",
                "message": request.decision.message or (
                    "The user rejected this command. Do not run it or retry it unless the user explicitly asks."
                ),
            }
            status = "denied"

        interrupt_id = str(context.get("interrupt_id") or "")
        resume_id = interrupt_id or request.approval_id
        is_hitl_resumable = context.get("source") == "agent_langgraph_hitl" and bool(interrupt_id)
        # 关键：必须在 set_decision 之前查询 siblings。set_decision 会把当前审批
        # 翻成 terminal 状态，而保留策略可能在同一此 save 里将其逐出存储；若在
        # 之后查询就会漏掉当前审批（siblings 为空 → all([])=True → 以空决策调度
        # resume → _resume_in_background 直接关闭事件流，agent 不恢复，前端橙条）。
        siblings = (
            [
                item
                for item in command_approval_store.list()
                if isinstance(item.get("context"), dict)
                and str(item.get("context", {}).get("interrupt_id") or "") == interrupt_id
            ]
            if is_hitl_resumable
            else []
        )
        approval = command_approval_store.set_decision(request.approval_id, status, decision)
        if is_hitl_resumable:
            # 把当前审批（含刚写入的 decision）合并进 siblings，并兜底确保它在列，
            # 避免存储剪枝或历史裁剪导致该兄弟组决策不完整。
            siblings = [approval if item.get("id") == approval.get("id") else item for item in siblings]
            if not any(item.get("id") == approval.get("id") for item in siblings):
                siblings.append(approval)
            if all(item.get("decision") is not None for item in siblings):
                asyncio.create_task(
                    _resume_in_background(resume_id, approval, siblings)
                )
                return {
                    "status": "ok",
                    "approval": approval,
                    "events": [],
                    "resumed": True,
                    "resume_id": resume_id,
                }
            return {"status": "ok", "approval": approval, "events": [], "resumed": False}
        return {"status": "ok", "approval": approval, "events": [], "resumed": False}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
async def _resume_in_background(resume_id: str, approval: dict[str, Any], siblings: list[dict[str, Any]]) -> None:
    """Run the agent resume in the background and stream events via the event bus."""
    ordered = sorted(
        siblings,
        key=lambda item: int(item.get("context", {}).get("action_index") or 0),
    )
    decisions = [item.get("decision") for item in ordered if item.get("decision")]
    # If any question is rejected, inject a stop signal so the resume
    # path does NOT re-enter the agent graph — the turn ends.
    _has_question_reject = any(
        item.get("decision", {}).get("type") == "reject"
        and item.get("context", {}).get("kind") == "question"
        for item in ordered
    )
    if _has_question_reject:
        decisions.insert(0, {"type": "_stop_turn"})
    context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
    session_id = str(context.get("session_id") or "")
    memory_manager.note_turn_active(session_id) if session_id else None
    # A resume re-enters the graph and writes checkpoints on the session's
    # thread_id. Mark the session active for the resume's duration so the sweep
    # never trims the interrupt checkpoint mid-resume and the streaming guard
    # correctly sees the session as busy.
    if session_id:
        agent_registry.checkpoint_manager.mark_active(session_id)
    try:
        if not decisions:
            approval_event_bus.close(resume_id)
            return
        done: dict[str, Any] | None = None
        # resume_interrupt now yields events in real time, so each one is pushed
        # to the SSE bus as soon as it is produced instead of buffering the whole
        # resume. This gives the frontend live progress during the agent's resume
        # and surfaces newly hit approvals immediately.
        async for event in agent_registry.resume_interrupt(approval, decisions):
            if event.get("type") == "done":
                done = event
            event["session_id"] = session_id
            event["resume_id"] = resume_id
            await approval_event_bus.publish(resume_id, event)
        if done and session_id:
            try:
                session = session_store.require(session_id)
                last = session.messages[-1] if session.messages else None
                if last is not None and last.role == "assistant":
                    # 同一次 agent turn 的多次 resume 共享同一条 assistant 消息：
                    # 把本次 resume 的 parts 合并进最后一条消息，而不是每次 append 新消息，
                    # 避免切换会话重载后同一次回复被拆成多个气泡。
                    last.parts = _merge_message_parts(last.parts, done.get("parts") or [])
                    last.content = str(done.get("content") or last.content or "")
                    last.provider = str(done.get("provider") or last.provider or "")
                    last.model = str(done.get("model") or last.model or "")
                    session_store.save(session)
                    done["content"] = last.content
                    done["parts"] = last.parts
                else:
                    session_store.append_message(
                        session_id,
                        role="assistant",
                        content=str(done.get("content") or ""),
                        mode="single",
                        provider=str(done.get("provider") or ""),
                        model=str(done.get("model") or ""),
                        parts=done.get("parts") or [],
                    )
            except KeyError:
                pass
            # Success → clear previous error
            try:
                session = session_store.require(session_id)
                session.last_error = ""
                session_store.save(session)
            except KeyError:
                pass
        for item in ordered:
            command_approval_store.mark_consumed(item.get("id", ""))
        # Resume reached a terminal `done`: the turn is over, so the disposable
        # checkpoint thread is no longer needed — delete it (fresh-start handles
        # it next turn too, but cleaning here keeps the DB empty). Shielded so
        # the delete still completes if this background task is cancelled.
        if done and session_id:
            try:
                await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning("resume checkpoint delete failed for %s", session_id, exc_info=True)
    except Exception as exc:
        # Surface the failure on the bus AND record it in the trace log so a
        # silently-dead resume (answer sent but agent never continues) is
        # diagnosable from agent_trace.jsonl instead of vanishing.
        try:
            agent_registry.trace_store.record(
                "agent_activity", "error",
                {
                    "session_id": session_id,
                    "provider": str(context.get("provider") or ""),
                    "provider_id": str(context.get("provider_id") or ""),
                    "model": str(context.get("model") or ""),
                    "language": str(context.get("language") or "zh"),
                    "work_mode": str(context.get("work_mode") or "build"),
                    "autonomy": str(context.get("autonomy") or "guarded"),
                    "streaming": True,
                },
                {"error": str(exc)[:400], "resume_id": resume_id, "approval_id": approval.get("id", "")},
            )
        except Exception:  # noqa: BLE001 - a trace hiccup must never mask the resume error
            logger.warning("failed to record resume error trace", exc_info=True)
        await approval_event_bus.publish(
            resume_id,
            {"type": "error", "session_id": session_id, "error": str(exc)[:400], "resume_id": resume_id},
        )
        # Persist error summary to session so it survives restart
        try:
            session = session_store.require(session_id)
            session.last_error = str(exc)[:400]
            session_store.save(session)
        except KeyError:
            pass
    finally:
        approval_event_bus.close(resume_id)
        # The resume is over: release the session so the next turn can start.
        if session_id:
            agent_registry.checkpoint_manager.mark_idle(session_id)
@router.get("/command-approvals/events/{resume_id}")
async def stream_approval_events(resume_id: str):
    """SSE stream of resume progress events for a given resume_id."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        queue = approval_event_bus.subscribe(resume_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.get("type") == "stream_end":
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            approval_event_bus.unsubscribe(resume_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
@router.get("/worker-events/{worker_run_id}")
async def stream_worker_events(worker_run_id: str):
    """SSE stream of a worker sub-agent's internal run events.

    Replays the persisted run history first, then follows live events, and
    terminates with ``worker_stream_end`` once the run finishes. The main agent
    stream only carries the ``delegate_*`` summary; the worker's detailed
    delta/tool/reasoning stream lives here and is consumed on demand when the
    user opens the worker block.
    """
    from fastapi.responses import StreamingResponse

    async def event_stream():
        try:
            async for event in worker_event_bus.stream(worker_run_id):
                if event is None:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a broken worker stream must never hang the client
            logger.warning("worker event stream error for %s", worker_run_id, exc_info=True)
            yield f"data: {json.dumps({'type': 'worker_stream_end'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
