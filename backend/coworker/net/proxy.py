"""One proxy decision for every outbound HTTP stack.

Why this module exists
----------------------
On CPython 3.11 ``urllib.request.getproxies()`` (the surface ``httpx`` reads)
does **not** return the macOS System Configuration ``ExceptionsList``. We
verified this on a machine whose system proxy bypassed ``47.76.63.128:1111``:
``getproxies()`` returned only ``http``/``https`` and no ``no`` key, while
``urllib.request.proxy_bypass("47.76.63.128")`` returned ``True``. The two
HTTP stacks in the same process therefore disagreed:

* provider probes (``urllib``) went **direct**;
* the real LLM stream (``httpx``) was forced through the local proxy and died
  with ``RemoteProtocolError: Server disconnected without sending a response``.

This module makes the decision once, for both stacks, with an explicit order:

    provider override > global override > NO_PROXY env > macOS ExceptionsList
    > visible system/env proxy > direct

It also strips the port from bypass entries. ``httpx``'s own matcher treats
``47.76.63.128:1111`` as a *hostname suffix* and can never match a URL host
(URLs never carry the port in ``url.host``), which is why a port-qualified
exception silently stops bypassing the proxy.
"""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlparse


class ProxyMode(str, Enum):
    """How an endpoint's proxy is chosen."""

    AUTO = "auto"      # honor bypass lists, then the visible system/env proxy
    DIRECT = "direct"  # never use a proxy
    PROXY = "proxy"    # always use ``proxy_url``


@dataclass(frozen=True)
class ProxyDecision:
    """The resolved proxy for one endpoint.

    ``proxy_url is None`` means "connect directly". ``reason``/``source`` are
    diagnostic only and are safe to log (credentials are redacted by
    :func:`describe`).
    """

    mode: ProxyMode
    proxy_url: str | None
    reason: str
    source: str

    @property
    def is_direct(self) -> bool:
        return self.proxy_url is None


_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_GLOBAL_MODE_ENV = "COWORKER_LLM_PROXY_MODE"
_GLOBAL_URL_ENV = "COWORKER_LLM_PROXY_URL"
# Legacy/ecosystem names that users may already have set. ``OPENAI_PROXY`` is
# the name langchain-openai documents.
_LEGACY_URL_ENVS = ("OPENAI_PROXY",)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _normalize_proxy_url(value: str | None) -> str | None:
    """Return a scheme-qualified proxy URL, or ``None`` when empty."""
    raw = (value or "").strip()
    if not raw:
        return None
    return raw if "://" in raw else f"http://{raw}"


def _split_host_port(entry: str) -> str:
    """Host part of a bypass entry, dropping a port.

    ``47.76.63.128:1111`` -> ``47.76.63.128``; ``[::1]:8080`` -> ``::1``;
    a bare IPv6 (multiple colons) is returned unchanged.
    """
    value = entry.strip()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _host_matches(host: str, entries: Iterable[str]) -> bool:
    """Whether ``host`` matches any bypass entry (``*``, suffix, exact)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    for raw in entries:
        pattern = str(raw or "").strip().lower().rstrip(".")
        if not pattern:
            continue
        if pattern == "*":
            return True
        candidate = _split_host_port(pattern)
        if not candidate or "/" in candidate:  # CIDR is not supported by httpx
            continue
        candidate = candidate.lstrip("*").lstrip(".")
        if not candidate:
            continue
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def _env_no_proxy_entries() -> list[str]:
    raw = ", ".join(
        filter(None, [os.environ.get("NO_PROXY", ""), os.environ.get("no_proxy", "")])
    )
    return [part for part in raw.split(",") if part.strip()]


def is_loopback_host(host: str) -> bool:
    """True for localhost / loopback literals (local bridge & MCP shims)."""
    value = (host or "").strip().lower().strip("[]")
    if not value:
        return False
    if value in {"localhost", "::1", "0.0.0.0"}:
        return True
    if value.endswith(".localhost"):
        return True
    if value.startswith("127."):
        return True
    return False


def macos_system_proxy_exceptions() -> tuple[str, ...]:
    """The macOS System Configuration proxy bypass list.

    ``_scproxy`` is a private stdlib module only present on macOS; any absence
    or failure degrades to an empty list (i.e. no extra bypasses).
    """
    try:
        import _scproxy  # type: ignore[import-not-found]
    except Exception:
        return ()
    try:
        settings = _scproxy._get_proxy_settings()  # type: ignore[attr-defined]
        return tuple(str(item) for item in (settings.get("exceptions") or ()))
    except Exception:
        return ()


def _visible_proxy_url() -> str | None:
    """The first proxy visible via env vars or the OS configuration."""
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = _normalize_proxy_url(os.environ.get(name))
        if value:
            return value
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return None
    for key in ("all", "https", "http"):
        value = _normalize_proxy_url(proxies.get(key))
        if value:
            return value
    return None


def _global_mode() -> ProxyMode:
    raw = (os.environ.get(_GLOBAL_MODE_ENV, "") or "").strip().lower()
    if not raw:
        return ProxyMode.AUTO
    try:
        return ProxyMode(raw)
    except ValueError:
        return ProxyMode.AUTO


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    if obj is None:
        return default
    try:
        value = getattr(obj, name, default)
    except Exception:
        return default
    return default if value is None else value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_proxy(base_url: str | None, provider: Any | None = None) -> ProxyDecision:
    """Resolve the proxy decision for ``base_url``.

    ``provider`` may be any object exposing ``proxy_mode`` / ``proxy_url``
    (e.g. :class:`coworker.providers.models.ProviderEntry`); duck typing keeps
    this module a dependency leaf.
    """
    host = ""
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower()
    except Exception:
        host = ""

    # 1. Provider-level override (per-endpoint, explicit user intent).
    provider_mode_raw = str(_attr(provider, "proxy_mode", "") or "").strip().lower()
    provider_url = _normalize_proxy_url(str(_attr(provider, "proxy_url", "") or ""))
    if provider_mode_raw:
        try:
            mode = ProxyMode(provider_mode_raw)
        except ValueError:
            mode = ProxyMode.AUTO
        if mode is ProxyMode.DIRECT:
            return ProxyDecision(ProxyMode.DIRECT, None, "provider-direct", "provider")
        if mode is ProxyMode.PROXY and provider_url:
            return ProxyDecision(ProxyMode.PROXY, provider_url, "provider-proxy", "provider")
    elif provider_url:
        return ProxyDecision(ProxyMode.PROXY, provider_url, "provider-proxy", "provider")

    # 2. Loopback is always direct. Local bridges (browser / computer) and a
    #    local MCP shim must never be routed through a forward proxy, which may
    #    refuse, mangle, or auth-gate them. An explicit provider override above
    #    still wins (a user can force a proxy if they really need one).
    if is_loopback_host(host):
        return ProxyDecision(ProxyMode.AUTO, None, "loopback", "local")

    # 3. Global override.
    global_mode = _global_mode()
    global_url = _normalize_proxy_url(os.environ.get(_GLOBAL_URL_ENV))
    if global_mode is ProxyMode.DIRECT:
        return ProxyDecision(ProxyMode.DIRECT, None, "global-direct", "global")
    if global_mode is ProxyMode.PROXY and global_url:
        return ProxyDecision(ProxyMode.PROXY, global_url, "global-proxy", "global")
    if global_url:
        return ProxyDecision(ProxyMode.PROXY, global_url, "global-proxy", "global")

    # 4. Legacy ecosystem override.
    for env_name in _LEGACY_URL_ENVS:
        explicit = _normalize_proxy_url(os.environ.get(env_name))
        if explicit:
            return ProxyDecision(ProxyMode.PROXY, explicit, f"env-{env_name.lower()}", "env")

    # 5. Per-endpoint bypass lists. Env first, then the macOS system list that
    #    Python's getproxies() drops on the floor.
    if host and _host_matches(host, _env_no_proxy_entries()):
        return ProxyDecision(ProxyMode.AUTO, None, "no-proxy-env", "env")
    if host and _host_matches(host, macos_system_proxy_exceptions()):
        return ProxyDecision(ProxyMode.AUTO, None, "system-proxy-exception", "system")

    # 6. A visible system/env proxy.
    visible = _visible_proxy_url()
    if visible:
        return ProxyDecision(ProxyMode.AUTO, visible, "system-proxy", "system")

    # 7. No proxy anywhere.
    return ProxyDecision(ProxyMode.AUTO, None, "no-proxy", "default")


def httpx_proxy_kwargs(decision: ProxyDecision) -> dict[str, Any]:
    """``httpx.AsyncClient`` kwargs that pin the wire behaviour to ``decision``.

    ``trust_env=False`` is essential: we already resolved every env/system
    input, so letting httpx re-read them would reintroduce the mismatch this
    module exists to remove.
    """
    return {"proxy": decision.proxy_url, "trust_env": False}


def urllib_opener(decision: ProxyDecision) -> urllib.request.OpenerDirector:
    """A urllib opener matching ``decision`` exactly.

    ``ProxyHandler({})`` disables proxies entirely; an explicit mapping routes
    both schemes through the chosen proxy. urllib's own auto-detection is never
    used, so provider probes and LLM streams cannot disagree again.
    """
    if decision.proxy_url:
        handler = urllib.request.ProxyHandler(
            {"http": decision.proxy_url, "https": decision.proxy_url}
        )
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def describe(decision: ProxyDecision) -> str:
    """Compact, log-safe description (proxy credentials are redacted)."""
    target = "direct" if decision.proxy_url is None else _redact_url(decision.proxy_url)
    return f"{decision.mode.value}:{target} ({decision.reason})"


def _redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return url
