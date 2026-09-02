"""Shared utility functions for MCP modules.

Extracted from mcp_session.py and mcp_test.py to avoid duplication.
"""

from __future__ import annotations


def flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Unwrap ``ExceptionGroup``/``BaseExceptionGroup`` into leaf exceptions.

    The MCP SDK and anyio wrap transport errors (e.g. ``httpx.HTTPStatusError``
    for a 401) inside exception groups, so classifying a failure by
    ``str(exc)`` only sees the group summary. Recursing into the groups lets us
    inspect the real leaf error.
    """
    leaves: list[BaseException] = []

    def _walk(e: BaseException) -> None:
        for leaf in getattr(e, "exceptions", ()) or ():
            if getattr(leaf, "exceptions", None):
                _walk(leaf)
            else:
                leaves.append(leaf)
        if not getattr(e, "exceptions", ()):
            leaves.append(e)

    _walk(exc)
    return leaves or [exc]


def friendly_error(exc: BaseException, transport: str = "") -> str:
    """Turn raw adapter/SDK exceptions into something a user can act on."""
    leaves = flatten_exceptions(exc)
    if any(isinstance(leaf, TimeoutError) for leaf in leaves):
        return "Connection timed out"
    # Prefer the most actionable leaf: a nested HTTPStatusError (e.g. 401/403).
    for leaf in leaves:
        if isinstance(leaf, FileNotFoundError):
            return f"Command not found: {leaf}"
        status_error = getattr(leaf, "response", None)
        if status_error is not None and getattr(status_error, "status_code", None):
            return f"Authentication required (401)" if status_error.status_code == 401 else f"HTTP {status_error.status_code}: {leaf}"
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "no such file or directory" in lowered:
        return f"Command not found: {text}"
    if "unauthorized" in lowered or "401" in lowered:
        return f"Authentication required (401): {text}"
    if "403" in lowered:
        return f"Access denied (403): {text}"
    if "404" in lowered:
        return f"Endpoint not found (404) -- check the URL: {text}"
    if transport == "sse" and "text/event-stream" in lowered:
        return f"Server did not return an SSE stream -- try HTTP transport: {text}"
    if len(text) > 300:
        text = text[:297] + "..."
    return text
