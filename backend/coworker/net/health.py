"""Provider health tracking: fast-fail recently broken endpoints.

``ProviderManager.cached_context_error`` only covers *local* providers and is
derived from a context-window probe, so a cloud provider that keeps dropping
streams had no fast-fail at all — every send paid the full retry budget before
showing an error.

This tracker records normalized failures for ANY provider and opens a
short, exponentially growing cooldown after a few consecutive failures. The
next send is refused immediately with the actionable hint, instead of making
the user wait again. A success clears the state.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .network_errors import NetworkError


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


_FAILURE_THRESHOLD = max(1, _env_int("COWORKER_PROVIDER_FAILURE_THRESHOLD", 2))
_BASE_COOLDOWN_S = max(1.0, _env_float("COWORKER_PROVIDER_COOLDOWN_S", 15.0))
_MAX_COOLDOWN_S = max(_BASE_COOLDOWN_S, _env_float("COWORKER_PROVIDER_MAX_COOLDOWN_S", 300.0))


@dataclass
class _HealthState:
    consecutive_failures: int = 0
    unavailable_until: float = 0.0
    last_kind: str = ""
    last_hint: str = ""


_STATE: dict[str, _HealthState] = {}


def _key(provider: object) -> str:
    return "{}|{}|{}".format(
        getattr(provider, "id", "") or "",
        getattr(provider, "base_url", "") or "",
        getattr(provider, "model", "") or "",
    )


def record_success(provider: object | None) -> None:
    if provider is not None:
        _STATE.pop(_key(provider), None)


def record_failure(provider: object | None, net: NetworkError) -> None:
    if provider is None:
        return
    state = _STATE.setdefault(_key(provider), _HealthState())
    state.consecutive_failures += 1
    state.last_kind = net.kind.value
    state.last_hint = net.user_hint
    if state.consecutive_failures >= _FAILURE_THRESHOLD and net.retryable:
        exponent = min(state.consecutive_failures - _FAILURE_THRESHOLD, 5)
        cooldown = min(_MAX_COOLDOWN_S, _BASE_COOLDOWN_S * (2 ** exponent))
        state.unavailable_until = time.monotonic() + cooldown


def should_fast_fail(provider: object | None) -> str | None:
    """Return the user-facing reason when the provider is in cooldown."""
    if provider is None:
        return None
    state = _STATE.get(_key(provider))
    if state is None:
        return None
    if time.monotonic() < state.unavailable_until:
        return state.last_hint or "LLM 服务暂不可用，请稍后重试。"
    return None


def reset_all() -> None:
    """Clear every tracked state (tests / provider config changes)."""
    _STATE.clear()
