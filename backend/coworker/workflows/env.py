"""Step execution environment.

The executor is deliberately decoupled from *how* a step is actually performed:
it calls a :class:`StepEnvironment`, so the same workflow can run in tests with
a fake environment, in the agent runtime with real tools, or (later) in the
electron process. Adapters map step kinds onto these methods.
"""

from __future__ import annotations

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

    def skill(self, name: str) -> Any:
        raise NotImplementedError("skill steps are not available in this environment")

    def agentic(self, prompt: str, step: Any) -> Any:
        raise NotImplementedError("agentic steps are not available in this environment")

    def supports_agentic(self) -> bool:
        """Whether this environment can hand a step to an agent (W23/P3)."""
        return False

    def human(self, step: Any, question: str, options: list[dict[str, str]] | None = None) -> Any:
        raise NotImplementedError("human steps are not available in this environment")

    def self_heal(self, step: Any, error: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Attempt an LLM/agentic repair of a failed step.

        Returns a mapping of replacement step fields (e.g. a new ``locator``) or
        ``None`` when no repair was found. Default: no self-healing.
        """
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
        skill_fn: Callable[..., Any] | None = None,
        agentic_fn: Callable[..., Any] | None = None,
        human_fn: Callable[..., Any] | None = None,
        heal_fn: Callable[..., Any] | None = None,
        evidence_fn: Callable[..., Any] | None = None,
    ):
        self._command_fn = command_fn
        self._tool_fn = tool_fn
        self._browser_fn = browser_fn
        self._app_fn = app_fn
        self._skill_fn = skill_fn
        self._agentic_fn = agentic_fn
        self._human_fn = human_fn
        self._heal_fn = heal_fn
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

    def skill(self, name: str) -> Any:
        if self._skill_fn is None:
            return super().skill(name)
        return self._skill_fn(name)

    def agentic(self, prompt: str, step: Any) -> Any:
        if self._agentic_fn is None:
            return super().agentic(prompt, step)
        return self._agentic_fn(prompt, step)

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

    def skill(self, name):
        return self._base.skill(name)

    def agentic(self, prompt, step):
        return self._base.agentic(prompt, step)

    def supports_agentic(self) -> bool:
        return self._base.supports_agentic()

    def human(self, step, question, options=None):
        from .model import NeedsHuman

        step_id = getattr(step, "id", "")
        if step_id in self._decisions:
            return self._decisions[step_id]
        raise NeedsHuman(step_id, question)

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
) -> CallbackEnvironment:
    """Compose a StepEnvironment from a set of LangChain-style tools + workspace.

    This is the single runtime bridge shared by the agent ``workflow`` tool and
    the scheduler, so a workflow behaves identically however it is triggered.
    ``provider_manager``/``llm`` enable the ``agentic`` step and LLM self-heal;
    ``data_dir`` enables evidence persistence.
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
        return target.invoke({k: v for k, v in args.items() if v is not None})

    def _app(action: str, payload: dict[str, Any], locator: dict[str, Any] | None) -> Any:
        script = tool_map.get("computer_script")
        comp = tool_map.get("computer")
        if action in ("script", "run_script") and script is not None:
            return script.invoke({"code": payload.get("code") or payload.get("script") or ""})
        if comp is None:
            raise RuntimeError("computer use is not available")
        args: dict[str, Any] = {"action": action, **payload}
        _merge_locator(args, locator)
        return comp.invoke({k: v for k, v in args.items() if v is not None})

    def _skill(name: str) -> Any:
        if skill_manager is None:
            raise RuntimeError("skill system unavailable")
        loaded = skill_manager.read_body(name)
        if loaded is None:
            raise RuntimeError(f"skill not found: {name}")
        return {"skill": name, "body": loaded[0]}

    def _human(step: Any, question: str, options: list[dict[str, str]] | None = None) -> Any:
        raise NeedsHuman(step.id, question)

    def _agentic(prompt: str, step: Any) -> Any:
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

    def _evidence(name: str, data: Any) -> None:
        from .evidence import save_evidence

        save_evidence(data_dir, name, data)

    return CallbackEnvironment(
        command_fn=_command,
        tool_fn=_invoke_tool,
        browser_fn=_browser,
        app_fn=_app,
        skill_fn=_skill,
        human_fn=_human,
        agentic_fn=_agentic,
        heal_fn=_heal,
        evidence_fn=_evidence,
    )


def _merge_locator(args: dict[str, Any], locator: dict[str, Any] | None) -> None:
    if not locator:
        return
    if "selector" in locator:
        args.setdefault("selector", locator["selector"])
    if "ref" in locator:
        args.setdefault("ref", locator["ref"])
    if "coords" in locator:
        try:
            x, y = locator["coords"]
            args.setdefault("x", x)
            args.setdefault("y", y)
        except Exception:  # noqa: BLE001
            pass
