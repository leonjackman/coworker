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
    workflow, _ = parse_workflow("name: bad/name\ndescription: x\nsteps:\n  - id: a\n    kind: set\n")
    assert validate(workflow) != []
    # Non-English / spaced names are allowed.
    ok, _ = parse_workflow("name: 我的 流程\ndescription: x\nsteps:\n  - id: a\n    kind: set\n")
    assert validate(ok) == []


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


class AgenticEnv(FakeEnv):
    """Fake environment that advertises and performs agent takeover."""

    def __init__(self, verdict="VERDICT: DONE", **kwargs):
        super().__init__(**kwargs)
        self._verdict = verdict
        self.agentic_calls = []

    def supports_agentic(self):
        return True

    def agentic(self, prompt, step):
        self.agentic_calls.append((step.id, prompt))
        return {"output": f"handled by agent\n{self._verdict}"}


def test_assertion_vocabulary(tmp_path):
    from coworker.workflows.assertions import evaluate

    target = tmp_path / "out.txt"
    target.write_text("hello world", encoding="utf-8")
    ctx = {"inputs": {"n": 3}}
    assert evaluate(f"file_exists {target}", None, ctx)[0] is True
    assert evaluate("file_exists /nope/missing.txt", None, ctx)[0] is False
    assert evaluate(f"file_contains {target} hello", None, ctx)[0] is True
    assert evaluate(f"file_contains {target} goodbye", None, ctx)[0] is False
    assert evaluate("exit_code 0", {"return_code": 0}, ctx)[0] is True
    assert evaluate("exit_code 0", {"return_code": 1}, ctx)[0] is False
    assert evaluate("exists inputs.n", None, ctx)[0] is True
    assert evaluate("not_exists inputs.missing", None, ctx)[0] is True
    assert evaluate("regex inputs.n ^3$", None, ctx)[0] is True


def test_on_error_skip_policy(manager):
    flow = """name: skip-flow
description: skip a failing step
steps:
  - id: flaky
    kind: tool
    do: nope
    on_error:
      then: skip
  - id: after
    kind: set
    name: reached
    value: "yes"
"""
    manager.create(flow)
    env = FakeEnv(tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    result = manager.run("skip-flow", env=env)
    assert result["status"] == "ok"
    assert result["run"]["context"]["steps"]["flaky"]["skipped"] is True
    assert result["run"]["context"]["vars"]["reached"] == "yes"


def test_on_error_human_policy(manager):
    flow = """name: human-flow
description: escalate to human
steps:
  - id: flaky
    kind: tool
    do: nope
    on_error:
      then: human
"""
    manager.create(flow)
    env = FakeEnv(tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    result = manager.run("human-flow", env=env)
    assert result["status"] == "needs_human"
    assert result["run"]["pending_step"] == "flaky"


def test_on_error_goto_policy(manager):
    flow = """name: goto-flow
description: jump to a recovery step
steps:
  - id: start
    kind: tool
    do: nope
    on_error:
      then: "goto:recover"
  - id: skipped_middle
    kind: set
    name: wrong
    value: "no"
  - id: recover
    kind: set
    name: recovered
    value: "yes"
"""
    manager.create(flow)
    env = FakeEnv(tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    result = manager.run("goto-flow", env=env)
    assert result["status"] == "ok"
    assert result["run"]["context"]["vars"]["recovered"] == "yes"
    assert "skipped_middle" not in result["run"]["completed"]


def test_agent_takeover_recovers_failed_step(manager):
    flow = """name: takeover-flow
description: agent recovers a failing step
steps:
  - id: act
    kind: tool
    do: nope
    goal: publish the article
"""
    manager.create(flow)
    env = AgenticEnv(tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("flaky ui")))
    result = manager.run("takeover-flow", env=env)
    assert result["status"] == "ok"
    assert result["run"]["context"]["steps"]["act"]["takeover"] is True
    assert env.agentic_calls and env.agentic_calls[0][0] == "act"


def test_agent_takeover_blocked_escalates_to_human(manager):
    flow = """name: blocked-flow
description: agent cannot complete
steps:
  - id: act
    kind: tool
    do: nope
"""
    manager.create(flow)
    env = AgenticEnv(verdict="VERDICT: BLOCKED", tool=lambda n, a: (_ for _ in ()).throw(RuntimeError("x")))
    result = manager.run("blocked-flow", env=env)
    assert result["status"] == "needs_human"


def test_mode_agent_step(manager):
    flow = """name: agent-mode
description: explicit agent step
steps:
  - id: think
    kind: agentic
    mode: agent
    goal: write a summary
"""
    manager.create(flow)
    env = AgenticEnv()
    result = manager.run("agent-mode", env=env)
    assert result["status"] == "ok"
    assert result["run"]["context"]["steps"]["think"]["takeover"] is True


def test_success_contract_merged(manager):
    flow = """name: success-flow
description: success spec gates the step
steps:
  - id: act
    kind: tool
    do: produce
    success:
      - "contains NEEDLE"
"""
    manager.create(flow)
    env = FakeEnv(tool=lambda n, a: {"ok": True, "text": "no needle here"})
    result = manager.run("success-flow", env=env)
    # Default policy with no agent capability -> abort/fail.
    assert result["status"] == "failed"


def test_human_approval_resume(manager):
    flow = """name: resume-approval
description: approval then resume
steps:
  - id: gate
    kind: set
    approval: true
    params:
      name: approved
      value: "yes"
"""
    manager.create(flow)
    # No interactive human channel -> the run pauses.
    blocked = FakeEnv(human=lambda s, q, o: (_ for _ in ()).throw(NotImplementedError()))
    first = manager.run("resume-approval", env=blocked)
    assert first["status"] == "needs_human"
    assert first["run"]["pending_step"] == "gate"
    # Resume with an approval decision.
    resumed = manager.resume(first["run"]["run_id"], {"gate": True}, env=FakeEnv())
    assert resumed["status"] == "ok"
    assert resumed["run"]["context"]["vars"]["approved"] == "yes"


def test_evidence_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        from coworker.workflows.env import build_tool_environment
        from coworker.workflows.evidence import read_index
        from coworker.workspace import Workspace

        wsroot = Path(tmp) / "ws"
        wsroot.mkdir()
        env = build_tool_environment(workspace=Workspace(wsroot), tools=[], data_dir=Path(tmp))
        env.evidence("run123:stepA", {"hello": "world"})
        env.evidence("run123:stepB", "data:image/png;base64," + "aGVsbG8=")
        index = read_index(Path(tmp), "run123")
        kinds = {e["step_id"]: e["kind"] for e in index}
        assert kinds.get("stepA") == "json"
        assert kinds.get("stepB") == "image"


def test_llm_self_heal_plumbing():
    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        def invoke(self, messages):
            return _Resp('{"locator": {"role": "button", "name": "New"}}')

    with tempfile.TemporaryDirectory() as tmp:
        from coworker.workflows.env import build_tool_environment
        from coworker.workflows.model import Step
        from coworker.workspace import Workspace

        wsroot = Path(tmp) / "ws"
        wsroot.mkdir()
        env = build_tool_environment(workspace=Workspace(wsroot), tools=[], llm=_LLM(), data_dir=Path(tmp))
        repaired = env.self_heal(Step(id="s", kind="app", do="click"), "no element", {})
        assert repaired == {"locator": {"role": "button", "name": "New"}}


def test_agentic_step_plumbing(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        from coworker.agent import headless
        from coworker.workflows.env import build_tool_environment
        from coworker.workflows.model import Step
        from coworker.workspace import Workspace

        monkeypatch.setattr(
            headless, "run_agent_task_sync", lambda **kwargs: {"status": "ok", "output": "agent done"}
        )
        wsroot = Path(tmp) / "ws"
        wsroot.mkdir()
        env = build_tool_environment(workspace=Workspace(wsroot), tools=[], llm=object(), data_dir=Path(tmp))
        result = env.agentic("do the thing", Step(id="a", kind="agentic"))
        assert result["output"] == "agent done"


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


def test_store_multi_root_project_scope():
    from coworker.workflows import WorkflowStore

    with tempfile.TemporaryDirectory() as tmp:
        primary = Path(tmp) / "user" / "workflows"
        project = Path(tmp) / "proj" / ".coworker" / "workflows"
        project.mkdir(parents=True)
        (project / "proj-flow.yaml").write_text(
            """name: proj-flow
description: project-scoped workflow
steps:
  - id: a
    kind: set
    name: k
    value: v
""",
            encoding="utf-8",
        )
        store = WorkflowStore(primary, lambda: [project])
        # list sees the project workflow
        names = {w.name for w in store.list_active()}
        assert "proj-flow" in names
        # get resolves it from the project root
        wf = store.get("proj-flow")
        assert wf is not None
        # saving writes back into the project root, not the primary root
        from dataclasses import replace

        store.save(replace(wf, description="project-scoped workflow v2"))
        assert (project / "proj-flow.yaml").is_file()
        assert not (primary / "proj-flow.yaml").exists()
        # a brand-new workflow goes to the primary root
        store.save(
            parse_workflow(
                "name: user-flow\ndescription: user scope\nsteps:\n  - id: a\n    kind: set\n    name: k\n    value: v\n"
            )[0]
        )
        assert (primary / "user-flow.yaml").is_file()


@pytest.mark.parametrize(
    "name",
    [
        "测试流程",
        "日本語のフロー",
        "한국어 흐름",
        "Русский поток",
        "تدفق عربي",
        "हिंदी प्रवाह",
        "ขั้นตอนภาษาไทย",
        "Ελληνική ροή",
        "תהליך עברי",
        "Luồng tiếng Việt",
        "Flux français",
    ],
)
def test_non_ascii_workflow_name(manager, name):
    flow = f'''name: {name}
description: localized name
steps:
  - id: a
    kind: set
    params:
      name: k
      value: v
'''
    created = manager.create(flow)
    assert created["status"] == "ok", created
    assert manager.get(name) is not None
    assert name in {w["name"] for w in manager.list()}
    assert manager.run(name, env=FakeEnv())["status"] == "ok"
    assert manager.version_detail(name, 1)["status"] == "ok"
    assert manager.delete(name)["status"] == "ok"


def test_reject_name_with_path_separator(manager):
    from coworker.workflows import WorkflowValidationError

    flow = "name: bad/name\ndescription: x\nsteps:\n  - id: a\n    kind: set\n"
    with pytest.raises(WorkflowValidationError):
        manager.create(flow)


def test_next_chain_ordering(manager):
    flow = """name: chain-flow
description: explicit next ordering
steps:
  - id: s1
    kind: set
    next: s3
    params:
      name: a
      value: "1"
  - id: s2
    kind: set
    params:
      name: b
      value: "2"
  - id: s3
    kind: set
    next: s2
    params:
      name: c
      value: "3"
"""
    manager.create(flow)
    result = manager.run("chain-flow", env=FakeEnv())
    assert result["status"] == "ok"
    assert result["run"]["completed"] == ["s1", "s3", "s2"]


def test_renumber_steps_ids_and_refs():
    from coworker.workflows.parser import parse_workflow, renumber_steps

    text = """name: renum
description: x
steps:
  - id: first
    kind: tool
    do: web_search
  - id: second
    kind: command
    params:
      command:
        - echo
        - "{{steps.first}}"
  - id: third
    kind: set
    next: ''
"""
    workflow, _ = parse_workflow(text)
    steps = renumber_steps(workflow.steps)
    assert [s.id for s in steps] == ["id:1", "id:2", "id:3"]
    assert steps[1].params["command"][1] == "{{steps.id:1}}"


def test_when_comparison_expression(manager):
    flow = """name: cond-flow
description: branch on comparison
inputs:
  url:
    type: string
    default: ""
steps:
  - id: check
    kind: branch
    when: "{{inputs.url}} != ''"
    then:
      - id: inner
        kind: set
        params:
          name: took
          value: "yes"
"""
    manager.create(flow)
    empty = manager.run("cond-flow", env=FakeEnv())
    assert empty["status"] == "ok"
    assert "took" not in empty["run"]["context"].get("vars", {})
    filled = manager.run("cond-flow", {"url": "https://x"}, env=FakeEnv())
    assert filled["status"] == "ok"
    assert filled["run"]["context"]["vars"]["took"] == "yes"


def test_renumber_does_not_corrupt_short_ids():
    from coworker.workflows.parser import parse_workflow, renumber_steps

    # Ids that are substrings of the "{{steps." prefix must not be corrupted.
    text = """name: short
description: x
steps:
  - id: s
    kind: set
    next: st
    params:
      name: k
      value: "{{steps.s}}"
  - id: st
    kind: command
    params:
      command:
        - echo
        - "{{steps.s}}"
"""
    workflow, _ = parse_workflow(text)
    steps = renumber_steps(workflow.steps)
    assert [s.id for s in steps] == ["id:1", "id:2"]
    assert steps[0].next == "id:2"
    assert steps[0].params["value"] == "{{steps.id:1}}"
    assert steps[1].params["command"][1] == "{{steps.id:1}}"


def test_rollback_non_ascii_name(manager):
    flow = '''name: 回滚测试
description: x
steps:
  - id: a
    kind: set
    params:
      name: k
      value: v
'''
    manager.create(flow)
    # Create a second version so v1 is archived.
    manager.update("回滚测试", flow.replace("value: v", "value: v2"))
    assert manager.get("回滚测试")["version"] == 2
    rolled = manager.rollback("回滚测试", 1)
    assert rolled["status"] == "ok", rolled
    assert manager.get("回滚测试")["version"] == 3


def test_renumber_level_first_with_branches():
    from coworker.workflows.parser import parse_workflow, renumber_steps

    text = """name: branched
description: x
steps:
  - id: start
    kind: branch
    when: "{{inputs.go}}"
    next: finish
    then:
      - id: inner
        kind: set
        params:
          name: k
          value: v
  - id: finish
    kind: set
    params:
      name: k
      value: v
"""
    workflow, _ = parse_workflow(text)
    steps = renumber_steps(workflow.steps)
    assert [s.id for s in steps] == ["id:1", "id:2"]  # siblings first
    assert steps[0].next == "id:2"
    assert [c.id for c in steps[0].then] == ["id:3"]  # children after the level


def test_workflow_review_settings_default_off(tmp_path):
    from coworker.config import read_workflow_review_settings

    cfg = read_workflow_review_settings(tmp_path)
    assert cfg["enabled"] is False
    assert cfg["aggressiveness"] == "cautious"
    assert cfg["approval_required"] is True


def test_apply_agent_workflow_create_and_update(manager):
    steps = [{"kind": "set", "params": {"name": "k", "value": "v"}}]
    created = manager.apply_agent_workflow("create", "auto-flow", steps, description="d")
    assert created["status"] == "ok"
    assert manager.get("auto-flow") is not None
    assert manager.get("auto-flow")["version"] == 1
    updated = manager.apply_agent_workflow("update", "auto-flow", steps, description="d2")
    assert updated["status"] == "ok"
    assert manager.get("auto-flow")["version"] == 2


def test_run_workflow_review_stage_then_apply(manager):
    import asyncio
    import json

    from coworker.workflows.review import run_workflow_review

    verdict = {
        "action": "create",
        "name": "auto-reviewed",
        "description": "auto",
        "steps": [{"kind": "set", "params": {"name": "k", "value": "v"}}],
    }

    class _Resp:
        content = json.dumps(verdict)

    class _LLM:
        async def ainvoke(self, messages):
            return _Resp()

    parts = [{"type": "tool_start", "name": "run_command"}]
    staged = asyncio.run(
        run_workflow_review(_LLM(), manager, session_id="s1", messages=[], parts=parts)
    )
    assert staged.get("staged") is True
    assert any(d["name"] == "auto-reviewed" for d in manager.list_pending())

    applied = asyncio.run(
        run_workflow_review(
            _LLM(), manager, session_id="s1", messages=[], parts=parts, approval_required=False
        )
    )
    assert applied.get("applied") is True
    assert manager.get("auto-reviewed") is not None


def test_render_steps_structured(manager):
    result = manager.render_steps(
        {
            "name": "viz-flow",
            "description": "visual",
            "steps": [
                {"id": "open", "kind": "set", "params": {"name": "k", "value": "v"}},
                {"id": "check", "kind": "assert", "do": "equals vars.k v"},
            ],
        }
    )
    assert result["status"] == "ok"
    assert "viz-flow" in result["yaml"]
    assert len(result["workflow"]["steps"]) == 2


def test_feedback_revision_plumbing():
    import asyncio

    from coworker.workflows.feedback import run_workflow_revision

    class _Resp:
        content = "name: my-flow\ndescription: revised\nsteps:\n  - id: a\n    kind: set\n    name: k\n    value: v\n"

    class _LLM:
        async def ainvoke(self, messages):
            return _Resp()

    result = asyncio.run(run_workflow_revision(_LLM(), "name: my-flow\ndescription: old\n", "make it better"))
    assert result["status"] == "ok"
    assert "revised" in result["yaml"]


def test_prompt_block_lists_active(manager):
    assert manager.prompt_block() == ""
    manager.create(SIMPLE)
    block = manager.prompt_block()
    assert "hello-flow" in block
    assert "<available_workflows>" in block
