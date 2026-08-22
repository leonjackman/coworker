"""Tests for the WorkerAgent module."""

import asyncio

import pytest

from coworker.workers.worker_config import TaskBrief, WorkerConfig, WorkerResult
from coworker.workers.worker_summarize import _rule_truncate


class TestWorkerConfig:
    def test_for_single_agent_defaults(self):
        config = WorkerConfig.for_single_agent(language="zh", max_concurrent=4)
        assert config.timeout == 600
        assert config.max_output_chars == 2000
        assert config.max_concurrent == 4
        assert config.language == "zh"

    def test_for_single_agent_custom(self):
        config = WorkerConfig.for_single_agent(language="en", max_concurrent=2)
        assert config.language == "en"
        assert config.max_concurrent == 2
        assert config.timeout == 600
        assert config.max_output_chars == 2000

    def test_for_single_agent_none_concurrent(self):
        config = WorkerConfig.for_single_agent(max_concurrent=None)
        assert config.max_concurrent == 4  # defaults to 4

    def test_for_delegation(self):
        config = WorkerConfig.for_delegation(
            memory_rel="proj/agent/BASE/MEMORY.md",
            language="en",
        )
        assert config.memory_rel == "proj/agent/BASE/MEMORY.md"
        assert config.language == "en"
        assert config.timeout == 600
        assert config.max_concurrent == 4
        assert config.max_output_chars == 2000


class TestTaskBrief:
    def test_basic(self):
        brief = TaskBrief(task="test task")
        assert brief.task == "test task"
        assert brief.context == ""
        assert brief.expected_output == ""
        assert brief.constraints == []

    def test_full(self):
        brief = TaskBrief(
            task="analyze",
            context="provided context",
            expected_output="JSON array",
            constraints=["no network", "no writes"],
        )
        assert brief.task == "analyze"
        assert brief.context == "provided context"
        assert brief.expected_output == "JSON array"
        assert brief.constraints == ["no network", "no writes"]


class TestWorkerResult:
    def test_success(self):
        result = WorkerResult(content="hello", success=True, raw_length=5)
        assert result.content == "hello"
        assert result.success is True
        assert result.raw_length == 5
        assert result.was_truncated is False
        assert result.error == ""

    def test_failure_timeout(self):
        result = WorkerResult(
            content="超时", success=False, error="timeout"
        )
        assert result.success is False
        assert result.error == "timeout"

    def test_future_fields(self):
        result = WorkerResult(
            content="done",
            artifacts=["src/auth.py", "tests/test_auth.py"],
            structured={"findings": 3},
        )
        assert result.artifacts == ["src/auth.py", "tests/test_auth.py"]
        assert result.structured == {"findings": 3}


class TestWorkerSummarize:
    def test_no_truncate_under_limit(self):
        result = _rule_truncate("short text", 2000)
        assert result == "short text"

    def test_truncate_over_limit(self):
        long_text = "x" * 3000
        result = _rule_truncate(long_text, 2000)
        # Must contain the first 2000 chars
        assert result.startswith("x" * 2000)
        # Must contain truncation notice
        assert "截断" in result
        assert "3000" in result

    def test_empty_content(self):
        result = _rule_truncate("", 2000)
        assert result == ""

    def test_exact_limit(self):
        exact = "y" * 2000
        result = _rule_truncate(exact, 2000)
        assert result == exact


class TestWorkerMaxDepth:
    """Engine-level delegation depth guard (root-cause fix for nested workers)."""

    def test_for_single_agent_max_depth(self):
        config = WorkerConfig.for_single_agent()
        assert config.max_depth == 3

    def test_for_delegation_max_depth(self):
        config = WorkerConfig.for_delegation(max_depth=5)
        assert config.max_depth == 5
        config2 = WorkerConfig.for_delegation()
        assert config2.max_depth == 3

    @pytest.mark.asyncio
    async def test_worker_refuses_to_run_beyond_max_depth(self):
        from coworker.workers.worker import WorkerAgent

        worker = WorkerAgent(
            llm=None,
            brief=TaskBrief(task="x"),
            config=WorkerConfig(max_depth=2),
            workspace=None,
            tools=[],
            approval_store=None,
            change_store=None,
            session_store=None,
            data_dir=None,
            mcp_session_manager=None,
            skill_manager=None,
            provider_name="",
            depth=2,  # at the cap → must refuse without building/running any graph
        )
        result = await worker.arun()
        assert result.success is False
        assert result.error == "max_depth"
        assert "max_depth" in result.content

    @pytest.mark.asyncio
    async def test_worker_below_max_depth_runs(self, monkeypatch):
        from coworker.workers import worker as worker_mod
        from coworker.workers.worker import WorkerAgent

        called = {"n": 0}

        async def fake_execute(self):
            called["n"] += 1
            return WorkerResult(content="ok", success=True)

        monkeypatch.setattr(WorkerAgent, "_execute", fake_execute)
        worker = WorkerAgent(
            llm=None,
            brief=TaskBrief(task="x"),
            config=WorkerConfig(max_depth=2),
            workspace=None,
            tools=[],
            approval_store=None,
            change_store=None,
            session_store=None,
            data_dir=None,
            mcp_session_manager=None,
            skill_manager=None,
            provider_name="",
            depth=0,
        )
        result = await worker.arun()
        assert result.success is True
        assert called["n"] == 1


class TestWorkerCancellation:
    """Stopping the main turn cancels workers; they must emit delegate_end and
    close their worker bus so the frontend spinner/SSE terminates."""

    @pytest.mark.asyncio
    async def test_cancelled_worker_closes_bus_and_emits_delegate_end(self, monkeypatch):
        import asyncio
        import uuid

        from coworker.events import WorkerEventBus
        from coworker.workers import worker as worker_mod
        from coworker.workers.worker import WorkerAgent

        emitted: list[dict] = []
        bus = WorkerEventBus()
        run_id = "canceltest"
        hung = asyncio.Event()

        class FakeGraph:
            async def astream(self, *a, **kw):
                # a real LangGraph astream is an async generator; yield one benign
                # chunk then hang forever until cancelled (like a long worker run)
                yield "custom", {"type": "context_usage", "used_chars": 0, "budget_chars": 0}
                while True:
                    await asyncio.sleep(60)
                    yield "custom", {"type": "context_usage", "used_chars": 0, "budget_chars": 0}

        worker = WorkerAgent(
            llm=None,
            brief=TaskBrief(task="x"),
            config=WorkerConfig(max_depth=2),
            workspace=None,
            tools=[],
            approval_store=None,
            change_store=None,
            session_store=None,
            data_dir=None,
            mcp_session_manager=None,
            skill_manager=None,
            provider_name="",
            emit=lambda e: emitted.append(e),
            worker_bus=bus,
            worker_run_id=run_id,
            depth=0,
        )
        monkeypatch.setattr(worker, "_build_graph", lambda: FakeGraph())
        monkeypatch.setattr(worker, "_build_state", lambda: {})

        task = asyncio.create_task(worker.arun())
        await asyncio.sleep(0.2)  # let it reach the hanging astream
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # delegate_end with error="cancelled" was emitted
        ends = [e for e in emitted if e.get("type") == "delegate_end"]
        assert ends, f"expected delegate_end, got {emitted}"
        assert ends[-1].get("error") == "cancelled"
        # worker bus run was closed → /worker-events SSE would terminate
        assert run_id in bus._closed, "worker bus run not closed on cancellation"
        assert run_id in bus._seen or run_id in bus._buffers or bus._is_seen(run_id)


class TestWorkerToolSetRecursionGuard:
    """Construction-site guard: spawned workers must never inherit spawn tools."""

    @pytest.mark.asyncio
    async def test_worker_tool_set_excludes_spawn_tools(self, monkeypatch, tmp_path):
        from pathlib import Path

        from coworker.agents import build_workspace_tools
        from coworker.workspace import Workspace
        from coworker.workers import worker as worker_mod

        class FakeLLM:
            model_name = "fake-model"

        captured = {}

        class FakeWorkerAgent:
            def __init__(self, *a, **kw):
                captured["tools"] = [getattr(t, "name", str(t)) for t in kw.get("tools", [])]
                captured["depth"] = kw.get("depth")

            async def arun(self):
                return WorkerResult(content="ok", success=True)

        monkeypatch.setattr(worker_mod, "WorkerAgent", FakeWorkerAgent)

        ws = Workspace(Path(tmp_path))
        tools = build_workspace_tools(
            ws,
            use_worker_enabled=True,
            worker_llm=FakeLLM(),
            language="zh",
            max_concurrent=2,
        )
        use_worker_tool = next(t for t in tools if getattr(t, "name", "") == "use_worker")
        await use_worker_tool.ainvoke({"task": "test task"})

        # The worker must NOT be handed any spawn/delegation tool...
        for forbidden in ("use_worker", "use_workers", "delegate_task", "delegate_parallel",
                          "create_team_member", "create_team"):
            assert forbidden not in captured["tools"], f"{forbidden} leaked into worker tools"
        # ...and its depth is exactly one below the caller's engine backstop.
        assert captured["depth"] == 1
        # It still keeps the read/exec tool catalog.
        assert "read_file" in captured["tools"]
        assert "run_command" in captured["tools"]

    @pytest.mark.asyncio
    async def test_use_workers_fanout_respects_max_concurrent(self, monkeypatch, tmp_path):
        """use_workers spawns every sub-task, caps concurrency at max_concurrent,
        and aggregates all results into a numbered list."""
        from pathlib import Path

        from coworker.agents import build_workspace_tools
        from coworker.workspace import Workspace
        from coworker.workers import worker as worker_mod

        class FakeLLM:
            model_name = "fake-model"

        state = {"active": 0, "peak": 0, "runs": []}

        class FakeWorkerAgent:
            def __init__(self, *a, **kw):
                self.brief = kw.get("brief")
                state["runs"].append(self.brief.task if self.brief else "?")

            async def arun(self):
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                await asyncio.sleep(0.05)
                state["active"] -= 1
                return WorkerResult(content=f"RESULT:{self.brief.task[:8]}", success=True)

        monkeypatch.setattr(worker_mod, "WorkerAgent", FakeWorkerAgent)

        ws = Workspace(Path(tmp_path))
        tools = build_workspace_tools(
            ws,
            use_worker_enabled=True,
            worker_llm=FakeLLM(),
            language="zh",
            max_concurrent=2,
        )
        fanout = next(t for t in tools if getattr(t, "name", "") == "use_workers")
        tasks = [{"task": f"task {i}", "context": "", "expected_output": ""} for i in range(5)]
        output = await fanout.ainvoke({"tasks": tasks})

        assert len(state["runs"]) == 5, f"expected 5 worker runs, got {state['runs']}"
        assert state["peak"] <= 2, f"concurrency exceeded max_concurrent: {state['peak']}"
        assert "--- Worker 1:" in output and "--- Worker 5:" in output
        assert output.count("RESULT:") == 5, f"aggregation lost results: {output}"

    @pytest.mark.asyncio
    async def test_use_workers_empty_tasks(self, monkeypatch, tmp_path):
        from pathlib import Path

        from coworker.agents import build_workspace_tools
        from coworker.workspace import Workspace
        from coworker.workers import worker as worker_mod

        class FakeLLM:
            model_name = "fake-model"

        monkeypatch.setattr(worker_mod, "WorkerAgent", object)
        ws = Workspace(Path(tmp_path))
        tools = build_workspace_tools(
            ws, use_worker_enabled=True, worker_llm=FakeLLM(), language="zh", max_concurrent=2,
        )
        fanout = next(t for t in tools if getattr(t, "name", "") == "use_workers")
        output = await fanout.ainvoke({"tasks": []})
        assert "未提供任何子任务" in output

    @pytest.mark.asyncio
    async def test_use_worker_and_use_workers_share_semaphore(self, monkeypatch, tmp_path):
        """A batch of use_worker calls and use_workers calls share one semaphore,
        so the global concurrent-worker budget is never exceeded."""
        import asyncio
        from pathlib import Path

        from coworker.agents import build_workspace_tools
        from coworker.workspace import Workspace
        from coworker.workers import worker as worker_mod

        class FakeLLM:
            model_name = "fake-model"

        state = {"active": 0, "peak": 0}

        class FakeWorkerAgent:
            def __init__(self, *a, **kw):
                pass

            async def arun(self):
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                await asyncio.sleep(0.08)
                state["active"] -= 1
                return WorkerResult(content="ok", success=True)

        monkeypatch.setattr(worker_mod, "WorkerAgent", FakeWorkerAgent)

        ws = Workspace(Path(tmp_path))
        tools = build_workspace_tools(
            ws, use_worker_enabled=True, worker_llm=FakeLLM(), language="zh", max_concurrent=2,
        )
        uw = next(t for t in tools if getattr(t, "name", "") == "use_worker")
        uws = next(t for t in tools if getattr(t, "name", "") == "use_workers")

        # One use_workers fan-out (3 tasks) + one use_worker, launched together.
        async def run():
            await asyncio.gather(
                uws.ainvoke({"tasks": [{"task": f"a{i}"} for i in range(3)]}),
                uw.ainvoke({"task": "b"}),
            )

        await run()
        assert state["peak"] <= 2, f"shared budget exceeded: {state['peak']}"
        assert state["peak"] == 2, f"expected the budget to be fully used, got {state['peak']}"
