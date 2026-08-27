"""Context guard: last line of defense before a request hits the provider.

Provides ``ContextGuardMiddleware`` and ``ContextOverflowError``.
"""

from collections.abc import Callable, Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import Runtime

from ...logger import get_logger
from .base import _msg_chars, cjk_token_counter
from ..core import CoworkerAgentState, MAX_IMAGES_PER_PROMPT

logger = get_logger(__name__)


class ContextOverflowError(RuntimeError):
    """The final request still exceeds the effective window after every staged
    reduction. Carries the measured size / limit so callers can surface a
    precise, friendly error instead of the provider's raw 400."""

    def __init__(self, message: str, *, measured_tokens: int = 0, limit_tokens: int = 0, steps: list[str] | None = None):
        super().__init__(message)
        self.measured_tokens = measured_tokens
        self.limit_tokens = limit_tokens
        self.steps = list(steps or [])


class ContextGuardMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Last line of defense before a request hits the provider.

    Every other context mechanism (trim, compaction, tool-result clearing)
    measures ``state["messages"]`` with an estimate; the provider measures the
    REAL request — system prompt + tool schemas + messages + template overhead,
    with its own tokenizer, and reserves ``max_output_tokens`` from the window.
    When estimate and reality disagree (dense code, base64 blobs, CJK, vision
    blocks) the state-side mechanisms can stay comfortably "under budget"
    while the real request sails past ``window − max_output`` — exactly the
    incident this guard exists to kill.

    Mounted INNERMOST (last in the middleware chain) so it measures the request
    after every other middleware has applied its system-prompt / tool / message
    overrides. When the calibrated measurement exceeds the effective input
    limit it applies staged reductions — cheapest and least-destructive first,
    all on request-local copies (checkpointed state and the UI transcript are
    untouched, same contract as ``ContextEditingMiddleware``):

      S1 externalize binary blobs (base64/data-URL runs are corrupted-on-
         arrival text: pure token waste) and degrade stale image blocks;
      S2 clear old tool results (keep=3, then keep=1);
      S3 truncate the oldest oversized tool results;
      S4 drop optional tool schemas (MCP tools);
      S5 emergency-drop the oldest messages (AI/Tool pairing preserved).

    If the request STILL does not fit, raises :class:`ContextOverflowError` so
    the runtime can emit a friendly terminal event + one-click compacted retry
    instead of leaking the provider's 400 mid-turn.

    The guard also publishes the calibrated measurement + raw estimate for the
    closed-loop calibration (the streaming loop pairs ``last_raw_estimate``
    with the provider-reported ``usage_metadata.input_tokens``).
    """

    # Image blocks kept intact during S1 degradation (the most recent ones are
    # the only ones still relevant to the model's current decision).
    KEEP_RECENT_IMAGES = 2
    # S3 truncation target per old tool result (opencode TOOL_OUTPUT_MAX_CHARS).
    TOOL_RESULT_KEEP_CHARS = 2_000
    # Stop reducing once comfortably under the limit (calibrated headroom).
    TARGET_RATIO = 0.95

    def __init__(
        self,
        *,
        window_tokens: int,
        max_output_tokens: int = 0,
        calibration_store: Any | None = None,
        calibration_key: str = "",
        mcp_tool_names_provider: Callable[[], set[str]] | None = None,
        window_source: str = "default",
        window_warning: str | None = None,
    ) -> None:
        from ...context import effective_input_limit

        self.window_tokens = int(window_tokens or 0)
        self.max_output_tokens = max(0, int(max_output_tokens or 0))
        self.limit_tokens = effective_input_limit(self.window_tokens or 128_000, self.max_output_tokens)
        self.calibration_store = calibration_store
        self.calibration_key = calibration_key or ""
        self.mcp_tool_names_provider = mcp_tool_names_provider
        self.window_source = window_source
        self.window_warning = window_warning
        # Calibration pairing: the streaming loop reads these right after each
        # successful model call and folds actual/estimate into the store.
        self.last_raw_estimate = 0
        self.last_measured = 0
        self.last_steps: list[str] = []

    # -- measurement --------------------------------------------------------

    def _factor(self) -> float:
        if self.calibration_store is not None and self.calibration_key:
            try:
                return float(self.calibration_store.get(self.calibration_key))
            except Exception:  # noqa: BLE001 - fall back to uncalibrated
                return 1.0
        return 1.0

    def _measure(self, request: Any) -> tuple[int, int, float]:
        """Return ``(raw_estimate, calibratedMeasurement, factor)`` for the FULL
        final request (messages + system + tools + per-message overhead)."""
        from ...context import (
            PER_MESSAGE_OVERHEAD_TOKENS,
            estimate_text_tokens,
            messages_tokens,
            tool_schema_tokens,
        )

        system_text = ""
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            content = getattr(system_message, "content", "")
            if isinstance(content, str):
                system_text = content
            elif isinstance(content, list):
                system_text = "\n".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
        messages = list(request.messages or [])
        raw = messages_tokens(messages)
        raw += tool_schema_tokens(getattr(request, "tools", None) or [])
        if system_text:
            raw += estimate_text_tokens(system_text)
        raw += PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
        factor = self._factor()
        return raw, int(round(raw * factor)), factor

    # -- staged reductions (all request-local) --------------------------------

    @staticmethod
    def _with_content(msg: Any, content: Any) -> Any:
        """Copy of ``msg`` with replaced content (keeps ids/pairing intact)."""
        try:
            return msg.model_copy(update={"content": content})
        except Exception:  # noqa: BLE001 - older pydantic / message classes
            try:
                return msg.copy(update={"content": content})
            except Exception:  # noqa: BLE001
                return msg

    def _strip_blobs_and_degrade_images(
        self,
        messages: list[Any],
        *,
        keep_images: int | None = None,
        scrub: bool = True,
    ) -> tuple[list[Any], int]:
        """S1: scrub base64/data-URL text runs and degrade stale image blocks.

        A truncated base64 blob is a CORRUPTED binary — the model can never use
        it, it only burns tokens (~36k per 50k chars). Old images are replaced
        with a text placeholder; the most recent ``keep_images`` image blocks
        survive (they are the ones the model is currently reasoning about).
        Returns ``(new_messages, changes)``.
        """
        from ...context import contains_binary_blob, scrub_text

        keep_budget = self.KEEP_RECENT_IMAGES if keep_images is None else max(0, int(keep_images))

        # Pass 1 (backwards): decide which image blocks are recent enough to keep.
        keep_marks: list[set[int]] = []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            marks: set[int] = set()
            if isinstance(content, list):
                for idx in range(len(content) - 1, -1, -1):
                    part = content[idx]
                    if isinstance(part, dict) and part.get("type") in ("image", "image_url", "audio", "video", "file"):
                        if keep_budget > 0:
                            marks.add(idx)
                            keep_budget -= 1
            keep_marks.append(marks)
        keep_marks.reverse()

        changed = 0
        new_messages: list[Any] = []
        for msg, marks in zip(messages, keep_marks):
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                new_content: list[Any] = []
                msg_changed = False
                for idx, part in enumerate(content):
                    if isinstance(part, dict):
                        ptype = part.get("type")
                        if ptype in ("image", "image_url", "audio", "video", "file"):
                            if idx in marks:
                                new_content.append(part)
                            else:
                                new_content.append(
                                    {"type": "text", "text": f"[{ptype} removed from context to save space]"}
                                )
                                msg_changed = True
                            continue
                        if ptype == "text":
                            if scrub:
                                text = part.get("text") or ""
                                if contains_binary_blob(text):
                                    scrubbed, n = scrub_text(text)
                                    if n:
                                        part = {**part, "text": scrubbed}
                                        msg_changed = True
                    new_content.append(part)
                if msg_changed:
                    changed += 1
                    msg = self._with_content(msg, new_content)
            elif isinstance(content, str) and scrub and contains_binary_blob(content):
                scrubbed, n = scrub_text(content)
                if n:
                    changed += 1
                    msg = self._with_content(msg, scrubbed)
            new_messages.append(msg)
        return new_messages, changed

    def _clear_tool_results(self, messages: list[Any], keep: int) -> list[Any]:
        """S2: forced ClearToolUsesEdit pass (trigger=0 ⇒ always applies)."""
        try:
            from langchain.agents.middleware import ClearToolUsesEdit

            edit = ClearToolUsesEdit(
                trigger=0,
                keep=keep,
                placeholder="[cleared]",
                exclude_tools=("write_todos", "memory", "memory_read", "ask_user"),
            )
            working = [m.model_copy() if hasattr(m, "model_copy") else m for m in messages]
            edit.apply(working, count_tokens=cjk_token_counter)
            return working
        except Exception:  # noqa: BLE001 - reduction step must never break a turn
            logger.warning("guard: tool-result clearing failed", exc_info=True)
            return messages

    @staticmethod
    def _image_count(messages: Iterable[Any]) -> int:
        """Total image/audio/video/file blocks across the request's messages."""
        from ...context import message_media_count

        return sum(message_media_count(m) for m in messages)

    def _truncate_old_tool_results(self, messages: list[Any]) -> tuple[list[Any], int]:
        """S3: truncate oversized tool results, oldest first."""
        from langchain_core.messages import ToolMessage

        changed = 0
        new_messages = list(messages)
        for idx, msg in enumerate(new_messages):
            if not isinstance(msg, ToolMessage):
                continue
            content = getattr(msg, "content", None)
            if not isinstance(content, str) or len(content) <= self.TOOL_RESULT_KEEP_CHARS:
                continue
            new_messages[idx] = self._with_content(
                msg, content[: self.TOOL_RESULT_KEEP_CHARS] + "\n…[truncated by context guard]"
            )
            changed += 1
        return new_messages, changed

    def _drop_mcp_tools(self, tools: list[Any] | None, messages: list[Any] | None = None) -> tuple[list[Any] | None, int]:
        """S4: drop optional MCP tool schemas (they ride on EVERY request).

        W5/S3: keeps the MCP tools the model actually CALLED recently (from the
        trailing tool-call history) so an in-flight MCP round is never stripped
        mid-turn; only never-used MCP schemas are dropped.

        Handles both ``BaseTool`` instances and raw schema dicts (ModelRequest
        accepts either, and the phase gate passes dicts through untouched).
        """
        if not tools or self.mcp_tool_names_provider is None:
            return tools, 0
        try:
            mcp_names = self.mcp_tool_names_provider()
        except Exception:  # noqa: BLE001 - a broken provider never gates the guard
            return tools, 0
        if not mcp_names:
            return tools, 0

        recent: set[str] = set()
        for msg in reversed(messages or []):
            for tc in getattr(msg, "tool_calls", None) or []:
                name = ""
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    name = str(fn.get("name") or "") if isinstance(fn, dict) else ""
                else:
                    name = str(getattr(tc, "name", "") or "")
                if name and name in mcp_names:
                    recent.add(name)
            if len(recent) >= 8:
                break

        def _tool_name(tool: Any) -> str:
            name = getattr(tool, "name", "")
            if name:
                return str(name)
            if isinstance(tool, dict):
                fn = tool.get("function")
                if isinstance(fn, dict):
                    return str(fn.get("name") or "")
                return str(tool.get("name") or "")
            return ""

        kept = [t for t in tools if _tool_name(t) not in mcp_names or _tool_name(t) in recent]
        return kept, len(tools) - len(kept)

    def _emergency_drop_oldest(self, messages: list[Any], limit: int, factor: float) -> tuple[list[Any], int]:
        """S5: drop oldest messages until under limit (AI/Tool pairing safe)."""
        from ...context import messages_tokens

        working = list(messages)
        dropped = 0
        while len(working) > 4:
            measured = int(round(messages_tokens(working) * factor))
            if measured <= limit * self.TARGET_RATIO:
                break
            working.pop(0)
            dropped += 1
            while working and getattr(working[0], "type", "") == "tool":
                working.pop(0)
                dropped += 1
        return working, dropped

    # -- guard core -----------------------------------------------------------

    def _guard(self, request: Any) -> Any:
        """Measure the final request; apply staged reductions when over limit.

        Returns the (possibly overridden) request, or raises
        :class:`ContextOverflowError` when nothing fits.
        """
        raw, measured, factor = self._measure(request)
        self.last_raw_estimate = raw
        self.last_measured = measured
        self.last_steps = []
        # Surface the FULL request size (messages + system prompt + tool schemas
        # + per-message overhead, calibrated) as the `context_usage` telemetry the
        # UI topbar renders — the single source of truth for "how full is the
        # context". The compaction middleware's older message-only estimate
        # undercounted the fixed system/tool overhead (the B-series blind spot),
        # so the guard — which already measures the real request — owns this
        # event now.
        self._emit_context_usage_event(request, raw, measured, factor, measured > self.limit_tokens)
        if measured <= self.limit_tokens:
            # Even when comfortably under the token budget, enforce the per-prompt
            # IMAGE-COUNT ceiling (e.g. vLLM --limit-mm-per-prompt.image=5): 6+
            # in-turn screenshots fit easily inside the window but still 400 the
            # provider. Cheap pre-check; the common path stays untouched.
            messages = list(request.messages or [])
            if self._image_count(messages) > MAX_IMAGES_PER_PROMPT:
                messages, _n = self._strip_blobs_and_degrade_images(
                    messages, keep_images=MAX_IMAGES_PER_PROMPT, scrub=False
                )
                # Re-sync the raw estimate so the closed-loop calibration pairs
                # the ACTUAL sent request (degraded images) with its usage.
                raw_capped, _measured, _ = self._measure(request.override(messages=messages))
                self.last_raw_estimate = raw_capped
                return self._finalize(request, {"messages": messages}, measured)
            return request

        logger.warning(
            "context guard: request %s tokens (calibrated, factor=%.2f) exceeds effective limit %s; reducing",
            measured, factor, self.limit_tokens,
        )
        self._emit_telemetry(request, measured, "reducing")

        overrides: dict[str, Any] = {}
        messages = list(request.messages or [])
        tools = getattr(request, "tools", None)

        # S1 — binary blobs + stale images (cheapest, zero information loss:
        # truncated base64 was already useless).
        messages, n1 = self._strip_blobs_and_degrade_images(messages)
        if n1:
            self.last_steps.append(f"blobs/images:{n1}")
            overrides["messages"] = messages

        def _current() -> tuple[int, int]:
            probe = request.override(**({"messages": messages} | ({"tools": tools} if tools is not None else {})))
            raw_now, measured_now, _ = self._measure(probe)
            self.last_raw_estimate = raw_now
            return raw_now, measured_now

        _, measured = _current()
        if measured <= self.limit_tokens * self.TARGET_RATIO:
            return self._finalize(request, overrides, measured)

        # S2 — clear stale tool results (keep=3, then keep=1).
        for keep in (3, 1):
            messages = self._clear_tool_results(messages, keep)
            overrides["messages"] = messages
            self.last_steps.append(f"clear_tools_keep{keep}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S3 — truncate the oldest oversized tool results.
        messages, n3 = self._truncate_old_tool_results(messages)
        if n3:
            overrides["messages"] = messages
            self.last_steps.append(f"truncate_tools:{n3}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S4 — drop optional MCP tool schemas (keeps recently-used MCP tools).
        tools, n4 = self._drop_mcp_tools(tools, messages)
        if n4:
            overrides["tools"] = tools
            self.last_steps.append(f"drop_mcp_tools:{n4}")
            _, measured = _current()
            if measured <= self.limit_tokens * self.TARGET_RATIO:
                return self._finalize(request, overrides, measured)

        # S5 — emergency drop of the oldest messages.
        messages, n5 = self._emergency_drop_oldest(messages, self.limit_tokens, factor)
        if n5:
            overrides["messages"] = messages
            self.last_steps.append(f"drop_oldest:{n5}")
            _, measured = _current()
            if measured <= self.limit_tokens:
                return self._finalize(request, overrides, measured)

        # S6 — nothing fits: raise so the runtime emits a friendly terminal
        # event + one-click compacted retry instead of the provider's raw 400.
        self._emit_telemetry(request, measured, "overflow")
        raise ContextOverflowError(
            f"request still {measured} tokens after staged reductions (limit {self.limit_tokens})",
            measured_tokens=measured,
            limit_tokens=self.limit_tokens,
            steps=self.last_steps,
        )

    def _finalize(self, request: Any, overrides: dict[str, Any], measured: int) -> Any:
        if not overrides:
            return request
        self._emit_telemetry(request, measured, "reduced")
        return request.override(**overrides)

    def _emit_telemetry(self, request: Any, measured: int, status: str) -> None:
        try:
            runtime = getattr(request, "runtime", None)
            writer = getattr(runtime, "stream_writer", None)
            if writer is None:
                return
            writer(
                {
                    "type": "context_guard",
                    "status": status,
                    "measured_tokens": measured,
                    "limit_tokens": self.limit_tokens,
                    "calibration_factor": round(self._factor(), 3),
                    "steps": list(self.last_steps),
                }
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            logger.debug("context_guard telemetry skipped", exc_info=True)

    def _emit_context_usage_event(
        self, request: Any, raw: int, measured: int, factor: float, over: bool
    ) -> None:
        """Emit the authoritative `context_usage` event for the UI topbar.

        Uses the FULL calibrated request measurement (``raw``/``measured`` from
        :meth:`_measure`, which counts system prompt + tool schemas + messages +
        per-message overhead) so the indicator reflects what is actually sent to
        the model — not just the message history.
        """
        try:
            runtime = getattr(request, "runtime", None)
            writer = getattr(runtime, "stream_writer", None)
            if writer is None:
                return
            messages = list(getattr(request, "messages", []) or [])
            used_chars = sum(_msg_chars(m) for m in messages)
            writer(
                {
                    "type": "context_usage",
                    "used_chars": used_chars,
                    "budget_chars": 0,
                    "used_tokens": raw,
                    "used_tokens_calibrated": measured,
                    "calibration_factor": round(float(factor), 3),
                    "budget_tokens": self.limit_tokens,
                    "active_budget_tokens": self.limit_tokens,
                    "window_tokens": self.window_tokens,
                    "effective_window_tokens": self.limit_tokens,
                    "max_output_tokens": self.max_output_tokens,
                    "compressed": bool(over),
                    "compacted": False,
                    "compact_count": 0,
                    "window_source": self.window_source,
                    "window_warning": self.window_warning,
                }
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            logger.debug("context_usage telemetry skipped", exc_info=True)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._guard(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._guard(request))
