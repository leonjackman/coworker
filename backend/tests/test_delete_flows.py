"""Acceptance tests for checkpoint lifecycle under delete / edit / export.

Verifies the per-session JSON checkpoint files are cleaned up correctly across
the flows that previously depended on the SQLite checkpoint DB: delete session,
delete project, edit-truncate (forget), export, and Settings clear.
"""

import asyncio
import os
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_stop_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from coworker import agents as agents_mod  # noqa: E402


@pytest.fixture()
def ckdir():
    return main.agent_registry.checkpoints_dir


async def _write_checkpoint(session_id: str) -> None:
    saver = await agents_mod._get_shared_checkpointer(main.agent_registry.checkpoints_dir)
    await saver.aput(
        config={"configurable": {"thread_id": session_id}},
        checkpoint={"v": 1, "id": "c1", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"n": 0}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
        metadata={"step": 1},
        new_versions={},
    )


def _files(ckdir: Path) -> list[str]:
    return sorted(f.name for f in ckdir.glob("*.json"))


def test_delete_session_removes_checkpoint(ckdir: Path):
    from fastapi.testclient import TestClient

    # Fresh session with a checkpoint file.
    sid = main.session_store.create("acceptance", project_id="").id
    asyncio.run(_write_checkpoint(sid))
    assert f"{sid}.json" in _files(ckdir)

    with TestClient(main.app) as client:
        resp = client.delete(f"/sessions/{sid}")
    assert resp.status_code == 200
    assert f"{sid}.json" not in _files(ckdir), "delete session must remove its checkpoint file"


def test_delete_project_removes_all_checkpoints(ckdir: Path):
    from fastapi.testclient import TestClient

    project = main.project_store.create(name="acceptance", workspace_path=str(Path("/tmp") / f"ws-{os.getpid()}"))
    project_id = project.id
    sids = []
    for i in range(2):
        sid = main.session_store.create("t", project_id=project_id).id
        sids.append(sid)
        asyncio.run(_write_checkpoint(sid))
    assert all(f"{s}.json" in _files(ckdir) for s in sids)

    with TestClient(main.app) as client:
        resp = client.delete(f"/projects/{project_id}")
    assert resp.status_code == 200
    assert all(f"{s}.json" not in _files(ckdir) for s in sids), "delete project must remove its sessions' checkpoint files"


def test_edit_truncate_forgets_checkpoint(ckdir: Path):
    """Edit truncates from the edited message onward and forgets the checkpoint."""
    sid = main.session_store.create("t").id
    asyncio.run(_write_checkpoint(sid))
    assert f"{sid}.json" in _files(ckdir)

    ok = asyncio.run(main.agent_registry.forget_runtime_checkpoint(sid))
    assert ok is True
    assert f"{sid}.json" not in _files(ckdir), "edit/regenerate must delete the checkpoint file"


def test_checkpoints_export_and_clear(ckdir: Path):
    from fastapi.testclient import TestClient

    sid = main.session_store.create("t").id
    asyncio.run(_write_checkpoint(sid))

    with TestClient(main.app) as client:
        resp = client.get("/checkpoints/export")
        assert resp.status_code == 200
        # Either a zip (files exist) or a JSON no-op.
        if resp.headers.get("content-type", "").startswith("application/zip"):
            zf = zipfile.ZipFile(io := __import__("io").BytesIO(resp.content))
            assert f"{sid}.json" in zf.namelist()
            io.close()
        else:
            assert resp.json()["size"] == 0

        resp2 = client.post("/checkpoints/clear")
        assert resp2.status_code == 200
    assert f"{sid}.json" not in _files(ckdir), "clear checkpoints must remove checkpoint files"
