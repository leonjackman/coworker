"""Browser screenshot image validation tests.

Guards the "Failed to load image: cannot identify image file" 400 incident: an
empty/blank capture (hidden webview, collapsed panel) must never be forwarded
to a vision provider as an ``image_url`` block, and must not write 0-byte
screenshot files to disk.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.browser.bridge_client import (  # noqa: E402
    _looks_like_image_data_url,
    _save_screenshot,
    build_browser_tool,
)


# ---------------------------------------------------------------------------
# _looks_like_image_data_url
# ---------------------------------------------------------------------------

def _data_url(body: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"


def test_valid_jpeg_data_url():
    assert _looks_like_image_data_url(_data_url(b"\xff\xd8\xff\xe0" + b"\x00" * 16))


def test_valid_png_data_url():
    assert _looks_like_image_data_url(_data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, mime="image/png"))


def test_empty_body_is_invalid():
    assert not _looks_like_image_data_url("data:image/jpeg;base64,")


def test_non_base64_body_is_invalid():
    assert not _looks_like_image_data_url("data:image/jpeg;base64,@@@not-base64@@@")


def test_non_image_bytes_are_invalid():
    assert not _looks_like_image_data_url(_data_url(b"not an image at all"))


def test_non_data_url_string_is_invalid():
    assert not _looks_like_image_data_url("")
    assert not _looks_like_image_data_url("screenshot.png")


# ---------------------------------------------------------------------------
# _save_screenshot
# ---------------------------------------------------------------------------

def test_save_screenshot_skips_empty_body(tmp_path: Path):
    result = _save_screenshot("data:image/jpeg;base64,", tmp_path, "sess-1")
    assert result is None
    assert not list(tmp_path.glob("screenshots/**/*"))


def test_save_screenshot_skips_invalid_body(tmp_path: Path):
    result = _save_screenshot("data:image/jpeg;base64,not-valid-b64!", tmp_path, "sess-1")
    assert result is None
    assert not list(tmp_path.glob("screenshots/**/*"))


def test_save_screenshot_writes_valid_body(tmp_path: Path):
    result = _save_screenshot(_data_url(b"\xff\xd8\xff\xe0" + b"\x00" * 16), tmp_path, "sess-1")
    assert result is not None
    written = Path(result)
    assert written.exists()
    assert written.stat().st_size > 0


# ---------------------------------------------------------------------------
# build_browser_tool screenshot branch
# ---------------------------------------------------------------------------

class _FakeBridge:
    """Stands in for the loopback bridge; returns the given screenshot dict."""

    def __init__(self, result: dict):
        self._result = result

    def screenshot(self):
        return self._result


def _run_screenshot_tool(result: dict, *, vision: bool) -> str | list:
    """Build the browser tool with a stubbed BridgeClient and invoke screenshot."""
    import coworker.browser.bridge_client as bc

    original = bc.BridgeClient
    bc.BridgeClient = lambda data_dir, **kw: _FakeBridge(result)  # type: ignore[assignment]
    try:
        tool = build_browser_tool(Path("/tmp"), vision=vision, session_id="sess-1")
        return tool.invoke({"action": "screenshot"})
    finally:
        bc.BridgeClient = original


def test_vision_forwarding_rejects_empty_screenshot():
    out = _run_screenshot_tool({"image": "data:image/jpeg;base64,"}, vision=True)
    assert isinstance(out, str)
    payload = json.loads(out)
    assert payload.get("error_code") == "screenshot_empty"
    assert "hidden" in payload.get("error", "")


def test_vision_forwarding_rejects_garbage_screenshot():
    out = _run_screenshot_tool({"image": _data_url(b"garbage bytes")}, vision=True)
    assert isinstance(out, str)
    payload = json.loads(out)
    assert payload.get("error_code") == "screenshot_empty"


def test_vision_forwarding_accepts_valid_screenshot():
    out = _run_screenshot_tool({"image": _data_url(b"\xff\xd8\xff\xe0" + b"\x00" * 16)}, vision=True)
    assert isinstance(out, list)
    image_block = [p for p in out if p.get("type") == "image_url"]
    assert len(image_block) == 1
    assert image_block[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
