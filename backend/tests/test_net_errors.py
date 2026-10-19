"""Unit tests for normalized network-error classification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from coworker.net.network_errors import (  # noqa: E402
    NetworkErrorKind,
    classify_network_error,
)


def test_remote_protocol_with_proxy_is_proxy_disconnect():
    exc = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    net = classify_network_error(
        exc, proxy="http://127.0.0.1:1082", endpoint="http://47.76.63.128:1111"
    )
    assert net.kind is NetworkErrorKind.PROXY_DISCONNECT
    assert net.retryable is True
    assert "代理" in net.user_hint
    assert net.proxy == "http://127.0.0.1:1082"
    assert "RemoteProtocolError" in net.root_cause


def test_remote_protocol_without_proxy_is_server_disconnect():
    exc = httpx.RemoteProtocolError("peer closed connection")
    net = classify_network_error(exc, endpoint="http://example:1234")
    assert net.kind is NetworkErrorKind.SERVER_DISCONNECT
    assert net.retryable is True


def test_connect_timeout():
    net = classify_network_error(httpx.ConnectTimeout("timed out"))
    assert net.kind is NetworkErrorKind.CONNECT_TIMEOUT


def test_read_timeout():
    net = classify_network_error(httpx.ReadTimeout("timed out"))
    assert net.kind is NetworkErrorKind.READ_TIMEOUT


def test_dns():
    net = classify_network_error(
        httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
    )
    assert net.kind is NetworkErrorKind.DNS
    assert net.retryable is False


def test_tls():
    net = classify_network_error(httpx.ConnectError("certificate verify failed"))
    assert net.kind is NetworkErrorKind.TLS


def test_stream_stall_by_message():
    net = classify_network_error(Exception("stream_chunk_timeout: No streaming chunk received"))
    assert net.kind is NetworkErrorKind.STREAM_STALL


def test_proxy_credentials_redacted():
    net = classify_network_error(
        httpx.RemoteProtocolError("drop"),
        proxy="http://user:secret@127.0.0.1:1082",
    )
    assert "secret" not in (net.proxy or "")
    assert "secret" not in net.user_hint


def test_unknown_is_not_retryable():
    net = classify_network_error(ValueError("boom"))
    assert net.kind is NetworkErrorKind.UNKNOWN
    assert net.retryable is False
    assert net.root_cause


def test_to_dict_shape():
    net = classify_network_error(httpx.RemoteProtocolError("drop"), proxy="http://p:1")
    payload = net.to_dict()
    assert payload["kind"] == "proxy_disconnect"
    assert payload["retryable"] is True
    assert payload["proxy"] == "http://p:1"
    assert "hint" in payload
