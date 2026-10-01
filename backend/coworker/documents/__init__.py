"""Office (docx/xlsx/pptx) + PDF document capability.

Public surface:
* ``build_document_tools`` — the four agent tools.
* ``read_document_bytes`` / ``build_document`` / ``edit_document_bytes`` /
  ``convert_document`` — the format-level primitives (also used for chat
  attachment extraction).
* ``detect_format`` / ``decode_data_url`` / ``DocumentError`` — helpers.
"""

from .build import build_document
from .common import (
    DEFAULT_READ_MAX_CHARS,
    FORMAT_BY_SUFFIX,
    DocumentError,
    decode_data_url,
    detect_format,
    suffix_of,
)
from .convert import convert_document
from .edit import edit_document_bytes
from .extract import extract_text, read_document_bytes
from .tools import build_document_tools

__all__ = [
    "DEFAULT_READ_MAX_CHARS",
    "FORMAT_BY_SUFFIX",
    "DocumentError",
    "build_document",
    "build_document_tools",
    "convert_document",
    "decode_data_url",
    "detect_format",
    "edit_document_bytes",
    "extract_text",
    "read_document_bytes",
    "suffix_of",
]
