"""Streaming runtimes and the agent runtime registry.

Extracted from the former monolithic ``coworker/agents.py``:

* :func:`_aclose_on_exit` — generator wrapper that ALWAYS acloses the inner
  generator (the vLLM-stop abort fix);
* :class:`OpenAICompatibleStreamRuntime` — the main streaming runtime;
* :class:`SimulatedStreamRuntime` — offline/demo runtime;
* :class:`AgentRuntimeRegistry` — resolves runtimes by mode/provider.

Depends on ``agent.core``, ``agent.graph``, ``agent.middleware`` and
``agent.model_defaults`` (the deepest agent modules); nothing imports runtime.
"""

import asyncio
import os
from collections.abc import AsyncGenerator

from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.errors import GraphRecursionError

from ..changes import ChangeStore
from ..checkpoints import CheckpointManager
from ..config import BackendSettings
from ..context import CalibrationStore
from ..events import session_event_bus, worker_event_bus
from ..logger import get_logger
from ..mcp.mcp import McpManager
from ..memory.layout import DEFAULT_AGENT_NAME
from ..project_snapshot import ProjectSnapshotManager
from ..providers import ProviderEntry, ProviderManager
from ..sessions import SessionStore
from ..traces import AGENT_TRACE_FILENAME, AgentTraceStore
from ..workspace import (
    COMMAND_APPROVAL_FILENAME,
    TOOL_AUDIT_FILENAME,
    CommandApprovalStore,
    Workspace,
    fingerprint_path_for,
)
from .core import (
    LOOP_REASON_FINAL,
    LOOP_REASON_HITL,
    LOOP_REASON_OVERFLOW,
    LOOP_REASON_STEP_CAP,
    AgentMode,
    AgentStreamRuntime,
    Autonomy,
    Language,
    WorkMode,
    _CHANGE_TOOL_NAMES,
    _estimate_file_changes,
    _get_shared_checkpointer,
    _merge_event_parts,
    _message_chunk_events,
    _normalize_usage,
    _normalize_usage_total,
    _open_checkpointer,
    _resolve_project_memory_dir,
    _clean_final_content,
    _terminate_stray_tools,
    agent_run_config,
    is_context_overflow_error,
    is_image_limit_error,
    normalize_autonomy,
    normalize_language,
    normalize_phase,
    normalize_work_mode,
    prepare_agent_messages,
    trace_context,
    _runtime_context_budget,
)
from .graph import _change_to_public, _path_from_tool_input, build_coworker_agent_graph, build_workspace_tools
from .middleware import (
    ContextOverflowError,
    mcp_policy_resolver,
    record_runtime_interrupts,
    stream_event_from_interrupt,
)


def _prepend_compaction_summary(
    prepared_messages: list[Any], summary: str, language: Language
) -> list[Any]:
    """C1: prepend the prior compaction summary as a HumanMessage.

    The model sees the previous "先前对话摘要" so anchored-update compaction
    accumulates (codex keeps the summary in the replacement history; opencode
    reorders via filterCompacted). Never raises — a hiccup just keeps the
    prepared messages unchanged.
    """
    if not summary:
        return prepared_messages
    try:
        from langchain_core.messages import HumanMessage

        from .middleware.base import _compaction_summary_prefix

        summary_msg = HumanMessage(
            content=f"{_compaction_summary_prefix(language)}{summary}",
            additional_kwargs={"lc_source": "summarization"},
        )
        return [summary_msg, *prepared_messages]
    except Exception:  # noqa: BLE001 - summary injection must never break a turn
        return prepared_messages
from .model_defaults import ReasonPreservingChatOpenAI, openai_compatible_base_url, provider_llm_kwargs

logger = get_logger(__name__)

# W1: LRU bound for the per-session runtime/graph cache. One entry per active
# session; evicts least-recently-used first. Deleted sessions are evicted via
# ``AgentRuntimeRegistry.evict_runtime``.
RUNTIME_CACHE_MAX = 128

# W4/N3: classified retry policy for the sampling loop. Overflow compacts and
# retries once (unchanged); rate-limit / transient errors back off
# exponentially and retry up to RETRY_RETRIES times; fatal errors raise
# immediately. Retries only apply BEFORE anything was emitted.
RETRY_RETRIES = 2
RETRY_BACKOFF_BASE = 2.0


def _classify_retry_error(exc: BaseException) -> str:
    """Classify a provider error into ``overflow`` / ``rate_limit`` /
    ``transient`` / ``fatal`` (N3: no one-size-fits-all max_retries)."""
    if is_context_overflow_error(exc):
        return "overflow"
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "rate_limit" in msg or "too many requests" in msg:
        return "rate_limit"
    if any(k in msg for k in ("500", "502", "503", "504", "connection", "timeout", "temporarily", "overloaded", "try again")):
        return "transient"
    return "fatal"


async def _aclose_on_exit(agen: AsyncGenerator[Any, None]) -> AsyncGenerator[Any, None]:
    """Yield from ``agen`` but ALWAYS ``aclose()`` it — on normal end, on a
    raised exception, and on ``GeneratorExit``/cancellation of this wrapper.

    ``async for`` alone does NOT close an async generator when the loop is
    interrupted by an exception. A task.cancel() (user Stop / client
    disconnect) that lands between stream chunks would otherwise leave the
    graph.astream generator suspended with its langchain → httpx → provider
    HTTP request still running — the "vLLM keeps generating after Stop" bug.
    Wrapping every ``graph.astream`` call with this guarantees the upstream
    request is aborted as soon as the consumer chain (``_sse_events`` /
    ``_tracked_stream``) tears down.
    """
    try:
        async for item in agen:
            yield item
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass


class OpenAICompatibleStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpoints_dir: Path, provider: ProviderEntry, model_override: str | None = None, change_store: ChangeStore | None = None, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None, data_dir: Path | None = None, mcp_session_manager: Any | None = None, skill_manager: Any | None = None, memory_manager: Any | None = None, project_store: Any | None = None, agent: str = DEFAULT_AGENT_NAME, project_id: str | None = None, settings: Any | None = None, checkpoint_manager: Any | None = None):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.settings = settings
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(**provider_llm_kwargs(self.model_name, provider, self._openai_compatible_base_url(provider), data_dir=data_dir))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoint_manager = checkpoint_manager
        self.change_store = change_store
        self.session_store = session_store
        self.referenced_sessions = set(referenced_sessions or set())
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.project_id = project_id or ""
        self.agent = agent or DEFAULT_AGENT_NAME
        self._delegation_buffer: list[dict[str, Any]] = []
        # Interjection (插話): consumed-steer frames buffered for persistence.
        # Mirrors ``_delegation_buffer`` — see ``_steer_emit_live``.
        self._steer_buffer: list[dict[str, Any]] = []
        self.context_budget_chars, self.context_window_tokens, self.context_window_source, self.context_window_warning, self.max_output_tokens = _runtime_context_budget(provider, model_override)
        self.provider_vision = bool(getattr(provider, "vision", False))
        # W1 (compile-cache prerequisite): the compiled graph is cached per
        # (work_mode, language, autonomy, references, web/browser names) and
        # reused across turns after ``reset_per_turn``. Per-turn data that the
        # tool closures must see CURRENT values of (turn_index) rides on the
        # STABLE audit-context dict, updated each turn.
        self._graph_cache: dict[tuple[Any, ...], Any] = {}
        # Stable audit-context dict captured by the compiled tool closures; the
        # per-turn ``session_id``/``turn_index`` fields are updated at each
        # ``_stream`` so cached closures always read CURRENT values (W1).
        self._audit_context: dict[str, Any] = {
            "session_id": "",
            "provider": self.provider_name,
            "provider_id": self.provider_id,
            "model": self.model_name,
            "workspace_path": str(self.workspace.root),
            "project_id": self.project_id or "",
            "turn_index": 1,
        }
        self._delegator: Any | None = None
        self._delegator_key: tuple[Any, ...] | None = None

    def _resolve_project_dir(self) -> str:
        """Resolve the project memory dir for this runtime.

        Prefers the explicit ``project_id`` threaded at construction time; a
        single workspace may host two projects (one per mode), so the legacy
        workspace-path reverse lookup is ambiguous and only used as a fallback
        for non-project / default-workspace runs.
        """
        if self.project_id:
            try:
                return self.project_store.memory_dir_for(self.project_id)
            except (KeyError, ValueError):
                pass
        return _resolve_project_memory_dir(self.project_store, str(self.workspace.root))

    @property
    def _memory(self) -> tuple[Any | None, Any | None, str]:
        """Return ``(project_scoped_manager, memory_store, agent_memory_rel)``."""
        if self.memory_manager is None or not getattr(self.memory_manager, "enabled", False):
            return None, None, ""
        project_dir = self._resolve_project_dir()
        view = self.memory_manager.for_project(project_dir, self.agent)
        agent_rel = ""
        if project_dir:
            agent_rel = f"{project_dir}/{self.agent}/BASE/MEMORY.md"
        return view, getattr(view, "store", None), agent_rel

    def _web_tools_for(self, session_id: str) -> list[Any]:
        """Web search/fetch tools when enabled, else ``[]``.

        The Tavily key is optional (``web_fetch`` is keyless; ``web_search``
        reports the missing-key state to the model). Resolved lazily per turn
        so settings / key changes are picked up on the next run without a
        restart. A broken config disables web silently. ``vision`` decides how
        fetched images are delivered; ``session_id`` scopes externalized bytes.
        """
        try:
            from coworker.web import resolve_web_tools

            return resolve_web_tools(
                self.data_dir,
                vision=bool(getattr(self, "provider_vision", False)),
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - a web misconfiguration must never break a turn
            logger.warning("web tools disabled (config error)", exc_info=True)
            return []

    @property
    def _web_capability_line(self) -> str:
        """Capability summary injected into the system prompt (3 states)."""
        try:
            from coworker.web import web_capability_line

            return web_capability_line(self.data_dir)
        except Exception:  # noqa: BLE001
            logger.warning("web capability line unavailable", exc_info=True)
            return ""

    def _browser_tool_for(self, session_id: str) -> Any | None:
        """Embedded-browser tool when the desktop bridge is up, else ``None``.

        Resolved lazily per turn so the tool appears the moment Electron
        registers the bridge. A broken bridge disables the tool silently.
        ``vision`` (provider capability) decides whether screenshots ride as
        native image blocks or are externalized to disk; ``session_id`` scopes
        the screenshot directory for cleanup.
        """
        try:
            from coworker.browser.bridge_client import resolve_browser_tool

            return resolve_browser_tool(
                self.data_dir,
                vision=bool(getattr(self, "provider_vision", False)),
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - a browser misconfiguration must never break a turn
            logger.warning("browser tool disabled (config error)", exc_info=True)
            return None

    @property
    def _browser_capability_line(self) -> str:
        """Capability summary injected into the system prompt (2 states)."""
        try:
            from coworker.browser.bridge_client import browser_capability_line

            return browser_capability_line(self.data_dir)
        except Exception:  # noqa: BLE001
            logger.warning("browser capability line unavailable", exc_info=True)
            return ""

    def _nudge_memory(self, session_id: str) -> None:
        """Phase 2: one call per settled turn; never blocks or raises.

        Uses the project-scoped memory view so that auto-extract writes to the
        correct ``<project_dir>/<agent>/BASE/MEMORY.md`` instead of ``USER.md``.
        """
        try:
            if self.memory_manager is None:
                return
            project_dir = self._resolve_project_dir()
            scoped = self.memory_manager.for_project(project_dir, self.agent)
            scoped.after_turn(session_id)
        except Exception:  # noqa: BLE001 - a memory hiccup must never break a turn
            logger.warning("memory nudge failed", exc_info=True)

    def _build_delegator(self, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy):
        """Return a team Delegator when the project org is multi-agent, else None."""
        try:
            from ..delegation import Delegator

            org_store = getattr(self.memory_manager, "org_store", None)
            if org_store is None or not getattr(self.memory_manager, "enabled", False):
                return None
            project_dir = self._resolve_project_dir()
            if not project_dir or not org_store.exists(project_dir):
                return None
            org = org_store.load(project_dir)
            if getattr(org, "mode", "single") != "multi":
                return None
            if not org_store.is_active(org, self.agent):
                return None
            return Delegator(
                org_store=org_store,
                memory_manager=self.memory_manager,
                project_store=self.project_store,
                workspace=self.workspace,
                caller_agent=self.agent,
                project_dir=project_dir,
                language=language,
                work_mode=work_mode,
                autonomy=autonomy,
                session_id=session_id,
                provider_name=self.provider_name,
                model_name=self.model_name,
                llm=self.llm,
                trace_store=self.trace_store,
                approval_store=self.approval_store,
                change_store=self.change_store,
                session_store=self.session_store,
                data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                emit=self._delegation_emit_live(session_id),
                worker_bus=worker_event_bus,
                vision=bool(getattr(self, "provider_vision", False)),
                context_window_tokens=self.context_window_tokens,
                max_output_tokens=self.max_output_tokens,
                calibration_key=CalibrationStore.key_for(self.provider_id, self.model_name),
            )
        except Exception:  # noqa: BLE001 - delegation must never break a turn
            logger.warning("delegation disabled", exc_info=True)
            return None

    def _delegation_event(self, event: dict[str, Any]) -> None:
        """Buffer a delegation SSE frame for the streaming loop to drain."""
        try:
            self._delegation_buffer.append(event)
        except Exception:  # noqa: BLE001 - never break on buffer append
            pass

    def _delegation_emit_live(self, session_id: str):
        """Delegation emit callback that buffers for persistence AND publishes
        live to the session event bus.

        The parent graph is BLOCKED awaiting the worker tool, so the buffered
        frames can only be drained (and reached the SSE) once the tool finishes.
        Publishing them to the session bus here means ``delegate_start`` / tool
        status reaches the frontend the moment it happens — the bus fan-out runs
        independently of the blocked generator.
        """

        def _emit(event: dict[str, Any]) -> None:
            self._delegation_event(event)
            try:
                session_event_bus.publish(session_id, event)
            except Exception:  # noqa: BLE001 - never break on a publish hiccup
                pass

        return _emit

    def _drain_delegation_events(self) -> list[dict[str, Any]]:
        try:
            events = list(self._delegation_buffer)
            self._delegation_buffer.clear()
            return events
        except Exception:  # noqa: BLE001
            return []

    def _steer_emit_live(self, session_id: str):
        """Interjection emit callback: buffer for persistence AND publish live.

        Called by ``SteerInjectionMiddleware`` the moment a steer is consumed by
        the graph. The buffered frame is drained by ``_stream`` into ``parts``
        so the "收到插話" notice round-trips through ``done.parts`` (survives a
        refresh); publishing to the session bus delivers it to the frontend's
        live SSE stream immediately.
        """

        def _emit(event: dict[str, Any]) -> None:
            try:
                # 持久化到 parts 的副本用 ``steer`` 型别（前端 PartSteer 直接渲染）；
                # 发给 session bus 的 SSE 事件维持 ``steer_injected``（前端事件分支）。
                part = {**event, "type": "steer"}
                self._steer_buffer.append(part)
            except Exception:  # noqa: BLE001 - never break on buffer append
                pass
            try:
                session_event_bus.publish(session_id, event)
            except Exception:  # noqa: BLE001 - never break on a publish hiccup
                pass

        return _emit

    def _drain_steer_events(self) -> list[dict[str, Any]]:
        try:
            events = list(self._steer_buffer)
            self._steer_buffer.clear()
            return events
        except Exception:  # noqa: BLE001
            return []

    def _goal_emit_live(self, session_id: str):
        """Goal-command emit callback: publish ``goal_updated`` to the session bus.

        Called by the ``update_goal`` model tool the moment it changes the goal
        status, so the TodoBlock goal section refreshes live (streaming channel);
        the HTTP /goal endpoints use ``_emit_goal_updated`` for the idle channel.
        """

        def _emit(event: dict[str, Any]) -> None:
            try:
                session_event_bus.publish(session_id, event)
            except Exception:  # noqa: BLE001 - never break on a publish hiccup
                pass

        return _emit

    async def _force_compact(self, graph: Any, inputs: dict[str, Any], config: Any) -> None:
        """Halve the context budget on the middleware and nudge the checkpoint
        so the overflow retry sends a strictly smaller request.

        The middleware's ``budget_chars`` is mutable and read on every
        ``abefore_model``; halving it guarantees the retried turn trims harder.
        """
        try:
            middleware = getattr(graph, "_cw_context_middleware", None)
            if middleware is not None and hasattr(middleware, "budget_chars"):
                middleware.budget_chars = max(20_000, int(middleware.budget_chars * 0.5))
            if middleware is not None and hasattr(middleware, "budget_tokens"):
                middleware.budget_tokens = max(5_000, int(middleware.budget_tokens * 0.5))
            logger.info("forced context compaction for overflow retry (budget halved)")
        except Exception:  # noqa: BLE001 - best-effort
            logger.warning("overflow compaction failed", exc_info=True)

    def _fold_calibration(self, graph: Any, actual_prompt_tokens: int) -> None:
        """Feed one real usage observation into the closed-loop calibration.

        Pairs the provider-reported prompt tokens with the guard's RAW pre-send
        estimate of the same request; the learned factor corrects every future
        meter/trim/guard decision for this (provider, model).
        """
        try:
            guard = getattr(graph, "_cw_context_guard", None)
            store = getattr(guard, "calibration_store", None)
            key = getattr(guard, "calibration_key", "") or ""
            estimated = int(getattr(guard, "last_raw_estimate", 0) or 0)
            if store is None or not key or estimated <= 0 or actual_prompt_tokens <= 0:
                return
            store.update(key, actual_tokens=actual_prompt_tokens, estimated_tokens=estimated)
        except Exception:  # noqa: BLE001 - calibration must never break a turn
            logger.debug("calibration fold skipped", exc_info=True)

    def _fold_calibration_from_error(self, graph: Any, exc: BaseException) -> None:
        """Learn from an overflow 400: providers report the real prompt size in
        the rejection message; folding it calibrates the meter immediately."""
        try:
            from ..context import parse_overflow_actual_tokens

            actual = parse_overflow_actual_tokens(str(exc))
            if actual:
                self._fold_calibration(graph, actual)
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("overflow calibration fold skipped", exc_info=True)

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        return openai_compatible_base_url(provider)

    async def stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy, *, compaction_state: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=False, compaction_state=compaction_state):
            yield event

    async def stream_rerun(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy, *, compaction_state: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Re-run the agent from a full message history (rollback/regenerate/edit).

        Unlike ``stream``, this treats the given messages as the complete initial
        state (no checkpoint append). The session checkpoint must already have
        been reset by the caller so the history is rebuilt from scratch.
        """
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=True, compaction_state=compaction_state):
            yield event

    async def _stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy, *, rerun: bool, compaction_state: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root), "project_id": self.project_id,
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        interrupt_context = {**audit_context, "language": language, "work_mode": work_mode, "autonomy": autonomy, "referenced_sessions": list(self.referenced_sessions)}
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "rerun" if rerun else "stream"})
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}
        yield {"type": "stage", "name": "executing", "status": "running"}

        try:
            from coworker.web import web_capability_status

            cap_status = web_capability_status(self.data_dir)
        except Exception:  # noqa: BLE001 - a broken capability probe must never break a turn
            cap_status = "ok"
        if cap_status != "ok":
            yield {"type": "web_setup_hint", "status": cap_status, "session_id": session_id}

        prepared_messages = prepare_agent_messages(messages)
        turn_index = self._next_turn_index(session_id)
        memory_view, memory_store, memory_rel = self._memory
        # W1: cached tool closures read CURRENT values from the stable
        # audit-context dict; session_id/turn_index are updated each turn.
        self._audit_context["session_id"] = session_id
        self._audit_context["turn_index"] = turn_index

        async with _open_checkpointer(self.checkpoints_dir) as checkpointer:
            graph = self._compiled_graph(
                session_id=session_id,
                language=language,
                work_mode=work_mode,
                autonomy=autonomy,
                checkpointer=checkpointer,
                memory_view=memory_view,
                memory_store=memory_store,
                memory_rel=memory_rel,
            )

            # C1: re-inject the persisted compaction summary from a previous turn
            # so (a) the model sees the prior context ("先前对话摘要" message —
            # codex keeps the summary in the replacement history) and (b) the
            # anchored-update summarizer accumulates instead of re-summarizing
            # the full history every turn. The per-turn checkpoint is discarded,
            # so this session-sourced state is the source of truth.
            messages_for_input = prepared_messages
            compaction_state = compaction_state or {}
            if compaction_state.get("summary"):
                messages_for_input = _prepend_compaction_summary(
                    prepared_messages, compaction_state["summary"], language
                )

            inputs = {
                "messages": messages_for_input,
                "work_mode": work_mode,
                "language": language,
                "phase": normalize_phase(None, work_mode),
                "autonomy": autonomy,
                "session_id": session_id,
                "context_summary": str(compaction_state.get("summary") or ""),
                "context_summarized_fingerprints": [str(x) for x in compaction_state.get("fingerprints") or []],
            }
            config = agent_run_config(
                session_id=session_id, provider=self.provider_name, model=self.model_name,
                language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
            )

            content_parts: list[str] = []
            tool_state: dict[str, dict[str, Any]] = {}
            parts: list[dict[str, Any]] = []
            # Token usage for this stream run, summed from the model node's final
            # AIMessage usage_metadata so each model call inside the tool loop is
            # counted exactly once.
            run_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
            # C1: compaction state captured from node updates so the turn's done
            # event can persist it to the session (the per-turn checkpoint is
            # discarded, so without this the summary never survives a turn).
            compact_summary = ""
            compact_count = 0
            compact_fingerprints: list[str] = []
            compact_failed = False
            loop_reason = ""

            # W1: the compiled graph + stable workspace are reused across turns;
            # reset per-turn mutable state (budget halving, steer buffers, phase,
            # fragment caches) and inject this turn's steer-emit callback.
            try:
                self.workspace.begin_turn()
                # W1 upstream chain: the runtime is cached per session, so the
                # delegate/steer event buffers would leak a previous turn's
                # undrained notification frames into the next turn. Any frame
                # already applied to the conversation is a notification only —
                # safe to drop at a fresh-turn boundary.
                self._delegation_buffer.clear()
                self._steer_buffer.clear()
                reset = getattr(graph, "_cw_reset_per_turn", None)
                if reset is not None:
                    reset(steer_emit=self._steer_emit_live(session_id))
            except Exception:  # noqa: BLE001 - a reset failure must never break a turn
                logger.warning("per-turn graph reset failed for %s", session_id, exc_info=True)

            # Overflow recovery / classified retry (W4/N3): run the stream, and if
            # the provider rejects the request for being too long before anything
            # was emitted, force a tighter budget (compact once); rate-limit /
            # transient errors back off exponentially. Retries never re-emit.
            for _attempt in range(RETRY_RETRIES + 1):
                try:
                    async for stream_mode, chunk in _aclose_on_exit(graph.astream(inputs, config=config, stream_mode=["messages", "custom", "updates"])):
                        # Drain any delegation SSE frames buffered by the delegate
                        # tools. They were ALREADY published live to the session
                        # bus by _delegation_emit_live, so here we only record them
                        # in `parts` so they persist and round-trip through the
                        # authoritative `done.parts`.
                        for delegate_event in self._drain_delegation_events():
                            parts.append(delegate_event)
                        # Interjection (插話) consumed-steer frames (published live
                        # by SteerInjectionMiddleware); record them in parts so the
                        # "收到插話" notice persists and round-trips via done.parts.
                        for steer_event in self._drain_steer_events():
                            parts.append(steer_event)
                        if stream_mode == "messages":
                            msg, _meta = chunk
                            # LangGraph's "messages" stream mode also captures the
                            # model stream of nested sub-agents (worker / delegation)
                            # because they share the parent LLM instance. Those chunks
                            # belong on the worker bus (see WorkerAgent._execute), so
                            # drop them here — otherwise the worker's deltas/tool calls
                            # leak into the main SSE stream and double-persist.
                            _meta_sid = (_meta or {}).get("coworker.session_id") if isinstance(_meta, dict) else None
                            if _meta_sid and _meta_sid != session_id:
                                continue
                            try:
                                for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                    yield event
                            except GeneratorExit:
                                raise
                            except Exception:
                                # The stream must keep going (the chunk is non-fatal),
                                # but never swallow it silently — a missing tool card /
                                # text segment would otherwise be undiagnosable.
                                logger.exception("Failed to emit message-chunk event")
                        elif stream_mode == "custom":
                            if isinstance(chunk, dict):
                                event_type = chunk.get("type", "")
                                if event_type == "context_usage":
                                    # The middleware has no session context, so
                                    # stamp the active session id before forwarding.
                                    yield {**chunk, "session_id": session_id}
                                elif event_type == "context_guard":
                                    yield {**chunk, "session_id": session_id}
                                elif event_type in ("plan_start", "plan_delta", "plan_end"):
                                    parts.append(chunk)
                                    yield chunk
                        elif stream_mode == "updates":
                            if "__interrupt__" in chunk:
                                approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, interrupt_context, mcp_policy_resolver(self.mcp_session_manager))
                                self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals]})
                                for approval in approvals:
                                    event = stream_event_from_interrupt(approval)
                                    yield event
                                return
                            # write_todos updates the todo list via a Command state update.
                            for node_name, node_update in chunk.items():
                                if isinstance(node_update, dict) and "todos" in node_update:
                                    yield {"type": "todos", "todos": node_update.get("todos") or []}
                                if isinstance(node_update, dict):
                                    node_messages = node_update.get("messages")
                                    if isinstance(node_messages, list) and node_messages:
                                        last_msg = node_messages[-1]
                                        usage = getattr(last_msg, "usage_metadata", None) or {}
                                        if isinstance(usage, dict):
                                            p, c = _normalize_usage(usage)
                                            run_usage["prompt_tokens"] += p
                                            run_usage["completion_tokens"] += c
                                            # Closed-loop calibration: pair the
                                            # provider's ACTUAL prompt tokens (cache
                                            # INCLUDED — cached tokens still occupy
                                            # the window, T3) with the guard's raw
                                            # pre-send estimate of the same request.
                                            p_total, _ = _normalize_usage_total(usage)
                                            self._fold_calibration(graph, p_total)
                                    cs = node_update.get("context_summary")
                                    if cs:
                                        compact_summary = str(cs)
                                    cc = node_update.get("context_compact_count")
                                    if cc:
                                        compact_count = int(cc)
                                    fp = node_update.get("context_summarized_fingerprints")
                                    if isinstance(fp, list):
                                        compact_fingerprints = [str(x) for x in fp]
                                    cf = node_update.get("context_compact_failed")
                                    if cf:
                                        compact_failed = True
                                    lr = node_update.get("loop_reason")
                                    if lr:
                                        loop_reason = str(lr)
                    break
                except asyncio.CancelledError:
                    raise
                except ContextOverflowError as exc:
                    # The pre-send guard already applied every staged reduction
                    # and the request still does not fit. Emit a precise terminal
                    # error (never the provider's raw 400) and compact so the
                    # user's next action starts from a smaller resident set.
                    logger.warning("context guard overflow: %s", str(exc)[:300])
                    self.trace_store.record(
                        "agent_activity", "error", current_trace_context,
                        {"error": str(exc)[:400], "kind": "context_overflow"},
                    )
                    await self._force_compact(graph, inputs, config)
                    yield {
                        "type": "error",
                        "session_id": session_id,
                        "error": (
                            "上下文已滿：即使清理了舊工具結果與早期消息，請求仍超出模型可用窗口。"
                            "已自動壓縮歷史，請點重試或繼續對話。"
                        ),
                        "error_code": "context_overflow",
                        "measured_tokens": exc.measured_tokens,
                        "limit_tokens": exc.limit_tokens,
                        "loop_reason": LOOP_REASON_OVERFLOW,
                    }
                    return
                except GraphRecursionError as exc:
                    # MAX_STEPS_PROMPT backstop reached (W2/N1): a legitimately
                    # long turn that outlives the step cap must NOT surface a raw
                    # langgraph GRAPH_RECURSION_LIMIT error. Stop gracefully with
                    # loop_reason="step_cap" and a user note, then fall through to
                    # the normal done emission.
                    logger.warning("step cap reached for session %s: %s", session_id, str(exc)[:200])
                    loop_reason = LOOP_REASON_STEP_CAP
                    if not content_parts:
                        content_parts.append(
                            "\n（已達單次任務的最大工具步驟數上限，已安全停止。"
                            "可讓任務更聚焦或分步後繼續。）"
                        )
                    break
                except Exception as exc:
                    if (
                        _attempt == 0
                        and not content_parts
                        and not parts
                        and is_context_overflow_error(exc)
                    ):
                        logger.warning("context overflow; compacting and retrying once: %s", str(exc)[:200])
                        # A provider overflow carries the REAL token count in its
                        # message — feed it to calibration before retrying so the
                        # very failure that slipped through corrects the meter.
                        self._fold_calibration_from_error(graph, exc)
                        await self._force_compact(graph, inputs, config)
                        continue
                    if (
                        _attempt == 0
                        and not content_parts
                        and not parts
                        and is_image_limit_error(exc)
                    ):
                        # 服务端对每 prompt 图数设了上限（如 vLLM --limit-mm-per-prompt.image）。
                        # prepare_agent_messages 已先限流到 MAX_IMAGES_PER_PROMPT，这里作为兜底：
                        # 极少发生的图数超限（例如服务端上限比预期更低）时，少带图重试点一次，
                        # 避免把原始 400 直接丢给用户。
                        logger.warning("image limit overflow; retrying once with a single image: %s", str(exc)[:200])
                        prepared_messages = prepare_agent_messages(messages, max_images=1)
                        inputs["messages"] = prepared_messages
                        continue
                    retry_kind = _classify_retry_error(exc)
                    if (
                        retry_kind in ("rate_limit", "transient")
                        and _attempt < RETRY_RETRIES
                        and not content_parts
                        and not parts
                    ):
                        delay = RETRY_BACKOFF_BASE * (2 ** _attempt)
                        logger.warning(
                            "transient provider error (%s); backing off %.1fs and retrying attempt %d: %s",
                            retry_kind, delay, _attempt + 1, str(exc)[:200],
                        )
                        await asyncio.sleep(delay)
                        continue
                    self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
                    # Do NOT yield an error event here before raising: every consumer
                    # of this stream wraps it in `_sse_events`, whose producer already
                    # turns a raise into exactly one terminal `error` event (via the
                    # `on_error` callback, enriched with session_id/provider/model/
                    # base_url). Yielding a second error here produces a double error
                    # event on the wire (one as a normal event, one as the terminal
                    # error), which the frontend state machine treats as two
                    # conflicting terminal states. Just record the trace and re-raise.
                    raise

        final_content = "".join(content_parts)
        # D5: combined final-content cleanup (plan leak + compaction echo).
        _mw = getattr(graph, "_cw_context_middleware", None)
        final_content = _clean_final_content(final_content, parts, getattr(_mw, "last_summary", "") or "")
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content)})
        merged_parts = _merge_event_parts(_terminate_stray_tools(parts))
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        self._nudge_memory(session_id)
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": merged_parts, "usage": run_usage, "loop_reason": loop_reason or LOOP_REASON_FINAL, "compaction": {"summary": compact_summary, "count": compact_count, "fingerprints": compact_fingerprints, "failed": compact_failed}}

    def _compiled_graph(
        self,
        *,
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        autonomy: Autonomy,
        checkpointer: Any,
        memory_view: Any | None,
        memory_store: Any | None,
        memory_rel: str,
    ) -> Any:
        """W1 (compile cache): build the agent graph ONCE per turn-dependency
        fingerprint and reuse it across turns after ``reset_per_turn``.

        The graph is cached on ``(work_mode, language, autonomy, references,
        web/browser names)``. Per-turn data the tool closures must see CURRENT
        values of (turn_index) rides on the stable ``self._audit_context``;
        the delegator is built once per session (it spawns fresh worker runs
        per call, so reuse is safe). The checkpointer is the shared JSON saver
        and threads are reset per turn, so baking it is safe.
        """
        web_tools = self._web_tools_for(session_id)
        browser_tool = self._browser_tool_for(session_id)
        key = (
            work_mode,
            language,
            autonomy,
            frozenset(self.referenced_sessions),
            tuple(sorted(getattr(t, "name", "") for t in web_tools)),
            bool(browser_tool),
        )
        cached = self._graph_cache.get(key)
        if cached is not None:
            return cached

        delegator_key = (session_id, language, work_mode, autonomy)
        if self._delegator_key != delegator_key or self._delegator is None:
            self._delegator = self._build_delegator(session_id, language, work_mode, autonomy)
            self._delegator_key = delegator_key

        audit = self._audit_context
        graph = build_coworker_agent_graph(
            self.llm,
            build_workspace_tools(
                self.workspace,
                audit,
                change_store=self.change_store,
                session_store=self.session_store,
                referenced_sessions=self.referenced_sessions,
                skill_manager=self.skill_manager,
                memory_store=memory_store,
                memory_rel=memory_rel,
                delegator=self._delegator,
                caller_agent=self.agent,
                web_tools=web_tools,
                browser_tool=browser_tool,
                use_worker_enabled=True,
                language=language,
                max_concurrent=self.settings.max_concurrent_workers if self.settings else 4,
                worker_llm=self.llm,
                worker_session_id=session_id,
                worker_work_mode=work_mode,
                worker_autonomy=autonomy,
                worker_provider_name=self.provider_name,
                worker_approval_store=self.approval_store,
                worker_data_dir=self.data_dir,
                worker_mcp_session_manager=self.mcp_session_manager,
                delegation_emit=self._delegation_emit_live(session_id),
                worker_bus=worker_event_bus,
                worker_context_window_tokens=self.context_window_tokens,
                worker_max_output_tokens=self.max_output_tokens,
                worker_calibration_key=CalibrationStore.key_for(self.provider_id, self.model_name),
                session_id=session_id,
                goal_emit=self._goal_emit_live(session_id),
            ),
            work_mode=work_mode,
            language=language,
            autonomy=autonomy,
            checkpointer=checkpointer,
            approval_store=self.approval_store,
            data_dir=self.data_dir,
            mcp_session_manager=self.mcp_session_manager,
            skill_manager=self.skill_manager,
            memory_manager=memory_view,
            workspace=self.workspace,
            context_budget=self.context_budget_chars,
            context_window_tokens=self.context_window_tokens,
            context_window_source=self.context_window_source,
            context_window_warning=self.context_window_warning,
            web_capability=self._web_capability_line,
            browser_capability=self._browser_capability_line,
            max_output_tokens=self.max_output_tokens,
            calibration_key=CalibrationStore.key_for(self.provider_id, self.model_name),
        )
        self._graph_cache[key] = graph
        return graph

    def _handle_message_chunk(
        self, msg: Any, content_parts: list[str], tool_state: dict[str, dict[str, Any]], parts: list[dict[str, Any]], session_id: str = "",
    ) -> list[dict[str, Any]]:
        return _message_chunk_events(
            msg,
            content_parts,
            tool_state,
            parts,
            session_id=session_id,
            real_file_changes=self._real_file_changes,
        )

    def _real_file_changes(self, tc_id: str, tool_state: dict[str, dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
        state = tool_state.get(tc_id) or {}
        tool_name = str(state.get("name") or "")
        input_raw = str(state.get("input") or "")
        if tool_name in _CHANGE_TOOL_NAMES and self.change_store is not None and session_id:
            raw_path = _path_from_tool_input(tool_name, input_raw)
            if raw_path:
                normalized = self.workspace.normalize_rel_path(raw_path)
                change = self.change_store.match_and_claim(session_id, tool_name, normalized)
                if change is not None:
                    return [_change_to_public(change)]
        return _estimate_file_changes(tool_name, input_raw)

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted turn, yielding progress events in real time.

        Every decision (approve / reject / continue_discuss / respond) resumes
        the SAME graph execution via ``Command(resume=...)`` — the official
        LangGraph HITL contract. The middleware synthesizes the corresponding
        ToolMessage (reject -> error feedback, respond -> human answer, plan
        approve -> execute transition), so the model always gets to continue
        instead of the turn being hard-terminated (fixes D4/D5).
        """
        from langgraph.types import Command

        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        language = normalize_language(context.get("language"))
        work_mode = normalize_work_mode(str(context.get("work_mode") or "build"))
        autonomy = normalize_autonomy(context.get("autonomy"))
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        content_parts: list[str] = []
        parts: list[dict[str, Any]] = []
        decision_types = ", ".join(str(item.get("type")) for item in decisions)
        self.trace_store.record("agent_activity", "resolved", current_trace_context, {"approval_id": approval.get("id", ""), "decisions": decision_types})

        # If HITL approved write operations, mark the workspace so that
        # resolve_write_path() will accept external paths during the resumed run.
        approved_approvals = self.approval_store.list()
        for approval_rec in approved_approvals:
            decision = approval_rec.get("decision") or {}
            if decision.get("type") == "approve":
                tool_name = str(approval_rec.get("tool_name", ""))
                if tool_name in ("write_file", "replace_in_file", "apply_text_edits", "run_command"):
                    self.workspace._allow_external_write = True
                    break

        config = agent_run_config(
            session_id=session_id, provider=self.provider_name, model=self.model_name,
            language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )

        async with _open_checkpointer(self.checkpoints_dir) as checkpointer:
            memory_view, memory_store, memory_rel = self._memory
            self._audit_context["session_id"] = session_id
            graph = self._compiled_graph(
                session_id=session_id,
                language=language,
                work_mode=work_mode,
                autonomy=autonomy,
                checkpointer=checkpointer,
                memory_view=memory_view,
                memory_store=memory_store,
                memory_rel=memory_rel,
            )
            interrupt_id = str(context.get("interrupt_id") or "")
            # If a question was rejected, stop the turn immediately instead of
            # re-entering the agent graph.
            if any(d.get("type") == "_stop_turn" for d in decisions):
                mode = work_mode
                if not mode:
                    mode = normalize_work_mode("build") if work_mode is None else work_mode
                yield {
                    "type": "done",
                    "content": "",
                    "mode": mode.value if hasattr(mode, "value") else str(mode),
                    "autonomy": autonomy or "guarded",
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "parts": [],
                    "loop_reason": LOOP_REASON_HITL,
                }
                return
            resume_map: dict[str, Any] = {interrupt_id: {"decisions": decisions}} if interrupt_id else {"decisions": decisions}
            tool_state: dict[str, dict[str, Any]] = {}
            resumed_loop_reason: str = LOOP_REASON_HITL
            try:
                # ``update`` seeds the session_id so the steer middleware can
                # resolve the interjection inbox during a resumed (HITL) turn too.
                async for stream_mode, chunk in _aclose_on_exit(graph.astream(Command(resume=resume_map, update={"session_id": session_id}), config=config, stream_mode=["messages", "custom", "updates"])):
                    # Delegation frames are published live to the session bus by
                    # _delegation_emit_live; record them in parts for persistence.
                    for delegate_event in self._drain_delegation_events():
                        parts.append(delegate_event)
                    for steer_event in self._drain_steer_events():
                        parts.append(steer_event)
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        # Same nested-sub-agent filter as _stream: worker / delegation
                        # chunks captured by the parent stream must not leak into the
                        # resumed session's SSE.
                        _meta_sid = (_meta or {}).get("coworker.session_id") if isinstance(_meta, dict) else None
                        if _meta_sid and _meta_sid != session_id:
                            continue
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                yield event
                        except GeneratorExit:
                            raise
                        except Exception:
                            # The stream must keep going (the chunk is non-fatal),
                            # but never swallow it silently — a missing tool card /
                            # text segment would otherwise be undiagnosable.
                            logger.exception("Failed to emit message-chunk event")
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type == "context_usage":
                                # The middleware has no session context, so
                                # stamp the active session id before forwarding.
                                yield {**chunk, "session_id": session_id}
                            elif event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                yield chunk
                    elif stream_mode == "updates":
                        if "__interrupt__" in chunk:
                            approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, context, mcp_policy_resolver(self.mcp_session_manager))
                            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals], "resumed": True})
                            for item in approvals:
                                event = stream_event_from_interrupt(item)
                                yield event
                            continue
            except GraphRecursionError as exc:
                # Step-cap backstop hit during a resumed turn — stop gracefully
                # (same note as the main stream) instead of surfacing a raw
                # langgraph GRAPH_RECURSION_LIMIT error.
                logger.warning("step cap reached during resume for %s: %s", session_id, str(exc)[:200])
                resumed_loop_reason = LOOP_REASON_STEP_CAP
                if not content_parts:
                    content_parts.append(
                        "\n（已達單次任務的最大工具步驟數上限，已安全停止。"
                        "可讓任務更聚焦或分步後繼續。）"
                    )
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400], "resumed": True})
                raise
            finally:
                # Reset so the flag cannot leak into subsequent turns or agent runs.
                self.workspace._allow_external_write = False

        final_content = "".join(content_parts)
        # D5: combined final-content cleanup (plan leak + compaction echo).
        _mw = getattr(graph, "_cw_context_middleware", None)
        final_content = _clean_final_content(final_content, parts, getattr(_mw, "last_summary", "") or "")
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content), "resumed": True})
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        self._nudge_memory(session_id)
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": _merge_event_parts(_terminate_stray_tools(parts)), "loop_reason": resumed_loop_reason}
        return


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace

    async def stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def stream_rerun(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def _stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        user_message = messages[-1]["content"] if messages else ""
        if isinstance(user_message, list):
            user_message = " ".join(
                part.get("text", "")
                for part in user_message
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {autonomy}\n\n你说：{user_message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {autonomy}\n\nYou said: {user_message}"
            )
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": "simulated", "model": ""}
        for chunk in content:
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "content": content, "mode": self.mode, "provider": "simulated", "model": ""}


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings, session_store: SessionStore | None = None, mcp_session_manager: Any | None = None, skill_manager: Any | None = None, memory_manager: Any | None = None, project_store: Any | None = None):
        self.settings = settings
        self.session_store = session_store
        self.skill_manager = skill_manager
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.default_workspace = Workspace(
            # 空项目兜底永远指向系统保留的聊天沙箱目录，绝不落到应用自身仓库
            # 根目录（settings.workspace_dir），避免 agent 拥有应用源码读写权限。
            settings.data_dir / "chat",
            settings.data_dir / TOOL_AUDIT_FILENAME,
            fingerprint_path_for(settings.data_dir, settings.data_dir / "chat"),
        )
        self.approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
        self.trace_store = AgentTraceStore(settings.data_dir / AGENT_TRACE_FILENAME)
        self.change_store = ChangeStore(settings.data_dir)
        self.snapshot_manager = ProjectSnapshotManager(settings.data_dir)
        self.provider_manager = ProviderManager(settings.data_dir / "providers.json", settings.data_dir)
        self.mcp_manager = McpManager(settings.data_dir / "mcp_servers.json")
        self.mcp_session_manager = mcp_session_manager
        # Per-session JSON checkpoint files (single-writer model, cf. cline):
        # each session keeps ONE atomic `checkpoints/<session_id>.json` while its
        # turn is in flight / an approval is pending, and deletes it at turn end.
        # There is no shared SQLite file, so write-lock contention / busy_timeout
        # / "database is locked" are physically impossible.
        self.checkpoints_dir = settings.data_dir / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        # Migrate away from the legacy shared SQLite checkpoint DB: it is a
        # disposable per-turn cache rebuilt from session history, so it is simply
        # removed (backed up first) rather than migrated.
        legacy_db = settings.data_dir / "runtime_checkpoints.sqlite"
        if legacy_db.exists():
            try:
                backup = settings.data_dir / "db-backups"
                backup.mkdir(parents=True, exist_ok=True)
                import shutil
                for suffix in ("", "-wal", "-shm"):
                    src = Path(f"{legacy_db}{suffix}")
                    if src.exists():
                        shutil.move(str(src), str(backup / src.name))
                logger.info("migrated legacy runtime_checkpoints.sqlite into %s", backup)
            except Exception:  # noqa: BLE001 - migration must never break startup
                logger.warning("failed to migrate legacy checkpoint DB: %s", legacy_db, exc_info=True)
        self.checkpoint_manager = CheckpointManager(
            self.checkpoints_dir,
            sessions_dir=settings.data_dir / "sessions",
        )
        # W1: per-session runtime/graph cache (LRU-bounded; evicted on session
        # delete). Each entry holds a compiled graph + middleware instances whose
        # per-turn state is reset via ``reset_per_turn`` at every turn start.
        self._runtime_cache: dict[tuple[Any, ...], AgentStreamRuntime] = {}

    async def forget_runtime_checkpoint(self, session_id: str) -> bool:
        """Best-effort checkpoint reset; returns whether the delete completed.

        Runs on the event loop through the single shared JSON-file saver, which
        serializes deletes with every other checkpoint write (single-writer
        model). Deletion is just removing ``checkpoints/<session_id>.json`` —
        no database to lock. The checkpoint is disposable per turn, so a failed
        delete is harmless — the next turn rebuilds from session history anyway.
        """
        try:
            checkpointer = await _get_shared_checkpointer(self.checkpoints_dir)
            await checkpointer.adelete_thread(session_id)
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning("forget_runtime_checkpoint failed for %s: %s", session_id, exc, exc_info=True)
            return False
    def _provider_for_request(self, provider_id: str | None, model: str | None) -> ProviderEntry | None:
        if provider_id:
            config = self.provider_manager.load()
            provider = config.find_enabled(provider_id)
            if not provider:
                raise RuntimeError(f"Provider {provider_id} is not enabled or not found")
            return replace(provider, model=model or provider.model)
        provider = self.provider_manager.default_provider()
        if provider and model:
            return replace(provider, model=model)
        return provider

    def _workspace_or_default(self, workspace: Workspace | None = None) -> Workspace:
        return workspace or self.default_workspace

    def list_agent_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.trace_store.list(limit)

    def get_stream_runtime(self, mode: AgentMode, session_id: str | None = None, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None, agent: str | None = None, project_id: str | None = None) -> AgentStreamRuntime:
        # W1: reuse the per-session runtime (and its compiled graph) across turns
        # when nothing that changes the build differs. Keyed on the session +
        # everything the compiled graph depends on; LRU-bounded, evicted on
        # session delete. Per-turn state is reset via ``reset_per_turn``.
        if session_id:
            key = (
                session_id, mode, provider_id, model,
                str(workspace.root) if workspace is not None else None,
                project_id, agent, frozenset(referenced_sessions or ()),
            )
            cached = self._runtime_cache.get(key)
            if cached is not None:
                return cached
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if not provider and self.settings.agent_provider == "openai":
            from ..providers.catalog import get_provider_meta

            meta = get_provider_meta("openai")
            provider = ProviderEntry(
                id="env-openai",
                name=meta["name"] if meta else "OpenAI",
                provider_type="openai",
                base_url=os.getenv("COWORKER_OPENAI_BASE_URL", meta["base_url"] if meta else "https://api.openai.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                model=self.settings.openai_model,
                enabled=True,
            )
        if not provider:
            if self.settings.agent_provider == "simulated":
                return SimulatedStreamRuntime(self.settings, selected_workspace, session_store=self.session_store, referenced_sessions=referenced_sessions)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            runtime = OpenAICompatibleStreamRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpoints_dir, provider, model, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir, mcp_session_manager=self.mcp_session_manager, skill_manager=self.skill_manager, memory_manager=self.memory_manager, project_store=self.project_store, agent=agent or DEFAULT_AGENT_NAME, project_id=project_id, settings=self.settings, checkpoint_manager=self.checkpoint_manager)
            if session_id:
                self._runtime_cache[key] = runtime
                if len(self._runtime_cache) > RUNTIME_CACHE_MAX:
                    self._runtime_cache.popitem(last=False)
            return runtime
        raise RuntimeError(f"Unsupported agent mode for streaming: {mode}")

    def evict_runtime(self, session_id: str) -> None:
        """Drop the cached runtime for a session (called on session delete)."""
        if not session_id:
            return
        for key in [k for k in self._runtime_cache if k[0] == session_id]:
            self._runtime_cache.pop(key, None)

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted agent turn (HITL approval) using the stream runtime.

        The approval context carries the provider id, workspace path, and session
        metadata so the same graph can be rebuilt against the existing checkpoint.
        Events are forwarded in real time from the runtime generator.
        """
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        project_id = str(context.get("project_id") or "") or None
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME, fingerprint_path_for(self.settings.data_dir, Path(str(workspace_path))))
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        # Runtime construction resolves the context window with a synchronous
        # network probe (cold cache); keep it off the event loop. Positional
        # order: (mode, session_id, provider_id, model, workspace).
        runtime = await asyncio.to_thread(
            self.get_stream_runtime, "single", session_id, provider_id or None, model or None, workspace,
            referenced_sessions=referenced_sessions, project_id=project_id,
        )
        async for event in runtime.resume_interrupt(approval, decisions):
            yield event

    def _stream_runtime_from_context(self, context: dict[str, Any]) -> AgentStreamRuntime:
        session_id = str(context.get("session_id") or "")
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        project_id = str(context.get("project_id") or "") or None
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME, fingerprint_path_for(self.settings.data_dir, Path(str(workspace_path))))
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        # Positional order: (mode, session_id, provider_id, model, workspace).
        return self.get_stream_runtime("single", session_id, provider_id or None, model or None, workspace, referenced_sessions=referenced_sessions, agent=str(context.get("agent") or "") or None, project_id=project_id)

    async def rerun_stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
        provider_id: str | None = None, model: str | None = None, referenced_sessions: set[str] | None = None,
        workspace_path: str | None = None, agent: str | None = None, project_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Reset the session checkpoint and re-run the agent from full history."""
        # Routed through the single shared checkpointer (single-writer model) so
        # the delete is serialized with all other checkpoint I/O and never
        # contends on SQLite's file-level write lock.
        await self.forget_runtime_checkpoint(session_id)
        context = {
            "session_id": session_id,
            "provider_id": provider_id or "",
            "model": model or "",
            "referenced_sessions": list(referenced_sessions or []),
            # The project workspace must be threaded through or regenerate/edit
            # would run against the DEFAULT workspace and write files in the
            # wrong place (compare resume_interrupt, which passes it).
            "workspace_path": workspace_path,
            "agent": agent or "",
            "project_id": project_id or "",
        }
        # Runtime construction resolves the context window with a synchronous
        # network probe (cold cache); keep it off the event loop (regenerate/edit
        # are async SSE handlers).
        runtime = await asyncio.to_thread(self._stream_runtime_from_context, context)
        async for event in runtime.stream_rerun(messages, session_id, language, work_mode, autonomy):
            yield event