"""Central error-policy boundary tests.

The agent must only abort the turn for *systemic* failures. Behavioral failures
(MCP/SSE/account/skill/workflow/memory tool errors, construction hiccups) are
handed to the model or degraded -- never a red error. All of that policy lives
in ``coworker.errors``; these tests pin it plus the one tool-level boundary that
applies it, and that a fallback is always logged for debugging.
"""

import asyncio
import errno
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402
from anyio import ClosedResourceError  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from langgraph.errors import GraphBubbleUp  # noqa: E402

from coworker.agent.middleware.tool_error_fallback import ToolErrorFallbackMiddleware  # noqa: E402
from coworker.errors import (  # noqa: E402
    Action,
    FatalToolError,
    Severity,
    Site,
    behavior_message,
    classify,
    handle,
    is_connection_error,
)
from coworker.mcp.mcp_session import McpSessionClosedError, _is_transport_error  # noqa: E402

# Matched by name in the classifier (avoids importing the agent stack).
ContextOverflowError = type("ContextOverflowError", (RuntimeError,), {})


def _request(name: str = "file_list", tool_call_id: str = "tc1") -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "id": tool_call_id})


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# --- classification -----------------------------------------------------------


def test_classify_behavioral():
    assert classify(ValueError("x")) is Severity.BEHAVIORAL
    assert classify(McpSessionClosedError("dead session")) is Severity.BEHAVIORAL
    assert classify(ClosedResourceError()) is Severity.BEHAVIORAL
    assert classify(NotImplementedError()) is Severity.BEHAVIORAL


def test_classify_fatal():
    assert classify(ContextOverflowError("too big")) is Severity.FATAL
    assert classify(MemoryError()) is Severity.FATAL
    assert classify(FatalToolError("stop")) is Severity.FATAL
    # Resource exhaustion is systemic; a plain OSError (e.g. file-not-found) is not.
    assert classify(OSError(errno.ENOSPC, "No space left on device")) is Severity.FATAL
    assert classify(OSError(errno.ENOENT, "No such file")) is Severity.BEHAVIORAL


def test_classify_cancelled_and_interrupt():
    assert classify(asyncio.CancelledError()) is Severity.CANCELLED
    assert classify(GeneratorExit()) is Severity.CANCELLED
    assert classify(GraphBubbleUp()) is Severity.INTERRUPT


# --- policy table -------------------------------------------------------------


def test_behavioral_goes_to_model_at_tool_site():
    assert handle(Site.TOOL, ValueError("x")).action is Action.TOOL_MESSAGE


def test_behavioral_degrades_away_from_tool_site():
    for site in (Site.STREAM, Site.CONSTRUCTION, Site.RESUME):
        assert handle(site, ValueError("x")).action is Action.DEGRADE


def test_fatal_and_interrupt_and_cancel_always_raise():
    assert handle(Site.TOOL, MemoryError()).action is Action.RAISE
    assert handle(Site.STREAM, ContextOverflowError("x")).action is Action.RAISE
    assert handle(Site.TOOL, asyncio.CancelledError()).action is Action.RAISE
    assert handle(Site.TOOL, GraphBubbleUp()).action is Action.RAISE


# --- site-aware connection errors (LLM drop vs MCP tool drop) -----------------


def test_connection_error_is_systemic_outside_a_tool():
    # A dropped provider stream (e.g. RemoteProtocolError in the 'model' task)
    # must abort with a real error, not silently degrade.
    exc = httpx.RemoteProtocolError("peer closed connection without sending complete message body")
    assert handle(Site.STREAM, exc).severity is Severity.FATAL
    assert handle(Site.STREAM, exc).action is Action.RAISE
    assert handle(Site.RESUME, exc).action is Action.RAISE


def test_connection_error_at_tool_site_stays_behavioral():
    exc = httpx.RemoteProtocolError("peer closed connection")
    assert handle(Site.TOOL, exc).action is Action.TOOL_MESSAGE


def test_is_connection_error_covers_httpx_and_os():
    assert is_connection_error(httpx.RemoteProtocolError("x"))
    assert is_connection_error(httpx.ReadTimeout("x"))
    assert is_connection_error(ConnectionResetError())
    assert not is_connection_error(ValueError("x"))


def test_mcp_transport_error_recognizes_httpx():
    # So a dead MCP SSE channel is reconnected instead of swallowed.
    assert _is_transport_error(httpx.RemoteProtocolError("peer closed connection"))
    assert _is_transport_error(httpx.ReadTimeout("x"))
    assert not _is_transport_error(ValueError("bad args"))


# --- tool boundary ------------------------------------------------------------


def test_tool_fallback_returns_error_message_for_behavioral():
    mw = ToolErrorFallbackMiddleware()

    def handler(request):
        raise McpSessionClosedError("dead session")

    message = mw.wrap_tool_call(_request(), handler)
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.tool_call_id == "tc1"
    assert "file_list" in message.content


@pytest.mark.parametrize("exc", [MemoryError(), ContextOverflowError("x")])
def test_tool_fallback_reraises_fatal(exc):
    mw = ToolErrorFallbackMiddleware()

    def handler(request):
        raise exc

    with pytest.raises(type(exc)):
        mw.wrap_tool_call(_request(), handler)


def test_tool_fallback_reraises_cancellation_and_interrupt():
    mw = ToolErrorFallbackMiddleware()

    def cancel(request):
        raise asyncio.CancelledError()

    def interrupt(request):
        raise GraphBubbleUp()

    with pytest.raises(asyncio.CancelledError):
        mw.wrap_tool_call(_request(), cancel)
    with pytest.raises(GraphBubbleUp):
        mw.wrap_tool_call(_request(), interrupt)


def test_tool_fallback_passes_success_through():
    mw = ToolErrorFallbackMiddleware()
    marker = ToolMessage(content="ok", tool_call_id="tc1")

    def handler(request):
        return marker

    assert mw.wrap_tool_call(_request(), handler) is marker


def test_tool_fallback_async_returns_error_message():
    mw = ToolErrorFallbackMiddleware()

    async def handler(request):
        raise ValueError("boom")

    message = asyncio.run(mw.awrap_tool_call(_request(), handler))
    assert isinstance(message, ToolMessage) and message.status == "error"


# --- text + logging -----------------------------------------------------------


def test_behavior_message_is_non_empty():
    assert behavior_message(ValueError("boom"), tool="file_list").strip()
    assert behavior_message(ClosedResourceError(), tool="file_list").strip()


def _with_capture(fn):
    logger = logging.getLogger("coworker.errors")
    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return handler.records


def test_fallback_is_logged_with_site_and_action():
    records = _with_capture(lambda: handle(Site.TOOL, ValueError("boom"), tool="file_list"))
    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert warnings, "a handled/fallback error must be logged"
    text = warnings[0].getMessage()
    assert "site=tool" in text and "action=tool_message" in text and "exc_type=ValueError" in text
    assert warnings[0].exc_info is not None  # traceback preserved for debugging


def test_cancellation_is_not_logged_as_warning():
    records = _with_capture(lambda: handle(Site.TOOL, asyncio.CancelledError()))
    assert not [r for r in records if r.levelno >= logging.WARNING]
