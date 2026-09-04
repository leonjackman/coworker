"""Tests for the pluggable web-search backends (:mod:`coworker.search`) and
the reworked config / tool wiring in :mod:`coworker.web`.

No network access: every transport is stubbed via monkeypatch. Verifies the
shared JSON contract (legacy-Tavily shape), the fallback-chain ordering
(tavily > duckduckgo > browser), config clamping, and the 4-state capability
reporting.
"""

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker import web as web_mod  # noqa: E402
from coworker.search import base as search_base  # noqa: E402
from coworker.search import browser_engine  # noqa: E402
from coworker.search import ddgs_engine  # noqa: E402
from coworker.search import tavily_engine  # noqa: E402
from coworker.web import WebConfig, load_web_config, write_web_block  # noqa: E402

os.environ.setdefault("COWORKER_LOG_LEVEL", "WARNING")


def _enabled_dir(tmp_path, **patch):
    """A data dir whose web block is enabled with the given overrides."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    merged = {"enabled": True, "provider": "duckduckgo", "browser_engine": "bing"}
    merged.update(patch)
    write_web_block(data_dir, merged)
    return data_dir


# ── Config model / persistence ─────────────────────────────────────────────

def test_defaults_are_keyless_free_provider(tmp_path):
    data_dir = _enabled_dir(tmp_path)
    cfg = load_web_config(data_dir)
    assert cfg.enabled is True
    assert cfg.provider == "duckduckgo"
    assert cfg.browser_engine == "bing"
    # Round-trips through read_web_block.
    block = web_mod.read_web_block(data_dir)
    assert block["provider"] == "duckduckgo"
    assert block["browser_engine"] == "bing"


def test_invalid_provider_and_engine_are_rejected(tmp_path):
    data_dir = _enabled_dir(tmp_path)
    merged = web_mod.write_web_block(data_dir, {"provider": "bing", "browser_engine": "yahoo"})
    assert merged["provider"] == "duckduckgo"  # unchanged
    assert merged["browser_engine"] == "bing"  # unchanged
    merged = web_mod.write_web_block(data_dir, {"provider": "tavily", "browser_engine": "baidu"})
    assert merged["provider"] == "tavily"
    assert merged["browser_engine"] == "baidu"


def test_from_dict_sanitises_existing_files(tmp_path):
    cfg = WebConfig.from_dict({"enabled": True, "provider": "yahoo", "browser_engine": "ask"})
    assert cfg.provider == "duckduckgo"
    assert cfg.browser_engine == "bing"


# ── DuckDuckGo engine ──────────────────────────────────────────────────────

class _FakeDDGS:
    # Configured via class attributes so ``cls()`` in the engine picks them up.
    results: list = []
    error: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, safesearch="moderate", max_results=None):
        if self.error:
            raise self.error
        return self.results


def _ddgs_search(monkeypatch, items=None, error=None):
    _FakeDDGS.results = items if items is not None else []
    _FakeDDGS.error = error
    monkeypatch.setattr(ddgs_engine, "_DDGS", _FakeDDGS)
    monkeypatch.setattr(ddgs_engine, "_DDGS_UNAVAILABLE", False)
    return ddgs_engine.DuckDuckGoEngine().search("test", max_results=5)


def test_ddgs_normalization(monkeypatch):
    rs = _ddgs_search(
        monkeypatch,
        items=[
            {"title": "One", "href": "https://one.com", "body": "snippet one"},
            {"title": "Two", "href": "https://two.com", "body": ""},
            {"title": "Three"},  # no url → skipped
        ],
    )
    assert rs.error == ""
    assert rs.provider == "duckduckgo"
    assert len(rs.results) == 2
    assert rs.results[0].title == "One"
    assert rs.results[0].url == "https://one.com"
    assert rs.results[0].content == "snippet one"


def test_ddgs_empty_is_success(monkeypatch):
    rs = _ddgs_search(monkeypatch, items=[])
    assert rs.error == ""
    assert rs.results == []


def test_ddgs_exception_is_error(monkeypatch):
    rs = _ddgs_search(monkeypatch, error=RuntimeError("boom"))
    assert rs.error.startswith("DuckDuckGo search failed")
    assert rs.results == []


def test_ddgs_missing_library(monkeypatch):
    monkeypatch.setattr(ddgs_engine, "_DDGS", None)
    monkeypatch.setattr(ddgs_engine, "_DDGS_UNAVAILABLE", True)
    rs = ddgs_engine.DuckDuckGoEngine().search("x")
    assert "unavailable" in rs.error


# ── Tavily engine ──────────────────────────────────────────────────────────

def test_tavily_engine_normalization(monkeypatch):
    def fake_tavily(query, key, *, max_results=8, search_depth="basic"):
        return {
            "error": "",
            "answer": "synthesized",
            "results": [
                {"title": "T", "url": "https://t.com", "content": "c", "score": 0.9},
                {"title": "U", "url": "https://u.com", "content": "d", "score": None},
            ],
        }

    monkeypatch.setattr(tavily_engine, "tavily_search", fake_tavily)
    rs = tavily_engine.TavilyEngine(api_key="k").search("q", max_results=8, search_depth="advanced")
    assert rs.answer == "synthesized"
    assert len(rs.results) == 2
    assert rs.results[0].score == 0.9
    assert rs.results[1].to_dict()["score"] is None


def test_tavily_engine_error_passthrough(monkeypatch):
    def fake_tavily(query, key, *, max_results=8, search_depth="basic"):
        return {"error": "Tavily API error 401: bad key", "answer": "", "results": []}

    monkeypatch.setattr(tavily_engine, "tavily_search", fake_tavily)
    rs = tavily_engine.TavilyEngine(api_key="k").search("q")
    assert "401" in rs.error


# ── Browser engine helpers ─────────────────────────────────────────────────

def test_browser_extract_parses_bridge_payload():
    raw = {
        "result": json.dumps(
            {"blocked": False, "items": [{"title": "A", "url": "https://a.com/x", "snippet": "s"}]}
        )
    }
    parsed = browser_engine._extract(raw)
    assert parsed and parsed[0].title == "A"
    assert parsed[0].url == "https://a.com/x"


def test_browser_extract_blocked_empty():
    assert browser_engine._extract({"result": json.dumps({"blocked": True, "items": []})}) is None
    assert browser_engine._extract({"result": "not json"}) is None
    assert browser_engine._extract({"error": "bridge down"}) is None


# ── Chain construction ─────────────────────────────────────────────────────

def test_chain_default_is_duckduckgo_only(monkeypatch):
    from coworker.search import build_chain

    monkeypatch.setattr(tavily_engine, "get_tavily_key", lambda d: None)
    monkeypatch.setattr("coworker.browser.bridge_client.browser_available", lambda d: False)
    chain = build_chain(WebConfig(enabled=True, provider="duckduckgo"), None)
    assert [e.name for e in chain] == ["duckduckgo"]


def test_chain_tavily_with_key_then_ddg(monkeypatch):
    from coworker.search import build_chain

    monkeypatch.setattr(tavily_engine, "get_tavily_key", lambda d: "secret")
    monkeypatch.setattr("coworker.browser.bridge_client.browser_available", lambda d: False)
    chain = build_chain(WebConfig(enabled=True, provider="tavily"), "data")
    assert [e.name for e in chain] == ["tavily", "duckduckgo"]


def test_chain_browser_never_pulls_tavily_without_key(monkeypatch):
    from coworker.search import build_chain

    monkeypatch.setattr(tavily_engine, "get_tavily_key", lambda d: None)
    monkeypatch.setattr("coworker.browser.bridge_client.browser_available", lambda d: True)
    chain = build_chain(WebConfig(enabled=True, provider="browser", browser_engine="bing"), "data")
    assert [e.name for e in chain] == ["browser", "duckduckgo"]
    # Browser head carries the sub-engine selection through to the engine.
    assert chain[0].engine == "bing"


# ── run_web_search dispatch & fallback ─────────────────────────────────────

class _FakeEngine:
    def __init__(self, name, error=False, results=None):
        self.name = name
        self.error = error
        self.results = results

    def search(self, query, *, max_results=8, search_depth=""):
        if self.error:
            return search_base.SearchResultSet(error=f"{self.name} failed", provider=self.name)
        return search_base.SearchResultSet(
            results=[search_base.SearchResult(title="R", url="https://r.com", content="c")],
            provider=self.name,
        )


def _patch_chain(monkeypatch, engines):
    monkeypatch.setattr("coworker.search.build_chain", lambda cfg, data_dir, *, session_id="": engines)


def test_run_search_returns_first_success(monkeypatch):
    from coworker.search import run_web_search

    _patch_chain(monkeypatch, [_FakeEngine("tavily", error=True), _FakeEngine("duckduckgo")])
    out = run_web_search(WebConfig(enabled=True, provider="tavily"), None, query="q", max_results=5)
    assert out["error"] == ""
    assert out["provider_used"] == "duckduckgo"
    assert out["fell_back"] is True
    assert out["results"][0]["url"] == "https://r.com"
    assert "answer" in out and "provider" in out


def test_run_search_all_failed(monkeypatch):
    from coworker.search import run_web_search

    _patch_chain(monkeypatch, [_FakeEngine("duckduckgo", error=True)])
    out = run_web_search(WebConfig(enabled=True, provider="duckduckgo"), None, query="q", max_results=5)
    assert out["error_code"] == "all_backends_failed"
    assert out["fell_back"] is True
    assert out["results"] == []


def test_run_search_no_chain(monkeypatch):
    from coworker.search import run_web_search

    _patch_chain(monkeypatch, [])
    out = run_web_search(WebConfig(enabled=True, provider="tavily"), None, query="q", max_results=5)
    assert out["error_code"] == "no_backend"
    assert out["fell_back"] is False


# ── web_search tool (JSON contract) ────────────────────────────────────────

def test_web_search_tool_shapes_output(tmp_path, monkeypatch):
    data_dir = _enabled_dir(tmp_path, provider="duckduckgo")

    def fake_run(cfg, d, *, query, max_results, search_depth="", session_id=""):
        return {
            "error": "",
            "answer": "",
            "results": [{"title": "X", "url": "https://x.com", "content": "x"}],
            "provider": "duckduckgo",
            "provider_used": "duckduckgo",
            "fell_back": False,
        }

    monkeypatch.setattr("coworker.search.run_web_search", fake_run)
    tools = web_mod.build_web_tools(None, None, data_dir=str(data_dir))
    payload = tools[0].invoke({"query": "hello", "max_results": 3})
    data = json.loads(payload)
    assert data["results"][0]["title"] == "X"
    assert data["provider_used"] == "duckduckgo"


def test_web_fetch_tool_present_only_when_enabled(tmp_path):
    on_dir = _enabled_dir(tmp_path)
    names_on = {t.name for t in web_mod.build_web_tools(WebConfig(enabled=True, fetch_enabled=True), None, data_dir=str(on_dir))}
    names_no_fetch = {
        t.name for t in web_mod.build_web_tools(WebConfig(enabled=True, fetch_enabled=False), None, data_dir=str(on_dir))
    }
    assert "web_search" in names_on and "web_fetch" in names_on
    assert "web_fetch" not in names_no_fetch


def test_resolve_web_tools_gates_on_enabled(tmp_path):
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    write_web_block(off_dir, {"enabled": False})
    assert web_mod.resolve_web_tools(str(off_dir)) == []
    on_dir = _enabled_dir(tmp_path)
    tools = web_mod.resolve_web_tools(str(on_dir))
    assert {t.name for t in tools} == {"web_search", "web_fetch"}


# ── Capability status / system-prompt line ─────────────────────────────────

def test_capability_status_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(tavily_engine, "get_tavily_key", lambda d: None)
    monkeypatch.setattr("coworker.browser.bridge_client.browser_available", lambda d: False)

    off = tmp_path / "off"
    off.mkdir()
    write_web_block(off, {"enabled": False})
    assert web_mod.web_capability_status(str(off)) == "disabled"

    assert web_mod.web_capability_status(str(_enabled_dir(tmp_path, provider="duckduckgo"))) == "ok"
    assert web_mod.web_capability_status(str(_enabled_dir(tmp_path, provider="tavily"))) == "no_key"

    monkeypatch.setattr(tavily_engine, "get_tavily_key", lambda d: "secret")
    assert web_mod.web_capability_status(str(_enabled_dir(tmp_path, provider="tavily"))) == "ok"

    # Reuse a dir for the browser provider (same tmp_path adds a suffix to avoid clash).
    browser_dir = _enabled_dir(tmp_path, provider="browser")
    assert web_mod.web_capability_status(str(browser_dir)) == "browser_unavailable"


def test_capability_line_reflects_free_default(tmp_path, monkeypatch):
    monkeypatch.setattr("coworker.browser.bridge_client.browser_available", lambda d: False)
    data_dir = _enabled_dir(tmp_path, provider="duckduckgo")
    line = web_mod.web_capability_line(str(data_dir))
    assert "DuckDuckGo" in line
    assert "ENABLED" in line


def test_capability_line_disabled_no_tavily_mention(tmp_path):
    off = tmp_path / "off"
    off.mkdir()
    write_web_block(off, {"enabled": False})
    line = web_mod.web_capability_line(str(off))
    assert "DISABLED" in line
