"""Step execution environment.

The executor is deliberately decoupled from *how* a step is actually performed:
it calls a :class:`StepEnvironment`, so the same workflow can run in tests with
a fake environment, in the agent runtime with real tools, or (later) in the
electron process. Adapters map step kinds onto these methods.
"""

from __future__ import annotations

import re
from typing import Any, Callable


class StepEnvironment:
    """Default environment: every capability raises until overridden."""

    def command(self, argv: list[str], cwd: str = "", timeout: int = 30) -> Any:
        raise NotImplementedError("command steps are not available in this environment")

    def tool(self, name: str, args: dict[str, Any]) -> Any:
        raise NotImplementedError("tool steps are not available in this environment")

    def browser(self, action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        raise NotImplementedError("browser steps are not available in this environment")

    def app(self, action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        raise NotImplementedError("app steps are not available in this environment")

    def action(self, kind: str, action: str, payload: dict[str, Any]) -> Any:
        """Run a native server-side action (http/file/transform/notify)."""
        raise NotImplementedError(f"{kind} steps are not available in this environment")

    def skill(self, name: str, inputs: dict[str, Any] | None = None, step: Any = None) -> Any:
        raise NotImplementedError("skill steps are not available in this environment")

    def skill_body(self, name: str) -> str | None:
        """Return a skill's instructions (used by the agentic skill handoff)."""
        raise NotImplementedError("skill steps are not available in this environment")

    def agentic(self, prompt: str, step: Any, autonomy: str | None = None) -> Any:
        raise NotImplementedError("agentic steps are not available in this environment")

    def supports_agentic(self) -> bool:
        """Whether this environment can hand a step to an agent (W23/P3)."""
        return False

    def resume_decision(self, step_id: str) -> Any | None:
        """The human decision recorded for a pending step on resume, if any.

        ``None`` = no decision; ``True`` = approved; ``False`` = rejected.
        """
        return None

    def human(self, step: Any, question: str, options: list[dict[str, str]] | None = None) -> Any:
        raise NotImplementedError("human steps are not available in this environment")

    def self_heal(self, step: Any, error: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Attempt an LLM/agentic repair of a failed step.

        Returns a mapping of replacement step fields (e.g. a new ``locator``) or
        ``None`` when no repair was found. Default: no self-healing.
        """
        return None

    def supports_resolve(self) -> bool:
        """Whether this environment can resolve an intent-only step's BINDING."""
        return False

    def resolve_binding(self, step: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve the BINDING (do/params/locator) for a step that only states its
        intent (goal). Returns replacement fields or ``None``. Default: unsupported."""
        return None

    def evidence(self, name: str, data: Any) -> None:
        """Persist a piece of evidence (screenshot/snapshot/output). Default: no-op."""
        return None


class CallbackEnvironment(StepEnvironment):
    """Environment composed from optional callables.

    Every handler is optional; when absent the base class raises, which the
    executor reports as an unavailable capability. This is the production
    bridge used by the agent runtime.
    """

    def __init__(
        self,
        *,
        command_fn: Callable[..., Any] | None = None,
        tool_fn: Callable[..., Any] | None = None,
        browser_fn: Callable[..., Any] | None = None,
        app_fn: Callable[..., Any] | None = None,
        action_fn: Callable[..., Any] | None = None,
        skill_fn: Callable[..., Any] | None = None,
        skill_body_fn: Callable[..., Any] | None = None,
        agentic_fn: Callable[..., Any] | None = None,
        human_fn: Callable[..., Any] | None = None,
        heal_fn: Callable[..., Any] | None = None,
        resolve_fn: Callable[..., Any] | None = None,
        evidence_fn: Callable[..., Any] | None = None,
    ):
        self._command_fn = command_fn
        self._tool_fn = tool_fn
        self._browser_fn = browser_fn
        self._app_fn = app_fn
        self._action_fn = action_fn
        self._skill_fn = skill_fn
        self._skill_body_fn = skill_body_fn
        self._agentic_fn = agentic_fn
        self._human_fn = human_fn
        self._heal_fn = heal_fn
        self._resolve_fn = resolve_fn
        self._evidence_fn = evidence_fn

    def command(self, argv: list[str], cwd: str = "", timeout: int = 30) -> Any:
        if self._command_fn is None:
            return super().command(argv, cwd, timeout)
        return self._command_fn(argv, cwd=cwd, timeout=timeout)

    def tool(self, name: str, args: dict[str, Any]) -> Any:
        if self._tool_fn is None:
            return super().tool(name, args)
        return self._tool_fn(name, args)

    def browser(self, action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        if self._browser_fn is None:
            return super().browser(action, payload, locator)
        return self._browser_fn(action, payload, locator)

    def app(self, action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        if self._app_fn is None:
            return super().app(action, payload, locator)
        return self._app_fn(action, payload, locator)

    def action(self, kind: str, action: str, payload: dict[str, Any]) -> Any:
        if self._action_fn is None:
            return super().action(kind, action, payload)
        return self._action_fn(kind, action, payload)

    def skill(self, name: str, inputs: dict[str, Any] | None = None, step: Any = None) -> Any:
        if self._skill_fn is None:
            return super().skill(name, inputs, step)
        return self._skill_fn(name, inputs, step)

    def skill_body(self, name: str) -> str | None:
        if self._skill_body_fn is None:
            return None
        return self._skill_body_fn(name)

    def agentic(self, prompt: str, step: Any, autonomy: str | None = None) -> Any:
        if self._agentic_fn is None:
            return super().agentic(prompt, step, autonomy)
        return self._agentic_fn(prompt, step, autonomy=autonomy)

    def supports_agentic(self) -> bool:
        return self._agentic_fn is not None

    def human(self, step: Any, question: str, options: list[dict[str, str]] | None = None) -> Any:
        if self._human_fn is None:
            return super().human(step, question, options)
        return self._human_fn(step, question, options)

    def self_heal(self, step: Any, error: str, context: dict[str, Any]) -> dict[str, Any] | None:
        if self._heal_fn is None:
            return None
        return self._heal_fn(step, error, context)

    def supports_resolve(self) -> bool:
        return self._resolve_fn is not None

    def resolve_binding(self, step: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        if self._resolve_fn is None:
            return None
        return self._resolve_fn(step, context)

    def evidence(self, name: str, data: Any) -> None:
        if self._evidence_fn is not None:
            self._evidence_fn(name, data)


class DecisionEnvironment(StepEnvironment):
    """Wrap a base environment, resolving human/approval steps from a decisions map.

    Used to resume a ``needs_human`` run: the executor re-enters at the pending
    step and this environment answers it (bool for approvals, str for answers)
    while delegating every other capability to the base environment.
    """

    def __init__(self, base: StepEnvironment, decisions: dict[str, Any]):
        self._base = base
        self._decisions = decisions or {}

    def command(self, argv, cwd="", timeout=30):
        return self._base.command(argv, cwd=cwd, timeout=timeout)

    def tool(self, name, args):
        return self._base.tool(name, args)

    def browser(self, action, payload, locator):
        return self._base.browser(action, payload, locator)

    def app(self, action, payload, locator):
        return self._base.app(action, payload, locator)

    def action(self, kind, action, payload):
        return self._base.action(kind, action, payload)

    def skill(self, name, inputs=None, step=None):
        return self._base.skill(name, inputs, step)

    def skill_body(self, name):
        return self._base.skill_body(name)

    def agentic(self, prompt, step, autonomy: str | None = None):
        # An explicit human approval on resume carries over to the agent turn:
        # run it autonomously so approval-gated tools (e.g. computer control) no
        # longer interrupt — that is exactly what "Approve" means here.
        if self._decisions.get(getattr(step, "id", "")) is True:
            autonomy = "autonomous"
        return self._base.agentic(prompt, step, autonomy)

    def resume_decision(self, step_id: str):
        return self._decisions.get(step_id)

    def supports_agentic(self) -> bool:
        return self._base.supports_agentic()

    def human(self, step, question, options=None):
        from .model import NeedsHuman

        step_id = getattr(step, "id", "")
        if step_id in self._decisions:
            return self._decisions[step_id]
        raise NeedsHuman(step_id, question, kind="question", retryable=False)

    def self_heal(self, step, error, context):
        return self._base.self_heal(step, error, context)

    def evidence(self, name, data):
        return self._base.evidence(name, data)


def build_tool_environment(
    *,
    workspace: Any,
    tools: list[Any],
    skill_manager: Any | None = None,
    audit_context: dict[str, Any] | None = None,
    provider_manager: Any | None = None,
    llm: Any | None = None,
    data_dir: Any | None = None,
    approval_store: Any | None = None,
    agent_autonomy: str = "autonomous",
) -> CallbackEnvironment:
    """Compose a StepEnvironment from a set of LangChain-style tools + workspace.

    This is the single runtime bridge shared by the agent ``workflow`` tool and
    the scheduler, so a workflow behaves identically however it is triggered.
    ``provider_manager``/``llm`` enable the ``agentic`` step and LLM self-heal;
    ``data_dir`` enables evidence persistence.

    ``agent_autonomy`` controls the agentic-step permission. Workflow runs are
    unattended, so it defaults to ``"autonomous"``: the agent's own tool-approval
    (computer control, external writes, MCP, ask_user, …) never turns a run into
    a ``needs_human`` gate. Only an explicit ``approval: true`` step or a
    ``human`` step pauses a workflow.
    """
    from .model import NeedsHuman

    tool_map: dict[str, Any] = {}
    for tool in tools or []:
        name = getattr(tool, "name", "")
        if name:
            tool_map[name] = tool

    def _invoke_tool(name: str, args: dict[str, Any]) -> Any:
        target = tool_map.get(name)
        if target is None:
            raise RuntimeError(f"tool not available: {name}")
        try:
            return target.invoke(args)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"tool '{name}' failed: {exc}") from exc

    def _command(argv: list[str], cwd: str = "", timeout: int = 30) -> Any:
        return workspace.run_command(
            list(argv),
            cwd=cwd,
            timeout_seconds=int(timeout or 30),
            audit_context=audit_context,
        )

    def _browser(action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        target = tool_map.get("browser")
        if target is None:
            raise RuntimeError("browser is not available")
        args: dict[str, Any] = {"action": action, **payload}
        _merge_locator(args, locator)
        # Fail-closed: a click without ANY target would silently click (0,0).
        if action == "click" and not (
            ("x" in args and "y" in args) or args.get("selector") or args.get("text")
        ):
            raise RuntimeError("browser click requires a selector, text, or coordinates (x, y)")
        return target.invoke({k: v for k, v in args.items() if v is not None})

    def _app(action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        script = tool_map.get("computer_script")
        comp = tool_map.get("computer")
        if action in ("script", "run_script"):
            code = str(payload.get("code") or payload.get("script") or "")
            # A workflow step needs the STRUCTURED result that satisfies the
            # declared outputs (`result`/`text`), not the agent-rendered blocks —
            # so talk to the bridge client directly when we can.
            if data_dir is not None:
                try:
                    from coworker.computer.bridge_client import ComputerClient

                    client = ComputerClient(data_dir)
                    if payload.get("reset"):
                        client.ax_script_reset()
                    res = client.ax_script(code, int(payload.get("timeout_ms") or 0))
                    if isinstance(res, dict):
                        if res.get("error_code"):
                            raise RuntimeError(str(res.get("error") or res.get("error_code")))
                        return {
                            "blocks": res.get("blocks") or [],
                            "result": res.get("result"),
                            "text": res.get("text") or "",
                        }
                except RuntimeError:
                    raise
                except Exception:  # noqa: BLE001 - fall back to the agent tool
                    pass
            if script is not None:
                args: dict[str, Any] = {"code": code}
                if payload.get("reset") is not None:
                    args["reset"] = bool(payload.get("reset"))
                return script.invoke(args)
            raise RuntimeError("computer scripting is not available")
        if action in ("drag", "clipboard", "file_dialog"):
            if script is None:
                raise RuntimeError("computer scripting is not available")
            return script.invoke({"code": _computer_script_for(action, payload)})
        if comp is None:
            raise RuntimeError("computer use is not available")
        if action == "focus_window":
            app = str(payload.get("app") or "")
            if not app:
                raise RuntimeError("focus_window requires an app name")
            return comp.invoke({"action": "launch_app", "app": app})
        args: dict[str, Any] = {"action": action, **payload}
        _merge_locator(args, locator)
        # Semantic locator: resolve role/name/text to a ref by observing the AX
        # tree, so an author can write atomic nodes WITHOUT a runtime ref.
        if action in _REF_ACTIONS and not args.get("ref"):
            ref = _resolve_semantic_ref(tool_map, locator)
            if ref:
                args["ref"] = ref
        # Fail-closed: ref-based actions must actually have a ref.
        if action in ("click_ref", "double_click_ref", "right_click_ref", "show") and not args.get("ref"):
            raise RuntimeError(f"computer {action} requires a ref (or a role/name locator)")
        return comp.invoke({k: v for k, v in args.items() if v is not None})

    def _skill_body(name: str) -> str | None:
        if skill_manager is None:
            return None
        loaded = skill_manager.read_body(name)
        return loaded[0] if loaded else None

    def _skill(name: str, inputs: dict[str, Any] | None = None, step: Any = None) -> Any:
        """Skill step = agentic handoff.

        A skill is instructions for the agent, not a deterministic program, so a
        workflow "uses" a skill by handing it (plus the step goal and inputs) to
        the agent to perform — which is what makes referencing skills meaningful.
        """
        body = _skill_body(name)
        if body is None:
            raise RuntimeError(f"skill not found: {name}")
        goal = getattr(step, "goal", "") or f"Perform the '{name}' skill."
        prompt = _skill_prompt(name, body, goal, inputs or {})
        return _agentic(prompt, step)

    def _human(step: Any, question: str, options: list[dict[str, str]] | None = None) -> Any:
        raise NeedsHuman(step.id, question, kind="question", retryable=False)

    def _agentic(prompt: str, step: Any, autonomy: str | None = None) -> Any:
        from coworker.agent.graph import build_workspace_tools
        from coworker.agent.headless import run_agent_task_sync, build_default_llm

        if llm is None and build_default_llm(provider_manager, data_dir) is None:
            raise RuntimeError("agentic step requires a configured provider")
        agent_tools = list(tools)
        try:
            from coworker.web import resolve_web_tools

            agent_tools.extend(resolve_web_tools(data_dir))
        except Exception:  # noqa: BLE001
            pass
        agent_tools.extend(
            build_workspace_tools(
                workspace,
                skill_manager=skill_manager,
                web_tools=[],
                readonly=False,
            )
        )
        result = run_agent_task_sync(
            prompt=prompt,
            workspace=workspace,
            tools=agent_tools,
            provider_manager=provider_manager,
            llm=llm,
            data_dir=data_dir,
            approval_store=approval_store,
            skill_manager=skill_manager,
            session_id=f"workflow-{getattr(step, 'id', 'step')}",
            autonomy=autonomy or agent_autonomy,
        )
        if result.get("status") != "ok":
            raise RuntimeError(result.get("error") or "agentic step failed")
        return {"agentic": True, "output": result.get("output", "")}

    def _heal(step: Any, error: str, context: dict[str, Any]) -> dict[str, Any] | None:
        from .repair import suggest_repair

        return suggest_repair(
            step,
            error,
            provider_manager=provider_manager,
            llm=llm,
            data_dir=data_dir,
        )

    def _resolve(step: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        from .capabilities import CapabilityRegistry
        from .repair import suggest_binding

        return suggest_binding(
            step,
            CapabilityRegistry.declared(),
            provider_manager=provider_manager,
            llm=llm,
            data_dir=data_dir,
        )

    def _evidence(name: str, data: Any) -> None:
        from .evidence import save_evidence

        save_evidence(data_dir, name, data)

    def _native_action(kind: str, action: str, payload: dict[str, Any]) -> Any:
        from .native import run_native

        return run_native(kind, action, payload)

    return CallbackEnvironment(
        command_fn=_command,
        tool_fn=_invoke_tool,
        browser_fn=_browser,
        app_fn=_app,
        action_fn=_native_action,
        skill_fn=_skill,
        skill_body_fn=_skill_body,
        human_fn=_human,
        agentic_fn=_agentic,
        heal_fn=_heal,
        # Binding resolution needs a model; only enable it when one is available.
        resolve_fn=_resolve if (llm is not None or provider_manager is not None) else None,
        evidence_fn=_evidence,
    )


SKILL_BODY_MAX_CHARS = 12000


def _skill_prompt(name: str, body: str, goal: str, inputs: dict[str, Any]) -> str:
    import json as _json

    clipped = body if len(body) <= SKILL_BODY_MAX_CHARS else body[:SKILL_BODY_MAX_CHARS] + "\n…[truncated]"
    try:
        inputs_text = _json.dumps(inputs, ensure_ascii=False)[:1500] if inputs else "{}"
    except (TypeError, ValueError):
        inputs_text = str(inputs)[:1500]
    return (
        f"A saved workflow is executing and this step uses the '{name}' skill.\n"
        f"STEP GOAL: {goal}\n"
        f"INPUTS: {inputs_text}\n\n"
        "Follow the skill instructions below to accomplish the goal: use any tools "
        "you need, verify the result, and end with a final line `VERDICT: DONE` "
        "(or `VERDICT: BLOCKED` if you cannot complete it).\n\n"
        f"--- SKILL: {name} ---\n{clipped}"
    )


def _computer_script_for(action: str, payload: dict[str, Any]) -> str:
    """Generate cw-automa JS for the intent-level computer actions."""
    import json as _json

    app = _json.dumps(str(payload.get("app") or ""))
    if action == "drag":
        return (
            "(async () => { const app = await cua.getApp(" + app + "); "
            f"return await app.drag([{int(payload.get('x1', 0))}, {int(payload.get('y1', 0))}], "
            f"[{int(payload.get('x2', 0))}, {int(payload.get('y2', 0))}]); }})()"
        )
    if action == "clipboard":
        op = str(payload.get("op") or "paste")
        if op == "copy":
            return (
                "(async () => { const app = await cua.getApp(" + app + "); "
                "return await app.pressKey('cmd+c'); })()"
            )
        text = _json.dumps(str(payload.get("text") or ""))
        return "(async () => { const app = await cua.getApp(" + app + "); return await app.paste(" + text + "); })()"
    if action == "file_dialog":
        path = _json.dumps(str(payload.get("path") or ""))
        return (
            "(async () => { const app = await cua.getApp(" + app + "); "
            "await app.pressKey('cmd+shift+g'); await app.settle(); "
            "await app.typeText(" + path + "); await app.pressKey('enter'); return { ok: true }; })()"
        )
    raise RuntimeError(f"no script template for computer action '{action}'")


_REF_ACTIONS = {"click_ref", "double_click_ref", "right_click_ref", "show", "type_into"}
_REF_TOKEN = re.compile(r"\[?(ax[a-z_]+):([^\]\n#]*)#(\d+)\]?")


def _semantic_ref_from_text(text: str, role: str, name: str) -> str | None:
    """Find an AX ref like ``axbutton:导出#1`` by role and/or label."""
    role_norm = ""
    if role:
        role_l = role.strip().lower()
        role_norm = role_l if role_l.startswith("ax") else f"ax{role_l}"
    name_l = (name or "").strip().lower()
    for m in _REF_TOKEN.finditer(text or ""):
        token_role, label, index = m.group(1), m.group(2), m.group(3)
        if role_norm and token_role != role_norm:
            continue
        if name_l and name_l not in label.lower():
            continue
        return f"{token_role}:{label}#{index}"
    return None


def _resolve_semantic_ref(tool_map: dict[str, Any], locator: dict[str, Any] | None) -> str | None:
    """Observe the AX tree and resolve a ``{role, name/text}`` locator to a ref."""
    if not locator:
        return None
    role = str(locator.get("role") or "")
    name = str(locator.get("name") or locator.get("text") or locator.get("label") or "")
    if not role and not name:
        return None
    obs = tool_map.get("computer_observe")
    if obs is None:
        return None
    try:
        snapshot = obs.invoke({"action": "snapshot", "depth": 6})
    except Exception:  # noqa: BLE001 - observation failure -> caller fails closed
        return None
    text = snapshot if isinstance(snapshot, str) else str(snapshot)
    return _semantic_ref_from_text(text, role, name)


def _merge_locator(args: dict[str, Any], locator: dict[str, Any] | None) -> None:
    if not locator:
        return
    if "selector" in locator:
        args.setdefault("selector", locator["selector"])
    if "ref" in locator:
        args.setdefault("ref", locator["ref"])
    if "text" in locator:
        args.setdefault("text", locator["text"])
    if "exact" in locator:
        args.setdefault("exact", locator["exact"])
    if "coords" in locator:
        try:
            x, y = locator["coords"]
            args.setdefault("x", x)
            args.setdefault("y", y)
        except Exception:  # noqa: BLE001
            pass
