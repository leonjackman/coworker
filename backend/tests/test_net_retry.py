"""Unit tests for the bounded retry coordinator."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.net.network_errors import NetworkError, NetworkErrorKind  # noqa: E402
from coworker.net.retry import NetworkRetryCoordinator, RetryPolicy  # noqa: E402


def _net(kind: NetworkErrorKind, retryable: bool = True) -> NetworkError:
    return NetworkError(kind=kind, retryable=retryable, root_cause="x")


def test_retryable_kind_respects_max_attempts():
    coordinator = NetworkRetryCoordinator(RetryPolicy(max_attempts=2, total_budget_s=60))
    net = _net(NetworkErrorKind.SERVER_DISCONNECT)
    assert coordinator.should_retry(net) is True
    coordinator.begin_retry()
    assert coordinator.should_retry(net) is False


def test_proxy_disconnect_capped_at_one_retry():
    coordinator = NetworkRetryCoordinator(RetryPolicy(max_attempts=5, total_budget_s=60))
    net = _net(NetworkErrorKind.PROXY_DISCONNECT)
    assert coordinator.should_retry(net) is True
    coordinator.begin_retry()
    # A broken proxy does not recover on a fast retry; fail fast.
    assert coordinator.should_retry(net) is False


def test_non_retryable_never_retries():
    coordinator = NetworkRetryCoordinator(RetryPolicy(max_attempts=5, total_budget_s=60))
    assert coordinator.should_retry(_net(NetworkErrorKind.AUTH, retryable=False)) is False


def test_budget_exhaustion_blocks_retry():
    coordinator = NetworkRetryCoordinator(RetryPolicy(max_attempts=5, total_budget_s=0.01))
    time.sleep(0.02)
    assert coordinator.should_retry(_net(NetworkErrorKind.SERVER_DISCONNECT)) is False


def test_backoff_clamped_to_last_value():
    coordinator = NetworkRetryCoordinator(
        RetryPolicy(max_attempts=5, total_budget_s=60, backoff=(0.1, 0.2))
    )
    assert coordinator.begin_retry() == 0.1
    assert coordinator.begin_retry() == 0.2
    assert coordinator.begin_retry() == 0.2
