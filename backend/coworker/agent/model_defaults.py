"""Model-default sampling parameters and LLM construction.

Centralizes every provider-sampling decision in one leaf module so the runtimes
and middleware never hard-code temperatures / penalties:

* :func:`repetition_penalty_for` — repetition-prone families (qwen) get a
  stronger penalty on self-hosted endpoints.
* :func:`provider_llm_kwargs` — builds the ``ChatOpenAI`` kwargs for a provider,
  resolving the repetition penalty (self-hosted only).
* :class:`ReasonPreservingChatOpenAI` — the reasoning-preserving ChatOpenAI
  factory used by every runtime.

Dependency invariant: this module imports only ``coworker.providers`` (+ stdlib),
never ``coworker.agent`` internals, so it is always a leaf in the import DAG.
"""

import os
import re
from typing import Any

from ..providers import DEFAULT_MAX_OUTPUT_TOKENS, ProviderEntry, ProviderManager
from ..providers.catalog import get_provider_meta

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
# LLM construction
# ---------------------------------------------------------------------------


def _use_repetition_penalty(provider: ProviderEntry) -> bool:
    """Whether to apply repetition_penalty for this provider.

    True when the catalog marks the provider explicitly, or when the
    provider's base_url points to a local/private address (self-hosted).
    """
    meta = get_provider_meta(provider.provider_type)
    if meta and meta.get("use_repetition_penalty"):
        return True
    return ProviderManager._is_local(provider)


def openai_compatible_base_url(provider: ProviderEntry) -> str:
    base_url = provider.base_url.rstrip("/")
    meta = get_provider_meta(provider.provider_type)
    if meta and meta.get("url_trailing_v1") and not base_url.endswith("/v1"):
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
    def create(model: str, api_key: str, base_url: str | None, *, max_tokens: int = 0, repetition_penalty: float | None = None, parallel_tool_calls: bool | None = None, data_dir: Any = None, **overrides: Any) -> Any:
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
            model=model, api_key=api_key, base_url=base_url,
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
        _inject_llm_request_logger(kwargs, data_dir)
        for k, v in overrides.items():
            kwargs[k] = v
        return ChatOpenAI(**kwargs)


def provider_llm_kwargs(model_name: str, provider: ProviderEntry, base_url: str | None, data_dir: Any = None) -> dict[str, Any]:
    """Shared ``ChatOpenAI`` construction kwargs for the streaming runtimes.

    Applies the user-configured per-request output cap (max_output_tokens, default
    ``DEFAULT_MAX_OUTPUT_TOKENS``), and a repetition penalty on self-hosted
    endpoints only (cloud OpenAI-compatible APIs reject ``repetition_penalty``).
    The penalty is model-aware so repetition-prone families (qwen) get a stronger
    value than the 1.05 default.

    When ``COWORKER_LLM_LOG=1`` the async httpx client is wrapped so every
    request body (messages + tools + sampling params) and its response status is
    recorded to ``<data_dir>/llm-requests.log`` — the authoritative record for
    diffing what CW actually sent vs what the provider received.
    """
    max_tokens = provider.max_output_tokens if provider.max_output_tokens > 0 else DEFAULT_MAX_OUTPUT_TOKENS
    use_penalty = _use_repetition_penalty(provider)
    kwargs = dict(
        model=model_name,
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
    _inject_llm_request_logger(kwargs, data_dir)
    return kwargs


def _inject_llm_request_logger(kwargs: dict[str, Any], data_dir: Any) -> None:
    """Inject the request-logging async client into ``kwargs`` when enabled.

    Builds the inner client through langchain's own default-builder so socket
    options / timeouts / SSRF-safe transport are preserved; only the HTTP layer
    is wrapped to capture the serialized request body.
    """
    if os.environ.get("COWORKER_LLM_LOG", "0").strip().lower() in {"0", "false", "no", "off"}:
        return
    if kwargs.get("http_async_client") is not None:
        # Already injected (e.g. provider_llm_kwargs → create double path);
        # avoid double-wrapping the httpx client.
        return
    try:
        from langchain_openai.chat_models._client_utils import _get_default_async_httpx_client

        from ..llm_request_logger import wrap_async_client

        base_url = kwargs.get("base_url")
        timeout = kwargs.get("timeout") or kwargs.get("request_timeout")
        inner = _get_default_async_httpx_client(base_url, timeout)
        kwargs["http_async_client"] = wrap_async_client(inner, data_dir)
    except Exception:  # noqa: BLE001 - logging must never break LLM construction
        kwargs.pop("http_async_client", None)
