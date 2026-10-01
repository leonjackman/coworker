"""Convert documents: pdf<->images / pdf->text, and Office->pdf.

Returns a list of output blobs (``{"ext", "data", "mime"}``) so the caller can
decide file names and write them through ``Workspace.write_binary``. Office→PDF
uses an engine ladder (LibreOffice, then macOS iWork via AppleScript) because no
pure-Python renderer produces faithful Office layout.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .common import DocumentError
from .extract import read_document_bytes

_TEXT_TARGETS = {"text", "txt", "md", "markdown"}
_IMAGE_TARGETS = {"png", "jpg", "jpeg"}
_PDF_TARGET = "pdf"


def convert_document(
    fmt: str,
    data: bytes,
    to_format: str,
    *,
    source_name: str = "",
    pages: Any = None,
) -> dict[str, Any]:
    target = str(to_format or "").strip().lower()
    if target in _TEXT_TARGETS:
        return _to_text(fmt, data, target)
    if target in _IMAGE_TARGETS:
        return _to_images(fmt, data, target, pages)
    if target == _PDF_TARGET:
        return _to_pdf(fmt, data, source_name)
    raise DocumentError(f"Unsupported conversion target: {to_format}", "bad_target")


def _to_text(fmt: str, data: bytes, target: str) -> dict[str, Any]:
    result = read_document_bytes(data, fmt)
    text = str(result.get("text") or "")
    ext = ".md" if target in {"md", "markdown"} else ".txt"
    return {
        "engine": "extract",
        "lossless": False,
        "note": "Text extraction only: original layout, styling, tables and images are not preserved.",
        "outputs": [{"ext": ext, "data": text.encode("utf-8"), "mime": "text/markdown" if ext == ".md" else "text/plain"}],
    }


def _to_images(fmt: str, data: bytes, target: str, pages: Any) -> dict[str, Any]:
    if fmt != "pdf":
        raise DocumentError("Only PDF files can be converted to images.", "unsupported_conversion")
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"pypdfium2 unavailable: {exc}", "dependency_missing") from exc

    image_format = "JPEG" if target in {"jpg", "jpeg"} else "PNG"
    ext = ".jpg" if image_format == "JPEG" else ".png"
    mime = "image/jpeg" if image_format == "JPEG" else "image/png"
    indices = _parse_page_indices(pages)

    outputs: list[dict[str, Any]] = []
    pdf = pdfium.PdfDocument(data)
    try:
        total = len(pdf)
        selected = indices if indices is not None else list(range(total))
        for index in selected:
            if index < 0 or index >= total:
                raise DocumentError(f"page {index + 1} out of range (1-{total})", "bad_pages")
            page = pdf[index]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            if image_format == "JPEG":
                image.convert("RGB").save(buffer, "JPEG", quality=85)
            else:
                image.save(buffer, "PNG")
            outputs.append({"ext": ext, "data": buffer.getvalue(), "mime": mime, "page": index + 1})
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass
    if not outputs:
        raise DocumentError("no pages were rendered", "bad_pages")
    return {
        "engine": "pypdfium2",
        "lossless": False,
        "note": "Pages rasterized to images (2x); the result is a picture, text is no longer selectable.",
        "outputs": outputs,
    }


def _to_pdf(fmt: str, data: bytes, source_name: str) -> dict[str, Any]:
    if fmt == "pdf":
        return {
            "engine": "copy",
            "lossless": True,
            "note": "Byte-identical copy of the original PDF.",
            "outputs": [{"ext": ".pdf", "data": data, "mime": "application/pdf"}],
        }
    if fmt in {"docx", "xlsx", "pptx"}:
        pdf_bytes, engine = _office_to_pdf(fmt, data, source_name)
        return {
            "engine": engine,
            "lossless": True,
            "note": (
                f"Exported from the ORIGINAL .{fmt} file with a real layout engine ({engine}); "
                "the original layout/formatting is preserved."
            ),
            "outputs": [{"ext": ".pdf", "data": pdf_bytes, "mime": "application/pdf"}],
        }
    if fmt == "image":
        return {
            "engine": "pillow",
            "lossless": True,
            "note": "Image embedded unchanged as a single PDF page.",
            "outputs": [{"ext": ".pdf", "data": _image_to_pdf(data), "mime": "application/pdf"}],
        }
    raise DocumentError(f"Cannot convert {fmt} to PDF.", "unsupported_conversion")


def _parse_page_indices(pages: Any) -> list[int] | None:
    if pages in (None, "", []):
        return None
    tokens: list[str] = []
    if isinstance(pages, int):
        tokens = [str(pages)]
    elif isinstance(pages, (list, tuple)):
        tokens = [str(item) for item in pages]
    else:
        tokens = [part.strip() for part in str(pages).split(",")]
    indices: list[int] = []
    for token in tokens:
        if not token:
            continue
        if "-" in token:
            start_s, _, end_s = token.partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else start
            for number in range(start, end + 1):
                indices.append(number - 1)
        else:
            indices.append(int(token) - 1)
    return indices


def _image_to_pdf(data: bytes) -> bytes:
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Pillow unavailable: {exc}", "dependency_missing") from exc
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Not a valid image: {exc}", "corrupt_document") from exc
    if image.mode in {"RGBA", "P", "LA"}:
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PDF")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Office -> PDF engine ladder
# ---------------------------------------------------------------------------

_APP_BY_FORMAT = {"docx": "Pages", "xlsx": "Numbers", "pptx": "Keynote"}
_SUFFIX_BY_FORMAT = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx"}


def _office_to_pdf(fmt: str, data: bytes, source_name: str) -> tuple[bytes, str]:
    errors: list[str] = []
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            return _libreoffice_to_pdf(soffice, data, fmt), "libreoffice"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LibreOffice: {exc}")
    if sys.platform == "darwin" and _app_installed(_APP_BY_FORMAT[fmt]):
        try:
            return _applescript_to_pdf(fmt, data, source_name), "applescript"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"AppleScript: {exc}")
    detail = ("; ".join(errors)) if errors else "no LibreOffice and no compatible macOS app found"
    raise DocumentError(
        "Cannot convert Office document to PDF: " + detail + ". Install LibreOffice, or open and "
        "export the file manually.",
        "conversion_unavailable",
    )


def _libreoffice_to_pdf(soffice: str, data: bytes, fmt: str) -> bytes:
    suffix = _SUFFIX_BY_FORMAT[fmt]
    with tempfile.TemporaryDirectory() as workdir:
        src = os.path.join(workdir, f"input{suffix}")
        with open(src, "wb") as handle:
            handle.write(data)
        proc = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", workdir, src],
            capture_output=True,
            text=True,
            timeout=180,
            shell=False,
        )
        out = os.path.join(workdir, "input.pdf")
        if proc.returncode != 0 or not os.path.exists(out):
            raise DocumentError((proc.stderr or proc.stdout or "conversion failed")[:300], "conversion_failed")
        with open(out, "rb") as handle:
            return handle.read()


def _app_installed(app_name: str) -> bool:
    candidates = [
        f"/Applications/{app_name}.app",
        os.path.expanduser(f"~/Applications/{app_name}.app"),
    ]
    return any(os.path.isdir(path) for path in candidates)


def _applescript_to_pdf(fmt: str, data: bytes, source_name: str) -> bytes:
    app_name = _APP_BY_FORMAT[fmt]
    suffix = _SUFFIX_BY_FORMAT[fmt]
    with tempfile.TemporaryDirectory() as workdir:
        src = os.path.join(workdir, f"input{suffix}")
        out = os.path.join(workdir, "output.pdf")
        with open(src, "wb") as handle:
            handle.write(data)
        script = f'''
set inFile to POSIX file "{src}"
set outFile to POSIX file "{out}"
tell application "{app_name}"
    set d to open inFile
    export d to outFile as PDF
    close d saving no
end tell
'''
        proc = subprocess.run(
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=180,
            shell=False,
        )
        if proc.returncode != 0 or not os.path.exists(out):
            raise DocumentError((proc.stderr or "AppleScript export failed")[:300], "conversion_failed")
        with open(out, "rb") as handle:
            return handle.read()
