"""Unit tests for the unified outbound proxy resolver.

The resolver exists because CPython's ``urllib.request.getproxies()`` drops the
macOS system proxy ``ExceptionsList``, so ``httpx`` forced a self-hosted
provider through a local proxy while ``urllib`` (provider probes) connected
directly. These tests pin the precedence order and the port-stripping fix.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import coworker.net.proxy as proxy_mod  # noqa: E402
from coworker.net.proxy import ProxyMode, resolve_proxy  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
        "NO_PROXY", "no_proxy",
        "COWORKER_LLM_PROXY_MODE", "COWORKER_LLM_PROXY_URL", "OPENAI_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})
    monkeypatch.setattr(proxy_mod, "macos_system_proxy_exceptions", lambda: ())


class _Provider:
    def __init__(self, proxy_mode: str = "", proxy_url: str = ""):
        self.proxy_mode = proxy_mode
        self.proxy_url = proxy_url


# ---------------------------------------------------------------------------
# Host / bypass-entry matching
# ---------------------------------------------------------------------------


def test_host_matches_exact_suffix_and_wildcard():
    assert proxy_mod._host_matches("47.76.63.128", ["47.76.63.128"])
    assert proxy_mod._host_matches("api.example.com", ["example.com"])
    assert proxy_mod._host_matches("api.example.com", [".example.com"])
    assert proxy_mod._host_matches("api.example.com", ["*.example.com"])
    assert proxy_mod._host_matches("anything", ["*"])
    assert not proxy_mod._host_matches("notexample.com", ["example.com"])


def test_host_matches_strips_port():
    # The bug: httpx treats "47.76.63.128:1111" as a hostname suffix and never
    # matches the URL host (which has no port). We strip it instead.
    assert proxy_mod._split_host_port("47.76.63.128:1111") == "47.76.63.128"
    assert proxy_mod._split_host_port("[::1]:8080") == "::1"
    assert proxy_mod._split_host_port("::1") == "::1"
    assert proxy_mod._host_matches("47.76.63.128", ["47.76.63.128:1111"])


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_provider_direct_wins_over_visible_proxy(monkeypatch):
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    d = resolve_proxy("http://47.76.63.128:1111/v1", _Provider(proxy_mode="direct"))
    assert d.mode is ProxyMode.DIRECT
    assert d.proxy_url is None
    assert d.source == "provider"


def test_provider_proxy_wins(monkeypatch):
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    d = resolve_proxy(
        "https://api.openai.com/v1",
        _Provider(proxy_mode="proxy", proxy_url="10.0.0.1:3128"),
    )
    assert d.mode is ProxyMode.PROXY
    assert d.proxy_url == "http://10.0.0.1:3128"
    assert d.source == "provider"


def test_global_direct(monkeypatch):
    monkeypatch.setenv("COWORKER_LLM_PROXY_MODE", "direct")
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    d = resolve_proxy("https://api.openai.com/v1")
    assert d.proxy_url is None
    assert d.reason == "global-direct"


def test_global_proxy_url(monkeypatch):
    monkeypatch.setenv("COWORKER_LLM_PROXY_URL", "http://10.0.0.2:8080")
    d = resolve_proxy("https://api.openai.com/v1")
    assert d.proxy_url == "http://10.0.0.2:8080"
    assert d.reason == "global-proxy"


def test_no_proxy_env_bypasses_with_port():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("NO_PROXY", "47.76.63.128:1111")
        monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
        d = resolve_proxy("http://47.76.63.128:1111/v1")
        assert d.proxy_url is None
        assert d.reason == "no-proxy-env"
    finally:
        monkeypatch.undo()


def test_macos_exception_bypasses_proxy(monkeypatch):
    # This is the exact macOS configuration that produced the bug: the host is
    # in ExceptionsList but getproxies() never exposes it.
    monkeypatch.setattr(proxy_mod, "macos_system_proxy_exceptions", lambda: ("47.76.63.128:1111",))
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    d = resolve_proxy("http://47.76.63.128:1111/v1")
    assert d.proxy_url is None
    assert d.reason == "system-proxy-exception"


def test_system_proxy_used_for_non_exception(monkeypatch):
    monkeypatch.setattr(proxy_mod, "macos_system_proxy_exceptions", lambda: ("47.76.63.128:1111",))
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    d = resolve_proxy("https://api.openai.com/v1")
    assert d.proxy_url == "http://127.0.0.1:1082"
    assert d.reason == "system-proxy"


def test_no_proxy_anywhere_is_direct():
    d = resolve_proxy("https://api.openai.com/v1")
    assert d.proxy_url is None
    assert d.reason == "no-proxy"


def test_loopback_is_always_direct(monkeypatch):
    # Local bridges / local MCP shims must never be routed through a forward
    # proxy, even when a system proxy is visible.
    monkeypatch.setattr(proxy_mod, "_visible_proxy_url", lambda: "http://127.0.0.1:1082")
    for url in (
        "http://127.0.0.1:60016/state",
        "http://localhost:1234/x",
        "http://[::1]:9/x",
    ):
        d = resolve_proxy(url)
        assert d.proxy_url is None, url
        assert d.reason == "loopback", url


def test_provider_proxy_still_overrides_loopback():
    d = resolve_proxy(
        "http://127.0.0.1:1234", _Provider(proxy_mode="proxy", proxy_url="http://p:1")
    )
    assert d.proxy_url == "http://p:1"
    assert d.source == "provider"


def test_legacy_openai_proxy_env(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://10.0.0.3:9000")
    d = resolve_proxy("https://api.openai.com/v1")
    assert d.proxy_url == "http://10.0.0.3:9000"
    assert d.source == "env"


# ---------------------------------------------------------------------------
# Stack parity
# ---------------------------------------------------------------------------


def test_urllib_opener_matches_decision():
    import urllib.request

    direct = resolve_proxy("https://api.openai.com/v1")
    opener = proxy_mod.urllib_opener(direct)
    assert isinstance(opener, urllib.request.OpenerDirector)
    # The explicit ProxyHandler is what pins urllib's behaviour (empty mapping =
    # direct). Assert it survives construction rather than checking a name that
    # urllib folds into the unknown_open chain.
    assert any(type(h).__name__ in {"ProxyHandler", "UnknownHandler"} for h in opener.handlers)

    proxied = proxy_mod.ProxyDecision(ProxyMode.PROXY, "http://127.0.0.1:1082", "test", "test")
    assert proxy_mod.httpx_proxy_kwargs(proxied) == {
        "proxy": "http://127.0.0.1:1082",
        "trust_env": False,
    }
    assert proxy_mod.httpx_proxy_kwargs(direct) == {"proxy": None, "trust_env": False}


def test_describe_redacts_credentials():
    decision = proxy_mod.ProxyDecision(
        ProxyMode.PROXY, "http://user:secret@127.0.0.1:1082", "test", "test"
    )
    text = proxy_mod.describe(decision)
    assert "secret" not in text
    assert "127.0.0.1:1082" in text
