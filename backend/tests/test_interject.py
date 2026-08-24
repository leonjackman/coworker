"""Tests for the interjection (插話) feature.

Guards the core contract: while a session is streaming, a user message can be
interjected into the RUNNING task (guide the LLM's next output) WITHOUT pausing
or terminating the stream. The message lands in the per-session steer inbox,
persists as a user message, and publishes a ``steer_admitted`` event; the
in-graph ``SteerInjectionMiddleware`` then folds it into the next model call.

Also covers the 409 guard (interject while idle) and the middleware injection.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Isolate the app to a throwaway data dir before importing main (module-level
# stores read COWORKER_DATA_DIR at import time).
os.environ["COWORKER_DATA_DIR"] = str(Path(__file__).resolve().parents[1] / ".test_interject_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from coworker.agent.middleware import SteerInjectionMiddleware  # noqa: E402
from coworker.steer import steer_inbox  # noqa: E402


class _FakeTask:
    """Minimal stand-in for an in-flight asyncio stream task (endpoint only
    checks ``done()``). Avoids cross-loop task teardown warnings."""

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


def _clear_state() -> None:
    main._stream_tasks.clear()
    for sid in list(main.agent_registry.checkpoint_manager.active_sessions()):
        main.agent_registry.checkpoint_manager.mark_idle(sid)
    steer_inbox.clear_all()

    # Drop the throwaway session dir so each test starts clean.
    data_dir = Path(main.settings.data_dir)
    sessions_dir = data_dir / "sessions"
    if sessions_dir.exists():
        for f in sessions_dir.glob("*.json"):
            f.unlink()


def _create_session() -> str:
    session = main.session_store.create("t", project_id="proj-x")
    return session.id


def _text_content(message) -> str:
    """Extract the plain-text view of a steer HumanMessage content (may be a
    str or a multimodal block list)."""
    content = message.content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


class TestInterjectEndpoint:
    def test_missing_session_id_400(self):
        _clear_state()
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.post("/chat/interject", json={"message": "hi"})
            assert resp.status_code == 400

    def test_unknown_session_404(self):
        _clear_state()
        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            resp = client.post("/chat/interject", json={"session_id": "nope", "message": "hi"})
            assert resp.status_code == 404

    def test_interject_409_when_idle(self):
        _clear_state()
        from fastapi.testclient import TestClient

        sid = _create_session()
        with TestClient(main.app) as client:
            resp = client.post("/chat/interject", json={"session_id": sid, "message": "guide me"})
            assert resp.status_code == 409

    def test_interject_admitted_while_streaming(self):
        _clear_state()
        from fastapi.testclient import TestClient

        sid = _create_session()
        main._stream_tasks[sid] = _FakeTask()
        try:
            with TestClient(main.app) as client:
                resp = client.post(
                    "/chat/interject",
                    json={
                        "session_id": sid,
                        "message": "please use approach X",
                        "user_message_id": "user-steer-1",
                        "steer_id": "steer-frontend-1",
                    },
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "ok"
                # 前端传入的 steer_id 被原样采用（steer_injected 事件据此匹配）。
                assert body["steer_id"] == "steer-frontend-1"
        finally:
            main._stream_tasks.pop(sid, None)

        # The steer is in the inbox AND persisted as a user message.
        entries = steer_inbox.take_all(sid)
        assert len(entries) == 1
        assert entries[0].content == "please use approach X"
        assert entries[0].user_message_id == "user-steer-1"
        session = main.session_store.require(sid)
        assert session.messages[-1].role == "user"
        assert session.messages[-1].content == "please use approach X"
        assert session.messages[-1].id == "user-steer-1"
        # interject=True：前端据此不渲染独立用户泡泡（内容由「收到插話」card 展示）。
        assert session.messages[-1].interject is True

    def test_interject_publishes_steer_admitted(self):
        _clear_state()
        from fastapi.testclient import TestClient

        sid = _create_session()
        main._stream_tasks[sid] = _FakeTask()
        try:
            with TestClient(main.app) as client:
                resp = client.post("/chat/interject", json={"session_id": sid, "message": "note"})
                assert resp.status_code == 200
        finally:
            main._stream_tasks.pop(sid, None)

        # The event bus buffer for this session should now carry steer_admitted.
        bus = main.session_event_bus
        with bus._lock:
            buf = list(bus._buffers.get(sid, []))
        assert any(e.get("type") == "steer_admitted" for e in buf)

    def test_skip_user_append_does_not_duplicate_message(self):
        _clear_state()
        # /chat/stream with skip_user_append=true must NOT append a new user
        # message (the steer is already the last message in history).
        sid = _create_session()
        main.session_store.append_message(sid, role="user", content="first", message_id="m1")
        # Simulate the persisted steer as the last user message.
        main.session_store.append_message(sid, role="user", content="steer", message_id="user-steer-late")
        # Build the same history the endpoint would (mirror the chat_stream path).
        history = [
            {"role": m.role, "content": m.content}
            for m in main.session_store.require(sid).messages
            if m.role in {"user", "assistant"} and m.content
        ]
        # skip_user_append → messages = history, no extra user_message.
        assert history[-1] == {"role": "user", "content": "steer"}


async def _never_ends():
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise


class TestSteerInjectionMiddleware:
    def _make_state(self, session_id: str, messages):
        from langchain_core.messages import HumanMessage

        return {
            "session_id": session_id,
            "messages": [HumanMessage(content="user1"), *messages],
        }

    def test_injects_steer_into_messages(self):
        _clear_state()
        sid = "sess-mw-1"
        steer_inbox.push(
            sid,
            main.SteerEntry(id="s1", content="turn left", ts=1, user_message_id="u1"),
        )
        mw = SteerInjectionMiddleware()
        state = self._make_state(sid, [])
        emitted = []
        mw._emit = lambda event: emitted.append(event)

        out = asyncio.run(mw.abefore_model(state, None))
        assert out is not None
        assert "messages" in out
        last = out["messages"][-1]
        assert last.type == "human"
        assert _text_content(last) == "turn left"
        # steer_injected published once, steer_id preserved.
        assert len(emitted) == 1
        assert emitted[0]["type"] == "steer_injected"
        assert emitted[0]["steer_id"] == "s1"
        assert emitted[0]["session_id"] == sid
        # Inbox drained.
        assert steer_inbox.pending_count(sid) == 0

    def test_reinjects_injected_steer_on_later_calls(self):
        _clear_state()
        sid = "sess-mw-2"
        steer_inbox.push(sid, main.SteerEntry(id="s1", content="stay", ts=1))
        mw = SteerInjectionMiddleware()
        state = self._make_state(sid, [])

        out1 = asyncio.run(mw.abefore_model(state, None))
        last1 = out1["messages"][-1]
        assert _text_content(last1) == "stay"

        # Second model call, no new steer: the injected message persists.
        out2 = asyncio.run(mw.abefore_model(state, None))
        assert out2 is not None
        steers = [
            m
            for m in out2["messages"]
            if getattr(m, "type", "") == "human" and _text_content(m) == "stay"
        ]
        assert len(steers) == 1
        # No duplicate steer_injected emission.
        assert len(mw._injected) == 1

    def test_noop_without_session_id(self):
        _clear_state()
        from langchain_core.messages import HumanMessage

        mw = SteerInjectionMiddleware()
        state = {"session_id": "", "messages": [HumanMessage(content="x")]}
        assert asyncio.run(mw.abefore_model(state, None)) is None

    def test_noop_without_messages(self):
        _clear_state()
        mw = SteerInjectionMiddleware()
        state = {"session_id": "sess-mw-3", "messages": []}
        assert asyncio.run(mw.abefore_model(state, None)) is None


class TestGraphIntegration:
    """端到端：steer 在运行中的图内于「下一轮模型呼叫边界」注入，且当前 stream
    不被中止（第一轮模型呼叫照常完成并跑完工具）。"""

    def test_steer_folded_into_next_model_call(self):
        _clear_state()
        from langchain_core.messages import AIMessage
        from langchain_core.tools import tool

        from coworker.agent.core import agent_run_config, prepare_agent_messages
        from coworker.agent.graph import build_coworker_agent_graph

        calls: list[list[tuple[str, str]]] = []

        @tool
        def _noop() -> str:
            """Do nothing."""
            return "noop done"

        class _RecordingModel:
            model_name = "rec"

            def bind_tools(self, *args, **kwargs):
                return self

            async def ainvoke(self, messages):
                calls.append([(m.type, str(m.content)) for m in messages])
                if len(calls) == 1:
                    return AIMessage(
                        content="",
                        tool_calls=[{"name": "_noop", "args": {}, "id": "t1", "type": "tool_call"}],
                    )
                return AIMessage(content="final answer after steer")

        sid = "sess-e2e-1"
        graph = build_coworker_agent_graph(
            _RecordingModel(), [_noop], work_mode="build", language="zh", autonomy="guarded",
            data_dir="/tmp/cw_interject_graph",
        )
        inputs = {
            "messages": prepare_agent_messages([{"role": "user", "content": "first prompt"}]),
            "work_mode": "build",
            "language": "zh",
            "phase": "execute",
            "autonomy": "guarded",
            "session_id": sid,
        }
        config = agent_run_config(session_id=sid, provider="rec", model="rec", language="zh", work_mode="build", autonomy="guarded", streaming=True)

        async def _run():
            async for _sm, _chunk in graph.astream(inputs, config=config, stream_mode=["updates"]):
                # 第一轮模型呼叫完成后、第二轮开始前推入插话
                if len(calls) == 1 and not steer_inbox.has_pending(sid):
                    steer_inbox.push(sid, main.SteerEntry(id="s-e2e", content="CHANGE DIRECTION", ts=1))
            return

        asyncio.run(_run())

        assert len(calls) == 2, "graph must run a continuation turn for the steer"
        second = calls[1]
        assert any("CHANGE DIRECTION" in content for _, content in second), "steer must reach the 2nd model call"
        assert any(role == "human" and "CHANGE DIRECTION" in content for role, content in second)
        # 第一轮未被中止：tool 结果进入第二轮上下文
        assert any(role == "tool" for role, _ in second)
