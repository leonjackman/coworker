"""Packaging self-check for the document stack.

Run from a source venv (pre-build gate) and from the frozen PyInstaller binary
(``pybackend --selfcheck-documents``) to prove that every Office/PDF dependency —
plus its bundled data files (python-docx/python-pptx templates, pdfminer cmaps,
reportlab fonts) and native library (pdfium) — made it into the bundle.

Exercises the real create → read → edit → convert paths so a missing template,
font, cmap or dylib fails the build instead of a user's first request.
"""

from __future__ import annotations

import traceback

_DOCX = {"blocks": [{"type": "paragraph", "text": "selfcheck docx"}]}
_XLSX = {"sheets": [{"name": "S1", "rows": [["k", "v"], ["selfcheck", 1]]}]}
_PPTX = {"slides": [{"title": "Selfcheck", "bullets": ["bullet"]}]}
_PDF = {"blocks": [{"type": "paragraph", "text": "selfcheck pdf"}]}


def run_documents_selfcheck() -> int:
    """Return 0 on success; print the failure and return 1 otherwise."""
    try:
        from .build import build_document
        from .common import parse_structured
        from .convert import convert_document
        from .edit import edit_document_bytes
        from .extract import read_document_bytes

        docx = build_document("docx", _DOCX)
        assert "selfcheck docx" in read_document_bytes(docx, "docx")["text"], "docx read"

        xlsx = build_document("xlsx", _XLSX)
        assert "selfcheck" in read_document_bytes(xlsx, "xlsx")["text"], "xlsx read"
        xlsx2 = edit_document_bytes("xlsx", xlsx, [{"op": "set_cell", "sheet": "S1", "cell": "C1", "value": "edited"}])
        assert "edited" in read_document_bytes(xlsx2, "xlsx")["text"], "xlsx edit"

        pptx = build_document("pptx", _PPTX)
        assert "Selfcheck" in read_document_bytes(pptx, "pptx")["text"], "pptx read"

        pdf = build_document("pdf", _PDF)
        assert "selfcheck pdf" in read_document_bytes(pdf, "pdf")["text"], "pdf read (reportlab + pdfminer)"

        png = convert_document("pdf", pdf, "png", source_name="sc.pdf")
        assert png["outputs"] and png["outputs"][0]["data"], "pdf->png (pypdfium2 native lib)"

        # CJK PDF + json_repair + pdfminer cmaps: a real-world Chinese payload
        # with the unescaped quotes an LLM commonly emits.
        malformed = '{"title": "测试", "blocks": [{"type": "paragraph", "text": "取得"预备"身份"}]}'
        cjk_pdf = build_document("pdf", parse_structured(malformed, "content"))
        extracted = read_document_bytes(cjk_pdf, "pdf")["text"]
        assert "预备" in extracted and "测试" in extracted, "cjk pdf + json_repair + cmaps"

        print("documents selfcheck: OK (docx, xlsx, pptx, pdf, pdf->png, cjk+repair)")
        return 0
    except Exception:  # noqa: BLE001 - the point is to surface any packaging gap
        traceback.print_exc()
        print("documents selfcheck: FAILED")
        return 1


if __name__ == "__main__":
    raise SystemExit(run_documents_selfcheck())
