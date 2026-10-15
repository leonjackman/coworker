"""How an in-flight stream terminated: user Stop vs client disconnect vs error.

Kept dependency-free (no app state) so it can be unit-tested in isolation and
imported from both the streaming pipeline and the endpoints.

An explicit Stop (the ``/sessions/{id}/stop`` endpoint) records ``"user"``; a
stream that dies from a socket disconnect or an internal cancel has no record
and is classified ``"client"``. Emitting a plain ``stopped`` event (instead of an
``error``) is what stops a normal Stop/disconnect from surfacing as the
misleading "无法连接到 CoWorker 本地服务".
"""

from __future__ import annotations

import asyncio
from typing import Any

_stop_reasons: dict[str, str] = {}


def _mark_stop_reason(session_id: str, reason: str) -> None:
    _stop_reasons[session_id] = reason


def pop_stop_reason(session_id: str) -> str | None:
    """Consume and return the recorded stop reason ("user"/"client"), if any."""
    return _stop_reasons.pop(session_id, None)


def cancellation_event(session_id: str, exc: BaseException) -> dict[str, Any] | None:
    """Return a terminal ``stopped`` event when ``exc`` is a cancellation.

    A Stop / client disconnect is not a failure: emitting an ``error`` for it is
    what made the UI shout "无法连接到 CoWorker 本地服务". ``reason`` distinguishes
    an explicit user Stop from a client disconnect so the UI can word each.
    """
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return {
            "type": "stopped",
            "session_id": session_id,
            "reason": pop_stop_reason(session_id) or "client",
        }
    return None
