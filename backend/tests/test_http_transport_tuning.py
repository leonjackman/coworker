"""Tests for the weak-network LLM transport tuning (P0-1 / P0-2).

Covers the HTTP/2 + keep-alive + connect-timeout tuning injected into every
ChatOpenAI built by ``coworker.agent.model_defaults``, the tightened stream
chunk timeout, and the request-logging wrapper preserving the tuned transport.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from coworker.agent.model_defaults import (  # noqa: E402
    _build_tuned_async_httpx_client,
    _http2_available,
    _llm_stream_chunk_timeout,
)


@pytest.fixture(autouse=True)
def _isolate_proxy(monkeypatch):
    """Keep these tests independent of the machine's real proxy configuration."""
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
        "NO_PROXY", "no_proxy",
        "COWORKER_LLM_PROXY_MODE", "COWORKER_LLM_PROXY_URL", "OPENAI_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    import coworker.net.proxy as proxy_mod

    monkeypatch.setattr(proxy_mod, "macos_system_proxy_exceptions", lambda: ())
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})


def _pool(client):
    return client._transport._pool


def test_stream_chunk_timeout_default_is_bounded():
    # The default stall bound is 60s: long enough for a heavy prefill, short
    # enough that a dead-but-TCP-alive provider surfaces promptly instead of
    # looking "network congested" for minutes.
    assert _llm_stream_chunk_timeout() == 60.0


def test_https_client_enables_http2_and_long_keepalive():
    if not _http2_available():
        pytest.skip("h2 package not installed")
    client = _build_tuned_async_httpx_client("https://api.deepseek.com/v1", None)
    assert client is not None
    assert bool(getattr(client, "_cw_tuned_transport", False)) is True
    pool = _pool(client)
    assert pool._http2 is True
    assert pool._http1 is True  # ALPN falls back to HTTP/1.1 transparently
    assert pool._keepalive_expiry >= 60.0
    # openai honours a custom client's non-default timeout per request
    assert client.timeout.connect == 10.0


def test_plain_http_client_stays_http1():
    # http:// (local vLLM / Ollama) has no ALPN h2 — force HTTP/1.1, keep tuning.
    client = _build_tuned_async_httpx_client("http://10.0.0.5:8000/v1", None)
    assert client is not None
    assert _pool(client)._http2 is False
    assert _pool(client)._keepalive_expiry >= 60.0


def test_proxy_env_keeps_tuned_transport_and_pins_proxy(monkeypatch):
    # A visible proxy must NOT make us fall back to langchain's default client:
    # doing so re-read the system proxy with a different (exceptions-dropping)
    # resolver and forced every endpoint through it. We keep the tuned client
    # and pin the resolved proxy explicitly with trust_env=False.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    client = _build_tuned_async_httpx_client("https://api.deepseek.com/v1", None)
    assert client is not None
    assert bool(getattr(client, "_cw_tuned_transport", False)) is True
    assert client.trust_env is False
    decision = getattr(client, "_cw_proxy_decision")
    assert decision.proxy_url == "http://127.0.0.1:8888"


def test_provider_direct_bypasses_visible_proxy(monkeypatch):
    # A provider explicitly configured for direct traffic must bypass a visible
    # system/env proxy (the user's per-provider override wins).
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    from coworker.providers.models import ProviderEntry

    provider = ProviderEntry(
        id="p", name="local", provider_type="custom",
        base_url="http://47.76.63.128:1111/v1", proxy_mode="direct",
    )
    client = _build_tuned_async_httpx_client(
        "http://47.76.63.128:1111/v1", None, provider
    )
    assert client is not None
    decision = getattr(client, "_cw_proxy_decision")
    assert decision.proxy_url is None
    assert client.trust_env is False


def test_no_proxy_alone_does_not_disable_tuning(monkeypatch):
    # A lone NO_PROXY (very common) routes no traffic and must not disable the
    # weak-network tuning — otherwise tuning would be off on most machines.
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("ALL_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    client = _build_tuned_async_httpx_client("https://api.deepseek.com/v1", None)
    assert client is not None
    assert bool(getattr(client, "_cw_tuned_transport", False)) is True


def test_request_log_wrapper_preserves_tuned_transport(monkeypatch, tmp_path):
    # COWORKER_LLM_LOG must not silently drop http2/keep-alive on the wire.
    from coworker.llm_request_logger import wrap_async_client

    if not _http2_available():
        pytest.skip("h2 package not installed")
    client = _build_tuned_async_httpx_client("https://api.deepseek.com/v1", None)
    # Logging disabled → a lightweight wrapper that records failures only.
    assert wrap_async_client(client, tmp_path) is not client
    assert wrap_async_client(client, tmp_path)._record_success is False
    # Logging enabled → wrapper is a logging client that CARRIES the tuned
    # transport over (http2 / keep-alive preserved on the logged path).
    monkeypatch.setenv("COWORKER_LLM_LOG", "1")
    monkeypatch.setenv("COWORKER_DATA_DIR", str(tmp_path))
    wrapped = wrap_async_client(client, None)
    assert wrapped is not client
    assert wrapped._record_success is True
    assert wrapped._transport is client._transport
    assert _pool(wrapped)._http2 is True
