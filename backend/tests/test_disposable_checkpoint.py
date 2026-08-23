"""Tests for the disposable single-writer checkpoint architecture.

The runtime checkpoint is now a per-session JSON file (single-writer model, cf.
cline): one ``checkpoints/<session_id>.json`` per session, written atomically,
deleted at turn end. These tests pin down the building blocks: shared saver
identity, durability config, and per-session file deletes.

Note: importing ``main`` spins up the app's background machinery, so these tests
keep the shared saver on a throwaway temp directory and never leak connections.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_stop_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

from coworker import agents  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_shared_saver():
    saved = (agents._shared_checkpointer, agents._shared_checkpointer_init)
    agents._shared_checkpointer = None
    agents._shared_checkpointer_init = None
    yield
    agents._shared_checkpointer, agents._shared_checkpointer_init = saved


def test_agent_run_config_sets_exit_durability():
    config = agents.agent_run_config(
        session_id="s",
        provider="p",
        model="m",
        language="zh",
        work_mode="plan",
        autonomy="guarded",
        streaming=True,
    )
    assert config["configurable"]["__pregel_durability"] == "exit"


def test_shared_checkpointer_is_singleton(tmp_path: Path):
    async def scenario():
        cp1 = await agents._get_shared_checkpointer(tmp_path)
        cp2 = await agents._get_shared_checkpointer(tmp_path)
        assert cp1 is cp2, "all streams must share ONE JSON saver (single-writer model)"

    asyncio.run(scenario())


def test_adelete_thread_removes_file(tmp_path: Path):
    async def scenario():
        saver = await agents._get_shared_checkpointer(tmp_path)
        # aput writes a checkpoint for a thread, then adelete_thread removes it.
        await saver.aput(
            config={"configurable": {"thread_id": "del-me"}},
            checkpoint={"v": 1, "id": "cp1", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"n": 1}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
            metadata={"step": 1},
            new_versions={},
        )
        files = list((tmp_path).glob("*.json"))
        assert len(files) == 1 and files[0].name == "del-me.json"
        await saver.adelete_thread("del-me")
        assert list((tmp_path).glob("*.json")) == []

    asyncio.run(scenario())


def test_aput_writes_then_aget_tuple_pending(tmp_path: Path):
    async def scenario():
        saver = await agents._get_shared_checkpointer(tmp_path)
        cfg = await saver.aput(
            config={"configurable": {"thread_id": "t"}},
            checkpoint={"v": 1, "id": "cp1", "ts": "2026-01-01T00:00:00+00:00", "channel_values": {"n": 1}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
            metadata={"step": 1},
            new_versions={},
        )
        await saver.aput_writes(cfg, [("messages", "hi")], task_id="task1")
        tup = await saver.aget_tuple(cfg)
        assert tup is not None
        assert tup.pending_writes == [("task1", "messages", "hi")]

    asyncio.run(scenario())
