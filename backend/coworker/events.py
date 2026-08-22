"""Worker run event bus — per-run pub/sub for worker SSE streaming.

The main agent's SSE stream only carries ``delegate_start`` / ``delegate_progress`` /
``delegate_end`` summary frames. Each worker run publishes its internal stream
(``delta`` / ``reasoning_delta`` / ``tool_start`` / ``tool_delta`` / ``tool_end`` /
``plan_*`` / ``todos`` / ``context_usage`` / ``done`` / ``error``) to this bus keyed
by a unique ``worker_run_id``. The frontend subscribes to
``GET /worker-events/{worker_run_id}`` on demand: the bus replays the persisted run
history, then follows live events, then emits a terminal ``worker_stream_end``.

Thread-safety: worker runs execute on the main event loop (``use_worker`` tool) or
in a thread pool (delegation tools), while subscribers are async. Publishing is
synchronous and lock-protected; delivery to async subscribers is bridged through
``call_soon_threadsafe`` on the bound loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any

from coworker.logger import get_logger

logger = get_logger(__name__)

SSE_HEARTBEAT_SECONDS = float(os.environ.get("COWORKER_SSE_HEARTBEAT_SECONDS", "15.0"))


class WorkerEventBus:
    """Thread-safe, per-run pub/sub bus for worker run events."""

    def __init__(self, buffer_size: int = 512) -> None:
        self._lock = threading.RLock()
        self._buffers: dict[str, deque] = {}
        self._closed: set[str] = set()
        self._seen: set[str] = set()
        self._expected: set[str] = set()
        self._loaded: set[str] = set()
        self._notify: dict[str, asyncio.Event] = {}
        # Subscriber refcount per run. stream() increments on attach and purges
        # the run's in-memory state once the last subscriber leaves a closed run.
        # Worker runs stay disk-backed, so a later stream() re-hydrates from the
        # persisted JSONL (see purge()). Without this, every completed worker run
        # keeps its buffer + seen/closed/loaded/notify entries forever — a slow
        # but unbounded memory leak across many worker runs in a long-lived backend.
        self._subs: dict[str, int] = {}
        # buffer_size <= 0 means unbounded (deque without maxlen). The session
        # bus uses this: a bounded queue + positional cursor in stream() drops
        # deltas whenever publishing outpaces the SSE subscriber (model bursts
        # get evicted, the cursor goes stale, and the frontend freezes until the
        # turn's done.parts lands). Unbounded guarantees nothing in-flight is
        # ever lost; each turn's buffer is purged when the subscription ends.
        self._buffer_size = buffer_size
        # Subscribe-before-publish grace: an unknown run (no event published yet)
        # is heartbeated up to this many wait periods before terminating, instead
        # of giving up after a single short timeout. This closes the race where a
        # frontend expanding a just-spawned worker block subscribes before the
        # worker's first bus event and is handed a bogus terminal frame.
        self._unknown_idle_cap = int(os.environ.get("COWORKER_BUS_UNKNOWN_IDLE_CAP", "6"))
        self._data_dir: Path | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _new_buffer(self) -> deque:
        if self._buffer_size <= 0:
            return deque()
        return deque(maxlen=self._buffer_size)

    def configure(self, data_dir: Path | str | None) -> None:
        """Point the bus at a directory for on-disk event persistence (optional)."""
        if data_dir is None:
            return
        self._data_dir = Path(data_dir) / "worker_events"

    def _file_for(self, run_id: str) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / f"{run_id}.jsonl"

    def _bind_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def _load_from_disk(self, run_id: str) -> None:
        """Hydrate a persisted run from disk into the in-memory buffer (once)."""
        if run_id in self._loaded:
            return
        self._loaded.add(run_id)
        f = self._file_for(run_id)
        if f is None or not f.exists():
            return
        try:
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    buf = self._buffers.setdefault(run_id, self._new_buffer())
                    buf.append(json.loads(line))
                    self._seen.add(run_id)
        except Exception:  # noqa: BLE001 - replay must never break the stream
            logger.warning("worker event replay failed for %s", run_id, exc_info=True)

    def _is_seen(self, run_id: str) -> bool:
        if run_id in self._seen or run_id in self._buffers or run_id in self._expected:
            return True
        f = self._file_for(run_id)
        return f is not None and f.exists()

    def expect(self, run_id: str) -> None:
        """Register a worker run that is about to start (spawned) but has not
        published its first event yet.

        Called by the worker engine when it emits ``delegate_start``. Subscribers
        that attach to an *expected* run are treated as attaching to a real pending
        run: they never get an early ``worker_stream_end`` and simply heartbeat at
        the normal cadence until the run closes. Only never-registered (garbage)
        run ids hit the bounded unknown-run grace window.
        """
        with self._lock:
            self._expected.add(run_id)

    def _wake(self, run_id: str) -> None:
        ev = self._notify.get(run_id)
        if ev is None:
            return
        loop = self._loop
        if loop is not None and loop.is_running() and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(ev.set)
                return
            except RuntimeError:
                pass
        ev.set()

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Publish an event to a worker run. Safe to call from any thread."""
        with self._lock:
            self._seen.add(run_id)
            buf = self._buffers.setdefault(run_id, self._new_buffer())
            buf.append(event)
            f = self._file_for(run_id)
        if f is not None:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                with f.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001 - persistence is best-effort
                logger.warning("worker event persist failed for %s", run_id, exc_info=True)
        self._wake(run_id)

    def close(self, run_id: str) -> None:
        """Mark a run finished; append the terminal event (persisted for replay).

        If no subscriber is currently attached, the run's in-memory state is
        dropped right away (the on-disk JSONL still allows later replay). Active
        subscribers keep the buffer; stream()'s refcount purges once the last one
        leaves. This makes ``close()`` the authoritative cleanup signal, so even
        never-subscribed runs cannot leak memory.
        """
        terminal: dict[str, Any] = {"type": "worker_stream_end", "worker_run_id": run_id}
        with self._lock:
            self._closed.add(run_id)
            buf = self._buffers.setdefault(run_id, self._new_buffer())
            buf.append(terminal)
            f = self._file_for(run_id)
        if f is not None:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                with f.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(terminal, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001 - persistence is best-effort
                logger.warning("worker terminal persist failed for %s", run_id, exc_info=True)
        self._wake(run_id)
        # No live subscriber: nothing else will drain the buffer. Drop it now —
        # but ONLY when the run is disk-backed, so a later subscriber can still
        # re-hydrate the full history. Without disk, the in-memory buffer is the
        # only copy and must be kept (it is bounded at buffer_size). Active
        # subscribers keep the buffer; stream()'s refcount purges when the last
        # one leaves.
        with self._lock:
            if self._subs.get(run_id, 0) == 0 and self._data_dir is not None:
                self.purge(run_id)

    def purge(self, run_id: str) -> None:
        """Drop a finished run's memory footprint (buffer/notify/closed/loaded/seen/expected).

        Used by the session event bus after each turn so per-turn keys do not
        accumulate unboundedly. Worker runs remain disk-backed: the next
        ``stream()`` re-hydrates from the persisted JSONL via ``_load_from_disk``.
        """
        with self._lock:
            self._buffers.pop(run_id, None)
            self._notify.pop(run_id, None)
            self._closed.discard(run_id)
            self._loaded.discard(run_id)
            self._seen.discard(run_id)
            self._expected.discard(run_id)

    async def stream(self, run_id: str):
        """Async generator: replay persisted history, follow live, then terminate.

        Yields ``None`` on heartbeat timeout so the caller can emit an SSE
        comment line (``: ping``) to keep the connection alive. A subscriber
        that attaches before the run's first event gets a bounded grace window
        (subscribe-before-publish race) before the run is treated as finished.

        The generator holds a subscriber refcount for the run; once the last
        subscriber leaves a *closed* run, the run's in-memory state is purged
        (the on-disk JSONL still allows later replay).
        """
        self._bind_loop()
        cursor = 0
        unknown_idle = 0
        with self._lock:
            self._subs[run_id] = self._subs.get(run_id, 0) + 1
        try:
            while True:
                with self._lock:
                    self._load_from_disk(run_id)
                    buf = self._buffers.get(run_id)
                    closed = run_id in self._closed
                    seen = self._is_seen(run_id)
                    if buf is None:
                        if closed and not seen:
                            yield {"type": "worker_stream_end", "worker_run_id": run_id}
                            return
                        buf = deque()
                    items = list(buf)[cursor:]
                    cursor += len(items)
                for event in items:
                    yield event
                    if isinstance(event, dict) and event.get("type") == "worker_stream_end":
                        return
                with self._lock:
                    closed = run_id in self._closed
                    buf = self._buffers.get(run_id)
                    if buf is not None and cursor >= len(buf) and closed:
                        yield {"type": "worker_stream_end", "worker_run_id": run_id}
                        return
                ev = self._notify.setdefault(run_id, asyncio.Event())
                # Unknown runs (first event not yet published) get a short grace
                # window; idle known runs simply heartbeat at the normal cadence.
                with self._lock:
                    timeout = min(SSE_HEARTBEAT_SECONDS, 5.0) if not self._is_seen(run_id) else SSE_HEARTBEAT_SECONDS
                try:
                    await asyncio.wait_for(ev.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    with self._lock:
                        if not self._is_seen(run_id):
                            unknown_idle += 1
                            if closed or unknown_idle >= self._unknown_idle_cap:
                                yield {"type": "worker_stream_end", "worker_run_id": run_id}
                                return
                    yield None
                    continue
                ev.clear()
        finally:
            with self._lock:
                self._subs[run_id] = max(0, self._subs.get(run_id, 1) - 1)
                if self._subs[run_id] == 0:
                    del self._subs[run_id]
                    # Purge once the last subscriber leaves a disk-backed run: the
                    # history stays on disk, so a later stream() re-hydrates from the
                    # persisted JSONL. This also covers runs closed *after* a late
                    # subscriber re-hydrated them (their _closed marker was already
                    # wiped by close()'s purge-on-no-subscribers). Non-disk-backed
                    # buses (session bus) keep their buffer as the only copy — the
                    # session endpoints purge explicitly per turn.
                    if self._data_dir is not None:
                        self.purge(run_id)


worker_event_bus = WorkerEventBus()

# Session event bus: decouples main-turn SSE delivery from the runtime generator.
# The turn runs as a background task publishing every event here (keyed by
# session_id); /chat/stream, regenerate and edit endpoints subscribe to it
# (replay + live). Because the bus fan-out is independent of the graph generator
# being blocked on a long-running tool, tool/worker status transitions reach the
# frontend live — exactly like opencode's session event bus. Memory-only (no
# configure); UNBOUNDED buffer (a bounded deque + positional cursor would drop
# deltas whenever publishing outpaces the SSE subscriber), and each turn's buffer
# is purged when its subscription ends.
session_event_bus = WorkerEventBus(buffer_size=0)
