"""Tests for JsonFileCheckpointSaver against real LangGraph semantics.

The saver must behave like the SQLite checkpointer it replaces: checkpoints are
stored per thread/session as an atomic JSON file, the latest checkpoint is
resolved by uuid6 id ordering, pending writes round-trip, and an interrupt →
`Command(resume=...)` cycle works across saver instances (restart).
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import TypedDict

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

from coworker.checkpoint_store import JsonFileCheckpointSaver  # noqa: E402


class State(TypedDict):
    n: int


def make_graph(cp):
    async def step1(state):
        return {"n": state["n"] + 1}

    async def approval(state):
        decision = interrupt({"q": "ok?", "n": state["n"]})
        return {"n": state["n"] + (1 if decision == "yes" else 0)}

    g = StateGraph(State)
    g.add_node("step1", step1)
    g.add_node("approval", approval)
    g.add_edge(START, "step1")
    g.add_edge("step1", "approval")
    g.add_edge("approval", END)
    return g.compile(checkpointer=cp)


CONFIG = {"configurable": {"thread_id": "t1", "__pregel_durability": "exit"}}


@pytest.fixture
def cdir(tmp_path: Path) -> Path:
    return tmp_path / "checkpoints"


def test_interrupt_and_resume_across_saver_instances(cdir: Path):
    async def scenario():
        cp1 = JsonFileCheckpointSaver(cdir)
        g1 = make_graph(cp1)
        events = []
        async for _ in g1.astream({"n": 0}, config=CONFIG, stream_mode="updates"):
            events.append(_)
        assert any("__interrupt__" in ev for ev in events)

        # Interrupt checkpoint must be durable on disk (one small file).
        files = list(cdir.glob("*.json"))
        assert len(files) == 1 and files[0].name == "t1.json"

        # Resume with a NEW saver instance on the same dir (simulates restart).
        cp2 = JsonFileCheckpointSaver(cdir)
        g2 = make_graph(cp2)
        resumed = []
        async for _ in g2.astream(Command(resume="yes"), config=CONFIG, stream_mode="updates"):
            resumed.append(_)
        final = await cp2.aget_tuple(CONFIG)
        assert final.checkpoint["channel_values"]["n"] == 2

        await cp2.adelete_thread("t1")
        assert list(cdir.glob("*.json")) == []

    asyncio.run(scenario())


def test_file_is_atomic_and_matches_expected_shape(cdir: Path):
    async def scenario():
        cp = JsonFileCheckpointSaver(cdir)
        await cp.aput(
            config={"configurable": {"thread_id": "t2"}},
            checkpoint={
                "v": 1,
                "id": "cp1",
                "ts": "2026-01-01T00:00:00+00:00",
                "channel_values": {"n": 5},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            },
            metadata={"step": 3},
            new_versions={},
        )
        path = cdir / "t2.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["thread_id"] == "t2"
        assert set(data.keys()) == {"thread_id", "entries", "writes"}
        entry = data["entries"]["cp1"]
        assert entry["type"] in ("json", "msgpack")
        assert entry["checkpoint"]  # base64 blob present
        assert entry["metadata"]["step"] == 3
        assert entry["parent_checkpoint_id"] is None
        # No temp files left behind.
        assert list(cdir.glob(".*.tmp.*")) == []

    asyncio.run(scenario())


def test_aget_tuple_latest_and_by_id(cdir: Path):
    async def scenario():
        cp = JsonFileCheckpointSaver(cdir)
        cfg = {"configurable": {"thread_id": "t3"}}
        for i in range(3):
            cfg = await cp.aput(
                config=cfg,
                checkpoint={"v": 1, "id": f"cp{i}", "ts": f"2026-01-01T00:00:0{i}+00:00", "channel_values": {"n": i}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
                metadata={"step": i},
                new_versions={},
            )
        latest = await cp.aget_tuple({"configurable": {"thread_id": "t3"}})
        assert latest.checkpoint["channel_values"]["n"] == 2  # newest by id order
        cp0 = await cp.aget_tuple({"configurable": {"thread_id": "t3", "checkpoint_id": "cp0"}})
        cp1 = await cp.aget_tuple({"configurable": {"thread_id": "t3", "checkpoint_id": "cp1"}})
        cp2 = await cp.aget_tuple({"configurable": {"thread_id": "t3", "checkpoint_id": "cp2"}})
        assert cp0.parent_config is None  # cp0 is the root
        assert cp1.parent_config["configurable"]["checkpoint_id"] == "cp0"
        assert cp2.parent_config["configurable"]["checkpoint_id"] == "cp1"  # parent chain preserved

    asyncio.run(scenario())


def test_alist_ordering_filter_limit(cdir: Path):
    async def scenario():
        cp = JsonFileCheckpointSaver(cdir)
        for i in range(4):
            await cp.aput(
                config={"configurable": {"thread_id": "t4"}},
                checkpoint={"v": 1, "id": f"cp{i}", "ts": f"2026-01-01T00:00:0{i}+00:00", "channel_values": {"n": i}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
                metadata={"step": i},
                new_versions={},
            )
        rows = [t async for t in cp.alist({"configurable": {"thread_id": "t4"}})]
        assert [r.checkpoint["id"] for r in rows] == ["cp3", "cp2", "cp1", "cp0"]  # newest first
        limited = [t async for t in cp.alist({"configurable": {"thread_id": "t4"}}, limit=2)]
        assert len(limited) == 2
        filtered = [t async for t in cp.alist({"configurable": {"thread_id": "t4"}}, filter={"step": 2})]
        assert len(filtered) == 1 and filtered[0].checkpoint["id"] == "cp2"

    asyncio.run(scenario())


def test_checkpoint_manager_sweep_preserves_live_sessions(tmp_path: Path):
    from coworker.checkpoints import CheckpointManager

    cdir = tmp_path / "checkpoints"
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    mgr = CheckpointManager(cdir, sessions_dir=sdir)

    async def scenario():
        cp = JsonFileCheckpointSaver(cdir)
        await cp.aput(
            config={"configurable": {"thread_id": "live"}},
            checkpoint={"v": 1, "id": "c1", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"n": 0}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
            metadata={"step": 1},
            new_versions={},
        )
        await cp.aput(
            config={"configurable": {"thread_id": "orphan"}},
            checkpoint={"v": 1, "id": "c1", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"n": 0}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
            metadata={"step": 1},
            new_versions={},
        )
        # "live" session exists; "orphan" session does not.
        (sdir / "live.json").write_text("{}", encoding="utf-8")

        mgr.mark_active("live")
        stats = mgr.sweep()
        assert stats["orphan_threads"] == 1
        assert (cdir / "orphan.json").exists() is False
        assert (cdir / "live.json").exists() is True, "sweep must NOT delete active session files"

        mgr.mark_idle("live")
        # Idle live session: sweep must still preserve (may be a pending approval).
        mgr.sweep()
        assert (cdir / "live.json").exists() is True, "sweep must NOT delete idle live-session files"

    asyncio.run(scenario())
