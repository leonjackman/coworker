"""Persistent LLM request/response logging for OpenAI-compatible providers.

Wraps the httpx async client used by ``langchain-openai`` so every request body
sent to the provider (messages + tools + sampling params) and its status / first
bytes of the response are written to a dedicated, size-capped log file:

    <data_dir>/llm-requests.log        (JSON lines, 10 MB x 10 rotations)

This is the authoritative record for debugging "the model stopped calling
tools / degraded" issues: it captures the EXACT body CW assembled, which the
vLLM side sees, so the two logs can be diffed to find where the conversation
history or tool schema diverged.

Two recording modes:

* ``COWORKER_LLM_LOG=1`` — full request bodies + successful responses.
* default — a compact entry for every *failed* request only, including the
  normalized ``network_kind``, the resolved proxy, the endpoint and latency.
  This is what makes "there was no LLM request" diagnosable without turning on
  the (large) full-body log in normal operation.

Every entry carries ``session_id`` from the backend's async-local context, so a
single user report can be traced across ``app.log`` and ``llm-requests.log``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_LOG_ENV = "COWORKER_LLM_LOG"
_LOG_MAX_BYTES_ENV = "COWORKER_LLM_LOG_MAX_BYTES"
_LOG_BACKUP_ENV = "COWORKER_LLM_LOG_BACKUP_COUNT"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_BACKUP = 10

# Per-request char cap for the logged body (keeps one huge context from
# ballooning the log). Body is truncated as a whole, not per-message.
_MAX_BODY_CHARS = 60_000

# Failure entries are cheap but a broken link can fail many tool-loop calls in
# a row; collapse identical errors within this window.
_ERROR_LOG_INTERVAL_S = 5.0
_last_error_log: dict[str, float] = {}


def _enabled() -> bool:
    return os.environ.get(_LOG_ENV, "0").strip().lower() not in {"0", "false", "no", "off"}


def _rotate(path: Path, max_bytes: int, backup: int) -> None:
    """Size-capped rotation for a single log file (oldest dropped last)."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        for i in range(backup - 1, 0, -1):
            older = Path(f"{path}.{i}")
            newer = Path(f"{path}.{i + 1}")
            if older.exists():
                if newer.exists():
                    newer.unlink()
                older.rename(newer)
        path.rename(Path(f"{path}.1"))
    except Exception:  # noqa: BLE001 - logging must never break the request
        logger.debug("llm request log rotation failed: %s", exc_info=True)


def _current_session_id() -> str:
    try:
        from coworker.logger import current_session_id

        return current_session_id.get()
    except Exception:  # noqa: BLE001 - logging must never break the request
        return ""


class _LoggingAsyncClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that records request bodies and responses.

    Subclasses ``httpx.AsyncClient`` so OpenAI's ``isinstance`` check on the
    ``http_async_client`` kwarg passes while ``send`` additionally persists the
    serialized request body / response status to the rotation-capped log.
    """

    def __init__(
        self,
        inner: Any,
        log_path: Path,
        max_bytes: int,
        backup: int,
        *,
        record_success: bool,
    ) -> None:
        # Inherit base_url/timeout from the inner client so URLs and timeouts
        # match what langchain would have used. When the inner client is one of
        # CW's weak-network-tuned clients (http2 / raised keep-alive / socket
        # profile), carry its transport over so the logged path exercises the
        # SAME wire behaviour — a bare rebuild would silently drop the tuning.
        base_url = getattr(inner, "base_url", None)
        timeout = getattr(inner, "timeout", None)
        transport = None
        if bool(getattr(inner, "_cw_tuned_transport", False)):
            transport = getattr(inner, "_transport", None)

        # Preserve the resolved proxy decision. A rebuild that omitted it would
        # silently reconnect directly (or pick up the system proxy again).
        decision = getattr(inner, "_cw_proxy_decision", None)
        self._proxy_decision = decision
        self._proxy_description = getattr(inner, "_cw_proxy_description", "")

        kwargs: dict[str, Any] = {}
        if decision is not None:
            kwargs = {"proxy": getattr(decision, "proxy_url", None), "trust_env": False}
        elif inner is not None:
            kwargs = {"trust_env": bool(getattr(inner, "trust_env", True))}

        try:
            if transport is not None:
                super().__init__(base_url=base_url, timeout=timeout, transport=transport, **kwargs)
            else:
                super().__init__(base_url=base_url, timeout=timeout, **kwargs)
        except Exception:  # noqa: BLE001 - fall back to fully default client
            super().__init__()
        self._log_path = log_path
        self._max_bytes = max_bytes
        self._backup = backup
        self._record_success = record_success

    def _log(self, entry: dict[str, Any]) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(self._log_path, self._max_bytes, self._backup)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001
            logger.debug("llm request log write failed", exc_info=True)

    async def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """Record the request (and response status) then forward to httpx."""
        session_id = _current_session_id()
        proxy_url = getattr(self._proxy_decision, "proxy_url", None)
        started_at = time.monotonic()
        method = ""
        url = ""
        body_recorded = False
        try:
            body: dict[str, Any] | None = None
            try:
                if request is not None and getattr(request, "content", None):
                    body = json.loads(request.content.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                body = None
            url = str(getattr(request, "url", ""))
            method = str(getattr(request, "method", "")).upper()
            if self._record_success and body is not None and method == "POST":
                trimmed = json.dumps(body, ensure_ascii=False, default=str)
                if len(trimmed) > _MAX_BODY_CHARS:
                    trimmed = trimmed[:_MAX_BODY_CHARS] + '..."[TRUNCATED]'
                self._log({
                    "ts": _now_iso(),
                    "session_id": session_id,
                    "method": method,
                    "url": url,
                    "proxy": self._proxy_description,
                    "request": trimmed,
                })
                body_recorded = True
        except Exception:  # noqa: BLE001
            body_recorded = False

        try:
            response = await super().send(request, *args, **kwargs)
        except Exception as exc:
            self._log_failure(
                exc,
                session_id=session_id,
                method=method,
                url=url,
                proxy_url=proxy_url,
                latency_ms=round((time.monotonic() - started_at) * 1000),
            )
            raise

        if self._record_success:
            try:
                self._log({
                    "ts": _now_iso(),
                    "session_id": session_id,
                    "method": method,
                    "url": url,
                    "status": getattr(response, "status_code", None),
                    "proxy": self._proxy_description,
                    "latency_ms": round((time.monotonic() - started_at) * 1000),
                })
            except Exception:  # noqa: BLE001
                pass
        return response

    def _log_failure(
        self,
        exc: BaseException,
        *,
        session_id: str,
        method: str,
        url: str,
        proxy_url: str | None,
        latency_ms: int,
    ) -> None:
        try:
            from coworker.net.network_errors import classify_network_error

            net = classify_network_error(exc, proxy=proxy_url, endpoint=url)
            kind = net.kind.value
            key = f"{url}|{kind}"
            now = time.monotonic()
            if now - _last_error_log.get(key, 0.0) < _ERROR_LOG_INTERVAL_S:
                return
            _last_error_log[key] = now
            self._log({
                "ts": _now_iso(),
                "session_id": session_id,
                "method": method,
                "url": url,
                "status": "error",
                "network_kind": kind,
                "retryable": net.retryable,
                "error": net.root_cause,
                "proxy": self._proxy_description,
                "latency_ms": latency_ms,
            })
        except Exception:  # noqa: BLE001 - logging must never break the request
            logger.debug("llm failure log write failed", exc_info=True)


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def wrap_async_client(inner: Any, data_dir: Path | None) -> Any:
    """Return a request-logging ``httpx.AsyncClient``.

    ``inner`` is the httpx.AsyncClient that langchain-openai would otherwise
    construct; its base_url / timeout / transport / proxy decision are inherited
    so the wrapper behaves identically on the wire.

    Full bodies are recorded only when ``COWORKER_LLM_LOG=1``. Failed requests
    are always recorded (rate-limited per URL+kind), which is what makes
    "no LLM request appeared" debuggable without the large full-body log.
    """
    try:
        max_bytes = int(os.environ.get(_LOG_MAX_BYTES_ENV, str(_DEFAULT_MAX_BYTES)))
        backup = int(os.environ.get(_LOG_BACKUP_ENV, str(_DEFAULT_BACKUP)))
    except (TypeError, ValueError):
        max_bytes, backup = _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP
    data_dir = data_dir or Path(os.environ.get("COWORKER_DATA_DIR", "~/.coworker")).expanduser()
    log_path = Path(data_dir) / "llm-requests.log"
    return _LoggingAsyncClient(
        inner,
        log_path,
        max_bytes,
        backup,
        record_success=_enabled(),
    )
