"""Error-classification regressions for the MCP/streaming fixes.

Covers the root cause of the misleading "无法连接到 CoWorker 本地服务，请重新启动
应用。" message: a dead SSE MCP session raised ``anyio.ClosedResourceError`` (empty
message), it was not classified as a transport error, so it leaked unchanged to
the UI as an empty error.

- ``_is_transport_error`` must recognise anyio's closed/broken/end-of-stream
  errors (by type -- their messages are empty) so the session reconnects.
- ``error_text`` must never return an empty string.
- ``cancellation_event`` must turn a Stop / disconnect into a ``stopped`` event
  (never an ``error``), distinguishing "user" from "client".
- ``_serve_session`` keepalive must ping remote sessions and stop cleanly.
"""

import asyncio
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream  # noqa: E402

from coworker.api.stream_stop import (  # noqa: E402
    _mark_stop_reason,
    cancellation_event,
    pop_stop_reason,
)
from coworker.errors import error_text  # noqa: E402
from coworker.mcp.mcp_session import (  # noqa: E402
    McpSessionManager,
    _is_transport_error,
)


# --- transport classification (E) ---------------------------------------------


def test_anyio_closed_errors_are_transport_errors():
    assert _is_transport_error(ClosedResourceError())
    assert _is_transport_error(BrokenResourceError())
    assert _is_transport_error(EndOfStream())


def test_transport_error_detected_inside_exception_group():
    group = ExceptionGroup("mcp", [ClosedResourceError()])
    assert _is_transport_error(group)


def test_plain_tool_error_is_not_transport_error():
    assert not _is_transport_error(ValueError("bad args"))


# --- error text never empty (G) -----------------------------------------------


def test_error_text_never_empty():
    for exc in (
        asyncio.CancelledError(),
        GeneratorExit(),
        TimeoutError(),
        ClosedResourceError(),
        RuntimeError(""),
    ):
        assert error_text(exc).strip() != ""


def test_error_text_preserves_real_message():
    assert error_text(ValueError("boom")) == "boom"


# --- cancellation vs error (A) ------------------------------------------------


def test_cancellation_without_marker_is_client():
    evt = cancellation_event("sess-1", asyncio.CancelledError())
    assert evt == {"type": "stopped", "session_id": "sess-1", "reason": "client"}


def test_cancellation_after_user_stop_is_user():
    _mark_stop_reason("sess-2", "user")
    evt = cancellation_event("sess-2", asyncio.CancelledError())
    assert evt is not None and evt["reason"] == "user"
    # Reason is consumed: a later disconnect for the same session reads "client".
    assert pop_stop_reason("sess-2") is None


def test_non_cancellation_is_not_a_stopped_event():
    assert cancellation_event("sess-3", ValueError("boom")) is None


# --- keepalive (F) ------------------------------------------------------------


def _manager(interval: float, ping_timeout: float = 1.0) -> McpSessionManager:
    sm = McpSessionManager.__new__(McpSessionManager)
    sm.keepalive_interval = interval
    sm.keepalive_ping_timeout = ping_timeout
    return sm


class _FakeSession:
    def __init__(self):
        self.pings = 0

    async def send_ping(self):
        self.pings += 1


def test_keepalive_pings_until_stop():
    sm = _manager(0.05)
    session = _FakeSession()
    stop = asyncio.Event()

    async def run():
        task = asyncio.ensure_future(
            sm._serve_session("s", {"transport": "sse"}, session, stop)
        )
        await asyncio.sleep(0.18)
        stop.set()
        await task

    asyncio.run(run())
    assert session.pings >= 2


def test_keepalive_skips_local_transport():
    sm = _manager(0.01)
    session = _FakeSession()
    stop = asyncio.Event()
    stop.set()
    asyncio.run(sm._serve_session("s", {"transport": "stdio"}, session, stop))
    assert session.pings == 0


def test_keepalive_stops_when_ping_fails():
    sm = _manager(0.02)

    class _DeadSession:
        async def send_ping(self):
            raise ClosedResourceError()

    stop = asyncio.Event()

    async def run():
        # Must return promptly on its own (no stop set) once the ping fails.
        await asyncio.wait_for(
            sm._serve_session("s", {"transport": "sse"}, _DeadSession(), stop),
            timeout=1.0,
        )

    asyncio.run(run())
