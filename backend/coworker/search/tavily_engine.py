"""Tavily search backend (paid, best-quality) plus its secret helpers.

The API key is stored in the OS secret store (see :mod:`coworker.secrets`)
under service ``coworker.web`` / account ``tavily``. This module owns all
Tavily-specific code; the key helpers were historically exposed from
:mod:`coworker.web` and are re-exported there for backwards compatibility.

``tavily_search`` is kept as a standalone function (used by the settings "test
connection" flow) and is wrapped by :class:`TavilyEngine` for the fallback
chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from coworker.logger import get_logger

from .base import SearchResult, SearchResultSet

logger = get_logger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
_SECRET_SERVICE = "coworker.web"
_SECRET_ACCOUNT = "tavily"

#: Search depth value passed to Tavily by default.
DEFAULT_SEARCH_DEPTH = "basic"


# ── Secret helpers (Keychain-first, 0600-file fallback) ─────────────────────

def tavily_key_configured(data_dir: Path | str) -> bool:
    from coworker.secrets import get_secret

    return get_secret(Path(data_dir), _SECRET_SERVICE, _SECRET_ACCOUNT) is not None


def get_tavily_key(data_dir: Path | str) -> str | None:
    from coworker.secrets import get_secret

    return get_secret(Path(data_dir), _SECRET_SERVICE, _SECRET_ACCOUNT)


def set_tavily_key(data_dir: Path | str, api_key: str) -> None:
    from coworker.secrets import set_secret

    set_secret(Path(data_dir), _SECRET_SERVICE, _SECRET_ACCOUNT, api_key)


def delete_tavily_key(data_dir: Path | str) -> None:
    from coworker.secrets import delete_secret

    delete_secret(Path(data_dir), _SECRET_SERVICE, _SECRET_ACCOUNT)


# ── Search ──────────────────────────────────────────────────────────────────

def tavily_search(
    query: str,
    api_key: str,
    *,
    max_results: int = 8,
    search_depth: str = "basic",
) -> dict[str, Any]:
    """Run one Tavily search. Never raises; returns an error-carrying dict."""
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max(1, min(20, int(max_results))),
        "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
        "include_answer": True,
    }
    try:
        response = httpx.post(TAVILY_API_URL, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            body = exc.response.json()
            detail = str(body.get("detail") or body.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = exc.response.text[:200]
        logger.warning("tavily search failed status=%s detail=%r", exc.response.status_code, detail)
        return {"error": f"Tavily API error {exc.response.status_code}: {detail}", "answer": "", "results": []}
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning("tavily search request failed: %s", exc)
        return {"error": f"Tavily request failed: {exc}", "answer": "", "results": []}

    results = []
    for item in data.get("results") or []:
        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or ""),
                "score": item.get("score"),
            }
        )
    return {"error": "", "answer": str(data.get("answer") or ""), "results": results}


@dataclass
class TavilyEngine:
    """Search backend backed by the Tavily REST API (needs an API key)."""

    api_key: str
    name: str = "tavily"

    def search(self, query: str, *, max_results: int = 8, search_depth: str = "") -> SearchResultSet:
        depth = search_depth if search_depth in ("basic", "advanced") else DEFAULT_SEARCH_DEPTH
        raw = tavily_search(query, self.api_key, max_results=max_results, search_depth=depth)
        error = str(raw.get("error") or "")
        results = [
            SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or ""),
                content=str(r.get("content") or ""),
                score=r.get("score"),
            )
            for r in raw.get("results") or []
        ]
        return SearchResultSet(error=error, answer=str(raw.get("answer") or ""), results=results, provider=self.name)
