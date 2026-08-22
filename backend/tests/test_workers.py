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
