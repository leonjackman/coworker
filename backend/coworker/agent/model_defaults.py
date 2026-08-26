"""Model-default sampling parameters and LLM construction.

Centralizes every provider-sampling decision in one leaf module so the runtimes
and middleware never hard-code temperatures / penalties:

* :func:`temperature_for` — per-model-family sampling defaults ported from
  opencode's ``transform.ts`` (qwen → 0.55, gemini/glm/minimax/kimi/north →
  1.0, claude → None, everything else → ``None`` = omit, provider default).
* :func:`repetition_penalty_for` — repetition-prone families (qwen) get a
  stronger penalty on self-hosted endpoints.
* :func:`provider_llm_kwargs` — builds the ``ChatOpenAI`` kwargs for a provider,
  resolving the temperature (per-provider override > family default > omit) and
  the repetition penalty (self-hosted only).
* :class:`ReasonPreservingChatOpenAI` — the reasoning-preserving ChatOpenAI
  factory used by every runtime.

Dependency invariant: this module imports only ``coworker.providers`` (+ stdlib),
never ``coworker.agent`` internals, so it is always a leaf in the import DAG.
"""

import os
import re
from typing import Any

from ..providers import DEFAULT_MAX_OUTPUT_TOKENS, ProviderEntry, ProviderManager

# ---------------------------------------------------------------------------
# LLM stream chunk timeout
# ---------------------------------------------------------------------------


def _llm_stream_chunk_timeout() -> float:
    """Global timeout (seconds) for how long the LLM stream may pause between
    chunks.

    LangChain's default is 120s; slow / concurrent local providers can exceed
    that and get their reply truncated. Configurable via env; default 600s.
    """
    try:
        return float(os.environ.get("COWORKER_LLM_STREAM_CHUNK_TIMEOUT_S", "600.0"))
    except (TypeError, ValueError):
        return 600.0


# ---------------------------------------------------------------------------
# Repetition penalties
# ---------------------------------------------------------------------------

DEFAULT_REPETITION_PENALTY = 1.05

# Qwen-family models (esp. Qwen3 on greedy decoding) collapse into degenerate
# repetition loops far more readily than most models; the mild 1.05 default is
# too weak to break an established repetition attractor. Use a stronger penalty
# on self-hosted qwen endpoints (cloud APIs reject `repetition_penalty` anyway).
QWEN_REPETITION_PENALTY = 1.15


def repetition_penalty_for(model_name: str) -> float:
    name = (model_name or "").strip().lower()
    if name.startswith("qwen"):
        return QWEN_REPETITION_PENALTY
    return DEFAULT_REPETITION_PENALTY


# ---------------------------------------------------------------------------
# Per-family sampling temperatures (ported from opencode transform.ts)
# ---------------------------------------------------------------------------

_GEMINI_WITH_SAMPLING_DEFAULTS = [
    re.compile(r"gemini-2[.-]5(?:[.-]|$)"),
    re.compile(r"gemini-3-(?:flash|pro)(?:[.-]|$)"),
    re.compile(r"gemini-3[.-]1(?:[.-]|$)"),
    re.compile(r"gemini-3[.-]5-flash(?!-lite)(?:[.-]|$)"),
]


def temperature_for(model_name: str) -> float | None:
    """Per-model-family sampling temperature default, or ``None`` to omit the
    field entirely and let the provider use its own default.

    Mirrors opencode's ``ProviderTransform.temperature``: only families known to
    need an explicit value get one; everything else falls back to the provider
    default (and for reasoning models like ``o1``/``gpt-5``, omitting the field
    is the only universally-safe choice).
    """
    model_id = (model_name or "").lower()
    if "north-mini-code" in model_id:
        return 1.0
    if "qwen" in model_id:
        return 0.55
    if "claude" in model_id:
        return None
    if "gemini" in model_id:
        if any(pattern.search(model_id) for pattern in _GEMINI_WITH_SAMPLING_DEFAULTS):
            return 1.0
        return None
    if "glm-4.6" in model_id or "glm-4.7" in model_id:
        return 1.0
    if "minimax" in model_id:
        return 1.0
    if "kimi-k2" in model_id:
        # kimi-k2-thinking & kimi-k2.5 && kimi-k2p5 && kimi-k2-5
        if any(s in model_id for s in ("thinking", "k2.", "k2p", "k2-5")):
            return 1.0
        return 0.6
    return None


def _resolve_temperature(provider: ProviderEntry, model_name: str) -> float | None:
    """Per-provider override > family default > omit (provider default)."""
    if provider is not None and (provider.temperature or 0) > 0:
        return float(provider.temperature)
    return temperature_for(model_name)


# ---------------------------------------------------------------------------
# LLM construction
# ---------------------------------------------------------------------------


def openai_compatible_base_url(provider: ProviderEntry) -> str:
    base_url = provider.base_url.rstrip("/")
    if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
        return f"{base_url}/v1"
    return base_url


class ReasonPreservingChatOpenAI:
    """Factory that returns a :class:`ChatOpenAI` subclass which persists
    ``reasoning_content`` in ``additional_kwargs`` for OpenAI-compatible
    providers (DeepSeek, vLLM, Ollama, local proxy).

    Needed because the base ``langchain-openai`` class deliberately discards
    non‑standard delta fields in ``_convert_delta_to_message_chunk``.
    """

    @staticmethod
    def create(model: str, temperature: float | None, api_key: str, base_url: str | None, *, max_tokens: int = 0, repetition_penalty: float | None = None, parallel_tool_calls: bool | None = None) -> Any:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import AIMessageChunk

        _original = ChatOpenAI._convert_chunk_to_generation_chunk

        def _patched_convert(self: Any, chunk: dict, default_chunk_class: Any, base_generation_info: Any | None = None) -> Any:
            gen_chunk = _original(self, chunk, default_chunk_class, base_generation_info)
            if gen_chunk is None or gen_chunk.message is None:
                return gen_chunk
            msg = gen_chunk.message
            if isinstance(msg, AIMessageChunk):
                # The final usage-only chunk from vLLM/Ollama (stream_options.include_usage)
                # carries `choices: []` — guard against indexing into an empty list.
                choices = chunk.get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    additional = dict(getattr(msg, "additional_kwargs", {}) or {})
                    existing = additional.get("reasoning", "")
                    additional["reasoning"] = reasoning
                    object.__setattr__(msg, "additional_kwargs", additional)
            return gen_chunk

        # Patch at the class level so bind() / model_copy() clones inherit it
        ChatOpenAI._convert_chunk_to_generation_chunk = _patched_convert

        kwargs: dict[str, Any] = dict(
            model=model, temperature=temperature, api_key=api_key, base_url=base_url,
            # LangChain's built-in retry covers transient 5xx / connection
            # resets (default max_retries=2 with exponential backoff); a local
            # provider blip no longer fails the whole turn.
            max_retries=2,
            # Long-thinking / slow local providers (vLLM, Ollama) can pause
            # between chunks for well over langchain's 120s default; a fired
            # stream_chunk_timeout truncates the reply mid-generation. Use a
            # generous, configurable timeout so concurrent or slow tasks are not
            # killed just because the next token took a while.
            stream_chunk_timeout=_llm_stream_chunk_timeout(),
            # OpenAI-compatible servers (vLLM, DeepSeek, Ollama, ...) only include
            # token usage in a streaming response when the request asks for it, and
            # langchain-openai leaves stream_usage OFF for custom base URLs (it only
            # auto-enables for the default api.openai.com endpoint). Enable it
            # explicitly so every AI message carries usage_metadata for
            # context-budget telemetry.
            stream_usage=True,
        )
        if max_tokens and max_tokens > 0:
            # Bound a single model call so a degenerate / repeating generation is
            # cut off at the provider's configured cap instead of burning the GPU
            # (the 3b5bffff runaway). 0 = unset → provider/model default.
            kwargs["max_tokens"] = int(max_tokens)
        if repetition_penalty:
            # Repetition collapse is the root cause of degenerate generation under
            # greedy decoding. vLLM/Ollama accept `repetition_penalty` in the body;
            # only self-hosted providers opt in (callers gate this).
            kwargs["extra_body"] = {"repetition_penalty": float(repetition_penalty)}
        if parallel_tool_calls is not None:
            # Allow the model to emit multiple tool calls in one response — the
            # precondition for parallel use_worker / use_workers fan-out.
            kwargs["parallel_tool_calls"] = parallel_tool_calls
        return ChatOpenAI(**kwargs)


def provider_llm_kwargs(model_name: str, provider: ProviderEntry, base_url: str | None) -> dict[str, Any]:
    """Shared ``ChatOpenAI`` construction kwargs for the streaming runtimes.

    Applies the user-configured per-request output cap (max_output_tokens, default
    ``DEFAULT_MAX_OUTPUT_TOKENS``), the resolved sampling temperature (per-provider
    override > per-family default > omit), and a repetition penalty on self-hosted
    endpoints only (cloud OpenAI-compatible APIs reject ``repetition_penalty``).
    The penalty is model-aware so repetition-prone families (qwen) get a stronger
    value than the 1.05 default.
    """
    max_tokens = provider.max_output_tokens if provider.max_output_tokens > 0 else DEFAULT_MAX_OUTPUT_TOKENS
    use_penalty = ProviderManager._is_local(provider) or provider.provider_type in ("ollama", "llamacpp", "llmstudio", "lmstudio")
    return dict(
        model=model_name,
        temperature=_resolve_temperature(provider, model_name),
        api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed",
        base_url=base_url,
        max_tokens=max_tokens,
        # 允许模型在同一条回复里发出多个 tool call（并行工具调用）。这是
        # use_worker 并发的前提：只有模型能一次发 N 个 use_worker，LangGraph 的
        # ToolNode 才会用 asyncio.gather 让 N 个 worker 并行执行。只放宽上限、
        # 不强制——模型仍可单发。
        parallel_tool_calls=True,
        repetition_penalty=repetition_penalty_for(model_name) if use_penalty else None,
    )
