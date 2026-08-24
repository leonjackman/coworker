"""Per-session "steer" inbox for the interjection (插話) feature.

While the agent is streaming a reply, the user can pick a queued message and
interject it into the RUNNING task to guide the LLM's subsequent output and
thinking direction — without pausing or terminating the stream (the same
"steer" semantics opencode and codex implement).

The running ``create_agent`` graph polls this inbox at every model-call
boundary (``SteerInjectionMiddleware.abefore_model``): pending steers are folded
into the next model request as ``HumanMessage`` inputs, so the agent's next
reasoning step incorporates the guidance while the current stream is never
aborted. Steers that arrive too late (the graph already finished) are left
pending for the frontend's "auto-continue as next turn" fallback.

Thread-safety mirrors ``WorkerEventBus``: pushes come from the ``/chat/interject``
HTTP handler and consumers run inside the graph on the event loop, so a lock
guards the per-session lists.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SteerEntry:
    """One user interjection waiting to be injected into a running turn."""

    id: str
    content: str
    ts: int
    user_message_id: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    max_attachment_bytes: int = 25 * 1024 * 1024


class SteerInbox:
    """Per-session FIFO of interjection messages not yet consumed by the graph."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, list[SteerEntry]] = {}

    def push(self, session_id: str, entry: SteerEntry) -> None:
        with self._lock:
            self._entries.setdefault(session_id, []).append(entry)

    def has_pending(self, session_id: str) -> bool:
        with self._lock:
            return bool(self._entries.get(session_id))

    def pending_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._entries.get(session_id) or [])

    def take_all(self, session_id: str) -> list[SteerEntry]:
        """Drain every pending steer for a session, oldest first."""
        with self._lock:
            return list(self._entries.pop(session_id, []))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()


# Module-level singleton, mirroring ``session_event_bus`` / ``worker_event_bus``:
# both the /chat/interject handler (push) and the in-graph steer middleware
# (take_all) reference this same instance.
steer_inbox = SteerInbox()
