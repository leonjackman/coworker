"""Bounded retry policy shared by every model-call retry layer.

Before this module three independent retry mechanisms could stack:

1. ``openai`` SDK ``max_retries=2`` (fast connection backoff);
2. ``langchain-openai`` ``stream_chunk_timeout`` (120s per stall);
3. CW's ``StallRetryMiddleware`` (one more full call).

A single proxy disconnect could therefore occupy ~6 minutes. The coordinator
here owns the *app-level* retry decision and a hard total budget, so a broken
link fails within a predictable window instead of looking like an endless
"network congested" spinner.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .network_errors import NetworkError, NetworkErrorKind


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RetryPolicy:
    """Bounds for one logical model call (not the whole agent turn)."""

    max_attempts: int = 2
    total_budget_s: float = 60.0
    backoff: tuple[float, ...] = (0.5, 1.0, 2.0)

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        max_attempts = max(1, _env_int("COWORKER_LLM_MAX_ATTEMPTS", 2))
        total_budget = max(1.0, _env_float("COWORKER_LLM_RETRY_BUDGET_S", 60.0))
        return cls(max_attempts=max_attempts, total_budget_s=total_budget)


class NetworkRetryCoordinator:
    """Stateful retry gate for a single model call.

    Usage::

        coordinator = NetworkRetryCoordinator()
        while True:
            try:
                return await handler(request)
            except Exception as exc:
                net = classify_network_error(exc, proxy=proxy)
                if not coordinator.should_retry(net):
                    raise
                await asyncio.sleep(coordinator.begin_retry())
    """

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self.policy = policy or RetryPolicy.from_env()
        self._started = time.monotonic()
        self.attempt = 0

    def remaining_budget(self) -> float:
        return max(0.0, self.policy.total_budget_s - (time.monotonic() - self._started))

    def should_retry(self, net: NetworkError) -> bool:
        if not net.retryable:
            return False
        # ``attempt`` counts retries already performed; the first call is 0.
        if self.attempt >= self.policy.max_attempts - 1:
            return False
        if self.remaining_budget() <= 0:
            return False
        # A proxy that dropped the response almost never recovers on a fast
        # retry. Allow exactly one so the user gets a prompt, actionable error
        # instead of repeatedly hitting the same broken path.
        if net.kind is NetworkErrorKind.PROXY_DISCONNECT and self.attempt >= 1:
            return False
        return True

    def begin_retry(self) -> float:
        """Advance the attempt counter and return the backoff (seconds)."""
        index = min(self.attempt, len(self.policy.backoff) - 1)
        delay = self.policy.backoff[index] if self.policy.backoff else 0.0
        self.attempt += 1
        return max(0.0, min(delay, self.remaining_budget()))
