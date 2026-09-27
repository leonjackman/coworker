"""skill steps = agentic handoff (not a no-op body load)."""

from __future__ import annotations

import pytest

from coworker.workflows.env import _skill_prompt, build_tool_environment


class _SkillManager:
    def read_body(self, name):
        if name == "my-skill":
            return ("Follow these steps: do the thing.", "{}")
        return None


class _Step:
    id = "s1"
    goal = "export the data"


def test_skill_prompt_includes_goal_and_body():
    prompt = _skill_prompt("my-skill", "body text", "do X", {"a": 1})
    assert "my-skill" in prompt
    assert "do X" in prompt
    assert "body text" in prompt
    assert "VERDICT" in prompt


def test_skill_step_routes_to_agentic_not_body_load():
    env = build_tool_environment(workspace=None, tools=[], skill_manager=_SkillManager())
    # A missing skill fails clearly...
    with pytest.raises(RuntimeError):
        env.skill("missing", {}, _Step())
    # ...an existing skill is handed to the AGENT (no provider configured here →
    # the agentic handoff surfaces a provider error, proving it did not just
    # return the body).
    with pytest.raises(RuntimeError, match="provider"):
        env.skill("my-skill", {}, _Step())


def test_skill_body_accessor():
    env = build_tool_environment(workspace=None, tools=[], skill_manager=_SkillManager())
    assert env.skill_body("my-skill").startswith("Follow these steps")
    assert env.skill_body("missing") is None
