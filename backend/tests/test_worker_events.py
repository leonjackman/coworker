"""Tests for WorkerEventBus: expect()/subscribe-before-publish race, and the
close()-driven memory purge (root-cause fixes for the use_worker event bus).
"""

import asyncio
import tempfile

import pytest

from coworker.events import WorkerEventBus


async def consume_to_end(bus: WorkerEventBus, run_id: str) -> list[str]:
    """Production-style consumption: iterate to completion (do not break early),
    like the /worker-events SSE endpoint does."""
    got: list[str] = []
    async for ev in bus.stream(run_id):
        if ev is None:
            got.append("ping")
            continue
        got.append(ev.get("type"))
        if ev.get("type") == "worker_stream_end":
            pass  # keep consuming; the generator returns right after yielding it
    return got


class TestExpectSubscribeBeforePublish:
    @pytest.mark.asyncio
    async def test_expected_run_never_gets_early_terminal(self):
        """A run registered via expect() is a real pending run: a subscriber that
        attaches before the first event must NOT be handed a bogus worker_stream_end
        — it heartbeats and receives the events once they land."""
        bus = WorkerEventBus()
        bus._unknown_idle_cap = 1  # tiny cap: old code would terminate after ~5s
        bus.expect("run_eager")

        async def late_publisher():
            await asyncio.sleep(6.0)  # > old 5s grace
            bus.publish("run_eager", {"type": "delta", "content": "hi"})
            bus.close("run_eager")

        pub = asyncio.create_task(late_publisher())
        got = await consume_to_end(bus, "run_eager")
        await pub
        assert "delta" in got, f"expected delta for expected run, got {got}"
        assert "worker_stream_end" in got
        assert got.count("worker_stream_end") == 1

    @pytest.mark.asyncio
    async def test_unregistered_run_terminates_after_bounded_grace(self):
        """Garbage/never-registered run ids still terminate within a bounded wait,
        so a subscriber to a nonexistent run can never hang forever."""
        bus = WorkerEventBus()
        bus._unknown_idle_cap = 2  # 2 x ~5s grace
        t0 = asyncio.get_event_loop().time()
        got = await consume_to_end(bus, "never_exists")
        dt = asyncio.get_event_loop().time() - t0
        # Heartbeat pings may precede the terminal; the run must terminate on its
        # own after the bounded grace, never hang forever.
        assert got[-1] == "worker_stream_end"
        assert all(g in ("ping", "worker_stream_end") for g in got)
        assert 8 <= dt <= 14, f"expected ~10s bounded wait, got {dt:.1f}s"


class TestCloseDrivenPurge:
    @pytest.mark.asyncio
    async def test_unsubscribed_closed_run_is_purged(self):
        """A disk-backed run that was never subscribed is dropped from memory at
        close() — the JSONL keeps the history for later replay."""
        with tempfile.TemporaryDirectory() as td:
            bus = WorkerEventBus()
            bus.configure(td)
            bus.publish("run_never_sub", {"type": "delta", "content": "x"})
            bus.close("run_never_sub")
            for attr in ("_buffers", "_closed", "_seen", "_loaded", "_expected", "_subs"):
                assert "run_never_sub" not in getattr(bus, attr), f"{attr} not purged"

    @pytest.mark.asyncio
    async def test_purge_after_last_subscriber_leaves(self):
        """A disk-backed subscribed closed run is purged once the last subscriber
        exits (replay stays possible from disk)."""
        with tempfile.TemporaryDirectory() as td:
            bus = WorkerEventBus()
            bus.configure(td)
            bus.publish("run_purge", {"type": "delta", "content": "x"})
            bus.close("run_purge")
            got = await consume_to_end(bus, "run_purge")
            assert "delta" in got
            for attr in ("_buffers", "_closed", "_seen", "_loaded", "_expected", "_subs"):
                assert "run_purge" not in getattr(bus, attr), f"{attr} not purged"

    @pytest.mark.asyncio
    async def test_no_disk_bus_keeps_buffer(self):
        """A non-disk-backed bus (e.g. session bus) must NOT purge on close: its
        buffer is the only copy of the events."""
        bus = WorkerEventBus()  # no configure() -> no disk
        bus.publish("run_nodisk", {"type": "delta", "content": "x"})
        bus.close("run_nodisk")
        assert "run_nodisk" in bus._buffers, "no-disk bus must keep its buffer"
        assert "run_nodisk" in bus._closed

    @pytest.mark.asyncio
    async def test_disk_replay_after_purge(self):
        """Purged runs stay disk-backed: a later subscriber re-hydrates the full
        history (including the terminal) from the persisted JSONL."""
        bus = WorkerEventBus()
        with tempfile.TemporaryDirectory() as td:
            bus.configure(td)
            bus.publish("run_disk", {"type": "delta", "content": "persisted"})
            bus.close("run_disk")
            await consume_to_end(bus, "run_disk")
            assert "run_disk" not in bus._buffers
            got = await consume_to_end(bus, "run_disk")  # re-subscribe -> re-hydrate
            assert "delta" in got
            assert got[-1] == "worker_stream_end"

    @pytest.mark.asyncio
    async def test_cancellation_path_purges(self):
        """A client disconnect (CancelledError thrown into the generator) still
        purges a disk-backed run once the last subscriber leaves."""
        with tempfile.TemporaryDirectory() as td:
            bus = WorkerEventBus()
            bus.configure(td)
            bus.publish("run_cancel", {"type": "delta", "content": "x"})
            bus.close("run_cancel")

            async def cancellable():
                async for ev in bus.stream("run_cancel"):
                    if ev is not None:
                        break  # abandon at first event, then get cancelled

            task = asyncio.create_task(cancellable())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.1)
            assert "run_cancel" not in bus._buffers, f"cancel path leaked: {list(bus._buffers)}"
