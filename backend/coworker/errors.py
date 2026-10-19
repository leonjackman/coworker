"""Central error-handling hub for Coworker.

Every error path routes through :func:`handle` so the *decision* (abort vs.
fall back) and the *reporting* live in exactly one place -- no scattered
``if isinstance(...)`` checks, no per-tool/per-middleware duplicates.

Two categories only:

* **systemic** (``Severity.FATAL``) -- the environment itself is broken (the
  LLM is unreachable / rate-limited / rejected, the request overflows the
  window, the process hit ``MemoryError`` ...). These abort the turn and are
  shown to the user as a real error.
* **behavioral** (``Severity.BEHAVIORAL``) -- a *capability* failed this once
  (an MCP/SSE server dropped, a skill/workflow/memory tool errored, an account
  needs re-auth ...). These never abort: at a tool boundary they become an
  error ``ToolMessage`` the model can read and report; elsewhere they degrade.

:func:`error_text` additionally guarantees a non-empty message -- several
exceptions (``CancelledError``, ``GeneratorExit``, a bare ``TimeoutError``,
anyio's ``ClosedResourceError`` ...) stringify to ``""``.
"""

from __future__ import annotations

import asyncio
import errno
from dataclasses import dataclass
from enum import Enum

from coworker.logger import get_logger
from coworker.net.network_errors import (
    NetworkError,
    classify_network_error,
    is_stream_stall,
)

logger = get_logger(__name__)

# Low-level resource exhaustion (disk full, quota, fd/memory pressure) is a
# *systemic* fault, not a capability failure -- surface it as a real error.
_FATAL_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, name, None)
        for name in ("ENOSPC", "EDQUOT", "EMFILE", "ENFILE", "ENOMEM")
    )
    if code is not None
)

# A few empty-message exceptions we can name better than their class alone.
_FRIENDLY_NAMES = {
    "TimeoutError": "请求超时，请重试",
    "ClosedResourceError": "连接已中断",
    "BrokenResourceError": "连接已中断",
    "EndOfStream": "连接已中断",
    "CancelledError": "已取消",
    "GeneratorExit": "已取消",
}


class FatalToolError(Exception):
    """Raise inside a tool to force a fatal abort despite the fallback net.

    By default a tool exception is treated as behavioral (handed to the model).
    Raising :class:`FatalToolError` is the one explicit way a tool can say
    "this is systemic, stop the turn".
    """


class Severity(str, Enum):
    FATAL = "fatal"          # systemic -> abort + red error
    BEHAVIORAL = "behavioral"  # capability failure -> fall back, do not abort
    CANCELLED = "cancelled"  # Stop / client disconnect -> propagate as a stop
    INTERRUPT = "interrupt"  # langgraph HITL interrupt -> propagate


class Site(str, Enum):
    """Where the error surfaced (for classification + logging)."""

    TOOL = "tool"
    STREAM = "stream"
    CONSTRUCTION = "construction"
    RESUME = "resume"


class Action(str, Enum):
    """What the caller must do with the error."""

    RAISE = "raise"                # propagate (fatal / interrupt / cancellation)
    TOOL_MESSAGE = "tool_message"  # return an error ToolMessage to the model
    DEGRADE = "degrade"            # continue without the failed capability


@dataclass(frozen=True)
class Outcome:
    severity: Severity
    action: Action
    # Normalized network diagnosis (None for behavioral/cancellation errors).
    network: NetworkError | None = None


# --- text ---------------------------------------------------------------------


def error_text(exc: BaseException, *, limit: int = 400) -> str:
    """Return a non-empty, bounded description of ``exc``.

    Falls back to a friendly name (or the exception class name) when the message
    is empty, so the UI never has to guess -- and never shows a generic
    "connection lost" for something that was not a connection problem.
    """
    try:
        text = str(exc).strip()
    except Exception:  # noqa: BLE001 - a hostile __str__ must not break the stream
        text = ""
    if not text:
        name = type(exc).__name__ or "Error"
        text = _FRIENDLY_NAMES.get(name, name)
    return text[:limit]


def behavior_message(exc: BaseException, *, tool: str = "", server: str = "") -> str:
    """Model-facing text for a behavioral tool failure.

    Tells the model the tool failed, to change approach, and never to pretend
    success -- so the agent reports the limitation to the user instead of the
    turn dying.
    """
    what = tool or "工具"
    where = f"（服务：{server}）" if server else ""
    return (
        f"{what}{where}执行失败：{error_text(exc)}。"
        "请改用其它方式、缩小范围，或如实告知用户此能力本次不可用；不要重复同一调用。"
    )


# --- classification -----------------------------------------------------------

_PROVIDER_ERRORS: tuple[type[BaseException], ...] | None = None
_INTERRUPT_TYPE: type[BaseException] | None = None
_STALL_TYPE: type[BaseException] | None = None
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] | None = None


def _transport_errors() -> tuple[type[BaseException], ...]:
    """The raw HTTP transport errors (``httpx.TransportError`` subtree).

    The streaming model call can surface these unwrapped mid-stream
    (``RemoteProtocolError`` "peer closed connection", ``ReadTimeout``,
    ``ConnectError`` ...), which ``_provider_errors`` does not catch because they
    are not ``openai.*`` types.
    """
    global _TRANSPORT_ERRORS
    if _TRANSPORT_ERRORS is None:
        try:
            import httpx

            _TRANSPORT_ERRORS = (httpx.TransportError,)
        except Exception:  # noqa: BLE001 - httpx absent must not crash classification
            _TRANSPORT_ERRORS = ()
    return _TRANSPORT_ERRORS


def _provider_connection_errors() -> tuple[type[BaseException], ...]:
    try:
        import openai

        # ``APITimeoutError`` subclasses ``APIConnectionError``; both are the
        # SDK's wrapper around a dropped/unreachable provider connection.
        return (openai.APIConnectionError,)
    except Exception:  # noqa: BLE001 - openai absent must not crash classification
        return ()


def is_connection_error(exc: BaseException) -> bool:
    """True for a connection/transport failure of any layer (httpx, OS, openai)."""
    types = _transport_errors() + _provider_connection_errors()
    if types and isinstance(exc, types):
        return True
    return isinstance(exc, (ConnectionError, BrokenPipeError, EOFError, TimeoutError))


def _provider_errors() -> tuple[type[BaseException], ...]:
    global _PROVIDER_ERRORS
    if _PROVIDER_ERRORS is None:
        try:
            import openai

            _PROVIDER_ERRORS = (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.AuthenticationError,
                openai.PermissionDeniedError,
                openai.RateLimitError,
                openai.NotFoundError,
                openai.InternalServerError,
                openai.BadRequestError,
                openai.APIStatusError,
                openai.APIError,
            )
        except Exception:  # noqa: BLE001 - openai absent must not crash classification
            _PROVIDER_ERRORS = ()
    return _PROVIDER_ERRORS


def _interrupt_type() -> type[BaseException] | None:
    global _INTERRUPT_TYPE
    if _INTERRUPT_TYPE is None:
        try:
            from langgraph.errors import GraphBubbleUp

            _INTERRUPT_TYPE = GraphBubbleUp
        except Exception:  # noqa: BLE001
            _INTERRUPT_TYPE = None
    return _INTERRUPT_TYPE


def _stall_type() -> type[BaseException] | None:
    global _STALL_TYPE
    if _STALL_TYPE is None:
        try:
            from langchain_openai.chat_models._client_utils import StreamChunkTimeoutError

            _STALL_TYPE = StreamChunkTimeoutError
        except Exception:  # noqa: BLE001 - version drift must not crash classification
            _STALL_TYPE = None
    return _STALL_TYPE


def _is_provider_error(exc: BaseException) -> bool:
    types = _provider_errors()
    return bool(types) and isinstance(exc, types)


def _is_stall_error(exc: BaseException) -> bool:
    stall = _stall_type()
    if stall is not None and isinstance(exc, stall):
        return True
    text = str(exc)
    return "stream_chunk_timeout" in text or "No streaming chunk received" in text


def classify(exc: BaseException) -> Severity:
    """Classify ``exc`` as systemic / behavioral / cancellation / interrupt."""
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return Severity.CANCELLED
    interrupt = _interrupt_type()
    if interrupt is not None and isinstance(exc, interrupt):
        return Severity.INTERRUPT
    if isinstance(exc, (FatalToolError, MemoryError, SystemExit, KeyboardInterrupt)):
        return Severity.FATAL
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _FATAL_ERRNOS:
        return Severity.FATAL
    # ContextOverflowError is matched by name to avoid importing the agent
    # middleware stack into this low-level module (import cycle).
    if type(exc).__name__ == "ContextOverflowError":
        return Severity.FATAL
    if _is_provider_error(exc) or _is_stall_error(exc):
        return Severity.FATAL
    return Severity.BEHAVIORAL


def _severity_for(site: Site, exc: BaseException) -> Severity:
    """Site-aware severity.

    A connection/transport failure is *systemic* when it happens outside a tool
    (the LLM/provider is unreachable) but *behavioral* at a tool boundary (an
    MCP/remote tool's channel dropped and the model should be told). ``classify``
    stays site-agnostic; only this decides by location.
    """
    severity = classify(exc)
    if (
        severity is Severity.BEHAVIORAL
        and site in (Site.STREAM, Site.RESUME)
        and is_connection_error(exc)
    ):
        return Severity.FATAL
    return severity


def _decide(site: Site, severity: Severity) -> Action:
    """The one place that maps (site, severity) -> action."""
    if severity in (Severity.CANCELLED, Severity.INTERRUPT, Severity.FATAL):
        return Action.RAISE
    # Behavioral: a tool failure goes to the model; anywhere else we degrade
    # (continue without the failed capability) instead of aborting.
    return Action.TOOL_MESSAGE if site is Site.TOOL else Action.DEGRADE


# --- entry point --------------------------------------------------------------


def handle(
    site: Site,
    exc: BaseException,
    *,
    tool: str = "",
    server: str = "",
    detail: str = "",
    endpoint: str = "",
    proxy: str | None = None,
) -> Outcome:
    """Classify ``exc``, log the decision, and return what the caller must do.

    This is the single entry point for the "systemic vs behavioral" policy and
    for the debug log line the user asked for: whenever an error is turned into
    a fallback (or propagated as fatal), the site, the original error type +
    traceback, and the chosen action are recorded so a swallowed-but-handled
    failure is always traceable in the logs.

    ``endpoint`` / ``proxy`` let the normalized network diagnosis distinguish a
    proxy disconnect from a dead provider (and tell the user what to do).
    """
    severity = _severity_for(site, exc)
    action = _decide(site, severity)
    network: NetworkError | None = None
    if is_connection_error(exc) or _is_stall_error(exc):
        network = classify_network_error(exc, proxy=proxy, endpoint=endpoint)
    if severity is Severity.CANCELLED:
        # A Stop / client disconnect is expected, not an incident: no warning.
        logger.debug("error handled: site=%s cancelled (detail=%s)", site.value, detail or "-")
        return Outcome(severity=severity, action=action)
    logger.warning(
        "error handled: site=%s severity=%s action=%s exc_type=%s network_kind=%s "
        "tool=%s server=%s detail=%s",
        site.value,
        severity.value,
        action.value,
        type(exc).__name__,
        network.kind.value if network else "-",
        tool or "-",
        server or "-",
        detail or "-",
        exc_info=True,
    )
    return Outcome(severity=severity, action=action, network=network)
