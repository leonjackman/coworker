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
        self.goal_phase = "plan"
        self.goal_round = 0
        self.goal_status = ""
        self.goal_stop_reason = ""
        self.goal_token_budget = 0
        self.goal_tokens_used = 0
        self.goal_time_budget_seconds = 0
        self.goal_time_used = 0.0
        self.goal_repeat_count = 0
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
            goal_phase=self.goal_phase,
            goal_round=self.goal_round,
            goal_status=self.goal_status,
            goal_stop_reason=self.goal_stop_reason,
            goal_token_budget=self.goal_token_budget,
            goal_tokens_used=self.goal_tokens_used,
            goal_time_budget_seconds=self.goal_time_budget_seconds,
            goal_time_used=self.goal_time_used,
            goal_repeat_count=self.goal_repeat_count,
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
        if "goal_phase" in kwargs:
            self.goal_phase = kwargs["goal_phase"]
        if "goal_round" in kwargs:
            self.goal_round = kwargs["goal_round"]
        if "goal_status" in kwargs:
            self.goal_status = kwargs["goal_status"]
        if "goal_stop_reason" in kwargs:
            self.goal_stop_reason = kwargs["goal_stop_reason"]
        if "goal_tokens_used" in kwargs:
            self.goal_tokens_used = kwargs["goal_tokens_used"]
        if "goal_time_used" in kwargs:
            self.goal_time_used = kwargs["goal_time_used"]
        if "goal_repeat_count" in kwargs:
            self.goal_repeat_count = kwargs["goal_repeat_count"]
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
        if kwargs.get("status") is not None:
            self.goal_status = kwargs["status"]
        elif kwargs.get("done"):
            self.goal_status = "done"
        elif kwargs.get("paused"):
            self.goal_status = "paused"
        elif kwargs.get("interrupted"):
            self.goal_status = "interrupted"
        elif kwargs.get("stopped"):
            self.goal_status = "stopped"
        if kwargs.get("stop_reason") is not None:
            self.goal_stop_reason = kwargs["stop_reason"]
        elif kwargs.get("done"):
            self.goal_stop_reason = ""
        if kwargs.get("tokens_used") is not None:
            self.goal_tokens_used = kwargs["tokens_used"]
        if kwargs.get("time_used") is not None:
            self.goal_time_used = kwargs["time_used"]
        if kwargs.get("repeat_count") is not None:
            self.goal_repeat_count = kwargs["repeat_count"]
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
    select a round (1-based) whose stream raises instead of yielding. Each
    ``_stream`` call's kwargs are recorded on ``runtime.stream_calls["kw"]`` so
    tests can assert the injected goal_phase / goal_todo, and a yielded
    ``todos`` event is persisted to the store (mirroring main.py), so the next
    round reads the updated goal_todos when selecting the current todo.
    """
    active_store = store or FakeStore()

    def _generator(events):
        async def _gen():
            for event in events:
                if event.get("type") == "todos":
                    active_store.update_goal("s1", goal_todos=list(event.get("todos") or []))
                yield event

        return _gen()

    calls = {"n": 0, "kw": []}

    def _stream(*args, **kwargs):
        calls["n"] += 1
        calls["kw"].append(kwargs)
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
        session_store=active_store,
        change_store=change_store or FakeChangeStore(),
        mode="single",
        provider_name="fake",
        model_name="fake-model",
        agent="default_agent",
    )
    runtime.stream_calls = calls
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


def test_round_timeout_ends_round_and_continues():
    # A per-round wall-clock timeout must end ONLY the round, not the whole goal:
    # accounting is persisted and a fresh round continues (long tasks span rounds).
    store = FakeStore()
    rounds = [
        # Round 1 times out mid-stream.
        [{"type": "tool_start", "id": "t1", "name": "read_file", "input": ""}],
        # Round 2 achieves.
        [
            {"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"},
            {"type": "done", "content": "", "parts": [], "round_budget": False},
        ],
    ]
    runtime = _make_runtime(rounds, store=store, timeout_on=1)
    events = _run_goal(runtime)

    # The timed-out round surfaced as a goal_round(status="timeout") event and the
    # goal CONTINUED to a fresh round instead of terminating.
    assert any(e["type"] == "goal_round" and e.get("status") == "timeout" for e in events)
    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") in (None, "")
    assert store.goal_done is True
    assert store.goal_round == 2


def test_round_timeout_with_exhausted_budget_terminates():
    # When the budget is already spent, a round timeout terminates the goal as
    # budget_exhausted (the safety net) instead of starting another round.
    store = FakeStore()
    store.goal_time_budget_seconds = 1
    store.goal_time_used = 1.0  # budget already spent
    runtime = _make_runtime([[]], store=store, timeout_on=1)
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") == "budget_exhausted"
    assert store.goal_status == "stopped"
    assert store.goal_stop_reason == "budget_exhausted"


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
    # A stream failure (provider error / GraphRecursionError / ...) must end the
    # goal with a CLEAN goal_done(reason="stream_error") + persisted terminal state
    # instead of hanging the SSE stream or leaving the goal looking active.
    store = FakeStore()
    runtime = _make_runtime(
        [[{"type": "tool_start", "id": "t1", "name": "read_file", "input": ""}]],
        store=store,
        error_on=1,
    )
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") == "stream_error"
    assert store.goal_stopped is True
    assert store.goal_status == "stopped"
    assert store.goal_stop_reason == "stream_error"


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


# ---------------------------------------------------------------------------
# Phase progression: plan → execute → verify (one todo per execute round).
# ---------------------------------------------------------------------------


def test_plan_to_execute_to_verify_to_done():
    store = FakeStore()
    runtime = _make_runtime(
        [
            # Round 1 (plan): agent produces a plan (todos) then checks in.
            [
                {"type": "todos", "todos": [{"content": "A", "status": "pending"}, {"content": "B", "status": "pending"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "planned"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 2 (execute): focus on todo A, marks it done, B still open.
            [
                {"type": "todos", "todos": [{"content": "A", "status": "completed"}, {"content": "B", "status": "in_progress"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "A done"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 3 (execute): todo B done → all todos complete → verify.
            [
                {"type": "todos", "todos": [{"content": "A", "status": "completed"}, {"content": "B", "status": "completed"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "B done"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 4 (verify): agent runs verification and completes.
            [
                {"type": "goal_checkpoint", "achieved": True, "progress": "verified", "verification": "tests ok"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    # goal_round events carry the phase.
    assert [e.get("phase") for e in events if e["type"] == "goal_round"] == ["plan", "execute", "execute", "verify"]

    # Each round injected the right phase + current todo.
    kw = [k for k in runtime.stream_calls["kw"]]
    assert [k.get("goal_phase") for k in kw] == ["plan", "execute", "execute", "verify"]
    assert [k.get("goal_todo") for k in kw] == ["", "A", "B", ""]

    # Transitions were persisted via update_goal.
    assert {"goal_phase": "execute"} in store.updates
    assert {"goal_phase": "verify"} in store.updates

    # Terminal: achieved in the verify round, atomically committed done.
    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("verification") == "tests ok"
    assert store.goal_done is True


def test_plan_without_todos_stays_in_plan():
    # A plan round that produces no todos must NOT advance to execute; once todos
    # finally appear it advances.
    store = FakeStore()
    runtime = _make_runtime(
        [
            # Round 1: checkpoint but no todos → still "plan".
            [
                {"type": "goal_checkpoint", "achieved": False, "progress": "working"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 2: produces the plan.
            [
                {"type": "todos", "todos": [{"content": "X", "status": "pending"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "planned"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 3: execute, achieved.
            [
                {"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    assert [e.get("phase") for e in events if e["type"] == "goal_round"] == ["plan", "plan", "execute"]
    # Only ONE execute transition, and it happened after round 2.
    executes = [u for u in store.updates if u == {"goal_phase": "execute"}]
    assert len(executes) == 1
    assert [k.get("goal_phase") for k in runtime.stream_calls["kw"]] == ["plan", "plan", "execute"]
    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True


def test_execute_stays_in_execute_while_todos_remain():
    store = FakeStore()
    runtime = _make_runtime(
        [
            # Round 1: plan.
            [
                {"type": "todos", "todos": [{"content": "A", "status": "pending"}, {"content": "B", "status": "pending"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "planned"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 2: only todo A progressed; B remains → no verify transition.
            [
                {"type": "todos", "todos": [{"content": "A", "status": "in_progress"}, {"content": "B", "status": "pending"}]},
                {"type": "goal_checkpoint", "achieved": False, "progress": "on A"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
            # Round 3: done.
            [
                {"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"},
                {"type": "done", "content": "", "parts": [], "round_budget": False},
            ],
        ],
        store=store,
    )
    events = _run_goal(runtime)

    assert [e.get("phase") for e in events if e["type"] == "goal_round"] == ["plan", "execute", "execute"]
    assert {"goal_phase": "verify"} not in store.updates
    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True


# ---------------------------------------------------------------------------
# Governance: no-progress fingerprint, budget exhaustion, soft hand-off,
# persisted-todos phase transition, todos preservation, control-tool cleanup.
# ---------------------------------------------------------------------------


def test_identical_checkpoints_terminate_as_no_progress():
    # The 14-round runaway regression: an agent that calls finalize_goal(achieved=false)
    # with the SAME progress/verification every round must be detected as no-progress
    # and terminated instead of looping forever.
    store = FakeStore()
    identical = [
        {"type": "goal_checkpoint", "achieved": False, "progress": "已修復 3 個 bug", "verification": ""},
        {"type": "done", "content": "", "parts": [], "round_budget": False},
    ]
    runtime = _make_runtime([identical, identical, identical, identical], store=store)
    events = _run_goal(runtime)

    # Round 1 establishes the baseline; rounds 2-4 are identical → terminate.
    assert [e.get("phase") for e in events if e["type"] == "goal_round"] == ["plan", "plan", "plan", "plan"]
    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") == "no_progress"
    assert events[-1].get("stalled") is True
    assert store.goal_stopped is True
    assert store.goal_status == "stopped"
    assert store.goal_stop_reason == "no_progress"
    assert store.goal_repeat_count >= 3


def test_changed_progress_resets_repeat_counter():
    # Different progress each round is genuine progress → never trips no_progress.
    store = FakeStore()
    rounds = [
        [{"type": "goal_checkpoint", "achieved": False, "progress": "step A", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
        [{"type": "goal_checkpoint", "achieved": False, "progress": "step B", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
        [{"type": "goal_checkpoint", "achieved": False, "progress": "step C", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
        [{"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") in (None, "")
    assert store.goal_done is True
    assert store.goal_status == "done"


def test_budget_exhaustion_allows_wrap_up_then_terminates():
    # Budget accounting: when the goal hits the token budget, one wrap-up round is
    # allowed (with a "budget exhausted, wrap up now" prompt), then it terminates
    # as budget_exhausted instead of looping forever.
    store = FakeStore()
    store.goal_token_budget = 1_000_000
    store.goal_tokens_used = 1_000_000
    rounds = [
        # Round 1: the wrap-up round (not achieved → keeps going).
        [{"type": "goal_checkpoint", "achieved": False, "progress": "wrapping up", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
        # Round 2: loop top sees budget still exhausted + wrap-up already granted → terminate.
        [{"type": "goal_checkpoint", "achieved": False, "progress": "wrapping up", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime)

    # The first stream call carried a wrap-up budget note the model could see.
    kw0 = runtime.stream_calls["kw"][0]
    assert "預算已耗盡" in kw0.get("goal_budget_note", "")
    assert events[-1]["type"] == "goal_done"
    assert events[-1].get("reason") == "budget_exhausted"
    assert store.goal_status == "stopped"
    assert store.goal_stop_reason == "budget_exhausted"


def test_round_token_usage_accounted_and_soft_handoff():
    # Round usage is summed into the persisted goal_tokens_used, and a round that
    # spends more than the soft threshold asks the NEXT round to hand off.
    store = FakeStore()
    store.goal_token_budget = 1_000_000
    rounds = [
        # Round 1 spends 200k tokens (soft threshold = 150k of 1M).
        [
            {"type": "goal_checkpoint", "achieved": False, "progress": "big chunk", "verification": ""},
            {"type": "done", "content": "", "parts": [], "round_budget": False, "usage": {"prompt_tokens": 200_000, "completion_tokens": 0}},
        ],
        # Round 2: handoff hint injected; achieves.
        [{"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime)

    assert store.goal_tokens_used >= 200_000
    kw1 = runtime.stream_calls["kw"][1]
    assert "上一輪工作量較大" in kw1.get("goal_budget_note", "")
    assert "200000" in kw1.get("goal_budget_note", "")
    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True


def test_execute_transitions_to_verify_from_persisted_todos():
    # Regression: execute phase must advance to verify from the PERSISTED todos
    # even when the current round emits no write_todos event (the phase-stuck bug).
    store = FakeStore()
    store.goal_phase = "execute"
    store.goal_todos = [{"content": "A", "status": "completed"}, {"content": "B", "status": "completed"}]
    rounds = [
        [{"type": "goal_checkpoint", "achieved": False, "progress": "working", "verification": ""}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
        [{"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime, goal_continue_first=True)

    assert [e.get("phase") for e in events if e["type"] == "goal_round"] == ["execute", "verify"]
    assert {"goal_phase": "verify"} in store.updates
    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True


def test_terminal_commit_preserves_persisted_todos():
    # A terminal commit with empty last_todos must NOT clobber the todos that
    # earlier rounds persisted (the todos=[] bug).
    store = FakeStore()
    store.goal_todos = [{"content": "A", "status": "completed"}]
    rounds = [
        [
            {"type": "goal_checkpoint", "achieved": True, "progress": "完成", "verification": "ok"},
            {"type": "done", "content": "全部完成", "parts": [], "round_budget": False},
        ],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime)

    assert events[-1]["type"] == "goal_done"
    assert store.goal_done is True
    assert store.goal_todos == [{"content": "A", "status": "completed"}]


def test_control_tools_never_become_tool_cards():
    # finalize_goal / write_todos must never leak into the persisted parts as an
    # empty-name "Used tool:" error card, even when the tool-call name arrives in a
    # later streaming chunk than the args.
    from langchain_core.messages import AIMessageChunk, ToolMessage

    runtime = object.__new__(OpenAICompatibleStreamRuntime)
    content_parts: list[str] = []
    tool_state: dict = {}
    parts: list[dict] = []

    # Chunk 1: args arrive first, name empty → would create a stray tool_start.
    runtime._handle_message_chunk(
        AIMessageChunk(content="", tool_call_chunks=[{"index": 0, "id": "tc1", "name": "", "args": '{"progress":"x"}'}]),
        content_parts, tool_state, parts, "s1",
    )
    assert any(p.get("type") == "tool_start" and p.get("id") == "tc1" for p in parts)

    # Chunk 2: name arrives → control-tool cleanup must drop the stray card.
    runtime._handle_message_chunk(
        AIMessageChunk(content="", tool_call_chunks=[{"index": 0, "id": "tc1", "name": "finalize_goal", "args": '{"achieved":false}'}]),
        content_parts, tool_state, parts, "s1",
    )
    # ToolMessage arrives → goal_checkpoint emitted, still no card.
    events = runtime._handle_message_chunk(
        ToolMessage(content='{"achieved": false, "progress": "x"}', name="finalize_goal", tool_call_id="tc1"),
        content_parts, tool_state, parts, "s1",
    )
    assert [e.get("type") for e in events] == ["goal_checkpoint"]
    assert not any(p.get("id") == "tc1" for p in parts)
    assert "tc1" not in tool_state


def test_goal_budget_note_mirrors_remaining():
    from coworker.agents import _goal_budget_note

    note = _goal_budget_note(
        tokens_used=250_000, token_budget=1_000_000,
        time_used=60.0, time_budget_seconds=1800,
        phase="execute",
    )
    assert "250000" in note and "1000000" in note and "750000" in note
    assert "60" in note and "1800" in note


def test_effective_goal_status_legacy_mapping():
    from coworker.sessions import Session

    s = Session(id="x", title="t", created_at="", updated_at="", goal_done=True)
    assert s.effective_goal_status() == "done"
    s2 = Session(id="x", title="t", created_at="", updated_at="", goal_paused=True)
    assert s2.effective_goal_status() == "paused"
    s3 = Session(id="x", title="t", created_at="", updated_at="", goal_status="done", goal_paused=True)
    assert s3.effective_goal_status() == "done"


def test_goal_round_persisted_and_resume_continues():
    # The goal card's "round N" must survive session switches and pause/resume:
    # goal_stream persists goal_round each round, and a resumed goal continues the
    # counter from the persisted value instead of restarting at round 1.
    store = FakeStore()
    store.goal_round = 5
    rounds = [
        [{"type": "goal_checkpoint", "achieved": True, "progress": "done", "verification": "ok"}, {"type": "done", "content": "", "parts": [], "round_budget": False}],
    ]
    runtime = _make_runtime(rounds, store=store)
    events = _run_goal(runtime, goal_continue_first=True)

    # Resumed from persisted round 5 → the resumed round is round 6, and it was
    # persisted back so the next session-switch restore shows 6.
    assert [e.get("round") for e in events if e["type"] == "goal_round"] == [6]
    assert store.goal_round == 6


def test_usage_only_stream_chunk_does_not_crash_reasoning_patch():
    # Regression for the "goal stopped immediately with stream_error" bug: enabling
    # stream_usage=True makes vLLM/Ollama send a FINAL usage-only chunk with
    # `choices: []`. The reasoning-preserving patch indexed choices[0] directly and
    # raised "list index out of range", aborting the whole round. The patched
    # converter must tolerate the empty-choices chunk (and still surface usage).
    from langchain_core.messages import AIMessageChunk

    from coworker.agents import ReasonPreservingChatOpenAI

    llm = ReasonPreservingChatOpenAI.create(model="m", temperature=0, api_key="k", base_url="http://127.0.0.1:1/v1")
    usage_only_chunk = {
        "id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    out = llm._convert_chunk_to_generation_chunk(usage_only_chunk, AIMessageChunk, None)

    assert out is not None
    assert out.message.usage_metadata is not None
    assert out.message.usage_metadata["total_tokens"] == 8


def test_normalize_usage_accepts_both_key_naming():
    # Regression for the 04146cdd bug: langchain-core 1.x UsageMetadata uses
    # input_tokens/output_tokens, but the goal accumulator read prompt_tokens/
    # completion_tokens → goal_tokens_used was always 0 and the token budget was
    # silently disabled. Both key sets must normalize to the same numbers.
    from coworker.agents import _normalize_usage

    # langchain-core 1.x UsageMetadata (what the AIMessage carries)
    assert _normalize_usage({"input_tokens": 10270, "output_tokens": 59, "total_tokens": 10329}) == (10270, 59)
    # raw OpenAI-compatible usage dict (older / other providers)
    assert _normalize_usage({"prompt_tokens": 10270, "completion_tokens": 59, "total_tokens": 10329}) == (10270, 59)
    # empty / missing usage never crashes and yields zeros
    assert _normalize_usage({}) == (0, 0)
