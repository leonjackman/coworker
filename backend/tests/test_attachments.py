"""R2 attachment tests: the source fix is "inline-once + stub-on-replay" —
attachment full content is forwarded only on the turn it is first provided
(inline_attachments=True); history replay renders compact stubs
(inline_attachments=False) so a 120k-char attachment is never re-sent every
turn (opencode stripMedia / compaction-placeholder pattern)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.core import format_user_message  # noqa: E402

TEXT_ATT = [{"name": "a.txt", "type": "text/plain", "size": 5000, "content": "hello world " * 1000}]
IMAGE_ATT = [{"name": "p.png", "type": "image/png", "size": 999, "content": "data:image/png;base64,AAAA"}]
BIN_ATT = [{"name": "blob.bin", "type": "application/octet-stream", "size": 123, "binary": True, "content": "rawbytes"}]


def _flatten(v) -> str:
    if isinstance(v, str):
        return v
    return "\n".join(str(b.get("text", "")) for b in v)


def test_inline_mode_forwards_full_content():
    out = _flatten(format_user_message("msg", TEXT_ATT))
    assert "hello world" in out
    assert "Attached files (all forwarded" in out


def test_stub_mode_omits_content_and_lists_reference():
    out = _flatten(format_user_message("msg", TEXT_ATT, inline_attachments=False))
    assert "hello world" not in out
    assert "[Attachment: a.txt (5000 bytes, text/plain)]" in out
    assert "NOT repeated" in out  # the "re-attach / read with tools" hint


def test_stub_mode_images_do_not_replay_data_url():
    out = _flatten(format_user_message("msg", IMAGE_ATT, inline_attachments=False))
    assert "data:" not in out
    assert "[Image: p.png" in out


def test_stub_mode_binary_note():
    out = _flatten(format_user_message("msg", BIN_ATT, inline_attachments=False))
    assert "[Binary attachment: blob.bin" in out


def test_inline_mode_images_keep_data_url():
    blocks = format_user_message("msg", IMAGE_ATT)
    assert isinstance(blocks, list)
    assert any(
        b.get("type") == "image_url" and b.get("image_url", {}).get("url", "").startswith("data:image/png")
        for b in blocks
    )


def test_no_attachments_unchanged():
    out = format_user_message("hi", None, inline_attachments=False)
    assert out == [{"type": "text", "text": "hi"}]
