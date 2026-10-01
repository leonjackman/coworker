"""Create docx / xlsx / pptx / pdf documents from structured content.

The unified content contract (documented to the model by the tool description):

* ``docx`` / ``pdf`` — ``{"title": str?, "blocks": [Block, ...]}`` where a
  ``Block`` is ``{"type": "heading", "text": ..., "level": 1}``,
  ``{"type": "paragraph", "text": ...}``, ``{"type": "bullet", "text": ...}``
  or ``{"type": "table", "rows": [[str, ...], ...]}``.
* ``xlsx`` — ``{"sheets": [{"name": ..., "rows": [[value, ...], ...]}]}``.
* ``pptx`` — ``{"slides": [{"title": ..., "bullets": [...] | "text": ..., "notes": ...}]}``.

A plain string is accepted as a shorthand for a single paragraph (docx/pdf) or
a single-cell sheet/slide, so the model is not forced to nest JSON for simple
cases.
"""

from __future__ import annotations

import io
from typing import Any

from .common import DocumentError


def build_document(fmt: str, content: Any) -> bytes:
    if fmt == "docx":
        return _build_docx(content)
    if fmt == "xlsx":
        return _build_xlsx(content)
    if fmt == "pptx":
        return _build_pptx(content)
    if fmt == "pdf":
        return _build_pdf(content)
    raise DocumentError(f"Cannot create format: {fmt}", "unsupported_format")


def _normalize_blocks(content: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(content, str):
        return "", [{"type": "paragraph", "text": line} for line in content.splitlines() if line.strip()]
    if not isinstance(content, dict):
        raise DocumentError("content must be an object or a string", "bad_content")
    title = str(content.get("title") or "")
    blocks = content.get("blocks") or []
    if isinstance(blocks, str):
        blocks = [{"type": "paragraph", "text": line} for line in blocks.splitlines() if line.strip()]
    if not isinstance(blocks, list):
        raise DocumentError("content.blocks must be a list", "bad_content")
    return title, [b for b in blocks if isinstance(b, dict)]


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _build_docx(content: Any) -> bytes:
    try:
        import docx
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"python-docx unavailable: {exc}", "dependency_missing") from exc

    title, blocks = _normalize_blocks(content)
    document = docx.Document()
    if title:
        document.add_heading(title, level=0)
    for block in blocks:
        kind = str(block.get("type") or "paragraph")
        text = str(block.get("text") or "")
        if kind == "heading":
            level = int(block.get("level") or 1)
            document.add_heading(text, level=max(1, min(level, 9)))
        elif kind == "bullet":
            document.add_paragraph(text, style="List Bullet")
        elif kind == "table":
            rows = block.get("rows") or []
            if rows:
                columns = max(len(r) for r in rows)
                table = document.add_table(rows=len(rows), cols=columns)
                for r_index, row in enumerate(rows):
                    for c_index in range(columns):
                        value = row[c_index] if c_index < len(row) else ""
                        table.cell(r_index, c_index).text = str(value)
        else:
            document.add_paragraph(text)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def _build_xlsx(content: Any) -> bytes:
    try:
        import openpyxl
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"openpyxl unavailable: {exc}", "dependency_missing") from exc

    if isinstance(content, str):
        sheets = [{"name": "Sheet1", "rows": [[line] for line in content.splitlines()]}]
    elif isinstance(content, dict):
        sheets = content.get("sheets")
        if sheets is None:
            rows = content.get("rows") or []
            sheets = [{"name": str(content.get("name") or "Sheet1"), "rows": rows}]
    else:
        raise DocumentError("content must be an object or a string", "bad_content")
    if not isinstance(sheets, list) or not sheets:
        raise DocumentError("content.sheets must be a non-empty list", "bad_content")

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for spec in sheets:
        if not isinstance(spec, dict):
            raise DocumentError("each sheet must be an object", "bad_content")
        title = str(spec.get("name") or "Sheet")
        sheet = workbook.create_sheet(title=title[:31] or "Sheet")
        for row in spec.get("rows") or []:
            if isinstance(row, (list, tuple)):
                sheet.append(list(row))
            else:
                sheet.append([row])
    if not workbook.worksheets:
        workbook.create_sheet(title="Sheet1")
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------

def _build_pptx(content: Any) -> bytes:
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"python-pptx unavailable: {exc}", "dependency_missing") from exc

    if isinstance(content, str):
        slides = [{"title": "", "bullets": [line for line in content.splitlines() if line.strip()]}]
    elif isinstance(content, dict):
        slides = content.get("slides")
        if slides is None:
            slides = [content]
    else:
        raise DocumentError("content must be an object or a string", "bad_content")
    if not isinstance(slides, list) or not slides:
        raise DocumentError("content.slides must be a non-empty list", "bad_content")

    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    for spec in slides:
        if not isinstance(spec, dict):
            spec = {"text": str(spec)}
        slide = presentation.slides.add_slide(blank)
        title = str(spec.get("title") or "")
        top = Inches(0.6)
        if title:
            box = slide.shapes.add_textbox(Inches(0.6), top, Inches(9), Inches(1))
            frame = box.text_frame
            frame.text = title
            frame.paragraphs[0].font.size = _pt(32)
            top = Inches(1.7)
        bullets = spec.get("bullets")
        if bullets is None:
            text = spec.get("text")
            bullets = [line for line in str(text).splitlines() if line.strip()] if text else []
        if isinstance(bullets, str):
            bullets = [bullets]
        if bullets:
            box = slide.shapes.add_textbox(Inches(0.8), top, Inches(8.4), Inches(4.5))
            frame = box.text_frame
            frame.word_wrap = True
            for index, item in enumerate(bullets):
                para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                para.text = f"• {item}"
        notes = str(spec.get("notes") or "")
        if notes:
            try:
                slide.notes_slide.notes_text_frame.text = notes
            except Exception:  # noqa: BLE001
                pass
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _pt(size: int):
    from pptx.util import Pt

    return Pt(size)


# ---------------------------------------------------------------------------
# PDF (reportlab)
# ---------------------------------------------------------------------------

#: reportlab base-14 fonts (Helvetica) only cover Latin-1, so any CJK/Cyrillic/
#: etc. text renders as garbage. This CID font covers the full CJK range and is
#: built into reportlab (no font file to ship; the viewer supplies the glyphs).
_CJK_FONT = "STSong-Light"


def _register_cjk_font() -> str | None:
    """Register reportlab's built-in CJK CID font; return its name or ``None``."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        if _CJK_FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
        return _CJK_FONT
    except Exception:  # noqa: BLE001 - fall back to Latin-only fonts
        return None


def _needs_unicode_font(title: str, blocks: list[dict[str, Any]]) -> bool:
    def scan(value: Any) -> bool:
        text = str(value or "")
        return any(ord(ch) > 0xFF for ch in text)

    if scan(title):
        return True
    for block in blocks:
        if scan(block.get("text")):
            return True
        for row in block.get("rows") or []:
            if isinstance(row, (list, tuple)) and any(scan(cell) for cell in row):
                return True
    return False


def _build_pdf(content: Any) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"reportlab unavailable: {exc}", "dependency_missing") from exc

    from xml.sax.saxutils import escape

    title, blocks = _normalize_blocks(content)
    styles = getSampleStyleSheet()

    # Choose a font that can actually render the content: Helvetica for pure
    # Latin, the built-in CJK CID font as soon as any non-Latin-1 codepoint
    # appears (otherwise Chinese/Traditional/Japanese text garbles to notdef).
    base_font = "Helvetica"
    if _needs_unicode_font(title, blocks):
        cjk = _register_cjk_font()
        if cjk is None:
            raise DocumentError(
                "The document contains non-Latin characters but no Unicode PDF font "
                "could be registered.",
                "font_unavailable",
            )
        base_font = cjk

    def style(name: str) -> Any:
        if base_font == "Helvetica":
            return styles[name]
        return ParagraphStyle(f"{name}__uni", parent=styles[name], fontName=base_font)

    title_style = style("Title")
    body_style = style("BodyText")
    heading_styles = {1: style("Heading1"), 2: style("Heading2"), 3: style("Heading3")}

    def para(text: str, paragraph_style: Any, prefix: str = "") -> Any:
        # Paragraph treats a raw "\n" as whitespace; convert to explicit breaks
        # so multi-line paragraph values survive. Escape first, then insert tags
        # (``prefix`` is trusted markup such as the bullet entity).
        html = escape(str(text)).replace("\n", "<br/>")
        return Paragraph(prefix + html, paragraph_style)

    story: list[Any] = []
    if title:
        story.append(para(title, title_style))
        story.append(Spacer(1, 0.4 * cm))

    for block in blocks:
        kind = str(block.get("type") or "paragraph")
        text = str(block.get("text") or "")
        if kind == "heading":
            level = int(block.get("level") or 1)
            story.append(para(text, heading_styles[min(max(level, 1), 3)]))
        elif kind == "bullet":
            story.append(para(text, body_style, prefix="&bull; "))
        elif kind == "table":
            rows = [[str(cell) for cell in row] for row in (block.get("rows") or [])]
            if rows:
                table = Table(rows)
                table.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                            ("FONTNAME", (0, 0), (-1, -1), base_font),
                        ]
                    )
                )
                story.append(table)
                story.append(Spacer(1, 0.3 * cm))
        else:
            story.append(para(text, body_style))
            story.append(Spacer(1, 0.15 * cm))

    if not story:
        story.append(Paragraph("", body_style))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    doc.build(story)
    return buffer.getvalue()
