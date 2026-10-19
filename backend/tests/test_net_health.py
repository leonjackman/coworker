"""Unit tests for provider health fast-fail."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.net.health import (  # noqa: E402
    record_failure,
    record_success,
    reset_all,
    should_fast_fail,
)
from coworker.net.network_errors import NetworkError, NetworkErrorKind  # noqa: E402


class _Provider:
    id = "p"
    base_url = "http://example:1234"
    model = "m"


def _net(retryable: bool = True) -> NetworkError:
    return NetworkError(
        kind=NetworkErrorKind.SERVER_DISCONNECT,
        retryable=retryable,
        root_cause="x",
        user_hint="hint",
    )


def setup_function():
    reset_all()


def test_no_state_no_fast_fail():
    assert should_fast_fail(_Provider()) is None


def test_threshold_opens_cooldown():
    provider = _Provider()
    record_failure(provider, _net())
    assert should_fast_fail(provider) is None  # threshold is 2 by default
    record_failure(provider, _net())
    assert should_fast_fail(provider) == "hint"


def test_success_clears_state():
    provider = _Provider()
    record_failure(provider, _net())
    record_failure(provider, _net())
    assert should_fast_fail(provider) == "hint"
    record_success(provider)
    assert should_fast_fail(provider) is None


def test_non_retryable_never_opens_cooldown():
    provider = _Provider()
    for _ in range(5):
        record_failure(provider, _net(retryable=False))
    assert should_fast_fail(provider) is None
