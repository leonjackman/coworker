"""Shared types for Coworker's pluggable web-search backends.

Engines return :class:`SearchResultSet` and **never raise** — transport and
upstream failures surface as an ``error`` on the result set so the caller can
fall back to the next backend. The ``web_search`` tool serializes a result set
to the exact JSON contract Tavily used to produce, so agent prompts and tool
result parsing are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchResult:
    """One normalized web-search hit."""

    title: str
    url: str
    content: str
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "content": self.content, "score": self.score}


@dataclass(frozen=True)
class SearchResultSet:
    """Outcome of one search against one backend."""

    error: str = ""
    answer: str = ""
    results: list[SearchResult] = field(default_factory=list)
    provider: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.results)


@runtime_checkable
class SearchEngine(Protocol):
    """Minimal backend interface implemented by every provider."""

    name: str

    def search(self, query: str, *, max_results: int, search_depth: str) -> SearchResultSet:  # pragma: no cover - protocol
        ...
