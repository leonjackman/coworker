"""Context compaction / summarization middleware.

Provides ``CoworkerSummarizationMiddleware`` — a LangChain
``SummarizationMiddleware`` subclass with Coworker-specific behavior:
CJK-aware token counting, structured compaction prompts, anchored updates,
summarizer model fallback chains, and cheap-layer tool-result pruning.
"""

import copy
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import get_buffer_string

from ...logger import get_logger
from .base import (
    COMPACTION_PROMPTS,
    _COMPACTION_FLUSH,
    KEEP_RECENT_TOKENS,
    SUMMARY_INPUT_MAX_TOKENS,
    SUMMARY_OUTPUT_TOKENS,
    TOOL_OUTPUT_MAX_CHARS,
    _anchored_summary_prompt,
    _cap_summary,
    _compaction_summary_prefix,
    _summary_ok,
    cjk_token_counter,
    Language,
)
from ..core import (
    CoworkerAgentState,
    _estimate_tokens,
    _msg_tokens,
    context_budget_chars,
    context_budget_tokens,
)

logger = get_logger(__name__)


def _summarizer_candidates(data_dir: Path | None, primary_llm: Any) -> list[Any]:
    """The compaction summarizer runs on the USER'S DEFAULT MODEL — the same
    model driving the turn. No separate compaction-model selection (C3
    decision): codex uses the session model, opencode's ``compaction`` agent
    defaults to the session model, and LangChain's SummarizationMiddleware only
    takes a dedicated ``model`` when you opt in. If the default model fails,
    the runtime flags ``context_compact_failed`` so the UI can prompt the user
    to switch to an available model.

    ``data_dir`` is kept for call-site compatibility but no longer used.
    """
    if primary_llm is None:
        return []
    return [primary_llm]


class CoworkerSummarizationMiddleware(SummarizationMiddleware):
    """Framework-backed context compaction with Coworker-specific behavior.

    Subclasses LangChain's :class:`SummarizationMiddleware` to inherit the proven
    mechanics — token/cutoff selection with AI/Tool pair protection, structured
    summary prompt, and HumanMessage summary injection (provider-safe: never a
    system message mid-list, which vLLM/Qwen rejects) — while preserving
    Coworker's product behavior:

    * CJK-aware token counting (``_estimate_tokens``), not the ASCII-only
      ``count_tokens_approximately`` default.
    * ``context_usage`` SSE telemetry on every model call.
    * Mutable per-turn budget (the overflow-retry path halves it).
    * Cheap layer first: stale tool results are cleared (micro-compact) before
      resorting to a model summary.
    * Summary quality validation + fallback to the plain rolling ``_trim``.
    * Dedup so the same segment is never summarized twice (loop guard).
    * Summarizer model fallback chain (user default model first, then other
      configured models) instead of a single fixed LLM.
    """

    def __init__(self, budget_chars: int | None = None, llm: Any | None = None, summarizer_candidates: list[Any] | None = None, language: Language = "zh", context_window_tokens: int = 0, context_window_source: str = "default", context_window_warning: str | None = None, tool_edit: Any | None = None, max_output_tokens: int = 0, calibration_store: Any | None = None, calibration_key: str = ""):
        # The provider reserves ``max_output_tokens`` from the window for the
        # response; budgeting against the RAW window spends that reservation and
        # dies one token past the real input ceiling (the incident 400). Both the
        # char and token budgets are computed on the effective limit.
        self.max_output_tokens = max(0, int(max_output_tokens or 0))
        self.configured_budget = max(20_000, int(budget_chars or context_budget_chars(128_000, self.max_output_tokens)))
        # Mutable per-turn budget (the overflow retry path halves this). The UI
        # always reads ``configured_budget`` so the meter never jumps on a retry
        # — see B9.
        self.budget_chars = self.configured_budget
        # Token-space budget drives trimming/compaction (CJK-aware). Mirrors
        # ``budget_chars`` mutations (overflow retry halves both).
        self.budget_tokens = context_budget_tokens(
            context_window_tokens if context_window_tokens and context_window_tokens > 0 else 128_000,
            self.max_output_tokens,
        )
        self.language: Language = language if language in ("zh", "en") else "zh"
        # Real model context window (tokens) + how it was resolved, surfaced to the
        # UI so the meter shows usage against the ACTUAL window (not just the 75%
        # safety budget) and explains the source — B2/B8.
        self.context_window_tokens = context_window_tokens
        self.context_window_source = context_window_source
        # Human-readable warning about the window (unverified oversized override,
        # or server-reported cap). Surfaced to the UI via context_usage.
        self.context_window_warning = context_window_warning
        # Closed-loop tokenizer calibration (actual usage / raw estimate) shared
        # with the pre-send guard; the meter surfaces the factor + calibrated
        # usage so the topbar shows what the provider will REALLY charge.
        self.calibration_store = calibration_store
        self.calibration_key = calibration_key or ""
        self._summarized_segments: set[str] = set()
        # Cheap layer: ClearToolUsesEdit (Anthropic-style context editing) used
        # BOTH by this middleware (prune-aware trigger, CJK-counted) and by the
        # mounted ContextEditingMiddleware (transient per-call slimming).
        self.tool_edit = tool_edit
        # Summary-model fallback chain: user default model first, then other
        # configured models, then the primary (per-turn) model.
        self.llm = llm
        candidates = list(summarizer_candidates or ())
        if not candidates and llm is not None:
            candidates.append(llm)
        self.summarizer_candidates = candidates
        self.last_summary = ""
        if candidates:
            super().__init__(
                model=candidates[0],
                trigger=("tokens", 1),
                keep=("tokens", 1),
                token_counter=self._cjk_token_counter,
                summary_prompt=COMPACTION_PROMPTS.get(self.language, COMPACTION_PROMPTS["en"]),
                trim_tokens_to_summarize=4000,
            )
        else:
            # No model available at all: the middleware becomes trim-only.
            AgentMiddleware.__init__(self)
            self.model = None
            self.trigger = None
            self.keep = ("tokens", 1)
            self._trigger_clauses: list[Any] = []
            self._trigger_conditions: list[Any] = []
            self.token_counter = self._cjk_token_counter
            self._partial_token_counter = self._cjk_token_counter
            self.summary_prompt = COMPACTION_PROMPTS.get(self.language, COMPACTION_PROMPTS["en"])
            self.trim_tokens_to_summarize = 4000

    @staticmethod
    def _cjk_token_counter(messages: Iterable[Any]) -> int:
        """CJK-aware batch token counter used by trim/cutoff logic."""
        return cjk_token_counter(messages)

    def _pruned_messages(self, messages: list[Any]) -> list[Any]:
        """Apply the cheap tool-result clear on a copy (CJK-aware decision).

        Uses the SAME CJK/base64-aware counter as every other budget decision —
        the framework default (``count_tokens_approximately``) is ASCII-biased
        and made the prune trigger disagree with the trim trigger on the very
        same message list.
        """
        if self.tool_edit is None:
            return messages

        try:
            pruned = copy.deepcopy(list(messages))
            self.tool_edit.apply(pruned, count_tokens=self._cjk_token_counter)
            return pruned
        except Exception:  # noqa: BLE001 - pruning is best-effort
            logger.warning("tool-result pruning failed", exc_info=True)
            return messages

    def _determine_cutoff_index(self, messages: list[Any]) -> int:
        """Token-based cutoff with AI/Tool pairing protection (framework core).

        ``keep_recent`` is a fixed small window (``KEEP_RECENT_TOKENS``, aligned
        with opencode) instead of a fraction of the budget — so after a compact
        the resident set is ``recent + summary`` (~12k), not near the budget
        ceiling. The overflow-retry path that halves the budget keeps this fixed
        too (the summary is already small); trimming still honors the budget.
        """
        keep_recent = max(2_000, KEEP_RECENT_TOKENS)
        self.keep = ("tokens", keep_recent)
        return super()._determine_cutoff_index(messages)

    def _build_new_messages(self, summary: str) -> list[Any]:
        """Inject the summary as a HumanMessage (provider-safe, echo-strippable)."""
        return [
            HumanMessage(
                content=f"{_compaction_summary_prefix(self.language)}{summary}",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]

    def _flush_reminder(self) -> Any:
        """Memory-flush reminder — HumanMessage (never a mid-list system message)."""
        return HumanMessage(
            content=_COMPACTION_FLUSH.get(self.language, _COMPACTION_FLUSH["en"]),
            id="__compaction_flush__",
        )

    @staticmethod
    def _truncate_message_to_tokens(msg: Any, budget_tokens: int) -> Any:
        """Return a copy of ``msg`` with string content token-accurately bounded.

        C4: uses ``truncate_to_token_budget`` (CJK/Latin/base64 aware) instead of
        a chars×ratio heuristic. Only safe for plain-text user/system messages.
        """
        try:
            content = msg.content
        except Exception:  # noqa: BLE001
            return msg
        if not isinstance(content, str) or getattr(msg, "type", "") not in ("human", "system", "user"):
            return msg
        if _estimate_tokens(content) <= budget_tokens:
            return msg
        from langchain_core.messages import HumanMessage

        from ...context import truncate_to_token_budget

        truncated, _ = truncate_to_token_budget(content, budget_tokens)
        return HumanMessage(
            content=truncated,
            id=getattr(msg, "id", None),
        )

    def _trim(self, state: CoworkerAgentState) -> Any:
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        messages = state.get("messages", [])
        if not messages:
            return None
        total = sum(_msg_tokens(m) for m in messages)
        if total <= self.budget_tokens:
            return None
        # Keep the first message (system prompt) and then the most recent tail
        # (oldest-first drop). Oversized user/system content is truncated instead
        # of dropped so the model still sees the user's current input; oversized
        # tool/AI messages are dropped (cannot be truncated without breaking
        # tool-call pairing).
        head: list[Any] = []
        budget = self.budget_tokens
        for msg in messages[:1]:
            tokens = _msg_tokens(msg)
            if tokens > budget:
                # C4: truncate to the token budget exactly (CJK/Latin/base64 aware
                # via estimate_text_tokens) instead of a chars×ratio heuristic.
                msg = self._truncate_message_to_tokens(msg, budget)
            head.append(msg)
            budget -= _msg_tokens(msg)

        kept_tail: list[Any] = []
        for msg in reversed(messages[1:]):
            tokens = _msg_tokens(msg)
            if tokens >= self.budget_tokens:
                # Oversized message: truncate user/system, drop tool/AI.
                if getattr(msg, "type", "") in ("human", "system", "user"):
                    kept_tail.append(self._truncate_message_to_tokens(msg, self.budget_tokens))
                    budget = 0
                    break
                continue
            if budget - tokens < 0:
                break
            kept_tail.append(msg)
            budget -= tokens

        kept_tail.reverse()
        # Drop any leading ToolMessage whose triggering AIMessage landed in the
        # trimmed gap (a ToolMessage is always preceded by its AIMessage in the
        # list; keeping it alone would 400 the provider).
        while kept_tail and getattr(kept_tail[0], "type", "") == "tool":
            kept_tail.pop(0)

        kept = head + kept_tail
        if len(kept) == len(messages):
            return None
        # Increment the session-level compaction counter. The counter lives in
        # checkpointed state (not on this middleware, which is rebuilt every turn)
        # so it accumulates across turns — see B6.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept], "context_compact_count": 1}

    def _select_compact_plan(self, messages: list[Any]) -> tuple[str, Any, Any] | None:
        """Choose a compaction action: prune tool results, or summarize a segment.

        Returns ``("prune", pruned_messages, None)`` when clearing stale tool
        results alone fits the budget (cheap layer first — Anthropic micro-compact
        semantics), ``("summarize", to_summarize, preserved)`` when a model
        summary is required, or ``None`` when nothing needs to happen.
        """
        if sum(_msg_tokens(m) for m in messages) <= self.budget_tokens:
            return None
        working = messages
        if self.tool_edit is not None:
            working = self._pruned_messages(messages)
            if sum(_msg_tokens(m) for m in working) <= self.budget_tokens:
                return ("prune", working, None)
        cutoff = self._determine_cutoff_index(working)
        if cutoff <= 0:
            return None
        to_summarize, preserved = self._partition_messages(working, cutoff)
        if len(to_summarize) < 2:
            return None
        return ("summarize", to_summarize, preserved)

    def _finish_compact(self, to_summarize: list[Any], preserved: list[Any], summary: str) -> dict[str, Any] | None:
        """Assemble the compacted state from a valid summary (never raises)."""
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        if not summary or not _summary_ok(summary):
            return None
        fingerprint = "|".join(getattr(m, "id", "") or "" for m in to_summarize)
        if fingerprint in self._summarized_segments:
            # Already summarized this exact segment on a prior turn: do not loop.
            return None
        self._summarized_segments.add(fingerprint)
        if len(self._summarized_segments) > 64:
            self._summarized_segments.clear()
        self.last_summary = summary
        kept = [*self._build_new_messages(summary), *preserved]
        # Memory-flush reminder: tell the model the oldest history was compacted
        # and it should persist any still-relevant facts into long-term memory so
        # they survive beyond this session (ties into the auto-memory pipeline).
        kept.append(self._flush_reminder())
        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept],
            "context_compact_count": 1,
            "context_summary": summary,
            # Persist the (capped) fingerprint set so the dedup loop guard
            # survives middleware rebuilds across turns.
            "context_summarized_fingerprints": sorted(self._summarized_segments)[-64:],
        }

    def _compact_sync(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) < 4:
            return None
        plan = self._select_compact_plan(messages)
        if plan is None:
            return None
        kind, a, b = plan
        if kind == "prune":
            from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *a], "context_compact_count": 1}
        summary = self._create_summary(a, previous_summary=self.last_summary)
        compacted = self._finish_compact(a, b, summary)
        if compacted is not None:
            return compacted
        # Summarize plan ran but produced no usable summary (default model
        # failed / degenerate). Still trim so the turn is not lost, and flag the
        # failure so the runtime can prompt the user about the default model.
        updates: dict[str, Any] = {"context_compact_failed": True}
        trimmed = self._trim(state)
        if trimmed is not None:
            updates.update(trimmed)
        return updates

    async def _compact_async(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) < 4:
            return None
        plan = self._select_compact_plan(messages)
        if plan is None:
            return None
        kind, a, b = plan
        if kind == "prune":
            from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *a], "context_compact_count": 1}
        summary = await self._acreate_summary(a, previous_summary=self.last_summary)
        compacted = self._finish_compact(a, b, summary)
        if compacted is not None:
            return compacted
        updates: dict[str, Any] = {"context_compact_failed": True}
        trimmed = self._trim(state)
        if trimmed is not None:
            updates.update(trimmed)
        return updates

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self.last_summary = str(state.get("context_summary", "") or "")
        self._summarized_segments = set(state.get("context_summarized_fingerprints") or [])
        if not self.summarizer_candidates:
            return self._trim(state)
        try:
            compacted = self._compact_sync(state)
            if compacted is not None:
                return compacted
        except Exception:  # noqa: BLE001 - compaction must never break a turn
            logger.warning("context compaction failed; falling back to trim", exc_info=True)
        return self._trim(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self.last_summary = str(state.get("context_summary", "") or "")
        self._summarized_segments = set(state.get("context_summarized_fingerprints") or [])
        if not self.summarizer_candidates:
            return self._trim(state)
        try:
            compacted = await self._compact_async(state)
            if compacted is not None:
                return compacted
        except Exception:  # noqa: BLE001 - compaction must never break a turn
            logger.warning("context compaction failed; falling back to trim", exc_info=True)
        return self._trim(state)

    def _serialize_for_summary(self, messages: list[Any]) -> str:
        """Serialize messages for the summarizer: tool results truncated, input
        bounded to ``SUMMARY_INPUT_MAX_TOKENS`` (oldest dropped until it fits).

        Mirrors opencode's ``select``: the whole segment is visible to the
        summarizer (subject to a token budget) instead of only the last few
        thousand tokens, so first-time summaries are complete. Tool outputs are
        truncated to ``TOOL_OUTPUT_MAX_CHARS`` before formatting because they
        dominate the transcript and rarely carry summary-worthy prose.
        """
        if not messages:
            return ""

        serialized = copy.deepcopy(list(messages))
        for msg in serialized:
            if getattr(msg, "type", "") != "tool":
                continue
            try:
                content = msg.content
            except Exception:  # noqa: BLE001
                continue
            if isinstance(content, str) and len(content) > TOOL_OUTPUT_MAX_CHARS:
                msg.content = content[:TOOL_OUTPUT_MAX_CHARS] + "\n[truncated]"
        formatted = get_buffer_string(serialized, format="xml")
        if _estimate_tokens(formatted) <= SUMMARY_INPUT_MAX_TOKENS:
            return formatted
        # Drop the oldest messages until the serialized head fits. Pairing is
        # irrelevant here (plain text summarization input, not a provider call).
        for drop in range(1, len(serialized)):
            candidate = get_buffer_string(serialized[drop:], format="xml")
            if _estimate_tokens(candidate) <= SUMMARY_INPUT_MAX_TOKENS or drop == len(serialized) - 1:
                return candidate
        return formatted

    def _create_summary(self, messages_to_summarize: list[Any], previous_summary: str = "") -> str:
        """Synchronous summarizer with the fallback model chain (anchored)."""
        if not messages_to_summarize:
            return ""
        formatted = self._serialize_for_summary(messages_to_summarize)
        if not formatted:
            return ""
        prompt = _anchored_summary_prompt(self.summary_prompt, previous_summary).format(messages=formatted).rstrip()
        for model in self.summarizer_candidates:
            try:
                try:
                    response = model.invoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                        max_tokens=SUMMARY_OUTPUT_TOKENS,
                    )
                except TypeError:
                    # Model does not accept max_tokens as a generation kwarg;
                    # _cap_summary still enforces the output budget.
                    response = model.invoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                text = str(getattr(response, "content", "") or response or "").strip()
                text = _cap_summary(text)
                if _summary_ok(text):
                    return text
                logger.warning("summarizer output rejected (degenerate): %.120s", text)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                logger.warning("summarizer %s failed; trying next: %s", getattr(model, "model_name", "?"), str(exc)[:200])
        return ""

    async def _acreate_summary(self, messages_to_summarize: list[Any], previous_summary: str = "") -> str:
        """Async summarizer with the fallback model chain (anchored)."""
        if not messages_to_summarize:
            return ""
        formatted = self._serialize_for_summary(messages_to_summarize)
        if not formatted:
            return ""
        prompt = _anchored_summary_prompt(self.summary_prompt, previous_summary).format(messages=formatted).rstrip()
        for model in self.summarizer_candidates:
            try:
                try:
                    response = await model.ainvoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                        max_tokens=SUMMARY_OUTPUT_TOKENS,
                    )
                except TypeError:
                    response = await model.ainvoke(
                        prompt,
                        config={"metadata": {"lc_source": "summarization"}},
                    )
                text = str(getattr(response, "content", "") or response or "").strip()
                text = _cap_summary(text)
                if _summary_ok(text):
                    return text
                logger.warning("summarizer output rejected (degenerate): %.120s", text)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                logger.warning("summarizer %s failed; trying next: %s", getattr(model, "model_name", "?"), str(exc)[:200])
        return ""
