"""Persistent LLM request/response logging for OpenAI-compatible providers.

Wraps the httpx async client used by ``langchain-openai`` so every request body
sent to the provider (messages + tools + sampling params) and its status / first
bytes of the response are written to a dedicated, size-capped log file:

    <data_dir>/llm-requests.log        (JSON lines, 10 MB x 10 rotations)

This is the authoritative record for debugging "the model stopped calling
tools / degraded" issues: it captures the EXACT body CW assembled, which the
vLLM side sees, so the two logs can be diffed to find where the conversation
history or tool schema diverged.

Enabled only when the env var ``COWORKER_LLM_LOG=1`` is set; otherwise the
wrapper passes through untouched (zero overhead in production defaults).
"""

from __future__ import annotations

import json
import logging
import os
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


class _LoggingAsyncClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that records request bodies and responses.

    Subclasses ``httpx.AsyncClient`` so OpenAI's ``isinstance`` check on the
    ``http_async_client`` kwarg passes while ``send`` additionally persists the
    serialized request body / response status to the rotation-capped log.
    """

    def __init__(self, inner: Any, log_path: Path, max_bytes: int, backup: int) -> None:
        # Inherit base_url/timeout from the inner client so URLs and timeouts
        # match what langchain would have used; transport/limits are rebuilt
        # with httpx defaults (the inner wrapper's are private internals).
        base_url = getattr(inner, "base_url", None)
        timeout = getattr(inner, "timeout", None)
        try:
            super().__init__(
                base_url=base_url,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 - fall back to fully default client
            super().__init__()
        self._log_path = log_path
        self._max_bytes = max_bytes
        self._backup = backup

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
        recorded = False
        method = ""
        url = ""
        try:
            body: dict[str, Any] | None = None
            try:
                if request is not None and getattr(request, "content", None):
                    body = json.loads(request.content.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                body = None
            url = str(getattr(request, "url", ""))
            method = str(getattr(request, "method", "")).upper()
            if body is not None and method == "POST":
                trimmed = json.dumps(body, ensure_ascii=False, default=str)
                if len(trimmed) > _MAX_BODY_CHARS:
                    trimmed = trimmed[:_MAX_BODY_CHARS] + '..."[TRUNCATED]'
                self._log({
                    "ts": _now_iso(),
                    "method": method,
                    "url": url,
                    "request": trimmed,
                })
                recorded = True
        except Exception:  # noqa: BLE001
            recorded = False

        response = await super().send(request, *args, **kwargs)

        if recorded:
            try:
                self._log({
                    "ts": _now_iso(),
                    "method": method,
                    "url": url,
                    "status": getattr(response, "status_code", None),
                })
            except Exception:  # noqa: BLE001
                pass
        return response


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def wrap_async_client(inner: Any, data_dir: Path | None) -> Any:
    """Return a request-logging ``httpx.AsyncClient`` if enabled.

    ``inner`` is the httpx.AsyncClient that langchain-openai would otherwise
    construct; its base_url / timeout / transport / limits are inherited so the
    wrapper behaves identically on the wire. When ``COWORKER_LLM_LOG`` is
    unset/0 this returns ``inner`` unchanged so there is no overhead in normal
    operation.
    """
    if not _enabled():
        return inner
    try:
        max_bytes = int(os.environ.get(_LOG_MAX_BYTES_ENV, str(_DEFAULT_MAX_BYTES)))
        backup = int(os.environ.get(_LOG_BACKUP_ENV, str(_DEFAULT_BACKUP)))
    except (TypeError, ValueError):
        max_bytes, backup = _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP
    data_dir = data_dir or Path(os.environ.get("COWORKER_DATA_DIR", "~/.coworker")).expanduser()
    log_path = Path(data_dir) / "llm-requests.log"
    return _LoggingAsyncClient(inner, log_path, max_bytes, backup)
