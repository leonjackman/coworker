"""Normalize every network failure into one diagnosable, actionable kind.

The previous behaviour collapsed ``RemoteProtocolError``, ``ConnectError``,
``ReadTimeout`` and the openai wrappers into a single boolean
(``is_connection_error``) and ultimately surfaced ``"Connection error."`` to
the user. That made a proxy disconnect indistinguishable from a dead provider
or a slow prefill.

This module keeps the distinction:

* ``PROXY_DISCONNECT`` — the transport died while a proxy was in play. Retrying
  the same proxy rarely helps; the UI should offer "retry / switch to direct".
* ``SERVER_DISCONNECT`` — the provider (or its edge) closed the stream.
* ``CONNECT_TIMEOUT`` / ``READ_TIMEOUT`` / ``STREAM_STALL`` — bounded waits.
* ``DNS`` / ``TLS`` / ``AUTH`` / ``RATE_LIMIT`` — not worth a fast retry.

Only the standard library is imported eagerly; httpx/openai/langchain types are
resolved lazily so this module stays a dependency leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator


class NetworkErrorKind(str, Enum):
    PROXY_DISCONNECT = "proxy_disconnect"
    SERVER_DISCONNECT = "server_disconnect"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    STREAM_STALL = "stream_stall"
    DNS = "dns"
    TLS = "tls"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


_RETRYABLE: frozenset[NetworkErrorKind] = frozenset(
    {
        NetworkErrorKind.PROXY_DISCONNECT,
        NetworkErrorKind.SERVER_DISCONNECT,
        NetworkErrorKind.CONNECT_TIMEOUT,
        NetworkErrorKind.READ_TIMEOUT,
        NetworkErrorKind.STREAM_STALL,
    }
)

_HINTS: dict[NetworkErrorKind, str] = {
    NetworkErrorKind.PROXY_DISCONNECT: (
        "LLM 请求经代理中断。请重试；若反复失败，请在模型设置中把该服务改为“直连”。"
    ),
    NetworkErrorKind.SERVER_DISCONNECT: "模型服务端断开了连接，请重试。",
    NetworkErrorKind.CONNECT_TIMEOUT: "连接模型服务超时，请检查网络或代理后重试。",
    NetworkErrorKind.READ_TIMEOUT: "模型服务响应超时，请稍后重试。",
    NetworkErrorKind.STREAM_STALL: "模型输出长时间无新内容，已中断，请重试。",
    NetworkErrorKind.DNS: "无法解析模型服务域名，请检查网络与 DNS 设置。",
    NetworkErrorKind.TLS: "TLS 握手失败（证书或代理拦截），请检查代理设置。",
    NetworkErrorKind.AUTH: "认证失败，请检查 API Key 是否正确。",
    NetworkErrorKind.RATE_LIMIT: "请求过于频繁或额度不足，请稍后重试。",
    NetworkErrorKind.UNKNOWN: "网络请求失败，请重试。",
}


@dataclass(frozen=True)
class NetworkError:
    """A normalized network failure."""

    kind: NetworkErrorKind
    retryable: bool
    root_cause: str
    endpoint: str = ""
    proxy: str | None = None
    user_hint: str = ""
    attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "retryable": self.retryable,
            "root_cause": self.root_cause,
            "endpoint": self.endpoint,
            "proxy": self.proxy,
            "hint": self.user_hint,
            "attempt": self.attempt,
        }


# ---------------------------------------------------------------------------
# Exception-chain helpers
# ---------------------------------------------------------------------------


def _iter_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def _type_names(exc: BaseException) -> set[str]:
    return {type(item).__name__ for item in _iter_chain(exc)}


def _text(exc: BaseException) -> str:
    return " | ".join(str(item) for item in _iter_chain(exc)).lower()


def _root_cause(exc: BaseException) -> str:
    chain = list(_iter_chain(exc))
    deepest = chain[-1] if chain else exc
    message = str(deepest).strip() or type(deepest).__name__
    return f"{type(deepest).__name__}: {message}"[:300]


def _is_httpx(exc: BaseException, name: str) -> bool:
    try:
        import httpx

        cls = getattr(httpx, name, None)
        return cls is not None and isinstance(exc, cls)
    except Exception:  # noqa: BLE001 - httpx absent must not break classification
        return False


def _is_openai(exc: BaseException, name: str) -> bool:
    try:
        import openai

        cls = getattr(openai, name, None)
        return cls is not None and isinstance(exc, cls)
    except Exception:  # noqa: BLE001 - openai absent must not break classification
        return False


def is_stream_stall(exc: BaseException) -> bool:
    """True for langchain-openai's per-chunk timeout."""
    text = _text(exc)
    if "stream_chunk_timeout" in text or "no streaming chunk received" in text:
        return True
    try:
        from langchain_openai.chat_models._client_utils import StreamChunkTimeoutError

        return isinstance(exc, StreamChunkTimeoutError)
    except Exception:  # noqa: BLE001 - version drift must not crash a turn
        return False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_kind(exc: BaseException, proxy: str | None) -> NetworkErrorKind:
    names = _type_names(exc)
    text = _text(exc)

    # Provider-level statuses first: they are not transport failures.
    if _is_openai(exc, "AuthenticationError") or _is_openai(exc, "PermissionDeniedError"):
        return NetworkErrorKind.AUTH
    if _is_openai(exc, "RateLimitError") or "429" in text:
        return NetworkErrorKind.RATE_LIMIT

    if is_stream_stall(exc):
        return NetworkErrorKind.STREAM_STALL

    # DNS: getaddrinfo/gaierror surfaces as httpx.ConnectError.
    if "gaierror" in names or any(
        marker in text
        for marker in (
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "getaddrinfo failed",
        )
    ):
        return NetworkErrorKind.DNS

    # TLS: certificate / handshake failures (a MITM proxy produces these).
    if "SSLError" in names or "certificate_verify_failed" in text or "certificate verify failed" in text:
        return NetworkErrorKind.TLS

    # Timeouts. openai.APITimeoutError subclasses APIConnectionError, so it must
    # be checked before the generic connection branches.
    if _is_httpx(exc, "ConnectTimeout") or _is_openai(exc, "APITimeoutError"):
        return NetworkErrorKind.CONNECT_TIMEOUT
    if _is_httpx(exc, "ReadTimeout") or _is_httpx(exc, "WriteTimeout") or _is_httpx(exc, "PoolTimeout"):
        return NetworkErrorKind.READ_TIMEOUT
    if "TimeoutError" in names or isinstance(exc, TimeoutError):
        return NetworkErrorKind.READ_TIMEOUT

    # Connection / protocol drops. A proxy in play changes both the meaning and
    # the remediation, so keep that distinction.
    if _is_httpx(exc, "RemoteProtocolError") or "RemoteProtocolError" in names:
        return NetworkErrorKind.PROXY_DISCONNECT if proxy else NetworkErrorKind.SERVER_DISCONNECT
    if _is_httpx(exc, "ConnectError") or "ConnectError" in names:
        return NetworkErrorKind.PROXY_DISCONNECT if proxy else NetworkErrorKind.SERVER_DISCONNECT
    if _is_openai(exc, "APIConnectionError") or "APIConnectionError" in names:
        return NetworkErrorKind.PROXY_DISCONNECT if proxy else NetworkErrorKind.SERVER_DISCONNECT
    if isinstance(exc, (ConnectionError, BrokenPipeError, EOFError)):
        return NetworkErrorKind.PROXY_DISCONNECT if proxy else NetworkErrorKind.SERVER_DISCONNECT

    return NetworkErrorKind.UNKNOWN


def classify_network_error(
    exc: BaseException,
    *,
    proxy: str | None = None,
    endpoint: str = "",
    attempt: int = 0,
) -> NetworkError:
    """Classify ``exc`` into a :class:`NetworkError`.

    ``proxy`` is the resolved proxy URL for the failed request (``None`` for a
    direct connection). Pass it whenever known: it is what lets the error say
    "the proxy dropped the response" instead of a generic disconnect.
    """
    kind = _classify_kind(exc, proxy)
    hint = _HINTS.get(kind, _HINTS[NetworkErrorKind.UNKNOWN])
    if kind is NetworkErrorKind.PROXY_DISCONNECT and proxy:
        hint = f"LLM 请求经代理 {_redact_url(proxy)} 中断。请重试；若反复失败，请在模型设置中把该服务改为“直连”。"
    elif kind is NetworkErrorKind.SERVER_DISCONNECT and endpoint:
        hint = f"模型服务端（{endpoint}）断开了连接，请重试。"
    return NetworkError(
        kind=kind,
        retryable=kind in _RETRYABLE,
        root_cause=_root_cause(exc),
        endpoint=endpoint,
        proxy=_redact_url(proxy) if proxy else None,
        user_hint=hint,
        attempt=attempt,
    )


def _redact_url(url: str) -> str:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return url
