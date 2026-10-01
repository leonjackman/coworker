"""Browser personal-data store tests (bookmarks / history / downloads)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.browser.store import BrowserStore, MAX_HISTORY_ENTRIES  # noqa: E402


def test_bookmark_add_dedupe_update_delete(tmp_path: Path):
    store = BrowserStore(tmp_path)

    first = store.add_bookmark("https://example.com", "Example")
    assert first["id"]
    assert first["title"] == "Example"

    # Same URL returns the existing entry (no duplicate).
    again = store.add_bookmark("https://example.com", "Example 2")
    assert again["id"] == first["id"]
    assert len(store.list_bookmarks()) == 1

    updated = store.update_bookmark(first["id"], title="Renamed")
    assert updated is not None and updated["title"] == "Renamed"

    assert store.remove_bookmark(first["id"]) is True
    assert store.list_bookmarks() == []


def test_bookmark_reorder(tmp_path: Path):
    store = BrowserStore(tmp_path)
    a = store.add_bookmark("https://a.com")
    b = store.add_bookmark("https://b.com")
    c = store.add_bookmark("https://c.com")

    ordered = store.reorder_bookmarks([c["id"], a["id"], b["id"]])
    assert [x["id"] for x in ordered] == [c["id"], a["id"], b["id"]]
    # Persisted.
    assert [x["id"] for x in store.list_bookmarks()] == [c["id"], a["id"], b["id"]]


def test_history_dedupe_and_cap(tmp_path: Path):
    store = BrowserStore(tmp_path)
    store.add_history("https://example.com", "Example")
    store.add_history("https://example.com", "Example reload")  # collapses
    items = store.list_history()
    assert len(items) == 1
    assert items[0]["visit_count"] == 2
    assert items[0]["title"] == "Example reload"

    store.add_history("https://other.com", "Other")
    items = store.list_history()
    assert [h["url"] for h in items] == ["https://other.com", "https://example.com"]

    # Search
    assert [h["url"] for h in store.list_history(query="other")] == ["https://other.com"]

    # Cap
    for i in range(MAX_HISTORY_ENTRIES + 25):
        store.add_history(f"https://site{i}.example", f"Site {i}")
    assert len(store.list_history(limit=MAX_HISTORY_ENTRIES + 100)) == MAX_HISTORY_ENTRIES


def test_history_skips_blank(tmp_path: Path):
    store = BrowserStore(tmp_path)
    assert store.add_history("") == {}
    assert store.add_history("about:blank") == {}
    assert store.list_history() == []


def test_download_lifecycle(tmp_path: Path):
    store = BrowserStore(tmp_path)
    store.add_download(download_id="d1", url="https://x/f.zip", filename="f.zip", total_bytes=100)
    assert store.list_downloads()[0]["state"] == "progressing"

    updated = store.update_download("d1", state="completed", received_bytes=100, path="/tmp/f.zip")
    assert updated is not None
    assert updated["state"] == "completed"
    assert updated["path"] == "/tmp/f.zip"

    assert store.remove_download("d1") is True
    assert store.list_downloads() == []


def test_clear_all(tmp_path: Path):
    store = BrowserStore(tmp_path)
    store.add_history("https://example.com")
    store.add_download(download_id="d1", url="https://x", filename="x")
    store.clear_history()
    store.clear_downloads()
    assert store.list_history() == []
    assert store.list_downloads() == []
