"""Tests for explicit session stop (user pressed Stop).

Guards the ``409 session is still generating`` incident: a client abort is only
observable as a socket disconnect, which uvicorn/Starlette can fail to propagate
promptly (the SSE generator keeps running into a dead socket and the runtime
graph can stall behind a checkpoint DB lock). The session then stays marked
"active" forever and the next edit/regenerate is rejected. ``/stop`` must
force-cancel the stream task and release the session regardless of whether the
graceful async-generator teardown ever runs.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Isolate the app to a throwaway data dir before importing main (module-level
# stores read COWORKER_DATA_DIR at import time).
os.environ["COWORKER_DATA_DIR"] = str(Path(__file__).resolve().parents[1] / ".test_stop_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def _clear_state() -> None:
    main._interrupted_sessions.clear()
    main._stream_tasks.clear()
    for sid in list(main.agent_registry.checkpoint_manager.active_sessions()):
        main.agent_registry.checkpoint_manager.mark_idle(sid)


def test_force_stop_clears_active_marker():
    _clear_state()
    sid = "sess-stop-1"
    main.agent_registry.checkpoint_manager.mark_active(sid)
    assert sid in main.agent_registry.checkpoint_manager.active_sessions()

    main._force_stop_session_stream(sid)

    assert sid not in main.agent_registry.checkpoint_manager.active_sessions()
    assert sid in main._interrupted_sessions, "stop must mark the session interrupted so the next run rebuilds fresh"


def test_force_stop_is_idempotent():
    _clear_state()
    sid = "sess-stop-2"
    main._force_stop_session_stream(sid)  # no active stream / no marker
    main._force_stop_session_stream(sid)  # twice
    assert sid not in main.agent_registry.checkpoint_manager.active_sessions()


def test_force_stop_cancels_inflight_task():
    _clear_state()
    sid = "sess-stop-3"

    async def scenario():
        main.agent_registry.checkpoint_manager.mark_active(sid)

        async def stuck():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(stuck())
        main._stream_tasks[sid] = task
        await asyncio.sleep(0.01)
        assert not task.cancelled()

        main._force_stop_session_stream(sid)
        await asyncio.sleep(0.01)

        assert task.cancelled()
        assert sid not in main._stream_tasks
        assert sid not in main.agent_registry.checkpoint_manager.active_sessions()

    asyncio.run(scenario())


def test_stop_endpoint_returns_ok():
    _clear_state()
    import httpx
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.post("/sessions/sess-stop-4/stop")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "session_id": "sess-stop-4"}
