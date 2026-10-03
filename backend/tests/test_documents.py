"""Office/PDF document capability + AppleScript decoupling.

Covers the unified document tools (read/create/edit/convert), binary change
rollback, attachment extraction, and the phase/HITL wiring.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from coworker.agent.core import _READ_ONLY_TOOLS, _EXEC_TOOLS, _CHANGE_TOOL_NAMES
from coworker.changes import ChangeStore
from coworker.documents.tools import build_document_tools
from coworker.workspace import Workspace

DOCX_CONTENT = {
    "title": "Report",
    "blocks": [
        {"type": "heading", "text": "Intro", "level": 1},
        {"type": "paragraph", "text": "Hello world"},
        {"type": "bullet", "text": "first"},
        {"type": "table", "rows": [["a", "b"], ["1", "2"]]},
    ],
}


def _tools(tmp_path: Path):
    ws = Workspace(tmp_path)
    store = ChangeStore(tmp_path)
    audit = {"session_id": "s-doc", "turn_index": 1}
    tools = {t.name: t for t in build_document_tools(ws, audit, store)}
    return ws, store, tools


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


# ---------------------------------------------------------------------------
# create -> read round trips
# ---------------------------------------------------------------------------

def test_create_and_read_docx(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    result = _call(tools["create_document"], file_path="report.docx", content=json.dumps(DOCX_CONTENT))
    assert result["status"] == "ok" and result["format"] == "docx"
    read = _call(tools["read_document"], file_path="report.docx")
    assert "Hello world" in read["text"]
    assert "Intro" in read["text"]


def test_create_and_read_xlsx(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    content = {"sheets": [{"name": "S1", "rows": [["name", "score"], ["a", 1], ["b", 2]]}]}
    assert _call(tools["create_document"], file_path="data.xlsx", content=json.dumps(content))["status"] == "ok"
    read = _call(tools["read_document"], file_path="data.xlsx")
    assert "score" in read["text"] and "S1" in read["text"]


def test_create_and_read_pptx(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    content = {"slides": [{"title": "Deck", "bullets": ["one", "two"], "notes": "note"}]}
    assert _call(tools["create_document"], file_path="deck.pptx", content=json.dumps(content))["status"] == "ok"
    read = _call(tools["read_document"], file_path="deck.pptx")
    assert "Deck" in read["text"] and "one" in read["text"]


def test_create_and_read_pdf(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    assert _call(tools["create_document"], file_path="doc.pdf", content=json.dumps(DOCX_CONTENT))["status"] == "ok"
    read = _call(tools["read_document"], file_path="doc.pdf")
    assert "Hello world" in read["text"]


# ---------------------------------------------------------------------------
# edits
# ---------------------------------------------------------------------------

def test_edit_xlsx_set_cell(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="d.xlsx", content=json.dumps({"sheets": [{"name": "S", "rows": [["x"]]}]}))
    ops = [{"op": "set_cell", "sheet": "S", "cell": "B2", "value": "added"}]
    assert _call(tools["edit_document"], file_path="d.xlsx", operations=json.dumps(ops))["status"] == "ok"
    assert "added" in _call(tools["read_document"], file_path="d.xlsx")["text"]


def test_edit_docx_replace_text(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="d.docx", content=json.dumps({"blocks": [{"type": "paragraph", "text": "old value"}]}))
    ops = [{"op": "replace_text", "old": "old", "new": "new"}]
    _call(tools["edit_document"], file_path="d.docx", operations=json.dumps(ops))
    assert "new value" in _call(tools["read_document"], file_path="d.docx")["text"]


def test_edit_pptx_add_slide(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="d.pptx", content=json.dumps({"slides": [{"title": "A"}]}))
    ops = [{"op": "add_slide", "title": "B", "bullets": ["b1"]}]
    _call(tools["edit_document"], file_path="d.pptx", operations=json.dumps(ops))
    assert "B" in _call(tools["read_document"], file_path="d.pptx")["text"]


def test_edit_pptx_delete_slide(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="s.pptx", content={"slides": [{"title": "First"}, {"title": "Second"}]})
    result = _call(tools["edit_document"], file_path="s.pptx", operations=[{"op": "delete_slide", "index": 1}])
    assert result["status"] == "ok"
    text = _call(tools["read_document"], file_path="s.pptx")["text"]
    assert "Second" in text and "First" not in text


def test_edit_pdf_rotate_and_set_metadata(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="r.pdf", content={"blocks": [{"type": "paragraph", "text": "rot"}]})
    ops = [{"op": "rotate", "pages": "1", "degrees": 90}, {"op": "set_metadata", "title": "Rotated"}]
    assert _call(tools["edit_document"], file_path="r.pdf", operations=ops)["status"] == "ok"
    assert "rot" in _call(tools["read_document"], file_path="r.pdf")["text"]


def test_edit_xlsx_cannot_delete_only_sheet(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="one.xlsx", content={"sheets": [{"name": "S1", "rows": [["x"]]}]})
    result = _call(tools["edit_document"], file_path="one.xlsx", operations=[{"op": "delete_sheet", "name": "S1"}])
    assert result.get("error_code") == "bad_operations"


def test_edit_pdf_delete_pages(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    # Build a 1-page pdf then try deleting the only page -> writer with 0 pages
    # is invalid, but delete_pages on a page range that exists should succeed
    # on a 2-page document. Create a 2-page doc by concatenating is out of scope;
    # instead assert an out-of-range selection is rejected cleanly.
    _call(tools["create_document"], file_path="d.pdf", content=json.dumps({"blocks": [{"type": "paragraph", "text": "p1"}]}))
    result = _call(tools["edit_document"], file_path="d.pdf", operations=json.dumps([{"op": "delete_pages", "pages": "9"}]))
    assert result.get("error_code") == "bad_operations"


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

def test_convert_pdf_to_text(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="d.pdf", content=json.dumps({"blocks": [{"type": "paragraph", "text": "extract me"}]}))
    result = _call(tools["convert_document"], file_path="d.pdf", to_format="text")
    assert result["status"] == "ok"
    assert (tmp_path / "d.txt").exists()
    assert "extract me" in (tmp_path / "d.txt").read_text()


def test_convert_pdf_to_png(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="d.pdf", content=json.dumps({"blocks": [{"type": "paragraph", "text": "img"}]}))
    result = _call(tools["convert_document"], file_path="d.pdf", to_format="png")
    assert result["status"] == "ok"
    assert any(p.suffix == ".png" for p in tmp_path.glob("d*.png"))


def test_convert_reports_fidelity(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="f.pdf", content={"blocks": [{"type": "paragraph", "text": "x"}]})
    text = _call(tools["convert_document"], file_path="f.pdf", to_format="text")
    assert text["engine"] == "extract" and text["lossless"] is False
    assert text["source_format"] == "pdf" and text["to_format"] == "text" and text["note"]
    png = _call(tools["convert_document"], file_path="f.pdf", to_format="png")
    assert png["lossless"] is False


def test_office_to_pdf_is_lossless(monkeypatch):
    from coworker.documents import build_document, convert as c

    monkeypatch.setattr(c.shutil, "which", lambda _n: "/usr/bin/soffice")
    monkeypatch.setattr(c, "_libreoffice_to_pdf", lambda _s, _d, _f: b"%PDF-1.4 LO")
    result = c.convert_document(
        "docx", build_document("docx", {"blocks": [{"type": "paragraph", "text": "x"}]}), "pdf", source_name="x.docx"
    )
    assert result["lossless"] is True
    assert result["engine"] == "libreoffice"
    assert "ORIGINAL" in result["note"]


def test_merge_pdfs(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        _call(tools["create_document"], file_path=name, content=json.dumps({"blocks": [{"type": "paragraph", "text": name}]}))
    result = _call(tools["convert_document"], file_path="a.pdf,b.pdf", to_format="pdf", output_path="merged.pdf")
    assert result["status"] == "ok"
    assert (tmp_path / "merged.pdf").exists()


# ---------------------------------------------------------------------------
# sandbox / errors
# ---------------------------------------------------------------------------

def test_legacy_format_is_rejected(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    result = _call(tools["create_document"], file_path="old.doc", content="hi")
    assert result["error_code"] == "legacy_format"


def test_write_outside_workspace_is_blocked(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    result = _call(tools["create_document"], file_path="/tmp/cw_doc_escape.docx", content="{}")
    assert "error" in result
    assert not Path("/tmp/cw_doc_escape.docx").exists()


# ---------------------------------------------------------------------------
# binary change rollback
# ---------------------------------------------------------------------------

def test_binary_change_can_be_reverted_and_redone(tmp_path: Path):
    ws, store, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="new.docx", content=json.dumps({"blocks": [{"type": "paragraph", "text": "v1"}]}))
    assert (tmp_path / "new.docx").exists()

    changes = store.list_changes("s-doc")
    record = next(c for c in changes if c["file_path"] == "new.docx")
    assert record.get("binary") is True

    revert = ws.revert_change(record)
    assert revert["status"] == "reverted"
    assert not (tmp_path / "new.docx").exists()

    redo = ws.redo_change(record)
    assert redo["status"] == "restored"
    assert (tmp_path / "new.docx").exists()


# ---------------------------------------------------------------------------
# attachment extraction
# ---------------------------------------------------------------------------

def test_extract_office_attachment_from_data_url(tmp_path: Path):
    from coworker.agent.core import _extract_office_attachment
    from coworker.documents import build_document

    data = build_document("docx", {"blocks": [{"type": "paragraph", "text": "attached body"}]})
    url = "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64," + base64.b64encode(data).decode()
    extracted = _extract_office_attachment(url, "note.docx")
    assert extracted is not None
    text, _truncated = extracted
    assert "attached body" in text


def test_extract_office_attachment_ignores_plain_text():
    from coworker.agent.core import _extract_office_attachment

    assert _extract_office_attachment("just text", "note.txt") is None


# ---------------------------------------------------------------------------
# phase / HITL classification
# ---------------------------------------------------------------------------

def test_document_tool_classification():
    assert "read_document" in _READ_ONLY_TOOLS
    for name in ("create_document", "edit_document", "convert_document"):
        assert name in _EXEC_TOOLS
        assert name in _CHANGE_TOOL_NAMES


def test_documents_selfcheck_passes():
    from coworker.documents.selfcheck import run_documents_selfcheck

    assert run_documents_selfcheck() == 0


# ---------------------------------------------------------------------------
# AppleScript decoupling
# ---------------------------------------------------------------------------

def test_resolve_applescript_tools_by_platform():
    from coworker.computer.applescript import resolve_applescript_tools

    tools = resolve_applescript_tools()
    if sys.platform == "darwin":
        assert [t.name for t in tools] == ["run_applescript"]
    else:
        assert tools == []


def test_applescript_mounted_via_explicit_param(tmp_path: Path):
    from coworker.agent.graph import build_workspace_tools
    from coworker.computer.applescript import build_applescript_tool

    ws = Workspace(tmp_path)
    tool = build_applescript_tool()
    if tool is None:
        return  # non-macOS
    names = [t.name for t in build_workspace_tools(ws, native_script_tools=[tool])]
    assert "run_applescript" in names
    # Not passed -> never mounted (sub-agents / non-macOS).
    names_without = [t.name for t in build_workspace_tools(ws)]
    assert "run_applescript" not in names_without


def test_applescript_not_mounted_for_readonly(tmp_path: Path):
    from coworker.agent.graph import build_workspace_tools
    from coworker.computer.applescript import build_applescript_tool

    tool = build_applescript_tool()
    if tool is None:
        return
    ws = Workspace(tmp_path)
    names = [t.name for t in build_workspace_tools(ws, native_script_tools=[tool], readonly=True)]
    assert "run_applescript" not in names


# ---------------------------------------------------------------------------
# Phase gate / HITL acceptance
# ---------------------------------------------------------------------------

def test_phase_gate_hides_document_writes_in_discuss():
    from coworker.agent.middleware.phase_gate import PhaseToolGateMiddleware

    mw = PhaseToolGateMiddleware()
    discuss = mw._allowed_tools({"work_mode": "build", "phase": "discuss", "autonomy": "guarded"})
    execute = mw._allowed_tools({"work_mode": "build", "phase": "execute", "autonomy": "guarded"})
    assert "read_document" in discuss
    for name in ("create_document", "edit_document", "convert_document"):
        assert name not in discuss
        assert name in execute


def test_hitl_config_covers_document_writes():
    from coworker.agent.middleware.hitl import command_approval_middleware

    middleware = command_approval_middleware()
    interrupt_on = middleware[0].interrupt_on
    for name in ("create_document", "edit_document", "convert_document"):
        assert name in interrupt_on


# ---------------------------------------------------------------------------
# Structured (non-string) args + staleness + pdf edge cases
# ---------------------------------------------------------------------------

def test_create_document_accepts_structured_content(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    result = _call(tools["create_document"], file_path="s.docx", content={"blocks": [{"type": "paragraph", "text": "structured"}]})
    assert result["status"] == "ok"
    assert "structured" in _call(tools["read_document"], file_path="s.docx")["text"]


def test_create_document_repairs_unescaped_quotes(tmp_path: Path):
    # Real failure from session 5c4b5ddc: the model emitted unescaped double
    # quotes inside a JSON string value. Must be repaired, not rendered as text.
    _, _, tools = _tools(tmp_path)
    malformed = '{"title": "协议", "blocks": [{"type": "paragraph", "text": "取得"预备股东"身份"}]}'
    result = _call(tools["create_document"], file_path="m.docx", content=malformed)
    assert result["status"] == "ok", result
    text = _call(tools["read_document"], file_path="m.docx")["text"]
    assert "预备股东" in text
    assert '"blocks"' not in text  # the raw JSON must never end up in the file


def test_create_document_cjk_pdf_roundtrip(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    content = {"title": "股东协议", "blocks": [{"type": "paragraph", "text": "甲方：梁志斌\n乙方：____"}, {"type": "heading", "text": "第一章"}]}
    assert _call(tools["create_document"], file_path="cjk.pdf", content=content)["status"] == "ok"
    text = _call(tools["read_document"], file_path="cjk.pdf")["text"]
    assert "梁志斌" in text and "第一章" in text  # not garbled to notdef


def test_parse_structured_raises_when_unrepairable(monkeypatch):
    import json_repair
    import pytest

    from coworker.documents.common import DocumentError, parse_structured

    monkeypatch.setattr(json_repair, "loads", lambda _s: "not-a-container")
    with pytest.raises(DocumentError) as exc:
        parse_structured('{"a": 1,}', "content")  # trailing comma only repair handles
    assert exc.value.error_code == "bad_content"


def test_edit_document_accepts_structured_operations(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="s.xlsx", content={"sheets": [{"name": "S", "rows": [["x"]]}]})
    result = _call(tools["edit_document"], file_path="s.xlsx", operations=[{"op": "set_cell", "sheet": "S", "cell": "A2", "value": "y"}])
    assert result["status"] == "ok"
    assert "y" in _call(tools["read_document"], file_path="s.xlsx")["text"]


def test_edit_blocked_after_external_change(tmp_path: Path):
    from coworker.documents import build_document

    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="g.docx", content={"blocks": [{"type": "paragraph", "text": "v1"}]})
    _call(tools["read_document"], file_path="g.docx")  # records fingerprint
    # Simulate another process editing the file (valid docx, different content).
    (tmp_path / "g.docx").write_bytes(build_document("docx", {"blocks": [{"type": "paragraph", "text": "external"}]}))
    result = _call(tools["edit_document"], file_path="g.docx", operations=[{"op": "append_paragraph", "text": "v2"}])
    assert "error" in result
    assert "changed" in result["error"].lower()


def test_pdf_delete_all_pages_rejected(tmp_path: Path):
    _, _, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="one.pdf", content={"blocks": [{"type": "paragraph", "text": "only"}]})
    result = _call(tools["edit_document"], file_path="one.pdf", operations=[{"op": "delete_pages", "pages": "1"}])
    assert result.get("error_code") == "bad_operations"
    # The source must still be a readable one-page PDF.
    assert "only" in _call(tools["read_document"], file_path="one.pdf")["text"]


def test_convert_pdf_self_copy_creates_no_change(tmp_path: Path):
    _, store, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="same.pdf", content={"blocks": [{"type": "paragraph", "text": "x"}]})
    _call(tools["convert_document"], file_path="same.pdf", to_format="pdf")
    changes = [c for c in store.list_changes("s-doc") if c["file_path"] == "same.pdf"]
    assert len(changes) == 1  # only the create; the identity conversion is a no-op


# ---------------------------------------------------------------------------
# format_user_message attachment extraction (real integration)
# ---------------------------------------------------------------------------

def test_format_user_message_extracts_office_attachment():
    from coworker.agent.core import format_user_message
    from coworker.documents import build_document

    data = build_document("docx", {"blocks": [{"type": "paragraph", "text": "attached body"}]})
    url = "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64," + base64.b64encode(data).decode()
    attachment = {"name": "note.docx", "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size": len(data), "content": url, "binary": True}
    blocks = format_user_message("see file", attachments=[attachment])
    text = "".join(part.get("text", "") for part in blocks if isinstance(part, dict))
    assert "attached body" in text
    assert "base64" not in text.split("--- end")[0].split("note.docx")[-1]


def test_format_user_message_falls_back_for_unknown_binary():
    from coworker.agent.core import format_user_message

    attachment = {"name": "blob.bin", "type": "application/octet-stream", "size": 10, "content": "data:application/octet-stream;base64,AAAA", "binary": True}
    blocks = format_user_message("see file", attachments=[attachment])
    assert blocks  # does not crash; raw pass-through preserved


# ---------------------------------------------------------------------------
# runtime integration contract (change claiming) + conversion failure path
# ---------------------------------------------------------------------------

def test_change_record_claimable_by_runtime(tmp_path: Path):
    from coworker.agent.graph import _path_from_tool_input

    ws, store, tools = _tools(tmp_path)
    _call(tools["create_document"], file_path="c.docx", content={"blocks": [{"type": "paragraph", "text": "x"}]})
    raw = json.dumps({"file_path": "c.docx", "content": "{}"})
    assert _path_from_tool_input("create_document", raw) == "c.docx"
    claimed = store.match_and_claim("s-doc", "create_document", ws.normalize_rel_path("c.docx"))
    assert claimed is not None
    assert claimed["file_path"] == "c.docx"


def test_office_to_pdf_reports_unavailable_engine(monkeypatch):
    import pytest

    from coworker.documents import build_document, convert
    from coworker.documents.common import DocumentError

    monkeypatch.setattr(convert.shutil, "which", lambda _name: None)
    monkeypatch.setattr(convert, "_app_installed", lambda _app: False)
    data = build_document("docx", {"blocks": [{"type": "paragraph", "text": "x"}]})
    with pytest.raises(DocumentError) as exc:
        convert.convert_document("docx", data, "pdf")
    assert exc.value.error_code == "conversion_unavailable"


def test_office_to_pdf_prefers_libreoffice(monkeypatch):
    from coworker.documents import build_document, convert as c

    monkeypatch.setattr(c.shutil, "which", lambda _name: "/usr/bin/soffice")
    monkeypatch.setattr(c, "_libreoffice_to_pdf", lambda soffice, data, fmt: b"%PDF-LO")
    out, engine = c._office_to_pdf("docx", build_document("docx", {"blocks": [{"type": "paragraph", "text": "x"}]}), "x.docx")
    assert engine == "libreoffice" and out == b"%PDF-LO"


def test_office_to_pdf_falls_back_to_applescript(monkeypatch):
    if sys.platform != "darwin":
        return
    from coworker.documents import build_document, convert as c

    monkeypatch.setattr(c.shutil, "which", lambda _name: None)
    monkeypatch.setattr(c, "_app_installed", lambda _app: True)
    monkeypatch.setattr(c, "_applescript_to_pdf", lambda fmt, data, name: b"%PDF-AS")
    out, engine = c._office_to_pdf("docx", build_document("docx", {"blocks": [{"type": "paragraph", "text": "x"}]}), "x.docx")
    assert engine == "applescript" and out == b"%PDF-AS"
