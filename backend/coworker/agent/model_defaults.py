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

    This is the "seems stuck like network congestion" knob: on a weak / lossy
    link a connection can stay TCP-alive while no token arrives for a long
    time. A huge value hides the stall behind an indefinite spinner; a modest
    value matches opencode's fail-fast behaviour. Configurable via env;
    default 120s.
    """
    try:
        return float(os.environ.get("COWORKER_LLM_STREAM_CHUNK_TIMEOUT_S", "120.0"))
    except (TypeError, ValueError):
        return 120.0


# ---------------------------------------------------------------------------
# LLM HTTP transport tuning (weak-network behaviour)
# ---------------------------------------------------------------------------

# httpx/openai keep the HTTP/1.1 pool's keep-alive for only 5s by default; a
# tool-loop pause or long provider-thinking gap longer than that forces a fresh
# TCP + TLS handshake on the NEXT model call. On a hotspot every handshake is
# several RTTs (~1-1.5s), so raise the keep-alive expiry so one pooled
# connection survives the whole session. Env: COWORKER_HTTP_KEEPALIVE_SECONDS.
_DEFAULT_HTTP_KEEPALIVE_SECONDS = 60.0

# connect timeout (seconds) for the provider link. openai's default is 5s,
# which is tight on a lossy hotspot where SYN retransmits are normal. Loosened
# so a slow-but-alive link is not misclassified as "provider unreachable"
# (which then cascades into multi-layer retries). Env: COWORKER_HTTP_CONNECT_TIMEOUT_S.
_DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0

# read timeout: safety net between bytes on the socket. Kept generous (SSE
# keepalives reset it) — stall detection is handled by the stream_chunk_timeout
# above, not by aborting the socket.
_HTTP_READ_TIMEOUT_SECONDS = 600.0


def _http_keepalive_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("COWORKER_HTTP_KEEPALIVE_SECONDS", str(_DEFAULT_HTTP_KEEPALIVE_SECONDS))))
    except (TypeError, ValueError):
        return _DEFAULT_HTTP_KEEPALIVE_SECONDS


def _http_connect_timeout_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("COWORKER_HTTP_CONNECT_TIMEOUT_S", str(_DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS))))
    except (TypeError, ValueError):
        return _DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS


def _http2_available() -> bool:
    """HTTP/2 is an opt-in transport; it needs the ``h2`` package (httpx[http2])."""
    if os.environ.get("COWORKER_HTTP2", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    try:
        import h2  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - h2 missing is the common case
        return False


def _http_proxy_visible() -> bool:
    """Best-effort check for a REAL forward proxy (custom transports disable
    httpx's proxy auto-detection, so we skip tuning and let langchain build its
    default client whenever traffic may actually be routed via a proxy).

    A lone ``NO_PROXY`` (very common) routes nothing and must not disable
    tuning; langchain's own ``_proxy_env_detected`` treats the macOS
    ``urllib.request.getproxies()`` ``{'no': ...}`` result as positive, which
    would defeat the tuning on most dev machines. We only bail out for an
    explicit ``*_PROXY`` env var or a system proxy that actually carries
    traffic (http/https/socks keys in ``getproxies()``).
    """
    proxy_env = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    if any(os.environ.get(name) for name in proxy_env):
        return True
    try:
        import urllib.request

        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - best-effort
        return False
    for key, value in proxies.items():
        if str(key).lower() != "no" and value:
            return True
    return False


def _build_tuned_async_httpx_client(base_url: str | None, timeout: Any) -> Any:
    """Return an httpx.AsyncClient tuned for weak / lossy provider links.

    Differences vs langchain-openai's default builder (which HTTP/1.1 + 5s
    keep-alive):

    * HTTP/2 enabled when the ``h2`` package is installed and the endpoint is
      HTTPS (multiplexes the tool loop's back-to-back model calls over one
      connection; plaintext/ALPN-less servers transparently fall back to
      HTTP/1.1).
    * keep-alive pool expiry raised (default 5s -> 60s) so a thinking/tool gap
      does not pay a fresh TCP+TLS handshake on the next call.
    * connect timeout loosened (5s -> 10s) for lossy links.

    The kernel TCP keep-alive / TCP_USER_TIMEOUT socket profile langchain
    computes from its ``LANGCHAIN_OPENAI_TCP_*`` envs is preserved. When a proxy
    env is visible we return langchain's own default client untouched so env /
    system proxy auto-detection keeps working.

    ``timeout`` mirrors langchain's ``_get_default_async_httpx_client``
    signature; our tuned client sets an explicit non-default timeout so openai
    honours it per request (openai ignores an httpx client whose timeout is the
    5s httpx default).
    """
    try:
        import httpx

        from langchain_openai.chat_models import _client_utils as _cu
    except Exception:  # noqa: BLE001 - never break LLM construction on tuning
        return None

    if _http_proxy_visible():
        return _cu._get_default_async_httpx_client(base_url, timeout)

    try:
        socket_options = tuple(_cu._resolve_socket_options(None))
    except Exception:  # noqa: BLE001 - best-effort
        socket_options = ()

    is_https = bool(str(base_url or "").strip().lower().startswith("https"))
    http2 = bool(is_https and _http2_available())
    keepalive = _http_keepalive_seconds()

    # Nothing to tune (no socket profile, no http2, default keep-alive):
    # keep langchain's own builder so behaviour is byte-identical to today.
    if not socket_options and not http2 and keepalive <= 0:
        return _cu._get_default_async_httpx_client(base_url, timeout)

    limits = httpx.Limits(
        max_connections=1000,
        max_keepalive_connections=100,
        keepalive_expiry=keepalive,
    )
    transport = httpx.AsyncHTTPTransport(
        http1=True,
        http2=http2,
        socket_options=list(socket_options),
        limits=limits,
    )
    base = (
        (base_url or "").rstrip("/")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    client = httpx.AsyncClient(
        base_url=base,
        timeout=httpx.Timeout(
            _HTTP_READ_TIMEOUT_SECONDS,
            connect=_http_connect_timeout_seconds(),
            write=_HTTP_READ_TIMEOUT_SECONDS,
            pool=5.0,
        ),
        transport=transport,
        follow_redirects=True,
    )
    # Marker so the request-logging wrapper can carry the tuned transport over.
    try:
        setattr(client, "_cw_tuned_transport", True)
    except Exception:  # noqa: BLE001 - cosmetic
        pass
    return client


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
            # Stall bound between stream chunks (weak-network behaviour): if no
            # token arrives for this long the generation is considered stalled
            # (a connection can stay TCP-alive while nothing is produced — the
            # "looks like network congestion" hang). 120s is long enough for a
            # heavy prefill yet short enough to surface a dead link promptly;
            # tune via COWORKER_LLM_STREAM_CHUNK_TIMEOUT_S.
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
        _inject_http_transport(kwargs, data_dir)
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
    _inject_http_transport(kwargs, data_dir)
    return kwargs


def _inject_http_transport(kwargs: dict[str, Any], data_dir: Any) -> None:
    """Inject a weak-network-tuned ``http_async_client`` into ChatOpenAI kwargs.

    Every streaming runtime (main agent, workers, delegation, skill review)
    builds its ChatOpenAI through this module, so the tuned transport is applied
    once per client. When ``COWORKER_LLM_LOG=1`` the tuned client is additionally
    wrapped by the request logger (which now preserves the tuned transport).
    """
    if kwargs.get("http_async_client") is not None:
        # Already injected (e.g. provider_llm_kwargs → create double path);
        # avoid double-wrapping the httpx client.
        return
    try:
        from ..llm_request_logger import wrap_async_client

        base_url = kwargs.get("base_url")
        timeout = kwargs.get("timeout") or kwargs.get("request_timeout")
        inner = _build_tuned_async_httpx_client(base_url, timeout)
        if inner is None:
            return
        kwargs["http_async_client"] = wrap_async_client(inner, data_dir)
    except Exception:  # noqa: BLE001 - tuning must never break LLM construction
        kwargs.pop("http_async_client", None)
