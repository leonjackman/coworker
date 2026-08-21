"""Focused tests for the goal-loop terminal-state ownership.

The goal loop must be the SOLE OWNER of the goal state machine: every terminal
decision (achieved / paused / stopped / timeout / stalled / max rounds) is
atomically committed to the session (flags + todos + final message in ONE
load→mutate→save via ``SessionStore.commit_goal_end``) BEFORE the terminal event
is yielded. A crash between "decision" and "persist" must never leave the goal
looking active, and a failure reason must never land as a blank bubble.

``goal_stream`` only touches ``self._stream``, ``self.session_store`` and
``self.change_store``, so these tests drive it with fakes — no LLM / LangGraph
needed. Each round gets its own fresh ``_stream`` generator, mirroring one graph
invocation per round.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agents import OpenAICompatibleStreamRuntime  # noqa: E402


class FakeStore:
    def __init__(self, *, goal_max_rounds=50, goal_force_count=0, goal_text="測試目標"):
        self.goal_text = goal_text
        self.goal_max_rounds = goal_max_rounds
        self.goal_force_count = goal_force_count
        self.goal_interrupted = False
        self.goal_done = False
        self.goal_just_edited = False
        self.goal_stopped = False
        self.goal_paused = False
        self.goal_todos: list[dict] = []
        self.messages: list[dict] = []
        self.updates: list[dict] = []
        self.commits: list[dict] = []
        self.saved = 0

    def require(self, session_id):
        return SimpleNamespace(
            goal_text=self.goal_text,
            goal_max_rounds=self.goal_max_rounds,
            goal_force_count=self.goal_force_count,
            goal_interrupted=self.goal_interrupted,
            goal_done=self.goal_done,
            goal_just_edited=self.goal_just_edited,
            goal_stopped=self.goal_stopped,
            goal_paused=self.goal_paused,
            goal_todos=self.goal_todos,
        )

    def save(self, session):
        self.saved += 1
        return session

    def update_goal(self, session_id, **kwargs):
        self.updates.append(kwargs)
        if "goal_force_count" in kwargs:
            self.goal_force_count = kwargs["goal_force_count"]
        if "goal_paused" in kwargs:
            self.goal_paused = kwargs["goal_paused"]
        if "goal_done" in kwargs:
            self.goal_done = kwargs["goal_done"]
        if "goal_todos" in kwargs:
            self.goal_todos = list(kwargs["goal_todos"])
        return self.require(session_id)

    def commit_goal_end(self, session_id, **kwargs):
        self.commits.append(kwargs)
        if kwargs.get("done") is not None:
            self.goal_done = kwargs["done"]
        if kwargs.get("paused") is not None:
            self.goal_paused = kwargs["paused"]
        if kwargs.get("stopped") is not None:
            self.goal_stopped = kwargs["stopped"]
        if kwargs.get("interrupted") is not None:
            self.goal_interrupted = kwargs["interrupted"]
        if kwargs.get("todos") is not None:
            self.goal_todos = list(kwargs["todos"])
        if kwargs.get("content"):
            self.messages.append(
                SimpleNamespace(
                    id=kwargs.get("message_id") or "generated", role="assistant",
                    content=kwargs["content"], parts=kwargs.get("parts") or [],
                )
            )
        return SimpleNamespace(messages=self.messages)


class FakeChangeStore:
    def __init__(self):
        self.assignments: list[tuple[str, str]] = []

    def assign_message(self, session_id, message_id):
        self.assignments.append((session_id, message_id))
        return 1


def _make_runtime(rounds, *, store=None, change_store=None, timeout_on=None, error_on=None):
    """Build a runtime whose ``_stream`` replays one event batch per round.

    ``rounds`` is a list of per-round event lists. ``timeout_on``/``error_on``
    select a round (1-based) whose stream raises instead of yielding.
    """

    def _generator(events):
        async def _gen():
            for event in events:
                yield event

        return _gen()

    calls = {"n": 0}

    def _stream(*args, **kwargs):
        calls["n"] += 1
        idx = calls["n"] - 1
        events = rounds[min(idx, len(rounds) - 1)]
        if timeout_on is not None and calls["n"] == timeout_on:
            async def _boom():
                for event in events:
                    yield event
                raise asyncio.TimeoutError()

            return _boom()
        if error_on is not None and calls["n"] == error_on:
            async def _err():
                for event in events:
                    yield event
                raise RuntimeError("boom")

            return _err()
        return _generator(events)

    runtime = SimpleNamespace(
        _stream=_stream,
        session_store=store or FakeStore(),
        change_store=change_store or FakeChangeStore(),
        mode="single",
        provider_name="fake",
        model_name="fake-model",
        agent="default_agent",
    )
    return runtime


def _run_goal(runtime, **kwargs):
    kwargs.setdefault("_cancel_event", asyncio.Event())
    return _run(_collect(OpenAICompatibleStreamRuntime.goal_stream(
        runtime, [], "s1", "zh", "build", "autonomous", goal_text="test", **kwargs,
    )))


async def _collect(stream):
    return [event async for event in stream]


def _run(coro):
    return asyncio.run(coro)


def test_achieved_commits_terminal_atomically():
    store = FakeStore()
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
                {"type": "goal_checkpoint", "achieved": True, "progress": "全部完成", "verification": "測試通過"},
                {"type": "done", "content": "", "parts": [{"type": "tool", "id": "t1", "name": "read_file", "status": "success"}], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    types = [e["type"] for e in events]
    assert types[0] == "goal_start"
    assert types[-1] == "goal_done"
    assert events[-1].get("verification") == "測試通過"

    # The terminal state must be committed atomically (one commit_goal_end call)
    # with goal_done=True and the message persisted in the same write.
    assert len(store.commits) == 1
    commit = store.commits[0]
    assert commit["done"] is True
    assert commit["content"] == "全部完成"
    assert store.goal_done is True
    # The message must be persisted (non-empty) and bound to changes.
    assert store.messages and store.messages[-1].content == "全部完成"
    assert len(runtime.change_store.assignments) == 1


def test_pause_commits_paused():
    store = FakeStore()
    store.goal_paused = True  # user paused before this round boundary
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
                {"type": "delta", "content": "讀取中"},
                {"type": "done", "content": "讀取中", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_paused"
    assert len(store.commits) == 1
    assert store.commits[0]["paused"] is True
    assert store.goal_paused is True


def test_timeout_commits_stopped():
    store = FakeStore()
    runtime = _make_runtime([[]], store=store, timeout_on=1)
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1]["reason"] == "timeout"
    # Atomic terminal commit: goal_stopped=True + non-empty label message.
    assert len(store.commits) == 1
    assert store.commits[0]["stopped"] is True
    assert store.commits[0]["content"] == "Agent timed out"
    assert store.goal_stopped is True
    assert store.messages and store.messages[-1].content == "Agent timed out"


def test_cancel_commits_stopped():
    store = FakeStore()
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
            ],
        ],
        store=store,
    )

    async def _run_with_cancel():
        cancel = asyncio.Event()

        async def _stream(*args, **kwargs):
            yield {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""}
            raise asyncio.CancelledError()

        runtime._stream = _stream
        events = []
        try:
            async for event in OpenAICompatibleStreamRuntime.goal_stream(
                runtime, [], "s1", "zh", "build", "autonomous", goal_text="test", _cancel_event=cancel,
            ):
                events.append(event)
        except asyncio.CancelledError:
            pass
        return events

    events = _run(_run_with_cancel())

    assert events[-1]["type"] == "goal_done"
    assert events[-1]["reason"] == "stopped"
    assert len(store.commits) == 1
    assert store.commits[0]["stopped"] is True
    assert store.goal_stopped is True


def test_max_rounds_cap_commits_stopped():
    store = FakeStore(goal_max_rounds=1)
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
                {"type": "done", "content": "未完", "parts": [], "round_budget": False},
            ],
            [
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)
    assert events[-1]["type"] == "goal_done"
    assert events[-1]["reason"] == "max_rounds_exceeded"
    assert len(store.commits) == 1
    assert store.commits[0]["stopped"] is True
    assert store.goal_stopped is True


def test_tools_but_no_checkpoint_stalls():
    # Regression for the reported stall: the agent used tools every round but
    # NEVER called finalize_goal. The old guard only stalled when the round had
    # NO tools; the new guard counts CONSECUTIVE empty rounds regardless of tools.
    store = FakeStore()
    round_events = [
        [
            {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
            {"type": "done", "content": "查了一些文件", "parts": [{"type": "tool", "id": "t1", "name": "read_file", "status": "success"}], "round_budget": False},
        ]
    ]
    runtime = _make_runtime(round_events, store=store)
    events = _run_goal(runtime)

    force_events = [e for e in events if e["type"] == "goal_force"]
    # GOAL_MAX_FORCE consecutive empty rounds → stalled.
    assert len(force_events) >= 2  # nudges fired
    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("stalled") is True
    # Terminal commit with stopped=True + non-empty label.
    assert store.commits and store.commits[-1]["stopped"] is True
    assert store.goal_stopped is True


def test_checkpoint_resets_stall_counter():
    # A checkpoint (real progress) resets the consecutive-empty-round counter, so
    # an agent that alternates progress and plain text never stalls.
    store = FakeStore()
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
                {"type": "goal_checkpoint", "achieved": False, "progress": "第一步完成"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            [
                # Empty round after progress: one nudge, counter incremented.
                {"type": "done", "content": "没 finalize", "parts": [], "round_budget": False},
            ],
            [
                # Checkpoint again → counter reset, no stall.
                {"type": "goal_checkpoint", "achieved": True, "progress": "全部完成", "verification": "ok"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("verification") == "ok"
    # No stall: the final checkpoint committed done=True.
    assert store.goal_done is True
    assert store.goal_stopped is False


def test_resume_continues_round_counter():
    store = FakeStore()
    runtime = _make_runtime(
        [
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": ""},
                {"type": "goal_checkpoint", "achieved": True, "progress": "完成", "verification": "ok"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime, goal_continue_first=True)

    rounds = [e.get("round") for e in events if e["type"] == "goal_round"]
    assert rounds == [1]
    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True


def test_unexpected_error_still_terminates():
    store = FakeStore()
    runtime = _make_runtime(
        [[{"type": "tool_start", "id": "t1", "name": "read_file", "input": ""}]],
        store=store,
        error_on=1,
    )
    with pytest.raises(RuntimeError):
        _run_goal(runtime)


def test_already_done_is_noop():
    store = FakeStore()
    store.goal_done = True
    runtime = _make_runtime(
        [
            [
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("already") is True
    # No spurious commit: nothing to persist.
    assert store.commits == []
