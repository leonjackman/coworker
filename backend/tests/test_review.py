"""Workflow review: grounded, validated, self-repairing authoring."""

from __future__ import annotations

import asyncio
import json

from coworker.workflows import WorkflowManager
from coworker.workflows.review import run_workflow_review


class _Resp:
    def __init__(self, content):
        self.content = content


class _SeqLLM:
    """Returns queued responses in order (one per ainvoke)."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _Resp(json.dumps(self._payloads.pop(0)))


def _parts():
    return [{"type": "tool_start", "name": "run_command"}]


def test_invalid_proposal_is_not_staged(tmp_path):
    manager = WorkflowManager(tmp_path)
    verdict = {
        "action": "create",
        "name": "bad-one",
        "description": "d",
        "steps": [{"kind": "tool", "params": {"seconds": 10}}],  # no do
    }
    llm = _SeqLLM([verdict, verdict])  # main + repair both invalid
    result = asyncio.run(run_workflow_review(llm, manager, session_id="s", messages=[], parts=_parts()))
    assert result.get("staged") is not True
    assert manager.list_pending() == []


def test_valid_proposal_stages(tmp_path):
    manager = WorkflowManager(tmp_path)
    verdict = {
        "action": "create",
        "name": "good-one",
        "description": "d",
        "steps": [{"kind": "set", "params": {"name": "k", "value": "v"}}],
    }
    llm = _SeqLLM([verdict])
    result = asyncio.run(run_workflow_review(llm, manager, session_id="s", messages=[], parts=_parts()))
    assert result.get("staged") is True
    assert any(d["name"] == "good-one" for d in manager.list_pending())


def test_repair_round_recovers(tmp_path):
    manager = WorkflowManager(tmp_path)
    bad = {"action": "create", "name": "fix-me", "description": "d", "steps": [{"kind": "tool"}]}
    good = {"action": "create", "name": "fix-me", "description": "d", "steps": [{"kind": "set", "params": {"name": "k", "value": "v"}}]}
    llm = _SeqLLM([bad, good])
    result = asyncio.run(run_workflow_review(llm, manager, session_id="s", messages=[], parts=_parts()))
    assert result.get("staged") is True
    assert llm.calls == 2
