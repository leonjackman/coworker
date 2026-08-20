"""Web search & page-fetch tools for the coworker agent.

Search is backed by the Tavily REST API (https://docs.tavily.com). The API key
is stored in the OS secret store (see :mod:`coworker.secrets`) under service
``coworker.web`` / account ``tavily``. Non-secret configuration (enabled /
provider / max_results / search_depth / fetch_enabled) lives in
``.coworker_settings.json`` under a ``web`` block, mirroring the memory and
retention settings blocks.

``web_search`` returns Tavily's already-cleaned results (title / url / content
plus an optional synthesized answer). ``web_fetch`` is a self-contained HTTP
fetch that converts HTML to Markdown and mirrors the anti-bot retry behaviour
of opencode's ``webfetch`` tool.
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

TAVILY_API_URL = "https://api.tavily.com/search"
_SECRET_SERVICE = "coworker.web"
_SECRET_ACCOUNT = "tavily"

_WEB_FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_WEB_FETCH_TIMEOUT_S = 30.0
_WEB_FETCH_MAX_TIMEOUT_S = 120.0

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

_DEFAULT_WEB_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "tavily",
    "max_results": 8,
    "search_depth": "basic",
    "fetch_enabled": True,
}


@dataclass(frozen=True)
class WebConfig:
    """Resolved (non-secret) web capability settings."""

    enabled: bool = False
    provider: str = "tavily"
    max_results: int = 8
    search_depth: str = "basic"
    fetch_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Any) -> "WebConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            provider=str(data.get("provider", "tavily")),
            max_results=int(data.get("max_results", 8)),
            search_depth=str(data.get("search_depth", "basic")),
            fetch_enabled=bool(data.get("fetch_enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "max_results": self.max_results,
            "search_depth": self.search_depth,
            "fetch_enabled": self.fetch_enabled,
        }


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
    if "provider" in patch and isinstance(patch["provider"], str) and patch["provider"]:
        merged["provider"] = patch["provider"]
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


# ── Tavily secret helpers (Keychain-first, 0600-file fallback) ─────────────

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


# ── Tavily search ──────────────────────────────────────────────────────────

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
            return {"ok": True, "is_image": False, "html": text, "markdown": ""}
        if out_format == "text":
            return {"ok": True, "is_image": False, "markdown": _extract_text(text)}
        try:
            return {"ok": True, "is_image": False, "markdown": _html_to_markdown(text)}
        except Exception:  # noqa: BLE001 - fall back to plain text extraction
            logger.warning("markdownify failed, falling back to text extraction")
            return {"ok": True, "is_image": False, "markdown": _extract_text(text)}

    return {"ok": True, "is_image": False, "markdown": text}


# ── LangChain tools ────────────────────────────────────────────────────────

class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="The search query to look up on the web.")
    max_results: int = Field(default=0, ge=0, le=20, description="Maximum number of results (0 = use configured default, 1–20).")
    search_depth: str = Field(default="", description="'basic' for fast results or 'advanced' for deep research (empty = configured default).")


class WebFetchArgs(BaseModel):
    url: str = Field(description="The http(s) URL to fetch and return as Markdown.")
    timeout: int = Field(default=30, ge=5, le=120, description="Timeout in seconds (5–120).")


def build_web_tools(web_config: WebConfig | None = None, api_key: str | None = None) -> list[Any]:
    """Build ``web_search`` / ``web_fetch`` LangChain tools for the agent.

    Tools are only constructed when the caller passes a resolved config; the
    agent wiring decides whether they are enabled / keyed at all.
    """
    from langchain_core.tools import tool

    cfg = web_config or WebConfig()
    key = api_key

    tools: list[Any] = []

    @tool(args_schema=WebSearchArgs)
    def web_search(query: str, max_results: int = 0, search_depth: str = "") -> str:
        """Search the web for current or public information on a topic."""
        if not key:
            return json.dumps(
                {
                    "error": "Tavily API key is not configured. Tell the user they must configure it "
                    "in Settings → Web (联网设置) → Tavily API Key before web search works.",
                    "error_code": "tavily_key_missing",
                    "answer": "",
                    "results": [],
                },
                ensure_ascii=False,
            )
        result = tavily_search(
            query,
            key,
            max_results=max_results or cfg.max_results,
            search_depth=search_depth or cfg.search_depth,
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
                return json.dumps(
                    {"error": "", "image": result.get("data_url", ""), "markdown": ""},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "", "markdown": result.get("markdown", "")}, ensure_ascii=False)

        tools.append(web_fetch)

    return tools


# ── Runtime-facing helpers ────────────────────────────────────────────────

def resolve_web_tools(data_dir: Path | str | None) -> list[Any]:
    """Web tools for a runtime/sub-agent when web is enabled, else ``[]``.

    The key is optional: ``web_fetch`` works without it, and ``web_search``
    reports the missing-key state to the model (which then prompts the user to
    configure Tavily in Settings → Web). When web is disabled no tools are
    mounted at all — the agent still learns the state from the capability line.
    """
    if data_dir is None:
        return []
    config = load_web_config(data_dir)
    if not config.enabled:
        return []
    return build_web_tools(config, get_tavily_key(data_dir))


def web_capability_status(data_dir: Path | str | None) -> str:
    """Current web capability: ``'disabled'`` | ``'no_key'`` | ``'ok'``."""
    if data_dir is None:
        return "disabled"
    config = load_web_config(data_dir)
    if not config.enabled:
        return "disabled"
    return "no_key" if not tavily_key_configured(data_dir) else "ok"


def web_capability_line(data_dir: Path | str | None) -> str:
    """One-line capability summary injected into the agent's system prompt."""
    status = web_capability_status(data_dir)
    if status == "ok":
        return (
            "Web access is ENABLED: use web_search for current/external information and "
            "web_fetch to read full pages. Cite the sources you used."
        )
    if status == "no_key":
        return (
            "Web access is ENABLED but the Tavily search key is not configured. web_fetch works "
            "without a key; web_search will fail with a 'key not configured' error. If the task "
            "needs live search results, tell the user to configure the Tavily API key in "
            "Settings → Web (联网设置), then retry."
        )
    return (
        "Web access is DISABLED — you have no web_search/web_fetch tools. If the task needs "
        "current or external information, tell the user they must enable 'web access' in "
        "Settings → Web (联网设置) first. Never fabricate URLs or claim you searched."
    )
