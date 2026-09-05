# -*- coding: utf-8 -*-

import asyncio
import os
import time
import shutil
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
from coworker.goal_feature import goal_feature
from coworker.api.state import (
    _stream_tasks,
    agent_registry,
    logger,
    provider_manager,
    session_store,
    settings,
    workspace_controller
)


async def _tracked_stream(stream_iter: Any, session_id: str):
    """Mark a session active while its stream is consumed so the periodic
    sweep never trims a thread that is currently being written to."""
    agent_registry.checkpoint_manager.mark_active(session_id)
    # Register the task consuming this stream (the _sse_events producer) so a
    # hard stop — e.g. deleting the session mid-generation — can cancel it.
    _stream_tasks[session_id] = asyncio.current_task()
    try:
        async for event in stream_iter:
            yield event
    finally:
        agent_registry.checkpoint_manager.mark_idle(session_id)
        if _stream_tasks.get(session_id) is asyncio.current_task():
            _stream_tasks.pop(session_id, None)
        # Force-close the wrapped stream so a cancelled turn tears the provider
        # HTTP request down (see _sse_events producer's finally). Without this,
        # closing this generator alone leaves the inner graph.astream suspended
        # and generating to a dead socket.
        try:
            await stream_iter.aclose()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass
SSE_TIMEOUT = int(os.environ.get("COWORKER_SSE_TIMEOUT", str(30 * 60)))
SSE_HEARTBEAT_SECONDS = float(os.environ.get("COWORKER_SSE_HEARTBEAT_SECONDS", "15.0"))
async def _sse_events(
    stream_iter: Any,
    on_event: Any = None,
    on_end: Any = None,
    on_error: Any = None,
):
    """Consume ``stream_iter`` and yield ``(kind, payload)`` tuples.

    kind is one of:
      * ``"event"``    — payload is a real event dict (``on_event`` already ran)
      * ``"heartbeat"``— no event for ``SSE_HEARTBEAT_SECONDS``; caller should
                         emit an SSE comment line to keep the connection alive
      * ``"error"``    — payload is the error event dict (stream raised, incl.
                         client-disconnect cancellation)
      * ``"end"``      — payload ``None``; the underlying stream finished

    ``on_event(event)`` runs for each real event (accumulate/persist side
    effects). ``on_end()`` runs once on normal completion and may return a final
    dict to emit (e.g. a synthetic ``done``) or ``None``. ``on_error(exc)`` runs
    when the stream raises and must return the error event dict.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()
    _IDLE_WARN_INTERVAL = 240.0  # re-warn every 4 min of provider silence
    _last_activity_ts = _get_monotonic()  # start from stream init, not 0
    _last_idle_warning_ts: float | None = None

    async def _producer():
        nonlocal _last_activity_ts
        try:
            async for event in stream_iter:
                _last_activity_ts = _get_monotonic()
                if on_event is not None:
                    on_event(event)
                await queue.put(("event", event))
            final = on_end() if on_end is not None else None
            if final is not None:
                await queue.put(("event", final))
        except BaseException as exc:  # incl. GeneratorExit / CancelledError
            # The terminal event must ALWAYS reach the client: without it the
            # frontend sees a clean EOF with neither `done` nor `error` and
            # renders a permanently "interrupted" (yellow) bubble. If the
            # caller's on_error handler itself fails, fall back to a plain
            # error event instead of letting the exception propagate (which
            # would leave only the "end" sentinel in the queue).
            try:
                err = on_error(exc) if on_error is not None else {"type": "error", "error": str(exc)[:400]}
            except BaseException:  # noqa: BLE001 - never lose the terminal event
                logger.exception("on_error handler failed for stream: %r", str(exc)[:200])
                err = {"type": "error", "error": "internal stream failure"}
            await queue.put(("error", err))
        finally:
            # CRITICAL teardown: ``async for`` does NOT close an async iterator
            # when the loop is interrupted by an exception. A task.cancel()
            # (user Stop / client disconnect) that lands between chunks leaves
            # the provider stream (graph.astream → langchain → httpx → vLLM
            # socket) running and generating to a dead socket — the "vLLM is
            # still running after Stop" bug. Force-close the whole chain here so
            # the upstream HTTP request is aborted promptly.
            try:
                await stream_iter.aclose()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
            await queue.put(("end", None))

    task = asyncio.ensure_future(_producer())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Emit SSE keep-alive comment every second of idle. Idle
                # accounting starts at stream init (`_last_activity_ts` is
                # monotonic from the start), so no 0-sentinel guard needed.
                elapsed = _get_monotonic() - _last_activity_ts
                # Repeatedly push idle-warning so the frontend's idle watchdog
                # keeps being reset while the provider is legitimately slow
                # (long thinking / large prefill). Without recurrence the client
                # would abort before a long per-provider stream timeout fires.
                if elapsed >= _IDLE_WARN_INTERVAL and (
                    _last_idle_warning_ts is None
                    or _get_monotonic() - _last_idle_warning_ts >= _IDLE_WARN_INTERVAL
                ):
                    _last_idle_warning_ts = _get_monotonic()
                    yield "event", {"type": "idle_warning", "seconds_idle": int(elapsed)}
                yield "heartbeat", None
                continue
            yield kind, payload
            if kind == "end":
                break
    finally:
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
def _get_monotonic() -> float:
    """Monotonic clock (never goes backwards) for tracking idle time."""
    return time.monotonic()
async def _publish_turn(
    session_id: str,
    stream_iter: Any,
    on_event: Any = None,
    on_end: Any = None,
    on_error: Any = None,
) -> None:
    """Background task: consume a runtime stream and publish its events to the
    session event bus (live, independent of the generator being blocked on a
    long-running tool). Preserves the terminal semantics of ``_sse_events``:
    the synthetic ``done``/error event is published, then the bus is closed so
    the subscribing endpoint unblocks.

    Invariant: this task ALWAYS closes the bus — on normal end, on error, and
    even when the task itself is cancelled (client disconnect). Otherwise the
    subscriber would hang waiting for a terminal ``worker_stream_end``.
    """
    try:
        published = 0
        async for kind, payload in _sse_events(
            _tracked_stream(stream_iter, session_id),
            on_event=on_event,
            on_end=on_end,
            on_error=on_error,
        ):
            if kind == "event":
                session_event_bus.publish(session_id, payload)
            elif kind == "error":
                session_event_bus.publish(session_id, payload)
                session_event_bus.close(session_id)
            elif kind == "end":
                session_event_bus.close(session_id)
            # Model bursts can drain hundreds of deltas in one synchronous run
            # (queue.get() on a non-empty queue never yields to the loop), which
            # would starve the SSE subscriber task. Yield periodically so the
            # subscriber keeps pace and the bus buffer never needs eviction.
            published += 1
            if published % 32 == 0:
                await asyncio.sleep(0)
    except BaseException:  # noqa: BLE001 - incl. cancellation; must close the bus
        session_event_bus.close(session_id)
        raise
def _emit_goal_updated(session_id: str, goal) -> dict | None:
    """Broadcast a ``goal_updated`` event on the session bus (streaming channel)
    and return the goal payload for the HTTP response (idle channel).

    Both channels land on the same frontend ``goal`` state — no second truth.
    """
    if goal is None:
        return None
    try:
        payload = goal.to_dict()
    except Exception:  # noqa: BLE001 - never break on a serialization hiccup
        return None
    try:
        session_event_bus.publish(
            session_id,
            {"type": "goal_updated", "session_id": session_id, "goal": payload},
        )
    except Exception:  # noqa: BLE001 - never break on a publish hiccup
        pass
    return payload
def _emit_goal_cleared(session_id: str) -> None:
    try:
        session_event_bus.publish(
            session_id,
            {"type": "goal_cleared", "session_id": session_id},
        )
    except Exception:  # noqa: BLE001 - never break on a publish hiccup
        pass
def _session_goal(session_id: str):
    """Return the session's goal, or ``None`` when the goal capability is off.

    This is the single read the *streaming pipeline* uses. When the feature is
    disabled it returns ``None`` so the whole request path treats the session
    exactly as if no goal ever existed — no continuation injection, no
    multi-round loop, no goal accounting, no degenerate/idle-stop guards and no
    ``goal_stream_end`` event. A clean A/B bypass for checking whether the goal
    prompts cause model degradation (降智).
    """
    if not goal_feature.is_enabled():
        return None
    try:
        return session_store.get_goal(session_id)
    except Exception:  # noqa: BLE001 - never break the stream on a goal probe
        return None
def _cleanup_session_screenshots(session_id: str) -> None:
    """Remove the session's externalized screenshots (best-effort)."""
    try:
        from coworker.browser.bridge_client import screenshots_dir_for

        target = screenshots_dir_for(settings.data_dir, session_id)
        if target is not None and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except Exception:  # noqa: BLE001 - cleanup must never break a delete
        logger.debug("screenshot cleanup failed for %s", session_id, exc_info=True)
def _require_goal(session_id: str):
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session_id
def _hard_stop_session_stream(session_id: str) -> None:
    """Hard-terminate any in-flight stream/task for a session.

    Called by session delete so a session can be removed even while it is
    mid-generation (regular stream, regenerate/edit). Cancels the task
    consuming the session's SSE stream; ``_tracked_stream``'s ``finally``
    marks the session idle so the caller's streaming guard returns promptly.
    """
    task = _stream_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
def _force_stop_session_stream(session_id: str) -> None:
    """Force-stop a session's in-flight generation and release it for new work.

    A client abort (Stop button) is only observable to the backend as a socket
    disconnect, which uvicorn/Starlette can fail to propagate promptly — the
    SSE generator keeps running (heartbeats into a dead socket) and the runtime
    graph can stall behind a checkpoint DB lock, so ``mark_idle`` (inside the
    stream consumer's ``finally``) may never run. The session then stays marked
    "active" forever and every later edit/regenerate for it is rejected with
    ``409 session is still generating``.

    Cancelling the stream-consuming task directly (``_hard_stop_session_stream``)
    makes Stop deterministic: the task's cancellation closes the tracked stream,
    which runs ``mark_idle``, and the explicit ``mark_idle`` below is an
    idempotent safety net for the case where the graph never unwinds. The turn's
    checkpoint thread is deleted by the stream's finally, and the next run
    rebuilds fresh from session history anyway.
    """
    _hard_stop_session_stream(session_id)
    agent_registry.checkpoint_manager.mark_idle(session_id)
async def _guard_session_not_streaming(session_id: str) -> None:
    """Reject destructive session mutations while a stream is in flight.

    Truncating/deleting a session (or its checkpoint) mid-stream can silently
    drop the reply the running agent is still producing, so refuse with 409
    instead of racing it.

    A short grace period covers the teardown window after the client aborts a
    stream: the abort races the reconnect (e.g. a rapid edit), so instantly
    returning 409 would reject an edit the user just issued.
    """
    for _ in range(30):
        if session_id not in agent_registry.checkpoint_manager.active_sessions():
            return
        await asyncio.sleep(0.1)
    raise HTTPException(
        status_code=409,
        detail="session is still generating; wait for the current response to finish",
    )
def _provider_id_for_model(provider_name: str, model: str) -> str:
    if provider_name:
        try:
            config = provider_manager.load()
            for provider in config.providers:
                if provider.name == provider_name or provider.model == model:
                    return provider.id
        except Exception:
            pass
    return ""
def _provider_name_for_id(provider_id: str) -> str:
    """Provider display name for a provider id ('' when unknown/disabled)."""
    if not provider_id:
        return ""
    try:
        provider = provider_manager.load().find_enabled(provider_id)
        return provider.name if provider is not None else ""
    except Exception:  # noqa: BLE001 - best-effort
        return ""
def _resolve_run_provider(session, provider_id: str, model: str) -> tuple[str, str]:
    """Resolve ``(provider_id, model)`` for regenerate/edit re-runs.

    Prefers the caller's explicit provider/model (the current UI selection — the
    same source /chat/stream and the context-usage preview use), so every run
    path and the topbar meter agree on the window. Falls back to the session's
    last-run provider/model, then to the default enabled provider, so invalid
    selections and legacy clients keep working.
    """
    try:
        config = provider_manager.load()
    except Exception:  # noqa: BLE001 - a broken config must never block a run
        return "", ""
    requested = config.find_enabled(provider_id) if provider_id else None
    if requested is not None:
        return requested.id, (model or requested.model)
    provider_name, stored_model = _session_provider_context(session)
    if provider_name:
        sid = _provider_id_for_model(provider_name, stored_model)
        if sid:
            return sid, stored_model or model
    first = next((p for p in config.providers if p.enabled), None)
    if first is not None:
        return first.id, (model or first.model)
    return "", ""
TOOL_REPLAY_MAX_TOKENS = 4000
def _truncate_tool_result(text: str) -> str:
    try:
        from coworker.context import truncate_to_token_budget

        truncated, _ = truncate_to_token_budget(text, TOOL_REPLAY_MAX_TOKENS)
        return truncated
    except Exception:  # noqa: BLE001 - never break history replay on a truncator hiccup
        return text
def _parts_to_conversation(message) -> list[dict[str, Any]]:
    """Reconstruct an assistant turn's full tool-call conversation from its
    stored ``parts``.

    A persisted assistant message stores the turn's interleaved text and tool
    results as ``parts`` (``{"type":"text","content"}`` and
    ``{"type":"tool","id","name","input","output","status"}``). Replaying only
    ``message.content`` would collapse every tool round into bare chatter, which
    invites the model to imitate that on continuation rounds (the observed
    "degraded / spinning" failure mode). Rebuild the standard
    ``assistant(tool_calls) → tool(result)`` pairs instead, mirroring codex
    (``reconstruct_history_from_rollout`` keeps response roles intact) and
    opencode (``toModelMessagesEffect`` preserves tool parts).
    """
    parts = getattr(message, "parts", None) or []
    if not parts:
        # No structured parts — fall back to the plain content.
        return [{"role": "assistant", "content": message.content or ""}]

    import json as _json

    out: list[dict[str, Any]] = []
    text_buf: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal text_buf, tool_calls
        if tool_calls:
            # Narration + tool calls belong to ONE assistant message (the
            # standard LangChain/OpenAI shape): content carries the text, and
            # tool_calls carry the parallel tool invocations.
            content = "\n".join(text_buf) if text_buf else None
            out.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [dict(tc) for tc in tool_calls],
                }
            )
            for tc in tool_calls:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(tc.get("result") or ""),
                    }
                )
            text_buf = []
            tool_calls = []
            return
        if text_buf:
            out.append({"role": "assistant", "content": "\n".join(text_buf)})
            text_buf = []

    for part in parts:
        ptype = part.get("type")
        if ptype == "text":
            content = part.get("content") or ""
            if tool_calls:
                # Text interleaved after a tool call: close the current batch,
                # then buffer the narration for the next assistant turn.
                flush()
            text_buf.append(str(content))
        elif ptype == "tool":
            args = part.get("input")
            args_str = "{}"
            try:
                if isinstance(args, str):
                    args_str = args
                else:
                    args_str = _json.dumps(args, ensure_ascii=False)
                parsed = _json.loads(args_str)
                if not isinstance(parsed, dict):
                    # 只接受 JSON 物件。空/非法/非物件 arguments（早期「工具 input
                    # 捕獲為空」bug 残留等）统一落为 {} —— 否则 LangChain
                    # convert_to_messages 会对 arguments 做 json.loads + args 字段
                    # 校验，空串抛 `Expecting value: line 1 column 1 (char 0)`，
                    # 字符串/数组又会被 args: dict 校验拒绝，直接打崩整轮。
                    args_str = "{}"
            except Exception:  # noqa: BLE001 - best-effort serialization
                args_str = "{}"
            tool_calls.append(
                {
                    "id": part.get("id") or f"tool-{len(out)}",
                    "type": "function",
                    "function": {"name": part.get("name") or "", "arguments": args_str},
                    "result": _truncate_tool_result(part.get("output_full") or part.get("output") or part.get("result") or ""),
                }
            )
    flush()
    return out or [{"role": "assistant", "content": message.content or ""}]
def _goal_round_has_tool_execution(session) -> bool:
    """Whether the latest assistant turn actually executed any tool.

    goal 续跑只有在模型本轮执行了实质工具（parts 含 ``tool``，如
    read/write/replace/run/search）时才继续；纯文字回答（碎片空转、或模型认为
    任务已完成的总结）没有可推进的事项，应停止续跑，避免 goal 无限续跑退化。
    ``write_todos`` / ``update_goal`` 不产生 tool part（前者由 TodoListMiddleware
    单独处理），所以「只整理列表」的轮同样被视为无实质进展。
    """
    try:
        if not session or not getattr(session, "messages", None):
            return False
        last = session.messages[-1]
        if getattr(last, "role", "") != "assistant":
            return False
        parts = getattr(last, "parts", None) or []
        return any(isinstance(p, dict) and p.get("type") == "tool" for p in parts)
    except Exception:  # noqa: BLE001 - best-effort, never break the stream
        return False
def _session_message_history(session) -> list[dict[str, Any]]:
    """Build the message history (role/content) that should be replayed when
    re-running the agent from a truncated point."""
    history = []
    for message in session.messages:
        if message.role not in {"user", "assistant"} or not message.content:
            continue
        if message.role == "user":
            history.append({"role": "user", "content": format_user_message(message.content, message.attachments, message.references, inline_attachments=False)})
        else:
            history.extend(_parts_to_conversation(message))
    return history
def _session_referenced_ids(session) -> set[str]:
    """Collect every session id the user referenced across the session's user
    messages (used to restore the read_session allowlist on rerun paths)."""
    ids: set[str] = set()
    for message in session.messages:
        if message.role != "user":
            continue
        for ref in message.references or []:
            ref_id = str(ref.get("id") or "").strip()
            if ref_id:
                ids.add(ref_id)
    return ids
def _session_provider_context(session) -> tuple[str, str]:
    for message in reversed(session.messages):
        if message.role == "assistant" and message.provider:
            return message.provider, message.model
    return "", ""
def _history_content_chars(content) -> int:
    """Char length of a stored message's TEXT blocks only (media skipped).

    Mirrors the runtime meter (``_msg_chars`` → ``_message_text``), which counts
    images at a per-item vision cost, never as text. Counting ``str(content)``
    on a multimodal list would charge the full base64 data URL as ~400x too many
    tokens, making every image-bearing session preview as "full".
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += len(str(part.get("text") or ""))
        return total
    return 0
def _history_content_tokens(content) -> int:
    """Token estimate of a stored message's content, matching the runtime meter.

    Text blocks use the CJK/base64-aware estimator; media blocks (image/audio/
    video/file) are charged the per-item vision cost — the same accounting as
    ``coworker.context.message_tokens``.
    """
    from coworker.context import TOKENS_PER_IMAGE_DEFAULT, estimate_text_tokens

    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                total += estimate_text_tokens(str(part.get("text") or ""))
            elif ptype in ("image", "image_url", "audio", "video", "file"):
                total += TOKENS_PER_IMAGE_DEFAULT
            else:
                total += estimate_text_tokens(str(part.get("text") or part.get("input") or ""))
        return total
    return 0
def _session_context_usage_snapshot(session, provider_id: str, model: str) -> dict | None:
    """Lightweight context-usage preview for a session, resolved from the
    CURRENT provider config (so older sessions immediately reflect model-window
    changes instead of a stale last-run value). Field shape matches the
    streaming ``context_usage`` event so the client reuses the same mapping.
    A subsequent run supersedes this with a fresh calibrated figure."""
    pm = provider_manager.load()
    provider = pm.find_enabled(provider_id) if provider_id else None
    if provider is None:
        enabled = [p for p in pm.providers if p.enabled]
        provider = enabled[0] if enabled else None
    if provider is None:
        return None
    budget_chars, window_tokens, source, warning, max_output = _runtime_context_budget(provider, model or None)
    from coworker.context import CalibrationStore, effective_input_limit, get_calibration_store

    budget_tokens = context_budget_tokens(window_tokens, max_output)
    effective = effective_input_limit(window_tokens, max_output)
    # Prefer the last FULL measurement persisted from a real run (system prompt +
    # tool schemas + messages + overhead, calibrated) — that is the true request
    # size. Fall back to a message-only estimate only for sessions that have
    # never run (nothing to measure yet).
    stored_cal = int(session.context_used_tokens_calibrated or 0)
    if stored_cal > 0:
        used_tokens = int(session.context_used_tokens or 0)
        used_tokens_calibrated = stored_cal
        used_chars = int(session.context_used_chars or 0)
        calibration_factor = float(session.context_calibration_factor or 0.0) or 1.0
    else:
        history = _session_message_history(session)
        used_chars = sum(_history_content_chars(m.get("content")) for m in history)
        used_tokens = sum(_history_content_tokens(m.get("content")) for m in history)
        # Reuse the same closed-loop calibration factor a real run would apply for
        # this (provider, model), so the preview does not jump on the next run.
        calibration_factor = 1.0
        if provider is not None:
            resolved_model = (model or None) or provider.model
            store = get_calibration_store(settings.data_dir)
            calibration_factor = store.get(CalibrationStore.key_for(provider.id, resolved_model or ""))
        used_tokens_calibrated = int(round(used_tokens * calibration_factor))
    return {
        "type": "context_usage",
        "used_chars": used_chars,
        "budget_chars": budget_chars,
        "used_tokens": used_tokens,
        "used_tokens_calibrated": used_tokens_calibrated,
        "calibration_factor": round(calibration_factor, 3),
        "budget_tokens": budget_tokens,
        "active_budget_tokens": budget_tokens,
        "window_tokens": window_tokens,
        "effective_window_tokens": effective,
        "max_output_tokens": max_output,
        # Over the effective input ceiling (window − max_output), which is what
        # the model actually receives — consistent with the live event.
        "compressed": used_tokens > effective,
        "compacted": False,
        "compact_count": 0,
        "window_source": source,
        "window_warning": warning,
    }
async def _revert_turn_changes(
    session_id: str,
    message_id: str,
    dropped_assistant_ids: list[str],
    *,
    mark: bool = True,
) -> dict[str, Any]:
    """Two-layer revert of the code changes a turn produced.

    Layer 1 (record-level, authoritative): ``revert_changes`` restores every
    ``active`` record via safe inverse edits. Layer 2 (git content carrier):
    ``revert_turn`` restores records whose content was too large for the change
    store and, when the workspace is exclusive, any shell-driven changes that
    left no record. Reverts that succeed are marked ``reverted`` so a later
    "redo" can re-apply them. Returns the merged ``revert_summary``.
    """
    revert_summary: dict[str, Any] = {"reverted_count": 0, "conflict_count": 0, "total": 0, "reverted_paths": []}
    if not dropped_assistant_ids:
        return revert_summary
    try:
        workspace = workspace_controller.workspace_for_session(session_id)
        # ① Record-level revert (authoritative, session-scoped).
        summary = agent_registry.change_store.revert_changes(session_id, dropped_assistant_ids, workspace)
        reverted_ids = [c.get("id") for c in summary.get("reverted", []) if c.get("id")]
        conflict_ids = [c.get("id") for c in summary.get("conflicts", []) if c.get("id")]
        # ② Git content carrier: records whose full content was too large for
        # the change store (and shell-driven changes, when exclusive) are
        # restored from the snapshot trees (session-scoped + current==post gated).
        dropped_records = agent_registry.change_store.changes_for_message_ids(session_id, dropped_assistant_ids)
        too_large_paths = {str(c.get("file_path") or "") for c in dropped_records if c.get("too_large")}
        record_paths = {str(c.get("file_path") or "") for c in dropped_records}
        git_summary = await asyncio.to_thread(
            agent_registry.snapshot_manager.revert_turn, session_id, message_id, workspace, too_large_paths=too_large_paths, record_paths=record_paths,
        )
        git_reverted = git_summary.get("reverted") or []
        git_conflicts = git_summary.get("conflicts") or []
        if mark:
            if reverted_ids:
                agent_registry.change_store.mark_reverted(session_id, reverted_ids, message_id)
            if conflict_ids:
                agent_registry.change_store.mark_abandoned(session_id, conflict_ids)
        revert_summary = {
            "reverted_count": summary.get("reverted_count", 0) + len(git_reverted),
            "conflict_count": summary.get("conflict_count", 0) + len(git_conflicts),
            "total": summary.get("total", 0) + len(git_reverted) + len(git_conflicts),
            "reverted_paths": [c.get("path") for c in summary.get("reverted", []) if c.get("path")] + [c.get("path") for c in git_reverted if c.get("path")],
        }
    except (ValueError, KeyError) as exc:
        # Workspace unavailable or session missing: skip file revert but keep the
        # caller's flow (message edit still allowed).
        logger.warning("_revert_turn_changes: code revert skipped for %s: %s", session_id, exc)
    except Exception as exc:  # noqa: BLE001 - revert must never break the edit/regenerate request
        logger.warning("_revert_turn_changes: code revert failed for %s: %s", session_id, exc, exc_info=True)
    return revert_summary
def _merge_message_parts(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge newly emitted resume parts into the assistant message's accumulated parts.

    Tools are merged by id (the newest entry wins), reasoning/plan blocks are
    coalesced so repeated resumes of the same turn do not duplicate blocks.
    """
    merged = list(existing)
    for part in incoming:
        ptype = part.get("type")
        if ptype == "tool":
            tool_id = part.get("id")
            index = next((i for i, p in enumerate(merged) if p.get("type") == "tool" and p.get("id") == tool_id), None)
            if index is not None:
                prev = merged[index]
                merged[index] = {
                    **prev,
                    **part,
                    # 不应用空值覆盖已有内容，避免后续 resume 的空字段抹掉工具名/参数
                    "name": part.get("name") or prev.get("name") or "",
                    "input": part.get("input") or prev.get("input") or "",
                }
            else:
                merged.append(dict(part))
        elif ptype in ("plan", "reasoning"):
            index = next((i for i, p in enumerate(merged) if p.get("type") == ptype), None)
            if index is not None:
                merged[index] = {**merged[index], **part}
            else:
                merged.append(dict(part))
        elif ptype == "text":
            # 各次 resume 会重放同一轮执行（工具按 id 去重），文本同样按内容去重，
            # 避免重放时 text part 重复叠加；多轮文本内容各不相同，天然追加。
            text_content = str(part.get("content") or "")
            if not text_content:
                continue
            exists = any(p.get("type") == "text" and p.get("content") == text_content for p in merged)
            if not exists:
                merged.append(dict(part))
        else:
            merged.append(dict(part))
    return merged
