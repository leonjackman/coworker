"""Small helpers for turning exceptions into user-facing text.

Several exceptions that flow through the streaming pipeline have an **empty**
``str()`` -- ``asyncio.CancelledError``, ``GeneratorExit``, a bare
``TimeoutError`` from ``asyncio.wait_for``, and anyio's
``ClosedResourceError`` / ``BrokenResourceError`` / ``EndOfStream``. Serializing
those as-is produces an empty ``error`` field, which the UI then misreads as a
connection failure. :func:`error_text` guarantees a non-empty, meaningful label.
"""

from __future__ import annotations

# A few empty-message exceptions we can name better than their class alone.
_FRIENDLY_NAMES = {
    "TimeoutError": "请求超时，请重试",
    "ClosedResourceError": "连接已中断",
    "BrokenResourceError": "连接已中断",
    "EndOfStream": "连接已中断",
    "CancelledError": "已取消",
    "GeneratorExit": "已取消",
}


def error_text(exc: BaseException, *, limit: int = 400) -> str:
    """Return a non-empty, bounded description of ``exc``.

    Falls back to a friendly name (or the exception class name) when the message
    is empty, so the UI never has to guess what happened -- and never shows a
    generic "connection lost" for something that was not a connection problem.
    """
    try:
        text = str(exc).strip()
    except Exception:  # noqa: BLE001 - a hostile __str__ must not break the stream
        text = ""
    if not text:
        name = type(exc).__name__ or "Error"
        text = _FRIENDLY_NAMES.get(name, name)
    return text[:limit]
