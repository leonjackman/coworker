"""LangChain tools for reading / creating / editing / converting documents.

Four unified tools are exposed to the agent:

* ``read_document``   — read-only extraction (docx/xlsx/pptx/pdf).
* ``create_document`` — write a NEW document from structured content.
* ``edit_document``   — in-place operations on an existing document.
* ``convert_document``— produce a new file (pdf<->images/text, Office->pdf, merge).

Every write goes through ``Workspace.write_binary`` so it is sandbox-checked,
atomic, audited and revertable. Tools return JSON and never raise.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .build import build_document
from .common import (
    DEFAULT_READ_MAX_CHARS,
    IMAGE_SUFFIXES,
    DocumentError,
    detect_format,
    parse_structured,
    suffix_of,
)
from .convert import convert_document as _convert_document
from .edit import edit_document_bytes
from .extract import read_document_bytes


class ReadDocumentArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative path to a .docx/.xlsx/.pptx/.pdf file.")
    max_chars: int = Field(
        default=DEFAULT_READ_MAX_CHARS,
        ge=1000,
        le=200_000,
        description="Cap on the returned text length (default 20000).",
    )


class CreateDocumentArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative path to create; the extension picks the format (.docx/.xlsx/.pptx/.pdf).")
    content: str | dict | list = Field(
        description=(
            "Author a NEW document from structured content (this does NOT convert or preserve an existing "
            "file's formatting — use convert_document for that). JSON describing the document. docx/pdf: "
            '{"title": str?, "blocks": [{"type":"heading"|"paragraph"|"bullet","text":str,"level":int?}, '
            '{"type":"table","rows":[[str,...],...]}]}. '
            'xlsx: {"sheets":[{"name":str,"rows":[[value,...],...]}]}. '
            'pptx: {"slides":[{"title":str?,"bullets":[str,...],"notes":str?}]}. '
            "A plain (non-JSON) string is treated as paragraphs / a single sheet / a text slide."
        )
    )


class EditDocumentArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative path to an existing .docx/.xlsx/.pptx/.pdf file.")
    operations: str | dict | list = Field(
        description=(
            'JSON array of operations. docx: {"op":"replace_text","old":..,"new":..,"count":int?}, '
            '{"op":"append_paragraph","text":..,"style":..?}, {"op":"delete_paragraph","index":int}. '
            'xlsx: {"op":"set_cell","sheet":..,"cell":"A1","value":..}, {"op":"append_row","sheet":..,"values":[..]}, '
            '{"op":"delete_rows","sheet":..,"start":int,"count":int}, {"op":"add_sheet","name":..}, {"op":"delete_sheet","name":..}. '
            'pptx: {"op":"add_slide","title":..,"bullets":[..]}, {"op":"delete_slide","index":int(1-based)}, {"op":"replace_text","old":..,"new":..}. '
            'pdf: {"op":"delete_pages","pages":"1,3-5"}, {"op":"rotate","pages":"1-3","degrees":90}, {"op":"set_metadata","title":..,"author":..}, {"op":"encrypt","password":..}.'
        )
    )


class ConvertDocumentArgs(BaseModel):
    file_path: str = Field(
        description=(
            "Workspace-relative source path. A comma-separated list of .pdf files merges them into one PDF "
            "(only when to_format is 'pdf')."
        )
    )
    to_format: str = Field(
        description=(
            "Target: pdf | png | jpg | text | md. Fidelity: docx/xlsx/pptx -> pdf is a LOSSLESS export "
            "that renders the ORIGINAL file (engine: libreoffice | applescript), preserving its layout; "
            "pdf -> pdf is a byte-identical copy; pdf -> png/jpg rasterizes pages; pdf -> text/md extracts "
            "text only (layout/tables lost). The result JSON reports engine, lossless and a note."
        )
    )
    output_path: str = Field(default="", description="Optional output path (defaults to the source name with the new extension).")
    pages: str = Field(default="", description="For pdf->images: page selection like '1,3-5' (default all).")


def build_document_tools(
    workspace: Any,
    audit_context: dict[str, Any] | None = None,
    change_store: Any = None,
) -> list[Any]:
    from langchain_core.tools import tool

    def _turn_index() -> int:
        return int(audit_context.get("turn_index", 1) if audit_context else 1)

    def _read_bytes(file_path: str) -> bytes:
        target = workspace.resolve_read_path(file_path)
        if not target.is_file():
            raise DocumentError(f"Not a file: {file_path}", "not_found")
        return target.read_bytes()

    def _rel(file_path: str) -> str:
        try:
            return workspace.normalize_rel_path(file_path)
        except Exception:  # noqa: BLE001
            return file_path

    @tool(args_schema=ReadDocumentArgs)
    def read_document(file_path: str, max_chars: int = DEFAULT_READ_MAX_CHARS) -> str:
        """Read a docx/xlsx/pptx/pdf document and return its text and light structure."""
        try:
            fmt = detect_format(file_path)
            data = _read_bytes(file_path)
            result = read_document_bytes(data, fmt, max_chars=max_chars)
            result["path"] = _rel(file_path)
            workspace.mark_read(file_path)
            return json.dumps(result, ensure_ascii=False)
        except DocumentError as exc:
            return json.dumps({"error": str(exc), "error_code": exc.error_code}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - a tool must never break the turn
            return json.dumps({"error": str(exc)[:400], "error_code": "document_error"}, ensure_ascii=False)

    @tool(args_schema=CreateDocumentArgs)
    def create_document(file_path: str, content: str) -> str:
        """Create a new docx/xlsx/pptx/pdf document from structured JSON content."""
        try:
            fmt = detect_format(file_path)
            payload = parse_structured(content, "content")
            data = build_document(fmt, payload)
            workspace.write_binary(
                file_path,
                data,
                tool_name="create_document",
                audit_context=audit_context,
                change_store=change_store,
                turn_index=_turn_index(),
            )
            return json.dumps(
                {
                    "status": "ok",
                    "path": _rel(file_path),
                    "format": fmt,
                    "bytes": len(data),
                    "lossless": False,
                    "note": "New document authored from the provided blocks; no source file was converted.",
                },
                ensure_ascii=False,
            )
        except DocumentError as exc:
            return json.dumps({"error": str(exc), "error_code": exc.error_code}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)[:400], "error_code": "document_error"}, ensure_ascii=False)

    @tool(args_schema=EditDocumentArgs)
    def edit_document(file_path: str, operations: str) -> str:
        """Edit an existing docx/xlsx/pptx/pdf document with JSON operations."""
        try:
            fmt = detect_format(file_path)
            data = _read_bytes(file_path)
            ops = parse_structured(operations, "operations")
            if isinstance(ops, dict):
                ops = [ops]
            if not isinstance(ops, list):
                raise DocumentError("operations must be a JSON array or object.", "bad_operations")
            new_data = edit_document_bytes(fmt, data, ops)
            workspace.write_binary(
                file_path,
                new_data,
                tool_name="edit_document",
                audit_context=audit_context,
                change_store=change_store,
                turn_index=_turn_index(),
            )
            return json.dumps(
                {"status": "ok", "path": _rel(file_path), "format": fmt, "operations": len(ops), "bytes": len(new_data)},
                ensure_ascii=False,
            )
        except DocumentError as exc:
            return json.dumps({"error": str(exc), "error_code": exc.error_code}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)[:400], "error_code": "document_error"}, ensure_ascii=False)

    @tool(args_schema=ConvertDocumentArgs)
    def convert_document(file_path: str, to_format: str, output_path: str = "", pages: str = "") -> str:
        """Convert a document (Office->pdf losslessly from the original; pdf<->images/text) or merge PDFs.

        Office->pdf feeds the ORIGINAL .docx/.xlsx/.pptx to a real layout engine,
        so the layout is preserved (lossless). The result reports engine/lossless/note.
        """
        try:
            sources = [part.strip() for part in str(file_path).split(",") if part.strip()]
            if not sources:
                raise DocumentError("file_path is required", "not_found")
            if len(sources) > 1:
                return _merge_pdfs(workspace, sources, output_path, audit_context, change_store, _turn_index())
            source = sources[0]
            # Images convert to PDF; detect by suffix before the format check.
            if suffix_of(source) in IMAGE_SUFFIXES:
                if str(to_format).lower() != "pdf":
                    raise DocumentError("Images can only be converted to PDF.", "unsupported_conversion")
                fmt = "image"
            else:
                fmt = detect_format(source)
            data = _read_bytes(source)
            result = _convert_document(fmt, data, to_format, source_name=source, pages=pages)
            outputs = result.get("outputs") or []
            written: list[str] = []
            stem = str(Path(source).with_suffix(""))
            source_rel = _rel(source)
            for index, out in enumerate(outputs):
                target = _output_name(output_path, stem, out, len(outputs), index)
                # No-op guard: a conversion that writes identical bytes back over
                # the source (e.g. pdf -> pdf) must not create a change record.
                if _rel(target) == source_rel:
                    try:
                        if workspace.resolve_read_path(source).read_bytes() == out["data"]:
                            written.append(source_rel)
                            continue
                    except Exception:  # noqa: BLE001 - fall through to a real write
                        pass
                workspace.write_binary(
                    target,
                    out["data"],
                    tool_name="convert_document",
                    audit_context=audit_context,
                    change_store=change_store,
                    turn_index=_turn_index(),
                )
                written.append(_rel(target))
            return json.dumps(
                {
                    "status": "ok",
                    "source_format": fmt,
                    "to_format": str(to_format).lower(),
                    "engine": result.get("engine"),
                    "lossless": bool(result.get("lossless")),
                    "note": result.get("note") or "",
                    "outputs": written,
                },
                ensure_ascii=False,
            )
        except DocumentError as exc:
            return json.dumps({"error": str(exc), "error_code": exc.error_code}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)[:400], "error_code": "document_error"}, ensure_ascii=False)

    return [read_document, create_document, edit_document, convert_document]


def _output_name(output_path: str, stem: str, out: dict[str, Any], total: int, index: int) -> str:
    ext = str(out.get("ext") or "")
    if output_path:
        if total > 1:
            base = str(Path(output_path).with_suffix(""))
            page = out.get("page") or (index + 1)
            return f"{base}-p{page}{ext}"
        return output_path
    return f"{stem}{ext}"


def _merge_pdfs(
    workspace: Any,
    sources: list[str],
    output_path: str,
    audit_context: dict[str, Any] | None,
    change_store: Any,
    turn_index: int,
) -> str:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"pypdf unavailable: {exc}", "error_code": "dependency_missing"}, ensure_ascii=False)
    writer = PdfWriter()
    try:
        for source in sources:
            if detect_format(source) != "pdf":
                raise DocumentError(f"merge only supports PDFs: {source}", "unsupported_conversion")
            data = workspace.resolve_read_path(source).read_bytes()
            reader = PdfReader(io.BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        target = output_path or f"{str(Path(sources[0]).with_suffix(''))}-merged.pdf"
        workspace.write_binary(
            target,
            buffer.getvalue(),
            tool_name="convert_document",
            audit_context=audit_context,
            change_store=change_store,
            turn_index=turn_index,
        )
        return json.dumps(
            {
                "status": "ok",
                "source_format": "pdf",
                "to_format": "pdf",
                "engine": "pypdf",
                "lossless": True,
                "note": f"Merged {len(sources)} PDFs by concatenating their pages unchanged.",
                "outputs": [target],
            },
            ensure_ascii=False,
        )
    except DocumentError as exc:
        return json.dumps({"error": str(exc), "error_code": exc.error_code}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)[:400], "error_code": "document_error"}, ensure_ascii=False)
