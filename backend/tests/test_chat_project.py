"""Tests for the reserved system 聊天 project.

Verifies: startup self-healing (record / sandbox folder / memory scaffold),
non-deletable / non-renameable, reserved workspace path not reusable, and that
empty-project resolution falls back to the chat sandbox — never the app repo
root (security fix).
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_chat_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID  # noqa: E402


def _chat_workspace_path() -> Path:
    return (main.settings.data_dir / "chat").resolve()


def _drop_chat_project_record() -> None:
    """Remove the chat project record from projects.json (simulate manual edit)."""
    config = main.project_store.load()
    config.projects = [p for p in config.projects if p.id != CHAT_PROJECT_ID]
    main.project_store.save(config)
    with pytest.raises(KeyError):
        main.project_store.require(CHAT_PROJECT_ID)


def test_chat_project_created_on_startup():
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.get("/projects")
        assert resp.status_code == 200
        projects = resp.json()["projects"]
        chat = next((p for p in projects if p["id"] == CHAT_PROJECT_ID), None)
        assert chat is not None, "聊天项目必须在启动时被创建"
        assert chat["is_chat"] is True
        assert chat["workspace_path"] == str(_chat_workspace_path())
        assert _chat_workspace_path().is_dir(), "聊天沙箱文件夹必须存在"
        project = main.project_store.require(CHAT_PROJECT_ID)
        assert project.memory_dir == CHAT_MEMORY_DIR


def test_chat_project_self_heals_deleted_folder():
    """用户手动删除整个聊天沙箱文件夹后，启动自愈会重建它。"""
    folder = _chat_workspace_path()
    assert folder.is_dir()
    folder.rmdir()
    assert not folder.exists()
    main._ensure_chat_project()
    assert folder.is_dir(), "聊天沙箱文件夹被删后必须按需重建"


def test_chat_project_self_heals_missing_record():
    """projects.json 记录被移除后，启动自愈会以固定 id 重建。"""
    _drop_chat_project_record()
    main._ensure_chat_project()
    project = main.project_store.require(CHAT_PROJECT_ID)
    assert project.id == CHAT_PROJECT_ID
    assert project.memory_dir == CHAT_MEMORY_DIR


def test_chat_project_delete_rejected():
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.delete(f"/projects/{CHAT_PROJECT_ID}")
    assert resp.status_code == 400, "系统聊天项目不可删除"
    main.project_store.require(CHAT_PROJECT_ID)  # 记录仍在


def test_chat_project_rename_rejected():
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.post(f"/projects/{CHAT_PROJECT_ID}/rename", json={"name": "改名"})
    assert resp.status_code == 400, "系统聊天项目不可重命名"


def test_chat_workspace_path_not_reusable():
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.post(
            "/projects",
            json={"name": "占用聊天目录", "workspace_path": str(_chat_workspace_path()), "mode": "single"},
        )
    assert resp.status_code == 400, "聊天沙箱目录不可被用户项目占用"


def test_empty_project_falls_back_to_chat_sandbox():
    """安全修正：空项目会话的工作区回退到聊天沙箱，绝不落到 app 仓库根目录。"""
    workspace = main.workspace_controller.default()
    assert workspace.root == _chat_workspace_path()
    assert workspace.root != main.settings.workspace_dir.resolve()

    sid = main.session_store.create("t", project_id="").id
    try:
        ws = main.workspace_controller.workspace_for_session(sid)
        assert ws.root == _chat_workspace_path()
    finally:
        main.session_store.delete(sid)

    ws2 = main.workspace_controller.workspace_for_chat()
    assert ws2.root == _chat_workspace_path()


def test_chat_stream_requires_project_invariant():
    """「必须有项目」的不变量保持：无 session 且无 project 的 /chat/stream 仍被拒绝。"""
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.post("/chat/stream", json={"message": "hi"})
    assert resp.status_code == 400
