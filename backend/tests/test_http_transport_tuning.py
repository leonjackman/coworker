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


def _pool(client):
    return client._transport._pool


def test_stream_chunk_timeout_default_is_bounded():
    # P0-2: the default stall bound dropped from 600s -> 120s so a dead-but-TCP-
    # alive provider surfaces promptly instead of looking "network congested".
    assert _llm_stream_chunk_timeout() == 120.0


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


def test_proxy_env_disables_tuning(monkeypatch):
    # A custom transport would shadow env-proxy auto-detection; when an active
    # proxy is configured we must fall back to langchain's default client.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    client = _build_tuned_async_httpx_client("https://api.deepseek.com/v1", None)
    assert client is not None
    assert bool(getattr(client, "_cw_tuned_transport", False)) is False


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
    # Logging disabled → wrapper is the tuned client itself (zero overhead).
    assert wrap_async_client(client, None) is client
    # Logging enabled → wrapper is a logging client that CARRIES the tuned
    # transport over (http2 / keep-alive preserved on the logged path).
    monkeypatch.setenv("COWORKER_LLM_LOG", "1")
    monkeypatch.setenv("COWORKER_DATA_DIR", str(tmp_path))
    wrapped = wrap_async_client(client, None)
    assert wrapped is not client
    assert wrapped._transport is client._transport
    assert _pool(wrapped)._http2 is True
