"""DuckDuckGo search backend — free, no API key, the default provider.

Uses the third-party ``ddgs`` package (PyPI: ``ddgs``), which talks to
DuckDuckGo's anonymous endpoints over plain HTTPS. The library is imported
lazily and guarded: if it is missing or its API shifts, the engine returns an
error-carrying result instead of crashing a turn, and the fallback chain moves
on to the next backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coworker.logger import get_logger

from .base import SearchResult, SearchResultSet

logger = get_logger(__name__)

_DDGS: Any | None = None
_DDGS_UNAVAILABLE = False

#: 0..2 quiet, 3..5 strict-ish; DuckDuckGo's own enum is handled by the lib.
_SAFESEARCH = "moderate"


def _load_ddgs() -> Any | None:
    """Return the ``DDGS`` class or ``None`` when the library is unusable."""
    global _DDGS, _DDGS_UNAVAILABLE
    if _DDGS is None and not _DDGS_UNAVAILABLE:
        try:
            mod = __import__("ddgs", fromlist=["DDGS"])
            cls = getattr(mod, "DDGS", None)
            if cls is not None:
                _DDGS = cls
            else:
                _DDGS_UNAVAILABLE = True
        except Exception:  # noqa: BLE001 - import failure must be survivable
            _DDGS_UNAVAILABLE = True
            logger.warning("'ddgs' library unavailable (pip install ddgs)")
    return _DDGS


@dataclass
class DuckDuckGoEngine:
    """Free web-search backend backed by DuckDuckGo's anonymous API."""

    name: str = "duckduckgo"

    def search(self, query: str, *, max_results: int = 8, search_depth: str = "") -> SearchResultSet:
        cls = _load_ddgs()
        if cls is None:
            return SearchResultSet(
                error="DuckDuckGo backend unavailable (install the 'duckduckgo-search' package)",
                provider=self.name,
            )
        n = max(1, min(20, int(max_results or 8)))
        try:
            client = cls()
            with client:
                items = list(client.text(query, safesearch=_SAFESEARCH, max_results=n))
        except Exception as exc:  # noqa: BLE001 - an upstream hiccup must never raise
            logger.warning("duckduckgo search failed: %s", exc)
            return SearchResultSet(error=f"DuckDuckGo search failed: {exc}", provider=self.name)

        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("href") or item.get("url") or "").strip()
            content = str(item.get("body") or "").strip()
            if title and url:
                results.append(SearchResult(title=title, url=url, content=content))
        # A zero-hit query is a *successful empty* answer (mirrors the legacy
        # Tavily shape); the caller only falls back when the backend reports an
        # error (transport failure, rate-limit exception, blocked page).
        return SearchResultSet(results=results, provider=self.name)
