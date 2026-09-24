"""Tests for the Workflow capability (P0/P1): parser, executor, store, manager."""

import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker.workflows import (  # noqa: E402
    CallbackEnvironment,
    StepEnvironment,
    WorkflowManager,
    parse_workflow,
    render_workflow,
    validate,
)
from coworker.workflows.templating import resolve_string  # noqa: E402


class FakeEnv(StepEnvironment):
    def __init__(self, *, command=None, tool=None, browser=None, app=None, skill=None, human=None, heal=None):
        self._command = command
        self._tool = tool
        self._browser = browser
        self._app = app
        self._skill = skill
        self._human = human
        self._heal = heal
        self.calls = []

    def command(self, argv, cwd="", timeout=30):
        self.calls.append(("command", list(argv)))
        if self._command is None:
            return {"ok": True}
        return self._command(argv, cwd, timeout)

    def tool(self, name, args):
        self.calls.append(("tool", name, args))
        if self._tool is None:
            return {"ok": True}
        return self._tool(name, args)

    def browser(self, action, payload, locator):
        self.calls.append(("browser", action, payload, locator))
        if self._browser is None:
            return {"ok": True}
        return self._browser(action, payload, locator)

    def app(self, action, payload, locator):
        self.calls.append(("app", action, payload, locator))
        if self._app is None:
            return {"ok": True}
        return self._app(action, payload, locator)

    def skill(self, name):
        self.calls.append(("skill", name))
        if self._skill is None:
            return {"ok": True}
        return self._skill(name)

    def human(self, step, question, options=None):
        self.calls.append(("human", step.id, question))
        if self._human is None:
            return True
        return self._human(step, question, options)

    def self_heal(self, step, error, context):
        if self._heal is None:
            return None
        return self._heal(step, error, context)


@pytest.fixture()
def manager():
    with tempfile.TemporaryDirectory() as tmp:
        yield WorkflowManager(Path(tmp))


SIMPLE = """---
name: hello-flow
description: A trivial test workflow
version: 1
inputs:
  who: world
steps:
  - id: greet
    kind: set
    name: greeting
    value: "hello {{inputs.who}}"
  - id: check
    kind: assert
    do: "equals vars.greeting hello coworker"
"""


def test_parse_and_validate():
    workflow, diagnostics = parse_workflow(SIMPLE)
    assert workflow is not None
    assert workflow.name == "hello-flow"
    assert validate(workflow) == []
    assert workflow.input_spec("who").default == "world"


def test_parse_rejects_bad_name():
    workflow, _ = parse_workflow("name: Bad Name\ndescription: x\nsteps:\n  - id: a\n    kind: set\n")
    assert validate(workflow) != []


def test_template_resolution():
    ctx = {"inputs": {"who": "world"}, "steps": {"a": {"x": 1}}}
    assert resolve_string("hi {{inputs.who}}", ctx) == "hi world"
    assert resolve_string("{{inputs.who}}", ctx) == "world"
    assert resolve_string("{{steps.a.x}}", ctx) == 1


def test_render_roundtrip():
    workflow, _ = parse_workflow(SIMPLE)
    text = render_workflow(workflow)
    again, _ = parse_workflow(text)
    assert again.name == workflow.name
    assert len(again.steps) == len(workflow.steps)


def test_executor_happy_path(manager):
    manager.create(SIMPLE)
    result = manager.run("hello-flow", {"who": "coworker"}, env=FakeEnv())
    assert result["status"] == "ok"
    run = result["run"]
    assert run["context"]["vars"]["greeting"] == "hello coworker"
    assert "greet" in run["completed"]


def test_executor_command_step(manager):
    flow = """name: cmd-flow
description: run a command
steps:
  - id: run
    kind: command
    do: echo hi
    post: ["ok"]
"""
    manager.create(flow)
    env = FakeEnv(command=lambda argv, cwd, timeout: {"ok": True, "argv": argv})
    result = manager.run("cmd-flow", env=env)
    assert result["status"] == "ok"
    assert env.calls[0] == ("command", ["echo", "hi"])


def test_executor_retry_then_success(manager):
    flow = """name: retry-flow
description: retry then succeed
steps:
  - id: flaky
    kind: command
    do: thing
    on_error:
      retry: 3
"""
    manager.create(flow)
    state = {"n": 0}

    def _cmd(argv, cwd, timeout):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    result = manager.run("retry-flow", env=FakeEnv(command=_cmd))
    assert result["status"] == "ok"
    assert state["n"] == 3


def test_executor_self_heal_and_patch(manager):
    flow = """name: heal-flow
description: heal a drifted locator
steps:
  - id: tap
    kind: app
    do: click_ref
    locator:
      role: button
      name: Old
"""
    manager.create(flow)

    def _app(action, payload, locator):
        if locator and locator.get("name") == "Real":
            return {"ok": True}
        raise RuntimeError("no AX element for ref")

    def _heal(step, error, context):
        return {"locator": {"role": "button", "name": "Real"}}

    result = manager.run("heal-flow", env=FakeEnv(app=_app, heal=_heal))
    assert result["status"] == "ok"
    # Version bumped and stored locator rewritten.
    patched = manager.get("heal-flow")
    assert patched["version"] == 2
    assert patched["steps"][0]["locator"]["name"] == "Real"


def test_executor_locator_fallback_ladder(manager):
    flow = """name: ladder-flow
description: fall back to a secondary locator
steps:
  - id: tap
    kind: browser
    do: click
    locator:
      selector: "#old"
      fallback:
        - selector: "#new"
"""
    manager.create(flow)

    def _browser(action, payload, locator):
        if locator and locator.get("selector") == "#new":
            return {"ok": True}
        raise RuntimeError("no element for selector")

    result = manager.run("ladder-flow", env=FakeEnv(browser=_browser))
    assert result["status"] == "ok"
    patched = manager.get("ladder-flow")
    assert patched["version"] == 2
    assert patched["steps"][0]["locator"]["selector"] == "#new"


def test_executor_assertion_failure(manager):
    flow = """name: fail-flow
description: postcondition fails
steps:
  - id: act
    kind: tool
    do: noop
    post: ["contains expected-token"]
"""
    manager.create(flow)
    result = manager.run("fail-flow", env=FakeEnv(tool=lambda n, a: {"ok": True}))
    assert result["status"] == "failed"
    assert "expected-token" in result["run"]["error"]


def test_executor_branch_and_loop(manager):
    flow = """name: control-flow
description: branch and loop
inputs:
  take: true
  items: [a, b, c]
steps:
  - id: decide
    kind: branch
    when: "{{inputs.take}}"
    then:
      - id: yes
        kind: set
        name: taken
        value: "yes"
    else:
      - id: no
        kind: set
        name: taken
        value: "no"
  - id: each
    kind: loop
    foreach: "{{inputs.items}}"
    body:
      - id: touch
        kind: tool
        do: echo
  - id: verify
    kind: assert
    do: "equals vars.taken yes"
"""
    manager.create(flow)
    env = FakeEnv()
    result = manager.run("control-flow", env=env)
    assert result["status"] == "ok"
    assert result["run"]["context"]["vars"]["taken"] == "yes"
    assert sum(1 for c in env.calls if c[0] == "tool") == 3


def test_checkpoint_resume(manager):
    flow = """name: resume-flow
description: resume after interruption
steps:
  - id: a
    kind: set
    name: x
    value: "1"
  - id: b
    kind: tool
    do: later
"""
    manager.create(flow)
    run_id = "run_resume_test"
    # First run fails at b; then resume succeeds.
    env = FakeEnv(tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    result = manager.run("resume-flow", run_id=run_id, env=env)
    assert result["status"] == "failed"
    # Simulate fixing the environment and resuming.
    result2 = manager.run("resume-flow", run_id=run_id, resume=True, env=FakeEnv())
    assert result2["status"] == "ok"
    assert "a" in result2["run"]["completed"]
    assert "b" in result2["run"]["completed"]


def test_human_approval_denied(manager):
    flow = """name: gate-flow
description: requires approval
steps:
  - id: publish
    kind: tool
    do: publish
    approval: true
"""
    manager.create(flow)
    result = manager.run("gate-flow", env=FakeEnv(human=lambda s, q, o: False))
    assert result["status"] == "needs_human"


def test_draft_lifecycle(manager):
    draft = """name: agent-made
description: generated by the agent
provenance:
  action: create
  sources: ["session:abc"]
steps:
  - id: a
    kind: set
    name: k
    value: v
"""
    staged = manager.stage_draft("agent-made", draft, sources=["session:abc"])
    assert staged["status"] == "ok"
    pending = manager.list_pending()
    assert any(d["name"] == "agent-made" for d in pending)
    approved = manager.approve_pending("agent-made")
    assert approved["status"] == "ok"
    assert manager.get("agent-made")["status"] == "active"
    assert manager.list_pending() == []


def test_rollback_and_export(manager):
    manager.create(SIMPLE)
    first = manager.get("hello-flow")
    # Change the description to force version 2.
    updated = manager.update("hello-flow", SIMPLE.replace("A trivial test workflow", "Changed"))
    assert updated["status"] == "ok"
    assert manager.get("hello-flow")["version"] == 2
    rolled = manager.rollback("hello-flow", 1)
    assert rolled["status"] == "ok"
    assert manager.get("hello-flow")["version"] == 3
    assert "A trivial test workflow" in manager.export("hello-flow")["yaml"]


def test_prompt_block_lists_active(manager):
    assert manager.prompt_block() == ""
    manager.create(SIMPLE)
    block = manager.prompt_block()
    assert "hello-flow" in block
    assert "<available_workflows>" in block
