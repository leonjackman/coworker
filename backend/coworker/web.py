"""Web search & page-fetch tools for the coworker agent.

Search is provided by :mod:`coworker.search` — a pluggable set of backends
(Tavily / DuckDuckGo / the embedded browser) with a fixed fallback chain, so
the agent no longer depends on a single provider. The default (DuckDuckGo)
needs no API key. Non-secret configuration (enabled / provider / browser_engine
/ max_results / search_depth / fetch_enabled) lives in
``.coworker_settings.json`` under a ``web`` block, mirroring the memory and
retention settings blocks. A Tavily API key (when that provider is selected)
is stored in the OS secret store under service ``coworker.web`` / account
``tavily``.

``web_search`` returns normalized results (title / url / content plus an
optional synthesized answer from Tavily). ``web_fetch`` is a self-contained
HTTP fetch that converts HTML to Markdown and mirrors the anti-bot retry
behaviour of opencode's ``webfetch`` tool.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from coworker.logger import get_logger

logger = get_logger(__name__)

# Tavily helpers live in the search backend package but were historically
# exposed from here; re-export for callers (main.py settings API, dashboard).
from coworker.search.tavily_engine import (  # noqa: E402
    delete_tavily_key,
    get_tavily_key,
    set_tavily_key,
    tavily_key_configured,
    tavily_search,
)

# Canonical provider / engine lists and defaults live in coworker.search; import
# them (do not redefine) so the settings layer and the search backends agree.
from coworker.search import (  # noqa: E402
    BROWSER_ENGINES as ALLOWED_BROWSER_ENGINES,
    DEFAULT_BROWSER_ENGINE,
    DEFAULT_PROVIDER,
    PROVIDERS as ALLOWED_PROVIDERS,
    provider_capability,
)

_WEB_FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_WEB_FETCH_TIMEOUT_S = 30.0
_WEB_FETCH_MAX_TIMEOUT_S = 120.0

# Hard cap on the readable content a single fetch returns to the model. The
# markdownify path previously had NO cap — one heavy page could dump hundreds of
# KB into a tool result and blow the context window before any trim could act.
# 100k chars ≈ 26–30k tokens: ample for a full article, bounded for the window.
WEB_FETCH_MAX_CHARS = 100_000
_WEB_FETCH_TRUNCATION_NOTE = "\n[content truncated by Coworker to fit context]"


def _cap_fetch_text(text: str) -> str:
    if len(text) <= WEB_FETCH_MAX_CHARS:
        return text
    return text[:WEB_FETCH_MAX_CHARS] + _WEB_FETCH_TRUNCATION_NOTE

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

@dataclass(frozen=True)
class WebConfig:
    """Resolved (non-secret) web capability settings.

    Field defaults are the single source of truth for the ``web`` settings
    block (``_DEFAULT_WEB_CONFIG`` is derived from here below).
    """

    enabled: bool = False
    provider: str = DEFAULT_PROVIDER
    browser_engine: str = DEFAULT_BROWSER_ENGINE
    max_results: int = 8
    search_depth: str = "basic"
    fetch_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "WebConfig":
        if not isinstance(data, dict):
            return cls()
        provider = str(data.get("provider", DEFAULT_PROVIDER))
        if provider not in ALLOWED_PROVIDERS:
            provider = DEFAULT_PROVIDER
        browser_engine = str(data.get("browser_engine", DEFAULT_BROWSER_ENGINE))
        if browser_engine not in ALLOWED_BROWSER_ENGINES:
            browser_engine = DEFAULT_BROWSER_ENGINE
        return cls(
            enabled=bool(data.get("enabled", False)),
            provider=provider,
            browser_engine=browser_engine,
            max_results=int(data.get("max_results", 8)),
            search_depth=str(data.get("search_depth", "basic")),
            fetch_enabled=bool(data.get("fetch_enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "browser_engine": self.browser_engine,
            "max_results": self.max_results,
            "search_depth": self.search_depth,
            "fetch_enabled": self.fetch_enabled,
        }


#: Defaults for the stored ``web`` block, derived from :class:`WebConfig` so
#: dataclass and settings-file defaults can never drift apart.
_DEFAULT_WEB_CONFIG: dict[str, Any] = WebConfig().to_dict()


def _settings_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / ".coworker_settings.json"


def _read_settings_file(data_dir: Path | str) -> dict[str, Any]:
    try:
        raw = _settings_path(data_dir).read_text(encoding="utf-8") or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupt/missing file falls back to empty
        return {}


def _write_settings_file(data_dir: Path | str, data: dict[str, Any]) -> None:
    from coworker.atomicio import atomic_write_json

    atomic_write_json(_settings_path(data_dir), data)


def load_web_config(data_dir: Path | str) -> WebConfig:
    """Read the ``web`` block from ``.coworker_settings.json`` (defaults win)."""
    return WebConfig.from_dict(_read_settings_file(data_dir).get("web"))


def read_web_block(data_dir: Path | str) -> dict[str, Any]:
    """Return the stored ``web`` block, filled with defaults for absent keys."""
    block = _read_settings_file(data_dir).get("web")
    if not isinstance(block, dict):
        block = {}
    merged = dict(_DEFAULT_WEB_CONFIG)
    merged.update({k: v for k, v in block.items() if k in _DEFAULT_WEB_CONFIG})
    return merged


def write_web_block(data_dir: Path | str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial ``web`` update into the settings file atomically.

    Only known keys are accepted; values are clamped to sane ranges. Returns
    the resulting merged block.
    """
    data = _read_settings_file(data_dir)
    block = data.get("web")
    if not isinstance(block, dict):
        block = {}
    merged = dict(_DEFAULT_WEB_CONFIG)
    merged.update({k: v for k, v in block.items() if k in _DEFAULT_WEB_CONFIG})

    if "enabled" in patch:
        merged["enabled"] = bool(patch["enabled"])
    if "provider" in patch and patch["provider"] in ALLOWED_PROVIDERS:
        merged["provider"] = patch["provider"]
    if "browser_engine" in patch and patch["browser_engine"] in ALLOWED_BROWSER_ENGINES:
        merged["browser_engine"] = patch["browser_engine"]
    if "max_results" in patch:
        try:
            merged["max_results"] = max(1, min(20, int(patch["max_results"])))
        except (TypeError, ValueError):
            pass
    if "search_depth" in patch and patch["search_depth"] in ("basic", "advanced"):
        merged["search_depth"] = patch["search_depth"]
    if "fetch_enabled" in patch:
        merged["fetch_enabled"] = bool(patch["fetch_enabled"])

    data["web"] = merged
    _write_settings_file(data_dir, data)
    return merged


# ── Page fetch (web_fetch) ─────────────────────────────────────────────────

def _html_to_markdown(html: str) -> str:
    import re

    from markdownify import markdownify as to_md

    # Fully remove script/style/meta blocks (markdownify's `strip` keeps their
    # inner text, leaking JS/CSS into the model's context).
    cleaned = re.sub(
        r"<\s*(script|style|noscript|title|iframe|svg|head)\b[^>]*>.*?</\s*\1\s*>",
        "",
        html,
        flags=re.S | re.I,
    )
    return to_md(
        cleaned,
        heading_style="ATX",
        strip=["script", "style", "meta", "link", "noscript", "iframe", "svg", "nav", "header", "footer"],
    )


def _extract_text(html: str) -> str:
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._skip_depth = 0
            self._parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if self._skip_depth > 0 or tag in ("script", "style", "noscript", "iframe", "object", "embed"):
                self._skip_depth += 1

        def handle_endtag(self, tag: str) -> None:
            if self._skip_depth > 0:
                self._skip_depth -= 1

        def handle_data(self, data: str) -> None:
            if self._skip_depth == 0 and data.strip():
                self._parts.append(data.strip())

        def text(self) -> str:
            return "\n".join(self._parts)

    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML must never break fetch
        pass
    return parser.text()[:200_000]


def fetch_page(url: str, *, timeout_s: float = 30.0, out_format: str = "markdown") -> dict[str, Any]:
    """Fetch a URL and return content for the model.

    Never raises; returns a dict with ``ok`` + either ``markdown``/``text`` or
    a base64 ``data_url`` for images, plus ``error`` on failure.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"ok": False, "error": "URL must start with http:// or https://", "markdown": ""}

    timeout_s = max(5.0, min(float(timeout_s), _WEB_FETCH_MAX_TIMEOUT_S))
    accept = {
        "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
        "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
        "html": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }.get(out_format, "*/*")

    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _attempt() -> httpx.Response:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout_s) as client:
            response = client.get(url)
            # Retry with an honest UA once when Cloudflare bot detection kicked in.
            if (
                response.status_code == 403
                and response.headers.get("cf-mitigated") == "challenge"
            ):
                with httpx.Client(
                    headers={**headers, "User-Agent": "coworker"},
                    follow_redirects=True,
                    timeout=timeout_s,
                ) as honest:
                    return honest.get(url)
            return response

    try:
        response = _attempt()
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        return {"ok": False, "error": f"Request failed: {exc}", "markdown": ""}

    if response.status_code >= 400:
        return {
            "ok": False,
            "error": f"HTTP {response.status_code}",
            "markdown": "",
        }

    content_type = response.headers.get("content-type", "").lower()
    mime = content_type.split(";")[0].strip()
    body = response.content

    if len(body) > _WEB_FETCH_MAX_BYTES:
        return {"ok": False, "error": "Response too large (exceeds 5MB limit)", "markdown": ""}

    if mime.startswith("image/"):
        return {
            "ok": True,
            "is_image": True,
            "data_url": f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}",
            "markdown": "",
        }

    text = body.decode("utf-8", errors="replace")

    if mime == "text/html" or "<html" in text[:2000].lower():
        if out_format == "html":
            return {"ok": True, "is_image": False, "html": _cap_fetch_text(text), "markdown": ""}
        if out_format == "text":
            return {"ok": True, "is_image": False, "markdown": _cap_fetch_text(_extract_text(text))}
        try:
            return {"ok": True, "is_image": False, "markdown": _cap_fetch_text(_html_to_markdown(text))}
        except Exception:  # noqa: BLE001 - fall back to plain text extraction
            logger.warning("markdownify failed, falling back to text extraction")
            return {"ok": True, "is_image": False, "markdown": _cap_fetch_text(_extract_text(text))}

    return {"ok": True, "is_image": False, "markdown": _cap_fetch_text(text)}


# ── LangChain tools ────────────────────────────────────────────────────────

class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="The search query to look up on the web.")
    max_results: int = Field(default=0, ge=0, le=20, description="Maximum number of results (0 = use configured default, 1–20).")
    search_depth: str = Field(default="", description="'basic' for fast results or 'advanced' for deep research (empty = configured default).")
    provider: str = Field(
        default="",
        description=(
            "Backend to use for THIS search only: 'tavily' | 'duckduckgo' | 'browser'. "
            "Empty = the user's configured default in Settings → Web. Honor an explicit user "
            "request such as 'search in the built-in browser' by passing 'browser'."
        ),
    )


class WebFetchArgs(BaseModel):
    url: str = Field(description="The http(s) URL to fetch and return as Markdown.")
    timeout: int = Field(default=30, ge=5, le=120, description="Timeout in seconds (5–120).")


def build_web_tools(web_config: WebConfig | None = None, api_key: str | None = None, *, vision: bool = False, data_dir: Path | str | None = None, session_id: str = "") -> list[Any]:
    """Build ``web_search`` / ``web_fetch`` LangChain tools for the agent.

    Tools are only constructed when the caller passes a resolved config; the
    agent wiring decides whether they are enabled at all. ``api_key`` is kept
    for signature compatibility but no longer consulted — the ``web_search``
    tool resolves provider + key lazily on every call (via
    :func:`coworker.search.run_web_search`), so settings changes take effect
    without rebuilding the cached graph. ``vision`` decides how fetched IMAGES
    are delivered: vision providers get a native ``image_url`` block, text-only
    providers get the bytes saved to disk and a path back — base64 never enters
    the context as text.
    """
    from langchain_core.tools import tool

    cfg = web_config or WebConfig()

    tools: list[Any] = []

    @tool(args_schema=WebSearchArgs)
    def web_search(query: str, max_results: int = 0, search_depth: str = "", provider: str = "") -> str:
        """Search the web for current or public information on a topic.

        Uses the configured provider (Tavily / DuckDuckGo / embedded browser);
        on failure it automatically falls back through the other free backends.
        ``provider`` pins this single search to a backend (e.g. 'browser' when
        the user asks to search in the built-in browser).
        """
        if data_dir is None:
            return json.dumps(
                {"error": "Web search is not available (no data directory).", "error_code": "no_backend", "answer": "", "results": []},
                ensure_ascii=False,
            )
        # Re-read config on every call so provider / engine / key changes apply
        # immediately, even while the compiled graph is cached.
        live_cfg = load_web_config(data_dir)
        if not live_cfg.enabled:
            return json.dumps(
                {"error": "Web access is disabled. Tell the user to enable 'web access' in Settings → Web (联网设置).", "answer": "", "results": []},
                ensure_ascii=False,
            )
        from coworker.search import run_web_search

        result = run_web_search(
            live_cfg,
            data_dir,
            query=query,
            max_results=max_results or live_cfg.max_results,
            search_depth=search_depth or live_cfg.search_depth,
            session_id=session_id,
            provider=provider,
        )
        return json.dumps(result, ensure_ascii=False)

    tools.append(web_search)

    if cfg.fetch_enabled:

        @tool(args_schema=WebFetchArgs)
        def web_fetch(url: str, timeout: int = 30) -> str:
            """Fetch a web page and return its readable content as Markdown."""
            result = fetch_page(url, timeout_s=float(timeout), out_format="markdown")
            if not result.get("ok"):
                return json.dumps({"error": result.get("error", "fetch failed"), "markdown": ""}, ensure_ascii=False)
            if result.get("is_image"):
                data_url = str(result.get("data_url", ""))
                if vision and data_url:
                    return [
                        {"type": "text", "text": f"Fetched image from {url}."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                # Text-only providers: externalize the bytes, return a path.
                try:
                    from coworker.browser.bridge_client import _save_screenshot

                    saved = _save_screenshot(data_url, data_dir, session_id) if data_url else None
                except Exception:  # noqa: BLE001 - externalization must never break fetch
                    saved = None
                if saved:
                    return json.dumps(
                        {"error": "", "image_saved_to": saved, "markdown": ""},
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {"error": "", "image": "", "markdown": "(image fetched but not delivered: model has no vision)"},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "", "markdown": result.get("markdown", "")}, ensure_ascii=False)

        tools.append(web_fetch)

    return tools


# ── Runtime-facing helpers ────────────────────────────────────────────────

def resolve_web_tools(data_dir: Path | str | None, *, vision: bool = False, session_id: str = "") -> list[Any]:
    """Web tools for a runtime/sub-agent when web is enabled, else ``[]``.

    The configured provider (Tavily / DuckDuckGo / embedded browser) is
    resolved lazily inside the tool on every call, so provider or key changes
    take effect without rebuilding the cached agent graph. When web is disabled
    no tools are mounted at all — the agent still learns the state from the
    capability line.
    """
    if data_dir is None:
        return []
    config = load_web_config(data_dir)
    if not config.enabled:
        return []
    return build_web_tools(config, None, vision=vision, data_dir=data_dir, session_id=session_id)


def web_capability_status(data_dir: Path | str | None) -> str:
    """Current web capability: ``'disabled'`` | ``'ok'`` | ``'no_key'`` |
    ``'browser_unavailable'``.

    ``ok`` means the *configured* provider is ready. ``no_key`` applies only to
    the Tavily provider (DuckDuckGo and the browser need no key);
    ``browser_unavailable`` applies when the embedded browser provider is
    selected but the desktop bridge is not registered. The per-provider
    readiness rules live in :func:`coworker.search.provider_capability`.
    """
    if data_dir is None:
        return "disabled"
    config = load_web_config(data_dir)
    if not config.enabled:
        return "disabled"
    return provider_capability(config.provider, data_dir)


def _web_provider_label(config: WebConfig) -> str:
    if config.provider == "tavily":
        return "Tavily (best quality)"
    if config.provider == "browser":
        return f"the embedded browser ({config.browser_engine})"
    return "DuckDuckGo (free, no key)"


def web_capability_line(data_dir: Path | str | None) -> str:
    """One-line capability summary injected into the agent's system prompt."""
    status = web_capability_status(data_dir)
    if status == "disabled":
        return (
            "Web access is DISABLED — you have no web_search/web_fetch tools. If the task needs "
            "current or external information, tell the user they must enable 'web access' in "
            "Settings → Web (联网设置) first. Never fabricate URLs or claim you searched."
        )
    config = load_web_config(data_dir) if data_dir is not None else WebConfig()
    provider = _web_provider_label(config)
    base = (
        f"Web access is ENABLED (search provider: {provider}). Use web_search for current/external "
        "information and web_fetch to read full pages. Cite the sources you used. "
        "If the user asks for a specific way to search (e.g. 'use the built-in browser', "
        "'use DuckDuckGo'), pass that backend via web_search's provider argument. "
    )
    if status == "ok":
        if config.provider == "browser":
            base += (
                "The search runs live in the user's embedded browser. If a search is blocked or empty, "
                "web_search automatically falls back to free DuckDuckGo results — the result JSON "
                "includes provider_used/fell_back so you know which backend served it."
            )
        else:
            base += (
                "If the configured provider fails, web_search automatically falls back to another free "
                "backend; the result JSON includes provider_used/fell_back so you know which served it."
            )
        return base
    if status == "no_key":
        return (
            base
            + "The Tavily key is not configured, so web_search uses the free DuckDuckGo fallback "
            "(result quality is basic; there is no AI answer). web_fetch works fully. If the user wants "
            "higher-quality results they can add a Tavily API key in Settings → Web (联网设置)."
        )
    # browser_unavailable
    return (
        base
        + "The embedded browser is not reachable (this desktop app's browser panel must be open), so "
        "web_search currently uses the free DuckDuckGo fallback. If the user needs browser-driven "
        "search, they should open the right-side Browser panel."
    )
