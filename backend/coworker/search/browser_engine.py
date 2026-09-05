"""Browser-driven search backend — the built-in embedded browser as a search
engine, with no third-party dependency and no API key.

This backend is only available in the desktop app, where Electron renders a
real Chromium ``<webview>`` in the right-hand panel and registers a loopback
HTTP bridge (see :mod:`coworker.browser.bridge_client`). A search simply:

1. ``navigate`` the live browser to a search-engine results page,
2. wait briefly for JS rendering to settle,
3. run a per-engine ``evaluate`` that extracts ``[{title, url, snippet}]``,
4. normalize the hits into a :class:`~coworker.search.base.SearchResultSet`.

Everything happens in the page the user can see. When the page is blocked
(CAPTCHA/consent/anti-bot) or yields nothing, the engine returns an error so
the caller's fallback chain can degrade to DuckDuckGo.

The embedded browser is a single shared webview, so concurrent searches across
sessions/parallel agents would trample each other; a process-wide lock
serializes bridge-backed searches.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from coworker.browser.bridge_client import BridgeClient
from coworker.logger import get_logger

from .base import SearchResult, SearchResultSet

logger = get_logger(__name__)

#: Engine used when no browser engine is configured (single source of truth).
DEFAULT_BROWSER_ENGINE = "bing"

#: A single browser page rarely holds more useful organic hits; cap the ask.
_MAX_RESULTS_PAGE = 10

#: Time to let a just-navigated page finish lazy rendering before extraction.
_SETTLE_SECONDS = 1.2

#: On the first ``browser_not_attached`` we wait once and retry: the desktop UI
#: auto-opens a browser tab on demand, which takes a moment to mount its guest.
_ATTACH_RETRY_SECONDS = 1.5

#: Extra waits (seconds) between re-extractions when a SERP has loaded but the
#: result nodes are not present yet. Engines render results lazily, and a hidden
#: (collapsed) browser panel throttles JS — a single immediate extract can come
#: back empty. We try a few times with growing delays before calling it blocked.
_RENDER_RETRY_SECONDS = (1.0, 1.6)

#: Serialize bridge-backed searches (one shared webview for all sessions).
_LOCK_TIMEOUT_SECONDS = 25.0
_search_lock = threading.Lock()


def _build_extract_js(containers: str, title_sel: str, snippet_sel: str) -> str:
    """Compose an IIFE that returns a JSON string of extracted organic hits.

    Two-stage extraction: engine-specific result containers first, then a
    generic ``a[href^=http]`` sweep as a fallback for selector drift. A page
    that produces no hits at all is flagged ``blocked`` (anti-bot page, consent
    wall, or a genuinely empty SERP).
    """
    js = r"""
(() => {
  const out = { blocked: false, items: [] };
  const seen = new Set();
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const norm = (u) => { try { const a = new URL(u, location.href); return a.href; } catch (e) { return u || ''; } };
  const push = (t, u, sn) => {
    t = clean(t).slice(0, 300); u = norm(u); sn = clean(sn).slice(0, 600);
    if (!t || !/^https?:/i.test(u) || seen.has(u)) return;
    seen.add(u);
    out.items.push({ title: t, url: u, snippet: sn });
  };
  const boxes = document.querySelectorAll('__CONTAINERS__');
  boxes.forEach((box) => {
    const a = box.querySelector('a[href]');
    if (!a) return;
    const t = box.querySelector('__TITLE__');
    const sn = box.querySelector('__SNIPPET__');
    const anchor = t && t.closest('a') ? t.closest('a') : a;
    push(t ? t.innerText : a.innerText, anchor.href || a.href, sn ? sn.innerText : '');
  });
  if (out.items.length < 3) {
    const sameHost = (u) => { try { return new URL(u).hostname === location.hostname; } catch (e) { return true; } };
    document.querySelectorAll('a[href^="http"]').forEach((a) => {
      if (seen.has(a.href)) return;
      const t = clean(a.innerText);
      if (t.length < 12 || sameHost(a.href)) return;
      const box = a.closest('li, p, div');
      push(t, a.href, box ? box.innerText : '');
    });
  }
  if (!out.items.length) out.blocked = true;
  return JSON.stringify(out);
})()
"""
    for placeholder, value in (
        ("__CONTAINERS__", containers),
        ("__TITLE__", title_sel),
        ("__SNIPPET__", snippet_sel),
    ):
        js = js.replace(placeholder, value)
    return js


#: URL templates + extraction selectors per engine. ``{q}`` / ``{n}`` are filled
#: by :meth:`BrowserSearchEngine.search`. Selectors drift upstream — the generic
#: two-stage sweep in the JS keeps this resilient until a refresh is needed.
ENGINE_SPECS: dict[str, dict[str, str]] = {
    "google": {
        "url": "https://www.google.com/search?q={q}&num={n}&hl=en&gl=us",
        "containers": "#search div.g, #search div[data-snc], div#search div[jscontroller]",
        "title": "h3",
        "snippet": ".VwiC3b, [data-sncf], .IsZvec",
    },
    "bing": {
        "url": "https://www.bing.com/search?q={q}&count={n}&mkt=en-US",
        "containers": "li.b_algo",
        "title": "h2",
        "snippet": ".b_caption p, p",
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q={q}&ia=web",
        "containers": "article[data-testid='result'], div[data-testid='result']",
        "title": "h2, .result__title",
        "snippet": ".result__snippet",
    },
    "baidu": {
        "url": "https://www.baidu.com/s?wd={q}&rn={n}",
        "containers": "div.result-op, div.c-container, div.result",
        "title": "h3",
        "snippet": ".c-abstract, span[class*='content-right']",
    },
    "sogou": {
        "url": "https://www.sogou.com/web?query={q}",
        "containers": "div.vrwrap, div.rb, div[class*='str_info']",
        "title": "h3",
        "snippet": ".fz-mid, div[class*='str_info']",
    },
}

#: Result engines drivable through the embedded browser, derived from the spec
#: table above so the two can never drift apart.
BROWSER_ENGINES: tuple[str, ...] = tuple(ENGINE_SPECS)


def _extract(raw: dict[str, Any]) -> list[SearchResult] | None:
    """Parse the bridge's ``evaluate`` payload; ``None`` on empty/blocked."""
    value = raw.get("result")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    elif isinstance(value, dict):
        parsed = value
    else:
        parsed = None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("blocked"):
        return None
    items = parsed.get("items")
    if not isinstance(items, list) or not items:
        return None
    results: list[SearchResult] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        if not title or not url:
            continue
        results.append(SearchResult(title=title, url=url, content=str(it.get("snippet") or "").strip()))
    return results or None


@dataclass
class BrowserSearchEngine:
    """Search backend that drives the user's embedded browser to a SERP."""

    data_dir: Path | str | None
    engine: str = DEFAULT_BROWSER_ENGINE
    session_id: str = ""
    name: str = "browser"

    def search(self, query: str, *, max_results: int = 8, search_depth: str = "") -> SearchResultSet:
        spec = ENGINE_SPECS.get(self.engine)
        if spec is None:
            return SearchResultSet(
                error=f"Unknown browser engine {self.engine!r} (expected one of {', '.join(BROWSER_ENGINES)})",
                provider=self.name,
            )
        if self.data_dir is None:
            return SearchResultSet(
                error="The embedded browser is only available in the desktop app",
                provider=self.name,
            )

        client = BridgeClient(self.data_dir)
        if client.state().get("error_code"):
            return SearchResultSet(
                error="The embedded browser is not available (start the desktop app and open the browser panel)",
                provider=self.name,
            )

        if not _search_lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
            return SearchResultSet(
                error="The embedded browser is busy with another task; ask the user to retry shortly",
                provider=self.name,
            )
        try:
            return self._search_locked(client, spec, query, max_results)
        finally:
            _search_lock.release()

    def _search_locked(self, client: BridgeClient, spec: dict[str, str], query: str, max_results: int) -> SearchResultSet:
        url = spec["url"].format(q=quote(query), n=max(1, min(_MAX_RESULTS_PAGE, int(max_results or 8))))
        nav = client.navigate(url)
        # The desktop UI auto-opens a browser tab when a search needs one; give
        # that mount a moment to attach, then retry once before surfacing an error.
        if nav.get("error_code") == "browser_not_attached":
            time.sleep(_ATTACH_RETRY_SECONDS)
            nav = client.navigate(url)
        if nav.get("error_code"):
            return SearchResultSet(error=self._friendly_error(nav), provider=self.name)
        page_title = str(nav.get("title") or "")
        time.sleep(_SETTLE_SECONDS)

        results: list[SearchResult] | None = None
        for attempt in range(len(_RENDER_RETRY_SECONDS) + 1):
            raw = client.evaluate(_build_extract_js(spec["containers"], spec["title"], spec["snippet"]))
            if raw.get("error_code"):
                return SearchResultSet(error=self._friendly_error(raw), provider=self.name)
            results = _extract(raw)
            if results:
                break
            if attempt < len(_RENDER_RETRY_SECONDS):
                time.sleep(_RENDER_RETRY_SECONDS[attempt])
        if results:
            return SearchResultSet(results=results[: max(1, min(_MAX_RESULTS_PAGE, int(max_results or 8)))], provider=self.name)

        hint = (page_title or "").strip()[:120]
        return SearchResultSet(
            error=f"Browser search via {self.engine} returned no results"
            + (f" (page: {hint!r})" if hint else "")
            + "; the page may still be rendering or blocked by CAPTCHA/anti-bot",
            provider=self.name,
        )

    @staticmethod
    def _friendly_error(result: dict[str, Any]) -> str:
        """Translate raw bridge failures into actionable messages.

        ``browser_not_attached`` is the common trap: the bridge (Electron) is up
        and registered, but no browser tab/<webview> is mounted — the user has
        the desktop app open without the right-side Browser panel visible.
        """
        code = result.get("error_code")
        raw = str(result.get("error") or "browser failure")
        if code == "browser_not_attached":
            return (
                "Browser search failed: no browser tab is open. Open the right-side Browser "
                "panel (or switch to an existing browser tab) in the desktop app, then retry."
            )
        if code in ("browser_unreachable", "browser_unavailable"):
            return (
                "Browser search failed: the embedded browser is not reachable. Make sure the "
                "desktop app is running and the right-side Browser panel is open."
            )
        return f"Browser search failed: {raw}"
