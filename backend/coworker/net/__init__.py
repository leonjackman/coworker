"""Unified outbound-network policy for Coworker.

This package is a leaf in the import DAG: it depends only on the standard
library (plus optional third-party libraries imported lazily inside
functions), so every layer — LLM clients, provider probes, MCP bridges, web
tools — can share the same proxy/error/retry decisions without import cycles.
"""

from .network_errors import NetworkError, NetworkErrorKind, classify_network_error
from .proxy import (
    ProxyDecision,
    ProxyMode,
    describe,
    httpx_proxy_kwargs,
    macos_system_proxy_exceptions,
    resolve_proxy,
    urllib_opener,
)
from .retry import NetworkRetryCoordinator, RetryPolicy

__all__ = [
    "NetworkError",
    "NetworkErrorKind",
    "NetworkRetryCoordinator",
    "ProxyDecision",
    "ProxyMode",
    "RetryPolicy",
    "classify_network_error",
    "describe",
    "httpx_proxy_kwargs",
    "macos_system_proxy_exceptions",
    "resolve_proxy",
    "urllib_opener",
]
