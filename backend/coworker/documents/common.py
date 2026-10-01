"""Shared helpers for the Office/PDF document tools.

The document capability lets the agent read, create, edit and convert
``docx`` / ``xlsx`` / ``pptx`` / ``pdf`` files inside the workspace. These
formats are binary containers, so every tool routes writes through
``Workspace.write_binary`` (atomic + change-capture + rollback) and reads
through bounded extractors, never through the plain-text file tools.

This module holds the format vocabulary and small parsing helpers shared by the
extractor / builder / editor / converter.
"""

from __future__ import annotations

import base64
import json
import re

#: Canonical format id per file suffix.
FORMAT_BY_SUFFIX: dict[str, str] = {
    ".docx": "docx",
    ".docm": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xltx": "xlsx",
    ".pptx": "pptx",
    ".pptm": "pptx",
    ".pdf": "pdf",
}

#: Legacy binary Office formats we can name but not process (no library parses
#: the pre-2007 OLE containers reliably). Reported as an actionable error.
LEGACY_OFFICE_SUFFIXES: dict[str, str] = {
    ".doc": "docx",
    ".xls": "xlsx",
    ".ppt": "pptx",
}

#: Image suffixes accepted as a PDF-conversion source.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"})

MIME_BY_FORMAT: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}

FORMAT_TO_EXT: dict[str, str] = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "pdf": ".pdf"}

DEFAULT_READ_MAX_CHARS = 20_000
MAX_READ_MAX_CHARS = 200_000


class DocumentError(Exception):
    """A user/agent-facing document failure with a stable ``error_code``."""

    def __init__(self, message: str, error_code: str = "document_error"):
        super().__init__(message)
        self.error_code = error_code


def suffix_of(name: str) -> str:
    lower = str(name or "").lower()
    dot = lower.rfind(".")
    return lower[dot:] if dot >= 0 else ""


def detect_format(name: str) -> str:
    """Return the canonical format id for a file name, or raise ``DocumentError``."""
    suffix = suffix_of(name)
    fmt = FORMAT_BY_SUFFIX.get(suffix)
    if fmt:
        return fmt
    legacy = LEGACY_OFFICE_SUFFIXES.get(suffix)
    if legacy:
        raise DocumentError(
            f"Legacy Office format '{suffix}' is not supported. Re-save it as "
            f"{FORMAT_TO_EXT[legacy]} first (e.g. open it and use Save As).",
            "legacy_format",
        )
    raise DocumentError(
        f"Unsupported document type '{suffix or name}'. Supported: .docx, .xlsx, .pptx, .pdf.",
        "unsupported_format",
    )


def detect_format_or_none(name: str) -> str | None:
    """Like :func:`detect_format` but returns ``None`` for unknown/legacy."""
    try:
        return detect_format(name)
    except DocumentError:
        return None


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]*)?;base64,(?P<data>.*)$", re.DOTALL)


def decode_data_url(content: str) -> tuple[bytes, str] | None:
    """Decode a ``data:<mime>;base64,<payload>`` string.

    Returns ``(bytes, mime)`` or ``None`` when ``content`` is not a base64 data
    URL. Used to pull the real bytes out of a chat attachment.
    """
    if not isinstance(content, str):
        return None
    match = _DATA_URL_RE.match(content.strip())
    if not match:
        return None
    try:
        raw = base64.b64decode(match.group("data"), validate=False)
    except (ValueError, TypeError):
        return None
    return raw, (match.group("mime") or "application/octet-stream")


def parse_structured(value: Any, what: str = "content") -> Any:
    """Parse model-supplied JSON, tolerating the malformations LLMs produce.

    Models routinely emit near-JSON — most commonly unescaped double quotes
    inside a string value (e.g. ``"text": "取得"预备股东"身份"``). A plain
    ``json.loads`` rejects that, and the old fallback then dumped the entire
    JSON blob into the document as literal prose (the "garbled PDF" bug). Here
    we first try strict JSON, then ``json_repair`` (which fixes stray quotes,
    trailing commas, single quotes, etc.). A value that is clearly JSON-shaped
    but still cannot be parsed raises a clear :class:`DocumentError` instead of
    silently rendering garbage. Plain (non-JSON) strings pass through unchanged.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    try:
        import json_repair

        repaired = json_repair.loads(stripped)
        if isinstance(repaired, (dict, list)):
            return repaired
    except Exception:  # noqa: BLE001 - fall through to the explicit error
        pass
    raise DocumentError(
        f"{what} looks like JSON but could not be parsed. Send valid JSON "
        '(escape quotes inside strings, e.g. use \\" instead of ").',
        "bad_content",
    )


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
