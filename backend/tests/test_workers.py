"""Tests for the WorkerAgent module."""

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
        for forbidden in ("use_worker", "delegate_task", "delegate_parallel",
                          "create_team_member", "create_team"):
            assert forbidden not in captured["tools"], f"{forbidden} leaked into worker tools"
        # ...and its depth is exactly one below the caller's engine backstop.
        assert captured["depth"] == 1
        # It still keeps the read/exec tool catalog.
        assert "read_file" in captured["tools"]
        assert "run_command" in captured["tools"]
