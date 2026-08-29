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


def test_chat_system_prompt_lazy_persona():
    """代码层：聊天项目使用 Lazzzy Boy 人设 base 提示词，普通项目不受影响。"""
    from coworker.agent.system_prompt import build_cw_chat_system_prompt, build_cw_system_prompt

    chat = build_cw_chat_system_prompt()
    normal = build_cw_system_prompt()
    assert "Lazzzy" in chat and "懒懒" in chat
    assert "聊天项目" in chat
    assert "Lazzzy" not in normal
    assert chat != normal


def test_chat_assembler_replaces_phase_fragment():
    """代码层：聊天模式下 phase 片段替换为聊天契约，去掉编码执行指令。"""
    from coworker.agent.middleware.system_assembler import SystemAssembler

    class FakeReq:
        def __init__(self, state: dict, system_message: str):
            self.state = state
            self.system_message = type("SM", (), {"content": system_message})()

        def override(self, **kwargs):
            return kwargs

    state = {"language": "zh", "phase": "execute", "work_mode": "build", "autonomy": "guarded", "messages": []}
    base = "BASE PROMPT"

    chat_text = SystemAssembler(chat_mode=True)._overrides(FakeReq(state, base))["system_message"].content
    assert "BASE PROMPT" in chat_text
    assert "聊天对话" in chat_text
    assert "edit files" not in chat_text.lower()

    normal_text = SystemAssembler(chat_mode=False)._overrides(FakeReq(state, base))["system_message"].content
    assert "edit files" in normal_text.lower()


def test_chat_memory_seeded():
    """记忆层：聊天项目的 SOUL/AGENT/CHAT/CONTEXT 预置 Lazzzy Boy 内容。"""
    root = main.memory_manager.root / "__chat__"
    soul = root / "default_agent" / "BASE" / "SOUL.md"
    agent_md = root / "default_agent" / "BASE" / "AGENT.md"
    # 模拟全新骨架后再自愈预置，保证用例顺序无关。
    soul.write_text("# SOUL\n\n（agent 的灵魂文件：人格、语气、核心行为）\n", encoding="utf-8")
    agent_md.write_text("# AGENT\n\n（agent 的工作模式：擅长领域、工具偏好）\n", encoding="utf-8")
    main._ensure_chat_project()

    assert "懒懒" in soul.read_text(encoding="utf-8")
    assert "Lazzzy" in agent_md.read_text(encoding="utf-8")
    assert "沙箱" in (root / "BASE" / "CHAT.md").read_text(encoding="utf-8")
    assert "聊天模式约束" in (root / "BASE" / "PROJECT" / "CONTEXT.md").read_text(encoding="utf-8")


def test_chat_memory_not_clobbered():
    """记忆层：用户编辑过的 SOUL.md 在再次 ensure 后不被覆盖。"""
    root = main.memory_manager.root / "__chat__"
    soul = root / "default_agent" / "BASE" / "SOUL.md"
    custom = "# SOUL\n\n用户自定义人格。\n"
    soul.write_text(custom, encoding="utf-8")
    main._ensure_chat_project()
    assert soul.read_text(encoding="utf-8") == custom
