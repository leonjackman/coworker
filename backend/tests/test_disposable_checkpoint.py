"""Tests for the disposable single-writer checkpoint architecture.

The runtime checkpoint DB is now a per-turn scratch cache (single-writer model,
cf. codex/opencode): every graph run shares ONE AsyncSqliteSaver serialized by
its own lock, writes are reduced to ~1 per turn via exit-durability, and each
turn deletes its thread so the DB never accumulates. These tests pin down the
building blocks: shared saver identity, durability config, and async deletes.
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
    saved = (agents._shared_checkpointer, agents._shared_checkpointer_conn, agents._shared_checkpointer_init)
    agents._shared_checkpointer = None
    agents._shared_checkpointer_conn = None
    agents._shared_checkpointer_init = None
    yield
    agents._shared_checkpointer, agents._shared_checkpointer_conn, agents._shared_checkpointer_init = saved


async def _close_shared_conn() -> None:
    conn = agents._shared_checkpointer_conn
    if conn is not None:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001 - cleanup
            pass
        agents._shared_checkpointer_conn = None
        agents._shared_checkpointer = None


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
    db = tmp_path / "cp.sqlite"

    async def scenario():
        cp1 = await agents._get_shared_checkpointer(db)
        cp2 = await agents._get_shared_checkpointer(db)
        assert cp1 is cp2, "all streams must share ONE AsyncSqliteSaver (single-writer model)"

    asyncio.run(scenario())
    asyncio.run(_close_shared_conn())


def test_adelete_thread_removes_rows(tmp_path: Path):
    db = tmp_path / "cp.sqlite"

    async def scenario():
        checkpointer = await agents._get_shared_checkpointer(db)
        await checkpointer.conn.execute(
            "INSERT OR REPLACE INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
            "VALUES (?, '', ?, NULL, 'json', ?, '{}')",
            ("del-me", "cp1", b'{"v":1}'),
        )
        await checkpointer.conn.commit()
        count = await (await checkpointer.conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", ("del-me",)
        )).fetchone()
        assert count[0] == 1
        await checkpointer.adelete_thread("del-me")
        count = await (await checkpointer.conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", ("del-me",)
        )).fetchone()
        assert count[0] == 0, "adelete_thread must remove the whole thread"

    asyncio.run(scenario())
    asyncio.run(_close_shared_conn())
