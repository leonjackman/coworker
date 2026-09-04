"""Pluggable web-search backends for the coworker agent.

Provides three interchangeable search providers plus a fallback chain:

* ``tavily``     — Tavily REST API (paid, best quality; needs an API key)
* ``duckduckgo`` — DuckDuckGo via the free ``duckduckgo-search`` package (default)
* ``browser``    — the desktop app's embedded Chromium browser driven against a
                   configured search engine (Google/Bing/DDG/Baidu/Sogou)

The tool layer calls :func:`run_web_search`, which tries the *selected*
provider first and then the others in a fixed quality order
(``tavily > duckduckgo > browser``), skipping any backend that is not ready.
Each attempt is cheap and never raises; the result is a dict that matches the
JSON contract ``web_search`` always returned (Tavily-shaped).
"""

from __future__ import annotations

from typing import Any

from coworker.logger import get_logger

from .base import SearchResult, SearchResultSet, SearchEngine  # noqa: F401  (re-export)
from . import browser_engine  # noqa: F401
from . import ddgs_engine  # noqa: F401
from . import tavily_engine  # noqa: F401

logger = get_logger(__name__)

#: Canonical providers, in fallback-priority order (tavily > duckduckgo > browser).
PROVIDERS = ("tavily", "duckduckgo", "browser")

#: Provider used when nothing is configured (free, keyless).
DEFAULT_PROVIDER = "duckduckgo"

#: Canonical browser sub-engines + their keyless default (single source of truth,
#: re-exported from :mod:`coworker.search.browser_engine`).
BROWSER_ENGINES = browser_engine.BROWSER_ENGINES
DEFAULT_BROWSER_ENGINE = browser_engine.DEFAULT_BROWSER_ENGINE


def provider_capability(provider: str | None, data_dir: Any) -> str:
    """Readiness of one backend: ``'ok'`` | ``'no_key'`` (Tavily) |
    ``'browser_unavailable'``.

    Shared by the chain builder and the agent's capability line so the
    "Tavily needs a key / browser needs the bridge" rules exist in one place.
    DuckDuckGo is always ready (it is the built-in fallback).
    """
    if provider == "tavily":
        key = tavily_engine.get_tavily_key(data_dir) if data_dir is not None else None
        return "ok" if key else "no_key"
    if provider == "browser":
        if data_dir is None:
            return "browser_unavailable"
        from coworker.browser.bridge_client import browser_available

        return "ok" if browser_available(data_dir) else "browser_unavailable"
    return "ok"


def _make_engine(provider: str, cfg: Any, data_dir: Any, session_id: str) -> SearchEngine:
    if provider == "tavily":
        key = tavily_engine.get_tavily_key(data_dir) if data_dir is not None else None
        return tavily_engine.TavilyEngine(api_key=key or "")
    if provider == "duckduckgo":
        return ddgs_engine.DuckDuckGoEngine()
    return browser_engine.BrowserSearchEngine(
        data_dir,
        engine=getattr(cfg, "browser_engine", None) or DEFAULT_BROWSER_ENGINE,
        session_id=session_id,
    )


def build_chain(cfg: Any, data_dir: Any, *, session_id: str = "") -> list[SearchEngine]:
    """Ordered engine list for one ``web_search`` call.

    Head is the configured provider; the rest follow :data:`PROVIDERS`
    (tavily > duckduckgo > browser) with unready backends dropped — Tavily only
    when a key exists, Browser only when the desktop bridge is up. DuckDuckGo
    is always ready and so always ends the chain, guaranteeing a fallback for
    the other two.
    """
    selected = getattr(cfg, "provider", None)
    if selected not in PROVIDERS:
        selected = DEFAULT_PROVIDER
    order = [selected] + [p for p in PROVIDERS if p != selected]

    engines: list[SearchEngine] = []
    for provider in order:
        if provider_capability(provider, data_dir) != "ok":
            continue
        engines.append(_make_engine(provider, cfg, data_dir, session_id))
    return engines


def run_web_search(
    cfg: Any,
    data_dir: Any,
    *,
    query: str,
    max_results: int,
    search_depth: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Execute one web search through the provider chain. Never raises.

    Returns a dict shaped exactly like the legacy Tavily payload, plus two
    transparency fields so the agent (and user) know which backend served the
    results: ``provider`` (the configured selection) and ``provider_used`` /
    ``fell_back`` when the chain degraded.
    """
    chain = build_chain(cfg, data_dir, session_id=session_id)
    if not chain:
        return {
            "error": "No web-search backend is available. Enable web access in Settings → Web "
            "(联网设置), or install the free 'ddgs' backend.",
            "error_code": "no_backend",
            "answer": "",
            "results": [],
            "provider": getattr(cfg, "provider", ""),
            "provider_used": "",
            "fell_back": False,
        }

    last_error = ""
    for engine in chain:
        try:
            result = engine.search(query, max_results=int(max_results or 8), search_depth=search_depth or "")
        except Exception as exc:  # noqa: BLE001 - a broken backend must never break a turn
            logger.warning("web search backend %s raised: %s", engine.name, exc)
            last_error = f"{engine.name}: {exc}"
            continue
        if result.error:
            last_error = f"{engine.name}: {result.error}"
            continue
        return {
            "error": "",
            "answer": result.answer,
            "results": [r.to_dict() for r in result.results],
            "provider": getattr(cfg, "provider", ""),
            "provider_used": result.provider or engine.name,
            "fell_back": (engine.name != getattr(cfg, "provider", "")),
        }

    return {
        "error": f"All search backends failed. Last error: {last_error}",
        "error_code": "all_backends_failed",
        "answer": "",
        "results": [],
        "provider": getattr(cfg, "provider", ""),
        "provider_used": "",
        "fell_back": True,
    }
