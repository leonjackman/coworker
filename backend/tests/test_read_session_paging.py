"""read_session paging: full referenced-session transcripts are reachable.

Replaces the old 60k head-cap (kept only the OLDEST messages, silently dropped
the newest decisions, and offered no way to page back). Now paging anchors at
the END of the transcript: offset=0 returns the most recent messages and
next_offset walks the agent back through older history to offset=0/next=0,
when the WHOLE session has been read.
"""

import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.core import (  # noqa: E402
    MAX_REFERENCE_SESSION_PAGE_CHARS,
    build_referenced_session_page,
)
from coworker.agent.middleware.tool_truncation import truncate_tool_content  # noqa: E402


def _msg(role, i, filler=60):
    body = f"message {i} content " + "x" * filler
    return SimpleNamespace(role=role, content=body, attachments=None, references=None)


def _session(n):
    messages = []
    for i in range(n):
        messages.append(_msg("user", i * 2))
        messages.append(_msg("assistant", i * 2 + 1))
    return SimpleNamespace(id="s-abc", title="proj session", messages=messages)


def _content_text(content):
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content


def _walk(session, page_size=20, max_chars=MAX_REFERENCE_SESSION_PAGE_CHARS):
    seen_content = []
    offset = 0
    pages = 0
    while True:
        page = build_referenced_session_page(session, offset=offset, page_size=page_size, max_chars=max_chars)
        pages += 1
        for m in page["messages"]:
            seen_content.append(_content_text(m["content"]))
        if not page["messages"] or page["next_offset"] == 0:
            break
        assert page["next_offset"] > offset, "next_offset must move backward into history"
        offset = page["next_offset"]
    return seen_content, pages


def test_first_page_returns_newest():
    session = _session(60)  # 120 messages
    page = build_referenced_session_page(session, offset=0, page_size=20)
    assert page["message_count"] == 20
    assert page["total_messages"] == 120
    assert _content_text(page["messages"][-1]["content"]).startswith("message 119")  # newest last, chronological
    assert page["truncated"] is True
    assert page["next_offset"] > 0


def test_walking_pages_covers_entire_session_in_order():
    session = _session(30)  # 60 messages
    seen, pages = _walk(session, page_size=10)
    assert pages == 6
    # walking is newest→oldest across pages, so coverage is a multiset check:
    # every message appears exactly once (no dupes, no gaps).
    expected = [f"message {i} content " + "x" * 60 for i in range(60)]
    assert sorted(seen) == sorted(expected)
    assert len(seen) == len(expected)


def test_small_page_size_still_covers_everything():
    session = _session(10)  # 20 messages
    seen, pages = _walk(session, page_size=3)
    assert pages == 7
    assert len(seen) == 20


def test_char_budget_bounds_page_but_progresses():
    # Tiny char budget forces one-message pages but must still reach the end.
    session = _session(12)  # 24 messages
    seen, pages = _walk(session, page_size=100, max_chars=90)
    assert pages >= 24
    assert len(seen) == 24


def test_short_session_single_page_no_paging():
    session = _session(2)  # 4 messages
    page = build_referenced_session_page(session, offset=0, page_size=20)
    assert page["message_count"] == 4
    assert page["truncated"] is False
    assert page["next_offset"] == 0
    assert "whole session" in page["hint"]


def test_offset_beyond_end_returns_empty_cleanly():
    session = _session(2)
    page = build_referenced_session_page(session, offset=999, page_size=20)
    assert page["message_count"] == 0
    assert page["next_offset"] == 0
    assert page["truncated"] is False


def test_tool_truncation_slims_session_pages_preserving_ptrs():
    session = _session(80)  # 160 messages ~ big
    payload = json.dumps(build_referenced_session_page(session, offset=0, page_size=100), ensure_ascii=False)
    assert len(payload) > 4_000
    out = truncate_tool_content(payload, budget_chars=2_000)
    parsed = json.loads(out)  # still valid JSON
    assert parsed["next_offset"] is not None or parsed["next_offset"] == 0
    assert "omitted" in json.dumps(parsed)
    assert len(out) < len(payload)
