"""Browser personal-data API tests (bookmarks / history / downloads routes)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.api import browser as browser_api  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(browser_api, "settings", SimpleNamespace(data_dir=tmp_path))
    app = FastAPI()
    app.include_router(browser_api.router)
    return TestClient(app)


def test_bookmark_routes(client: TestClient):
    created = client.post("/api/browser/bookmarks", json={"url": "https://example.com", "title": "Example"})
    assert created.status_code == 200
    bookmark_id = created.json()["item"]["id"]

    listed = client.get("/api/browser/bookmarks").json()["items"]
    assert [b["url"] for b in listed] == ["https://example.com"]

    patched = client.patch(f"/api/browser/bookmarks/{bookmark_id}", json={"title": "Renamed"})
    assert patched.json()["item"]["title"] == "Renamed"

    assert client.delete(f"/api/browser/bookmarks/{bookmark_id}").json()["ok"] is True
    assert client.get("/api/browser/bookmarks").json()["items"] == []


def test_history_routes(client: TestClient):
    client.post("/api/browser/history", json={"url": "https://a.com", "title": "A"})
    client.post("/api/browser/history", json={"url": "https://b.com", "title": "B"})
    items = client.get("/api/browser/history?query=b.com").json()["items"]
    assert [h["url"] for h in items] == ["https://b.com"]

    assert client.delete("/api/browser/history").json()["ok"] is True
    assert client.get("/api/browser/history").json()["items"] == []


def test_download_routes(client: TestClient):
    client.post(
        "/api/browser/downloads",
        json={"id": "d1", "url": "https://x/f.zip", "filename": "f.zip", "total_bytes": 10},
    )
    items = client.get("/api/browser/downloads").json()["items"]
    assert items[0]["id"] == "d1"

    patched = client.patch("/api/browser/downloads/d1", json={"state": "completed", "received_bytes": 10})
    assert patched.json()["item"]["state"] == "completed"

    assert client.delete("/api/browser/downloads/d1").json()["ok"] is True
