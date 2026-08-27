"""Loop protection middleware: tool-call cleaning, stall retry, repeated-call guard."""

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import AIMessage, HumanMessage

from ...logger import get_logger
from .base import _is_degenerate_text, _msg_tokens
from ..core import CoworkerAgentState

logger = get_logger(__name__)


class ToolCallCleanerMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Removes tool calls that the provider emitted without a tool name.

    Some OpenAI-compatible streaming servers (e.g. vLLM with Qwen3.6) can emit
    a parallel tool call whose delta never carries a ``name``, leaving an empty
    ``{"name": "", "args": {}}`` entry in the assistant message. LangChain keeps
    such entries in ``tool_calls``; executing them fails with an invalid-tool
    error, and the corrupted entry is then replayed to the provider on the next
    model call, producing a 400 (``Extra data``). This middleware strips these
    empty tool calls right after the model call so they never reach the tool
    executor or the provider.
    """

    def _clean(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        replacements: list[Any] = []
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                continue
            invalid = [t for t in tool_calls if not (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            if not invalid:
                continue
            valid = [t for t in tool_calls if (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            replacements.append(AIMessage(
                content=getattr(msg, "content", None) or "",
                tool_calls=valid,
                id=getattr(msg, "id", None),
                additional_kwargs=getattr(msg, "additional_kwargs", None) or {},
            ))
        if not replacements:
            return None
        return {"messages": replacements}

    def after_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)

    async def aafter_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)


class StallRetryMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Retry a model generation call once after a stream-chunk stall.

    ``langchain-openai`` aborts the stream when no chunk arrives for
    ``stream_chunk_timeout`` (``StreamChunkTimeoutError``). A flaky / briefly
    overloaded provider may recover immediately, so we retry the SINGLE model
    call once before letting the error propagate to the SSE layer (which would
    otherwise abort the whole turn). Only the model call is retried — tools are
    never re-run, so this is safe to apply at every model step.
    """

    def __init__(self, max_retries: int = 1) -> None:
        self.max_retries = max(1, int(max_retries))

    @staticmethod
    def _is_stall(exc: BaseException) -> bool:
        # Match by message as well as type: the exception class name/symbol can
        # shift between langchain-openai releases; the message is stable.
        if "stream_chunk_timeout" in str(exc) or "No streaming chunk received" in str(exc):
            return True
        try:
            from langchain_openai.chat_models._client_utils import StreamChunkTimeoutError
        except Exception:  # noqa: BLE001 - version drift must not crash a turn
            return False
        return isinstance(exc, StreamChunkTimeoutError)

    @staticmethod
    def _prompt_tokens(request: Any) -> int:
        """CJK-aware token estimate of the prompt this model call will send.

        Diagnostic only: lets a stall be attributed to an over-sized prompt
        (which some servers, e.g. vLLM, hang on silently instead of erroring).
        """
        messages = list(getattr(request, "messages", None) or [])
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            messages = [system_message, *messages]
        return sum(_msg_tokens(m) for m in messages)

    @staticmethod
    def _model_name(request: Any) -> str:
        model = getattr(request, "model", None)
        if model is not None:
            name = getattr(model, "model_name", None) or getattr(model, "name", None)
            if name:
                return str(name)
        return "?"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        attempt = 0
        while True:
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001 - retry only genuine stalls
                if not self._is_stall(exc):
                    raise
                attempt += 1
                prompt_tokens = self._prompt_tokens(request)
                model = self._model_name(request)
                if attempt > self.max_retries:
                    logger.error(
                        "model stream stalled repeatedly (chunk timeout); giving up "
                        "(model=%s, prompt_tokens≈%s): %s",
                        model,
                        prompt_tokens,
                        str(exc)[:400],
                    )
                    raise
                logger.warning(
                    "model stream stalled (chunk timeout); retrying call %d/%d "
                    "(model=%s, prompt_tokens≈%s)",
                    attempt,
                    self.max_retries,
                    model,
                    prompt_tokens,
                )


class RepeatedToolCallMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Stop models from blindly repeating the same failing tool call — or from
    degenerating into an endless text loop.

    ``create_agent`` hard-codes ``recursion_limit: 9_999``, so a model that
    re-emits one failing call (e.g. a ``find`` blocked by permissions) loops
    effectively forever. This middleware guards the trailing message history
    before every model call and, when a genuine loop is detected:

    * warns the model to change approach, then
    * on the hard cap strips every tool so the model MUST reply with a
      text-only final answer (the same "last step" mechanism opencode uses).

    It detects three genuine loop shapes, all purely from message history:

    1. CONSECUTIVE identical tool calls (name + canonicalized args) that keep
       failing — a model re-emitting the exact same call is a dead loop.
    2. Consecutive assistant messages with identical text content.
    3. A single assistant message that has already degenerated into repeated
       text (the qwen3-on-vLLM failure mode: "讓我做X：" repeated ~40× in one
       reply, which the old tool-only guard could never catch).

    IMPORTANT (2026-08-26, 對照 opencode/codex 調研）：**不按工具調用次數幹預**。
    真實多步任務（如「找 10 個 bug 每輪修 1 個」）天然需要大量工具調用（整條對話
    100+ 次）；opencode 默认 ``maxSteps=Infinity``、codex 的 turn loop 無上限。
    次數類守卫只會誤傷正常任務（正是「多調幾次工具就卡」的源頭），因此只保留上述
    三種真死循環防護，其餘一律交給模型 + context compaction。

    Only trailing runs count, so ordinary long tasks are unaffected. Mounted
    last (innermost) so its overrides are applied after PhaseToolGateMiddleware
    / SkillMiddleware / MemoryMiddleware.
    """

    def __init__(self, warn_after: int = 2, stop_after: int = 4, text_warn_after: int = 3, text_stop_after: int = 5) -> None:
        self.warn_after = max(1, int(warn_after))
        self.stop_after = max(self.warn_after + 1, int(stop_after))
        self.text_warn_after = max(1, int(text_warn_after))
        self.text_stop_after = max(self.text_warn_after + 1, int(text_stop_after))

    @staticmethod
    def _call_key(tool_call: Any) -> tuple[str, str]:
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
        try:
            canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            canonical = str(args)
        return str(name), canonical

    def _consecutive_repeats(self, messages: list[Any]) -> tuple[int, str, str]:
        """Return (count, name, last_result) for the trailing run of identical
        tool calls. ``count`` is how many identical calls are already in the
        history (0 = none)."""
        from langchain_core.messages import ToolMessage

        count = 0
        name = ""
        prev_key: tuple[str, str] | None = None
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                i -= 1
                continue
            if isinstance(msg, AIMessage):
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    break
                key = RepeatedToolCallMiddleware._call_key(calls[-1])
                if prev_key is None:
                    prev_key = key
                    count = 1
                elif key == prev_key:
                    count += 1
                else:
                    break
            else:
                break
            i -= 1
        if prev_key is not None:
            name = prev_key[0]
        last_result = ""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage) and (getattr(msg, "name", "") or "") == name:
                last_result = str(getattr(msg, "content", ""))[:200]
                break
        return count, name, last_result

    def _text_repeats(self, messages: list[Any]) -> int:
        """How many times the latest text-only assistant reply has ALREADY
        appeared in the history. A model that answers the same text every turn
        (user messages interleaved) is looping just as surely as one repeating a
        tool call — this catches that shape."""
        from langchain_core.messages import ToolMessage

        target: str | None = None
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None) or []:
                    return 0
                target = str(getattr(msg, "content", None) or "").strip()
                break
            return 0  # a user message is latest → fresh question, N/A
        if not target:
            return 0
        count = 0
        for msg in messages:
            if isinstance(msg, AIMessage) and not (getattr(msg, "tool_calls", None) or []):
                if str(getattr(msg, "content", None) or "").strip() == target:
                    count += 1
        return count

    @staticmethod
    def _is_degenerate_text(content: str) -> bool:
        """True when a single message repeats one unit several times — the
        qwen3 greedy-decoding collapse (e.g. '讓我搜索一下...' × 40)."""
        return _is_degenerate_text(content, min_repeat=5)

    def _last_message_degenerate(self, messages: list[Any]) -> bool:
        """True when the most recent non-tool assistant message is already
        degenerate repetition AND nothing newer than it demands a fresh answer
        (a new user message resets the condition so a normal follow-up question
        is never hijacked)."""
        from langchain_core.messages import ToolMessage

        for msg in reversed(messages):
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None) or []:
                    return False
                return RepeatedToolCallMiddleware._is_degenerate_text(getattr(msg, "content", None) or "")
            return False
        return False

    def _overrides(self, request: Any) -> dict[str, Any]:
        messages = list(request.messages or [])
        tool_count, tool_name, last_result = self._consecutive_repeats(messages)
        text_count = self._text_repeats(messages)
        degenerate = self._last_message_degenerate(messages)

        hard_stop = degenerate
        hard_reasons: list[str] = []
        if degenerate:
            hard_reasons.append("your previous reply degenerated into endless repetition")
        if tool_count >= self.stop_after:
            hard_stop = True
            hard_reasons.append(f"you already ran '{tool_name}' {tool_count} times")
        if text_count >= self.text_stop_after:
            hard_stop = True
            hard_reasons.append(f"you have already given the identical reply {text_count} times")

        if hard_stop:
            msg = (
                "STOP. " + "；".join(hard_reasons) + ". "
                "Do NOT make any more tool calls and do NOT repeat yourself. "
                "Provide your final answer as plain text now and explain what went wrong."
            )
            return {"tools": [], "messages": [*messages, HumanMessage(content=msg)]}

        warn_reasons: list[str] = []
        if tool_count >= self.warn_after:
            last_line = f" Last result: {last_result}" if last_result else ""
            warn_reasons.append(
                f"you already ran '{tool_name}' {tool_count} times in a row and it has "
                f"not succeeded.{last_line}"
            )
        if text_count >= self.text_warn_after:
            warn_reasons.append(f"you have already given the identical reply {text_count} times")
        if not warn_reasons:
            return {}

        msg = (
            "WARNING: " + "；".join(warn_reasons) + ". "
            "Do NOT repeat the same action or the same text. Change approach "
            "(different path, different tool, narrower scope) or answer directly."
        )
        return {"messages": [*messages, HumanMessage(content=msg)]}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return handler(request)
        return handler(request.override(**overrides))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return await handler(request)
        return await handler(request.override(**overrides))
