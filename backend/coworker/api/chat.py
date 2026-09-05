# -*- coding: utf-8 -*-

import asyncio
import json
import time
import uuid
from typing import Any, Optional
from dataclasses import replace
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
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID, ProjectStore
from coworker.providers import ProviderManager
from coworker.goal_prompts import (
    is_degenerate_text,
    render_budget_limit,
    render_goal_continuation,
    render_objective_updated,
)
from coworker.steer import SteerEntry, steer_inbox
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from coworker.logger import apply_log_config, current_session_id, get_logger, get_log_settings as _runtime_log_settings, init_logger, is_sensitive_key, redact, set_log_level as _set_log_level, truncate_log as _truncate_log
from fastapi.responses import StreamingResponse
from coworker.api.memory_org import (
    _ensure_agent_skeleton,
    _ensure_org,
    _project_memory_dir
)
from coworker.api.state import (
    _stream_tasks,
    agent_registry,
    app,
    logger,
    memory_manager,
    provider_manager,
    session_store,
    workspace_controller
)
from coworker.api.streaming import (
    _emit_goal_updated,
    _goal_round_has_tool_execution,
    _guard_session_not_streaming,
    _hard_stop_session_stream,
    _parts_to_conversation,
    _publish_turn,
    _session_goal
)

from fastapi import APIRouter

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    agent: Optional[str] = None
    mode: AgentMode = "single"
    language: Language = "zh"
    work_mode: Optional[str] = None
    autonomy: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[str] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    referenced_sessions: list[str] = Field(
        default_factory=list,
        description="Session ids the user explicitly referenced in this message; the agent may read them via the read_session tool.",
    )
def _resolve_references(referenced_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve pasted session ids to {id, title} entries, dropping unknown ids."""
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_id in referenced_ids:
        session_id = str(raw_id or "").strip()
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        try:
            session = session_store.load(session_id)
        except Exception:
            session = None
        if session is not None:
            resolved.append({"id": session.id, "title": session.title})
    return resolved
def resolve_request_autonomy(request) -> str:
    """Effective autonomy for a chat request."""
    if getattr(request, "autonomy", None):
        return normalize_autonomy(request.autonomy)
    return "guarded"
class ChatStreamRequest(ChatRequest):
    session_id: str = ""
    # 前端乐观渲染时生成的消息 id，回传以统一前后端 id（修复按 id 回退/重生成时 404）
    user_message_id: str = ""
    assistant_message_id: str = ""
    # 前端「文件体积上限」设置换算成的字节数；后端按此上限如实处理附件
    # （超过的附件不内联、在提示词中说明未转发）。None 时后端用默认 25MB。
    max_attachment_bytes: Optional[int] = None
    # 插話 (interject) 自动续跑：user 消息已由 /chat/interject 持久化（它就是
    # history 的最后一条 user 消息），这里不再 append，直接以 history 作为模型输入，
    # 避免重复写库与重复送上下文。
    skip_user_append: bool = False
def _cached_provider_unreachable(provider_id: str | None, model: str | None) -> str | None:
    """Best-effort fast-fail guard: return the cached "LLM 服务不可达" message
    when the requested LOCAL provider's context discovery recently failed, else
    ``None``. Pure cache read — never probes, never blocks the event loop.

    Only LOCAL providers fast-fail here: a failed discovery probe (e.g. a 401/404
    from a cloud/gateway endpoint that omits ``/v1/models``) does not mean chat
    completions are down, and the UI's ``context_error`` warning is only surfaced
    for local providers too — so the two must agree. See ``_resolve_context_window_full``.
    """
    try:
        if not provider_id:
            return None
        provider = provider_manager.load().find_enabled(provider_id)
        if not provider:
            return None
        if model and model != provider.model:
            provider = replace(provider, model=model)
        if not ProviderManager._is_local(provider):
            return None
        return ProviderManager.cached_context_error(provider)
    except Exception:  # noqa: BLE001 - a guard failure must never break a turn
        return None
async def _build_stream_runtime(
    mode: str,
    session_id: str | None,
    provider_id: str | None,
    model: str | None,
    workspace: Any,
    referenced_ids: set[str],
    agent: str,
    project_id: str | None,
) -> Any:
    """Build (or reuse from the W1 per-session cache) a stream runtime WITHOUT
    blocking the event loop.

    Runtime construction resolves the provider's context window, which performs
    a synchronous network probe whenever the discovery cache is cold (up to the
    3s probe timeout). That must never freeze the event loop that also serves
    other sessions' SSE streams/heartbeats, so the whole init runs on a thread.
    The per-session cache keyed on ``session_id`` makes later turns reuse the
    compiled graph (W1).
    """
    return await asyncio.to_thread(
        agent_registry.get_stream_runtime,
        mode,
        session_id,
        provider_id,
        model,
        workspace,
        referenced_sessions=referenced_ids,
        agent=agent,
        project_id=project_id,
    )
@router.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest):
    if not request.session_id and not request.project_id:
        raise HTTPException(status_code=400, detail="project_id is required to start a new chat")
    work_mode = normalize_work_mode(request.work_mode)
    autonomy = resolve_request_autonomy(request)
    references = _resolve_references(request.referenced_sessions)
    referenced_ids = {ref["id"] for ref in references}
    try:
        resolved_workspace = workspace_controller.workspace_for_chat(
            session_id=request.session_id or None,
            project_id=request.project_id,
        )
        agent = request.agent or DEFAULT_AGENT
        if request.session_id:
            try:
                existing = session_store.require(request.session_id)
                if request.agent:
                    existing.agent_id = request.agent
                    session_store.save(existing)
                elif existing.agent_id:
                    agent = existing.agent_id
            except KeyError:
                pass
        # Ensure org manifest so the agent runtime can use delegation & team tools.
        project_dir = _project_memory_dir(request.project_id or "")
        if project_dir:
            _ensure_agent_skeleton(project_dir, agent)
            _ensure_org(project_dir)
        runtime = await _build_stream_runtime(request.mode, request.session_id, request.provider_id, request.model, resolved_workspace, referenced_ids, agent, request.project_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = request.session_id
    memory_manager.note_turn_active(session_id) if session_id else None
    max_attachment_bytes = request.max_attachment_bytes
    history = []
    if request.session_id:
        try:
            session = session_store.require(request.session_id)
            for m in session.messages:
                if m.role == "user" and m.content:
                    history.append(
                        {
                            "role": "user",
                            "content": format_user_message(m.content, m.attachments, m.references, max_attachment_bytes=max_attachment_bytes, inline_attachments=False),
                        }
                    )
                elif m.role == "assistant" and (m.content or getattr(m, "parts", None)):
                    history.extend(_parts_to_conversation(m))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        # 无项目兜底：归入系统保留的聊天项目（沙箱），绝不产生空项目会话。
        session = session_store.create("", project_id=request.project_id or CHAT_PROJECT_ID, agent_id=agent)
        session_id = session.id

    # Correlate this turn's app.log records with the session. The id arrives in
    # the request body (the middleware can only see path/query), so bind it here.
    # Per-request task context is discarded after the response, so no reset is
    # needed; the middleware's reset is guarded against this nested set.
    current_session_id.set(session_id)

    # A session may only have ONE in-flight stream writing its checkpoint; a
    # second concurrent /chat/stream (e.g. a misbehaving client or a double
    # submit) would race the graph's SQLite writes on the same thread_id.
    await _guard_session_not_streaming(session_id)

    # The checkpoint DB is a DISPOSABLE per-turn scratch (single-writer model,
    # cf. codex/opencode): every turn rebuilds from the session history instead
    # of resuming a runtime checkpoint. Delete the thread so this run starts
    # fresh from `history + [user_message]` — this also covers crash leftovers
    # and aborted turns (Stop).
    await agent_registry.forget_runtime_checkpoint(session_id)

    if request.skip_user_append:
        # 插話自动续跑：steer 已由 /chat/interject 持久化为最后一条 user 消息。
        # 直接复用 history（不 append、不重复送模型）。
        user_message = None
        messages = history
    else:
        user_message = {"role": "user", "content": format_user_message(request.message, request.attachments, references, max_attachment_bytes=max_attachment_bytes, vision=bool(getattr(runtime, "provider_vision", True)))}
        session_store.append_message(session_id, role="user", content=request.message, mode=request.mode, work_mode=work_mode, autonomy=autonomy, attachments=request.attachments, references=references, message_id=request.user_message_id or None)
        messages = history + [user_message]
    if request.user_message_id:
        snapshot_user_message_id = request.user_message_id
    else:
        session_messages = session_store.require(session_id).messages
        snapshot_user_message_id = session_messages[-1].id if session_messages else ""

    async def event_stream():
        terminal_sent = False
        error_emitted = False
        interrupt_emitted = False
        accumulated_content = ""
        # Last full `context_usage` frame from the run, persisted on `done` so the
        # session-open preview shows the true request size (system + tools + …).
        last_context_usage: dict[str, Any] | None = None
        # Per-round state (goal 多轮续跑）: the assistant message id the CURRENT
        # round persists to. Round 0 reuses the client-supplied id (frontend
        # bubble reconciliation); continuation rounds get a freshly generated id
        # (never adopt as a successful client commit) and surface it via
        # `done.message_id` so the frontend creates a new bubble per round.
        current_round_assistant_id: str | None = request.assistant_message_id or None
        round_index = 0
        budget_wrapup_done = False
        last_seen_objective: str | None = None
        # W6/N2-P1: raw streaming events buffered for the incremental tool-boundary
        # persist; reset each goal round.
        _partial_parts: list[dict[str, Any]] = []
        stream_iter: Any

        # Fast-fail: when the requested provider was recently discovered as
        # unreachable (cached probe failure), refuse to start the agent and
        # surface the reason immediately instead of letting the client wait on
        # a connect timeout. Pure cache read — never probes, never blocks.
        cached_block = _cached_provider_unreachable(request.provider_id, request.model)
        if cached_block:
            yield f"data: {json.dumps({'type': 'error', 'session_id': session_id, 'error': cached_block}, ensure_ascii=False)}\n\n"
            return

        _USE_CLIENT_MESSAGE_ID = object()

        def _persist_assistant(content, mode, provider, model, parts, message_id=_USE_CLIENT_MESSAGE_ID):
            # By default the CURRENT ROUND's assistant id is used so a completed
            # turn's persisted message reconciles 1:1 with the frontend bubble
            # (round 0 = the client-supplied id; continuation rounds = the
            # per-round generated id). Error / disconnect partials pass
            # `message_id=None` so they get a freshly generated id: they must
            # NEVER be adopted as a successful commit by the frontend's
            # stream-settle reconciliation (which matches on the exact id).
            try:
                resolved_id = (
                    current_round_assistant_id if message_id is _USE_CLIENT_MESSAGE_ID else message_id
                )
                session = session_store.append_message(
                    session_id,
                    role="assistant",
                    content=content,
                    mode=mode or request.mode,
                    provider=provider or "",
                    model=model or request.model or "",
                    work_mode=work_mode,
                    autonomy=autonomy,
                    parts=parts or [],
                    message_id=resolved_id,
                )
                last = session.messages[-1] if session.messages else None
                if last is not None:
                    agent_registry.change_store.assign_message(session_id, last.id)
            except Exception:  # noqa: BLE001 - persistence is a side effect; a
                # write failure must NEVER corrupt the SSE stream contract (the
                # client still needs its terminal done/error event), otherwise a
                # transient fs/json/sqlite hiccup turns a reply into an endless
                # "interrupted" (yellow) bubble with no terminal event at all.
                logger.exception("Failed to persist assistant message for session %s", session_id)

        def _handle_event(event):
            nonlocal terminal_sent, accumulated_content, error_emitted, interrupt_emitted, last_context_usage, current_round_assistant_id
            etype = event.get("type", "")
            if etype == "context_usage":
                # Remember the latest full measurement; persisted on `done`.
                last_context_usage = event
            if etype in ("approval_required", "question_required"):
                # The agent interrupted to ask the user. The turn is NOT complete:
                # the stream must end without a synthetic `done` so the frontend
                # keeps the message in the "waiting" state instead of showing a
                # truncated reply as a finished bubble (fixes "回答後流式回覆提前結束").
                interrupt_emitted = True
            if etype == "delta" and event.get("content"):
                accumulated_content += event.get("content", "")
            if etype == "todos":
                try:
                    session_store.update_todos(session_id, event.get("todos") or [])
                except Exception:  # noqa: BLE001 - a todo persist hiccup must not kill the stream
                    logger.warning("update_todos(todos) failed for session %s", session_id, exc_info=True)
            if etype == "tool_end":
                # W6/N2-P1: at tool boundaries persist the current merged parts so a
                # mid-turn crash leaves a recoverable partial reply. Accumulate ALL
                # raw streaming events (text/reasoning/plan/tool) and write the
                # cumulative merged view at each tool boundary (idempotent replace).
                try:
                    _partial_parts.append(event)
                    if current_round_assistant_id:
                        merged = _merge_event_parts(list(_partial_parts))
                        session_store.replace_assistant_parts(session_id, current_round_assistant_id, merged)
                except Exception:  # noqa: BLE001 - incremental persist is best-effort
                    logger.debug("incremental tool persist failed for %s", session_id, exc_info=True)
            elif etype in ("delta", "reasoning_delta", "plan_delta", "plan_start", "plan_end", "tool_start", "tool_delta"):
                # Buffer for the incremental tool-boundary merge (P2-B).
                _partial_parts.append(event)
            if etype == "done":
                _persist_assistant(
                    event.get("content", ""),
                    event.get("mode"),
                    event.get("provider"),
                    event.get("model"),
                    event.get("parts"),
                )
                # 回带该轮 assistant 消息 id：前端据此识别是首轮（等于请求里的
                # assistant_message_id）还是续跑轮（新建气泡），并对账。
                event["message_id"] = current_round_assistant_id
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.warning("update_modes failed on done for session %s", session_id, exc_info=True)
                # Persist the last full context-usage measurement so the preview
                # reflects the real request size (system prompt + tool schemas +
                # messages + overhead), not a message-only undercount.
                if last_context_usage is not None:
                    try:
                        session_store.update_context_usage(
                            session_id,
                            used_tokens=int(last_context_usage.get("used_tokens", 0) or 0),
                            used_tokens_calibrated=int(last_context_usage.get("used_tokens_calibrated", 0) or 0),
                            used_chars=int(last_context_usage.get("used_chars", 0) or 0),
                            calibration_factor=float(last_context_usage.get("calibration_factor", 0.0) or 0.0),
                        )
                    except Exception:  # noqa: BLE001 - telemetry must never break the stream
                        logger.debug("update_context_usage failed for session %s", session_id, exc_info=True)
                # C1: persist the compaction state so the summary + fingerprint
                # set survive across turns / goal rounds (the per-turn LangGraph
                # checkpoint is discarded — this session record is the source of
                # truth for anchored-update compaction).
                compaction = event.get("compaction") or {}
                if compaction.get("summary") or compaction.get("count"):
                    try:
                        session_store.update_compaction(
                            session_id,
                            summary=str(compaction.get("summary") or ""),
                            fingerprints=compaction.get("fingerprints") or None,
                            count=compaction.get("count") or None,
                        )
                    except Exception:  # noqa: BLE001 - telemetry must never break the stream
                        logger.debug("update_compaction failed for session %s", session_id, exc_info=True)
                if compaction.get("failed"):
                    notice = (
                        "上下文压缩摘要生成失败（当前默认模型不可用或出错），已改用截断方式。"
                        "请在设置中确认模型可用性或更换模型。"
                    )
                    logger.warning("compaction failed for session %s: %s", session_id, notice)
                    event["compaction_notice"] = notice
                terminal_sent = True
                # Success → clear previous error
                try:
                    session = session_store.require(session_id)
                    session.last_error = ""
                    session_store.save(session)
                except KeyError:
                    pass
            elif etype == "error":
                # The runtime yields an explicit error event BEFORE re-raising so
                # the SSE layer can turn it into a proper terminal `error` event.
                # Do NOT mark terminal_sent here: no assistant message has been
                # persisted for this turn, and _on_error must still persist the
                # partial reply (or the "interrupted" marker) when the generator
                # raises — otherwise a stalled/errored run leaves NO assistant
                # message in the session (looks like the agent never answered).
                # error_emitted only suppresses _on_end's synthetic `done`.
                error_emitted = True

        runtime = await _build_stream_runtime(
            request.mode, request.session_id, request.provider_id, request.model,
            resolved_workspace, referenced_ids, agent, request.project_id,
        )

        def _compaction_state() -> dict[str, Any] | None:
            """C1: the session-persisted compaction state to re-inject this turn.

            The per-turn checkpoint is discarded, so this is the only place the
            prior compaction summary survives — re-inject it so the anchored
            update accumulates instead of re-summarizing the full history.
            """
            try:
                session = session_store.require(session_id)
            except KeyError:
                return None
            if not session.context_summary:
                return None
            return {
                "summary": session.context_summary,
                "fingerprints": list(session.context_summarized_fingerprints),
            }

        def _current_history() -> list[dict[str, Any]]:
            """Full user/assistant history from the session (post-round persist),
            so round N sees rounds 1..N-1 output (codex rollout semantics)."""
            try:
                session = session_store.require(session_id)
            except KeyError:
                return []
            history: list[dict[str, Any]] = []
            for m in session.messages:
                if m.role == "user" and m.content:
                    history.append(
                        {
                            "role": "user",
                            "content": format_user_message(m.content, m.attachments, m.references, max_attachment_bytes=max_attachment_bytes, inline_attachments=False),
                        }
                    )
                elif m.role == "assistant" and (m.content or getattr(m, "parts", None)):
                    history.extend(_parts_to_conversation(m))
            return history

        # 空转计数：连续纯文字轮（无工具执行、未调 update_goal）的轮数。
        idle_rounds = 0

        def _goal_injection(goal) -> str | None:
            """该续跑轮要注入的 system 首位内容（内部指令，不落库），或 None。"""
            nonlocal last_seen_objective
            if goal.status == "budget_limited":
                return render_budget_limit(goal)
            if last_seen_objective is not None and goal.objective != last_seen_objective:
                return render_objective_updated(goal)
            return render_goal_continuation(goal)

        async def _goal_rounds_iter():
            """单一生成器内层多轮循环（已拍板落地方式）。

            每轮调用一次 ``runtime.stream`` 依序 yield；``_publish_turn`` /
            SSE 订阅 / 会话事件总线只建一次，整个 goal 运行期保持单条 SSE 连接。
            每轮起点重置终态旗标，每轮独立 snapshot，每轮结束记账并决定是否续跑。
            """
            nonlocal terminal_sent, error_emitted, interrupt_emitted, accumulated_content, last_context_usage, current_round_assistant_id, round_index, budget_wrapup_done, last_seen_objective
            goal_stream_active = _session_goal(session_id) is not None
            inflight_anchor: str | None = None
            inflight_pre: str | None = None
            # 退化回复计数（同一回复内大量重复，qwen3 模式）：累计 ≥2 轮即 blocked。
            degenerate_rounds = 0
            # 连续纯文字（无工具执行）轮数：≥2 轮才停。首轮纯文字不直接 break，
            # 而是直接续跑，等待模型调用 update_goal(complete/blocked) 或撞预算硬停
            # （默认 token 预算为硬天花板）。idle_rounds 声明于 handler 作用域，此处
            # 以 nonlocal 取用。
            nonlocal idle_rounds

            def _begin_round(anchor: str) -> str | None:
                return agent_registry.snapshot_manager.begin_turn(session_id, anchor, resolved_workspace)

            def _end_round(anchor: str | None, pre: str | None) -> None:
                if pre is not None and anchor is not None:
                    agent_registry.snapshot_manager.end_turn(session_id, anchor, resolved_workspace)

            try:
                while True:
                    # ---- per-round reset（否则第 2 轮起 _on_error/_on_end 短路错乱）----
                    terminal_sent = False
                    error_emitted = False
                    interrupt_emitted = False
                    accumulated_content = ""
                    last_context_usage = None
                    # W6/N2-P1: clear the raw-event buffer for this round.
                    _partial_parts.clear()

                    if round_index == 0:
                        if request.skip_user_append and _session_goal(session_id) is not None:
                            # 会话恢复 / 空闲启动：round 0 本身就是续跑轮，注入 goal 上下文。
                            # 该轮的 assistant 消息仍复用客户端传入的 id（前端已预建气泡），
                            # 只有真正的续跑轮（round >= 1）才用后端生成的新 id。
                            goal = _session_goal(session_id)
                            injection = _goal_injection(goal)
                            # 以 user 角色注入（而非 system）：create_agent 总会把自带的
                            # system prompt 前置，system 角色注入会落在第 1 位，被严格
                            # provider（Qwen3.6/vLLM）以 "System message must be at the
                            # beginning" 400 拒绝。user 角色注入（同 steer/压缩摘要惯例）
                            # 紧随框架 system 之后，任何 provider 均接受。
                            round_messages = ([{"role": "user", "content": injection}] if injection else []) + history
                            current_round_assistant_id = request.assistant_message_id or None
                            round_anchor = current_round_assistant_id or snapshot_user_message_id
                            last_seen_objective = goal.objective
                        else:
                            # 普通首轮（用户消息 / interject skip_user_append 无 goal）。
                            round_messages = messages
                            current_round_assistant_id = request.assistant_message_id or None
                            round_anchor = snapshot_user_message_id
                    else:
                        goal = _session_goal(session_id)
                        if goal is None:
                            break
                        if goal.status != "active" and not (goal.status == "budget_limited" and not budget_wrapup_done):
                            break
                        if goal.status == "budget_limited":
                            # 预算兜底轮：注入 budget_limit，仅一轮后停。
                            budget_wrapup_done = True
                            injection = render_budget_limit(goal)
                        else:
                            injection = _goal_injection(goal)
                        round_history = _current_history()
                        if not round_history:
                            break
                        round_messages = ([{"role": "user", "content": injection}] if injection else []) + round_history
                        current_round_assistant_id = f"goal-round-{uuid.uuid4().hex[:12]}"
                        round_anchor = current_round_assistant_id
                        last_seen_objective = goal.objective

                    # 通知前端本轮（续跑轮）已开始：提前给出该轮 assistant 消息 id，
                    # 让前端以 running 态建泡并流式渲染 delta（done 前不折叠进组）。
                    if round_index > 0:
                        yield {
                            "type": "goal_round_start",
                            "session_id": session_id,
                            "round": round_index,
                            "message_id": current_round_assistant_id or "",
                        }

                    # ---- per-round snapshot（回滚按轮隔离）----
                    inflight_anchor = round_anchor
                    inflight_pre = await asyncio.to_thread(_begin_round, round_anchor)
                    try:
                        # 每轮从完整历史重建（codex rollout 语义）：上一轮跑完会以
                        # exit-durability 写盘同一个 <session>.json checkpoint。若直接复用
                        # 该 thread，add_messages reducer 会把本轮完整历史“追加”到旧 state
                        # 上，造成整段上下文每轮重复膨胀。先删掉 checkpoint 让每轮全新起跑。
                        # 中断轮（approval/question）的 checkpoint 在本轮内新写、且循环
                        # 立即 break，不会被这里的删除波及。
                        try:
                            await agent_registry.forget_runtime_checkpoint(session_id)
                        except Exception:  # noqa: BLE001 - fresh-start delete is best-effort
                            logger.warning("round-start checkpoint delete failed for %s", session_id, exc_info=True)
                        round_started = time.monotonic()
                        round_iter = runtime.stream(round_messages, session_id, request.language, work_mode, autonomy, compaction_state=_compaction_state())
                        round_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
                        async for ev in round_iter:
                            # 该轮实际 token 消耗（runtime 已累加该轮所有 model call）。
                            if ev.get("type") == "done" and isinstance(ev.get("usage"), dict):
                                round_usage = ev["usage"]
                            yield ev
                        round_elapsed = time.monotonic() - round_started
                    finally:
                        try:
                            await asyncio.to_thread(_end_round, inflight_anchor, inflight_pre)
                        except Exception:  # noqa: BLE001 - best-effort
                            logger.warning("round-end snapshot failed for %s", session_id, exc_info=True)
                        inflight_pre = None
                        inflight_anchor = None

                    # ---- accounting（仅 session 有 goal 时）----
                    if _session_goal(session_id) is not None:
                        # 整个 goal 过程的实际消耗：该轮所有 model call 的 prompt+completion。
                        token_delta = int(round_usage.get("prompt_tokens", 0) or 0) + int(round_usage.get("completion_tokens", 0) or 0)
                        try:
                            session_store.account_goal_usage(session_id, token_delta, round_elapsed)
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("account_goal_usage failed for %s", session_id, exc_info=True)
                        # 记录已完成的回合数（round 0 为第 1 轮 → round_index+1，供
                        # update_goal(blocked) 做引擎侧 ≥3 轮审计）。
                        try:
                            session_store.update_goal_round(session_id, round_index + 1)
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("update_goal_round failed for %s", session_id, exc_info=True)
                        # 每轮记帐后广播 goal_updated（含 tokens/time/round）→ 前端卡即时更新，
                        # 不再停在 set 时的 0/0/0。
                        try:
                            _g_after = _session_goal(session_id)
                            if _g_after is not None:
                                _emit_goal_updated(session_id, _g_after)
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("goal_updated emit failed for %s", session_id, exc_info=True)

                    # HITL：保留 interrupt checkpoint，goal 保持 active，等前端 resume。
                    if interrupt_emitted:
                        break

                    # ---- 退化回复检测（「一直重複說話」）----
                    # 同一回复内大量重复（qwen3 模式）。硬停只拦一轮，循环继续会再退化；
                    # 累计 ≥2 轮退化 → 目标 blocked，避免跨轮持续重复。
                    if _session_goal(session_id) is not None:
                        try:
                            session = session_store.require(session_id)
                            if session.messages and session.messages[-1].role == "assistant":
                                if is_degenerate_text(session.messages[-1].content):
                                    degenerate_rounds += 1
                                if degenerate_rounds >= 2:
                                    goal = _session_goal(session_id)
                                    if goal is not None and goal.status == "active":
                                        blocked = session_store.update_goal_status(session_id, "blocked")
                                        if blocked is not None:
                                            _emit_goal_updated(session_id, blocked)
                                    break
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("degenerate check failed for %s", session_id, exc_info=True)

                    # ---- 空转停止（推断完成，防空转退化 + 自动关卡片）----
                    # goal 模式每轮无条件续跑，但若模型本轮**没有执行任何实质工具**
                    # （只输出纯文字回答），说明它要么认为任务已完成（但忘了调
                    # update_goal）、要么在空转。连续 2 轮纯文字（无工具且未 done）：
                    # 视为模型已完成但漏发信号，引擎推断为 complete —— 前端收到
                    # complete 后约 2.5s 自动关闭 GoalCard（恢复 11cd0313 行为），
                    # 既不再无限续跑，也不卡在 active 无法关闭。
                    if _session_goal(session_id) is not None:
                        try:
                            _sess = session_store.require(session_id)
                            if not _goal_round_has_tool_execution(_sess):
                                idle_rounds += 1
                                if idle_rounds >= 2:
                                    # 连续 2 轮纯文字（未调 update_goal / 无工具）：
                                    # 推断为完成并置 complete，停止续跑。
                                    logger.debug("goal idle-stop: %d consecutive text-only rounds for %s -> inferred complete", idle_rounds, session_id)
                                    _ig = _session_goal(session_id)
                                    if _ig is not None and _ig.status == "active":
                                        completed = session_store.update_goal_status(session_id, "complete")
                                        if completed is not None:
                                            _emit_goal_updated(session_id, completed)
                                    break
                            else:
                                idle_rounds = 0
                        except Exception:  # noqa: BLE001 - never break the stream
                            logger.debug("idle-stop check failed for %s", session_id, exc_info=True)

                    # ---- continue decision ----
                    goal = _session_goal(session_id)
                    if goal is None:
                        break
                    if goal.status == "active":
                        round_index += 1
                        continue
                    if goal.status == "budget_limited" and not budget_wrapup_done:
                        # 预算刚超限：再跑一轮兜底（注入 budget_limit）后停。
                        round_index += 1
                        continue
                    break

                # 自然收口（goal 达成 / 暂停 / 清除 / 预算兜底完成）：通知前端整条
                # 续跑链结束，供其做最终 settle/收尾。HITL / error 不发（前者保持
                # waiting，后者 error 帧即终态）。
                if goal_stream_active and not interrupt_emitted and not error_emitted:
                    yield {"type": "goal_stream_end", "session_id": session_id}
                # Natural success → clear error
                if not error_emitted:
                    try:
                        session = session_store.require(session_id)
                        session.last_error = ""
                        session_store.save(session)
                    except KeyError:
                        pass
            finally:
                # 生成器被取消（客户端断开 / Stop）：当前轮 snapshot 尚未 end 则兜底关闭。
                if inflight_pre is not None and inflight_anchor is not None:
                    try:
                        await asyncio.to_thread(_end_round, inflight_anchor, inflight_pre)
                    except Exception:  # noqa: BLE001 - best-effort
                        pass

        stream_iter = _goal_rounds_iter()

        _raw_stream_iter = stream_iter

        async def _locked_stream_iterator(it, lock):
            if lock is None:
                async for _ev in it:
                    yield _ev
            else:
                async with lock:
                    async for _ev in it:
                        yield _ev

        stream_iter = _locked_stream_iterator(_raw_stream_iter, None)

        # Purge any previous turn's buffered events for this session so the new
        # subscription starts clean (the concurrency guard already serialized
        # turns, so no live subscriber is affected).
        session_event_bus.purge(session_id)

        def _on_event(event):
            event["session_id"] = session_id
            _handle_event(event)

        def _on_end():
            if not terminal_sent and not error_emitted and not interrupt_emitted:
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
                try:
                    session_store.update_modes(session_id, work_mode, autonomy)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.warning("update_modes failed in _on_end for session %s", session_id, exc_info=True)
                return {"type": "done", "session_id": session_id, "content": accumulated_content, "stream_end": True}
            if interrupt_emitted and not terminal_sent and not error_emitted:
                # Interrupted for an approval/question: persist the partial reply
                # so a refresh keeps the question context, but do NOT emit a done
                # frame — the frontend is waiting for the user's decision and must
                # keep the message in `waiting` until the resume stream settles it.
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [])
            return None

        def _on_error(exc):
            # Catch GeneratorExit / asyncio.CancelledError on client disconnect.
            nonlocal terminal_sent
            # The in-flight turn was interrupted (client abort / Stop). The turn's
            # checkpoint thread is deleted by the stream's finally (turn-end
            # cleanup), and the next /chat/stream deletes it again before starting
            # fresh from session history — no dirty state survives.
            if accumulated_content and not terminal_sent:
                # Persist the partial reply with a GENERATED id (not the
                # client-supplied one) so the frontend's stream-settle
                # reconciliation can never adopt a half reply as a successful
                # commit. The persist still binds this turn's tool changes to a
                # message for rollback (see assign_message in _persist_assistant).
                _persist_assistant(accumulated_content, request.mode, "", request.model or "", [], message_id=None)
            elif not terminal_sent:
                # The stream was cut before any text was emitted (client
                # disconnected mid-tool / mid-thought). Persist a short
                # interrupted marker so the session does not look like the
                # assistant never answered — otherwise a half-finished turn
                # (e.g. a team member creation that already took effect) is
                # invisible to the user after refresh. _persist_assistant is
                # fully guarded and can never raise, so this path cannot drop
                # the terminal error event either. Generated id: never adopt as
                # a successful commit.
                _persist_assistant("（会话流被中断，回复未完成）", request.mode, "", request.model or "", [], message_id=None)
            try:
                session_store.update_modes(session_id, work_mode, autonomy)
            except Exception:  # noqa: BLE001 - never break the terminal event
                logger.warning("update_modes failed in _on_error for session %s", session_id, exc_info=True)
            # turn 报错（非客户端取消 / Stop）且 goal active → blocked（对标 codex
            # TurnError→Blocked 停循环）。Stop / 客户端断开不改 goal 状态（选项 A）。
            # provider 400 BadRequest（请求格式问题，如 strict 模板拒绝 system 位置）
            # 除外：这是确定性、可重试的请求问题而非 agent 停滞，标 blocked 会让
            # GoalCard 停在错误态（即使重试成功也不切回 active）。
            if not isinstance(exc, (asyncio.CancelledError, GeneratorExit)) and not is_provider_bad_request(exc):
                try:
                    goal = _session_goal(session_id)
                    if goal is not None and goal.status == "active":
                        blocked = session_store.update_goal_status(session_id, "blocked")
                        if blocked is not None:
                            _emit_goal_updated(session_id, blocked)
                except Exception:  # noqa: BLE001 - never break the terminal event
                    logger.debug("error→blocked goal update failed for %s", session_id, exc_info=True)
            terminal_sent = True
            return {
                "type": "error",
                "session_id": session_id,
                "error": str(exc)[:400],
                "provider": getattr(runtime, "provider_name", "") or "",
                "model": getattr(runtime, "model_name", "") or "",
                "base_url": getattr(getattr(runtime, "llm", None), "base_url", "") or "",
            }

        # The turn runs as a background task that publishes to the session bus;
        # this endpoint subscribes (replay + live). Decoupling delivery from the
        # runtime generator means tool/worker status transitions reach the client
        # the moment they happen — even while the graph is blocked awaiting a
        # long-running tool (opencode-style session event bus). Snapshots are
        # taken per round inside ``_goal_rounds_iter`` (round 0 anchors on the
        # user message id; continuation rounds anchor on their own assistant id).
        subscription = session_event_bus.stream(session_id)
        turn_task = asyncio.create_task(
            _publish_turn(session_id, stream_iter, _on_event, _on_end, _on_error)
        )
        try:
            async for event in subscription:
                if event is None:
                    # SSE comment line: keep the connection (and the client's idle
                    # watchdog) alive while the agent is busy thinking/working.
                    yield ": ping\n\n"
                elif event.get("type") == "worker_stream_end":
                    break
                else:
                    event["session_id"] = session_id
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # Lifecycle binding: the connection is over (normal end or client
            # disconnect) ⟹ cancel the turn task. Its _publish_turn closes the
            # bus even under cancellation, so a late subscribe can never hang.
            turn_task.cancel()
            # Safety net: the client is gone, so force-terminate the underlying
            # stream consumer and release the session. Graceful cancellation can
            # stall behind a checkpoint DB lock (or a provider that ignores
            # cancellation), which would otherwise leave the session "active" and
            # reject the next edit/regenerate with 409 "session is still
            # generating". Both calls are idempotent. (The interrupted marker for
            # a client abort is set by _on_error in the producer's cancellation
            # handler — NOT here — so a normally-completed turn is never mistaken
            # for an interrupted one.)
            _hard_stop_session_stream(session_id)
            await asyncio.gather(turn_task, return_exceptions=True)
            agent_registry.checkpoint_manager.mark_idle(session_id)
            session_event_bus.purge(session_id)
            # The turn is over and the session released. Delete the disposable
            # checkpoint thread UNLESS the turn paused for an approval/question
            # (interrupt_emitted): that interrupt checkpoint must survive so the
            # resume (HITL) can continue the graph. Shielded so the delete still
            # completes even when the client disconnects right after `done`
            # (Starlette then cancels this generator, which would otherwise abort
            # the await mid-delete). The next /chat/stream also deletes the
            # thread, so a failed delete here is harmless.
            if not interrupt_emitted:
                try:
                    await asyncio.shield(agent_registry.forget_runtime_checkpoint(session_id))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.warning("turn-end checkpoint delete failed for %s", session_id, exc_info=True)
            # Persist error if the turn failed (no done event was emitted)
            if not terminal_sent:
                try:
                    session = session_store.require(session_id)
                    if not session.last_error:
                        session.last_error = "stream terminated without done event"
                    session_store.save(session)
                except KeyError:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
class InterjectRequest(BaseModel):
    session_id: str = ""
    message: str = ""
    # 前端乐观渲染时生成的 user 消息 id，与 /chat/interject 持久化的 id 一致
    # （late-steer 自动续跑时以同一 id + skip_user_append 复用 history）。
    user_message_id: str = ""
    # 前端生成的 steer id：steer_injected 事件会原样带回该 id，前端据此匹配并
    # 从 pending 列表移除，避免「已注入当前轮」的插话又被误判为 late-steer 而二次执行。
    steer_id: str = ""
    attachments: list[dict[str, Any]] = []
    referenced_sessions: list[str] = []
    max_attachment_bytes: int = 25 * 1024 * 1024
@router.post("/chat/interject")
async def interject(request: InterjectRequest):
    """把一条排队消息插入正在进行的流式任务，引导 LLM 后续输出与思考方向。

    与「排队」的区别：插话不会暂停/终止当前流式任务，而是把消息送入
    per-session 的 steer 收件箱，由图内的 ``SteerInjectionMiddleware`` 在下一
    次模型呼叫边界注入为 HumanMessage。当前在飞的 ``llm.stream`` 不被中止。

    仅当会话正处于流式（有在飞的 /chat/stream 任务）时才可插话；否则 409，
    前端回退为普通发送。
    """
    session_id = request.session_id
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    # Correlate this interject's app.log records with the session (body-provided).
    current_session_id.set(session_id)
    try:
        session_store.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 仅当会话正处于流式（有在飞的 /chat/stream 任务）时才可插话；否则 409，
    # 前端回退为普通发送。
    task = _stream_tasks.get(session_id)
    if task is None or task.done():
        raise HTTPException(
            status_code=409,
            detail="当前没有正在进行的任务，无法插话（消息已退回队列）。",
        )

    references = _resolve_references(request.referenced_sessions)
    steer_id = request.steer_id or str(uuid.uuid4())
    entry = SteerEntry(
        id=steer_id,
        content=request.message,
        ts=int(time.time() * 1000),
        user_message_id=request.user_message_id,
        attachments=request.attachments or [],
        references=references,
        max_attachment_bytes=request.max_attachment_bytes,
    )
    # 持久化为真实 user 消息：会话历史可回溯；late-steer 自动续跑时以
    # skip_user_append 直接复用 history（该 steer 已是最后一条 user 消息）。
    # interject=True 让前端不把它渲染为独立用户泡泡（内容由「收到插話」card 展示）。
    try:
        session_store.append_message(
            session_id,
            role="user",
            content=request.message,
            attachments=request.attachments or [],
            references=references,
            message_id=request.user_message_id or None,
            interject=True,
        )
    except Exception:  # noqa: BLE001 - persistence failure must not break the interject
        logger.exception("interject persist failed for session %s", session_id)

    steer_inbox.push(session_id, entry)
    try:
        session_event_bus.publish(
            session_id,
            {
                "type": "steer_admitted",
                "session_id": session_id,
                "steer_id": steer_id,
                "content": request.message,
            },
        )
    except Exception:  # noqa: BLE001 - a publish hiccup must not break the interject
        pass
    return {"status": "ok", "steer_id": steer_id, "session_id": session_id}
