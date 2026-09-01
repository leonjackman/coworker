"""Tests for NormalizeMessagesMiddleware + EnsureUserMessageMiddleware
(strict-provider message guards)."""

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_message_processor_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from coworker.agent.middleware.message_processor import (  # noqa: E402
    EnsureUserMessageMiddleware,
    NormalizeMessagesMiddleware,
)
from coworker.sessions import Session, SessionMessage, SessionStore  # noqa: E402


def _normalize(messages):
    mw = NormalizeMessagesMiddleware()
    return mw._normalize({"messages": messages})


def _text(msg):
    return getattr(msg, "content", "") or ""


def _tool_call(msg_id="t1", name="read_file"):
    return {"name": name, "args": {}, "id": msg_id, "type": "tool_call"}


def test_user_present_is_noop():
    msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), AIMessage(content="ok")]
    assert _normalize(msgs) is None


def test_empty_messages_is_noop():
    assert _normalize([]) is None


def test_misplaced_system_downgraded():
    """A mid-list system message is downgraded to human (system-first guard)."""
    msgs = [
        SystemMessage(content="sys"),
        AIMessage(content="", tool_calls=[_tool_call(name="run_command")]),
        SystemMessage(content="[CW-PLAN] residual"),
        ToolMessage(content="ok", tool_call_id="t1"),
    ]
    out = _normalize(msgs)
    assert out is not None
    types = [m.type for m in out]
    assert types[0] == "system"
    assert "human" in types
    assert _text(out[2]) == "[CW-PLAN] residual"


# ── EnsureUserMessageMiddleware: re-seed a degenerate empty resume ──

@pytest.fixture()
def reseed_store(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    session = Session(id="sess-reseed", title="t", project_id="p1", created_at=now, updated_at=now)
    session.messages = [
        SessionMessage(id="m1", role="user", content="make a skill", created_at=now),
        SessionMessage(id="m2", role="assistant", content="ok, doing it", created_at=now),
        SessionMessage(id="m3", role="user", content="no, use front matter", created_at=now),
    ]
    store = SessionStore(tmp_path)
    store.save(session)
    return store


def test_ensure_user_reseeds_empty_resume(reseed_store):
    """A degenerate resume with EMPTY messages re-seeds user history from the
    session store so the provider never receives `messages=[]`."""
    mw = EnsureUserMessageMiddleware(session_store=reseed_store)
    out = mw._ensure({"messages": [], "session_id": "sess-reseed"})
    assert out is not None
    assert "messages" in out
    humans = [m for m in out["messages"] if getattr(m, "type", None) == "human"]
    # only user-role messages are re-seeded
    assert len(humans) >= 1
    assert any("front matter" in getattr(m, "content", "") for m in humans)


def test_ensure_user_healthy_is_noop():
    mw = EnsureUserMessageMiddleware(session_store=None)
    assert mw._ensure({"messages": [HumanMessage(content="hi")], "session_id": "s"}) is None


def test_ensure_user_no_store_noop():
    mw = EnsureUserMessageMiddleware(session_store=None)
    assert mw._ensure({"messages": [], "session_id": "s"}) is None


def test_ensure_user_unknown_session_noop(tmp_path):
    store = SessionStore(tmp_path)
    mw = EnsureUserMessageMiddleware(session_store=store)
    assert mw._ensure({"messages": [], "session_id": "nope"}) is None
