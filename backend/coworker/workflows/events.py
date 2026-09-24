"""Run event emission (W32/W33).

The executor emits :class:`RunEvent` records through a small emitter. Events are
always persisted (so the SSE endpoint can poll/replay them), and an optional
in-process listener is notified for low-latency consumers.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from .model import RunEvent

Listener = Callable[[RunEvent], None]


class RunEventEmitter:
    """Sequenced, persisted event emitter for one run."""

    def __init__(self, run_id: str, store: Any, listener: Listener | None = None):
        self.run_id = run_id
        self._store = store
        self._listener = listener
        self._seq = 0

    def emit(
        self,
        type_: str,
        *,
        step_id: str = "",
        status: str = "",
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        self._seq += 1
        event = RunEvent(
            run_id=self.run_id,
            seq=self._seq,
            type=type_,
            step_id=step_id,
            status=status,
            message=message,
            data=data or {},
            at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            self._store.append_event(event)
        except Exception:  # noqa: BLE001 - event persistence must never break a run
            pass
        if self._listener is not None:
            try:
                self._listener(event)
            except Exception:  # noqa: BLE001
                pass
        return event


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def monotonic() -> float:
    return time.monotonic()
