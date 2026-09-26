"""Deterministic workflow executor (W05–W10).

Executes a :class:`Workflow` step by step against a :class:`StepEnvironment`:

* **W05/W06/W07** — sequence + control flow (branch/loop/parallel/subworkflow)
* **W08** — per-step retry/timeout/on_error policy
* **W09/W10** — checkpoint after each step; resume skips completed steps
* **W14** — pre/post assertions gate every action
* **W15/W16** — on failure, self-heal (patch + retry), else fail with evidence

No LLM is consulted on the happy path.
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from coworker.logger import get_logger

from .assertions import evaluate, evaluate_all
from .env import StepEnvironment
from .events import RunEventEmitter
from .model import (
    GotoStep,
    NeedsHuman,
    Run,
    SkippedStep,
    Step,
    StepFailed,
    Workflow,
)
from .templating import TemplateError, resolve, resolve_bool

logger = get_logger(__name__)

MAX_TOTAL_STEPS = 5000
MAX_LOOP_ITERATIONS = 1000
RESULT_PREVIEW_CHARS = 600

PatchCallback = Callable[[str, str, dict[str, Any]], None]


class WorkflowExecutor:
    def __init__(
        self,
        store: Any,
        *,
        secrets: Callable[[str], str | None] | None = None,
        listener: Callable[[Any], None] | None = None,
    ):
        self.store = store
        self.secrets = secrets
        self.listener = listener

    # ── public API ──────────────────────────────────────────────────────

    def run(
        self,
        workflow: Workflow,
        inputs: dict[str, Any] | None = None,
        *,
        env: StepEnvironment | None = None,
        run_id: str | None = None,
        resume: bool = False,
        trigger: str = "manual",
        on_patch: PatchCallback | None = None,
    ) -> Run:
        env = env or StepEnvironment()
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        emitter = RunEventEmitter(run_id, self.store, self.listener)

        if resume:
            run = self.store.load_run(run_id)
            if run is None:
                raise ValueError(f"no run to resume: {run_id}")
        else:
            context = self._build_context(workflow, inputs or {})
            run = Run(
                run_id=run_id,
                workflow=workflow.name,
                status="running",
                context=context,
                started_at=_now(),
                trigger=trigger,
            )

        run.status = "running"
        run.pending_step = ""
        emitter.emit(
            "run_start",
            status="running",
            data={"workflow": workflow.name, "resume": resume, "trigger": trigger},
        )
        try:
            from .fingerprint import current_fingerprint

            current_fp = current_fingerprint()
            if workflow.fingerprint and workflow.fingerprint != current_fp:
                emitter.emit(
                    "drift",
                    status="warning",
                    message="environment fingerprint changed since this workflow was saved",
                    data={"stored": workflow.fingerprint, "current": current_fp},
                )
        except Exception:  # noqa: BLE001 - fingerprinting is advisory only
            pass
        executor_state = _State(
            emitter=emitter, store=self.store, env=env, on_patch=on_patch, workflow=workflow
        )
        try:
            self._exec_steps(workflow, workflow.steps, run, executor_state)
            run.outputs = self._resolve_outputs(workflow, run.context)
            run.status = "ok"
            run.pending_step = ""
        except NeedsHuman as exc:
            run.status = "needs_human"
            run.error = str(exc)
            run.pending_step = exc.step_id
            emitter.emit(
                "needs_human",
                step_id=exc.step_id,
                status="needs_human",
                message=str(exc),
                data={"pending_step": exc.step_id},
            )
        except StepFailed as exc:
            run.status = "failed"
            run.error = str(exc)
            emitter.emit("error", step_id=exc.step_id, status="failed", message=exc.message)
        except Exception as exc:  # noqa: BLE001 - a run must never crash the caller
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            emitter.emit("error", status="failed", message=run.error)
            logger.warning("workflow %s run %s crashed: %s", workflow.name, run_id, exc)

        run.ended_at = _now()
        try:
            self.store.save_run(run)
        except Exception:  # noqa: BLE001
            pass
        emitter.emit("run_end", status=run.status, message=run.error)
        return run

    # ── context ─────────────────────────────────────────────────────────

    def _build_context(self, workflow: Workflow, inputs: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        errors: list[str] = []
        for spec in workflow.inputs:
            if spec.name in inputs and inputs[spec.name] is not None:
                resolved[spec.name] = inputs[spec.name]
            elif spec.default is not None:
                resolved[spec.name] = spec.default
            elif spec.required:
                errors.append(spec.name)
        if errors:
            raise StepFailed("inputs", f"missing required inputs: {', '.join(errors)}")
        # Allow undeclared inputs to pass through (handy for ad-hoc runs).
        for key, value in inputs.items():
            resolved.setdefault(key, value)
        return {"inputs": resolved, "steps": {}, "vars": {}}

    def _resolve_outputs(self, workflow: Workflow, context: dict[str, Any]) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        for name, ref in workflow.outputs.items():
            try:
                outputs[name] = resolve(ref, context, self.secrets)
            except TemplateError:
                continue
        return outputs

    # ── step walking ────────────────────────────────────────────────────

    def _exec_steps(
        self,
        workflow: Workflow,
        steps: list[Step],
        run: Run,
        state: "_State",
    ) -> None:
        steps = _order_steps(steps)
        index = 0
        while index < len(steps):
            step = steps[index]
            if step.id in run.completed:
                index += 1
                continue
            state.guard()
            if step.when:
                try:
                    if not resolve_bool(step.when, run.context, self.secrets):
                        state.emitter.emit("step_end", step_id=step.id, status="skipped")
                        index += 1
                        continue
                except TemplateError as exc:
                    raise StepFailed(step.id, f"bad when-condition: {exc}") from exc

            try:
                if step.foreach:
                    self._exec_loop_body(workflow, step, run, state)
                else:
                    result = self._exec_step(workflow, step, run, state)
                    if isinstance(result, SkippedStep):
                        result = result.to_dict()
                    run.context.setdefault("steps", {})[step.bind] = result
            except GotoStep as jump:
                target = next((i for i, s in enumerate(steps) if s.id == jump.target), None)
                if target is None:
                    raise StepFailed(step.id, f"goto target not found: {jump.target}") from jump
                state.emitter.emit(
                    "step_end", step_id=step.id, status="goto", message=f"goto {jump.target}"
                )
                if jump.target in run.completed:
                    run.completed.remove(jump.target)
                index = target
                continue
            run.completed.append(step.id)
            self._checkpoint(run)
            index += 1

    def _exec_loop_body(self, workflow: Workflow, step: Step, run: Run, state: "_State") -> None:
        try:
            items = resolve(step.foreach, run.context, self.secrets)
        except TemplateError as exc:
            raise StepFailed(step.id, f"bad foreach: {exc}") from exc
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            items = [items] if items not in (None, "") else []
        if len(items) > MAX_LOOP_ITERATIONS:
            raise StepFailed(step.id, f"loop exceeds {MAX_LOOP_ITERATIONS} iterations")
        collected: list[Any] = []
        for index, item in enumerate(items):
            run.context["loop"] = {"item": item, "index": index}
            for child in step.body:
                if child.id in run.completed:
                    continue
                state.guard()
                result = self._exec_step(workflow, child, run, state)
                run.context.setdefault("steps", {})[f"{step.bind}.{index}.{child.bind}"] = result
                collected.append({"index": index, "step": child.id, "result": result})
            self._checkpoint(run)
        run.context.setdefault("steps", {})[step.bind] = {
            "iterations": len(items),
            "results": collected,
        }
        run.context.pop("loop", None)

    # ── single step ─────────────────────────────────────────────────────

    def _exec_step(self, workflow: Workflow, step: Step, run: Run, state: "_State") -> Any:
        state.emitter.emit("step_start", step_id=step.id, status="running", data={"kind": step.kind})

        # Pre-conditions can gate an action (evaluated against the context).
        if step.pre:
            ok, message = evaluate_all(step.pre, run.context, run.context)
            if not ok:
                raise StepFailed(step.id, message)

        if step.approval:
            gate = self._request_approval(step, run, state)
            if not gate:
                raise NeedsHuman(step.id, f"approval denied for '{step.id}'")

        specs = self._success_specs(step)

        # mode=agent: skip the deterministic path entirely and let the agent do
        # the step (it must self-assess; failure escalates to a human gate).
        if step.mode == "agent":
            taken = self._takeover(step, "", run, state)
            if taken is None:
                raise NeedsHuman(step.id, f"agent step '{step.id}' could not complete")
            self._emit_success(step, run, state, taken)
            return taken

        attempts = int(step.on_error.get("retry", 0) or 0) + 1
        last_error = ""
        patched_step = step
        result: Any = None
        for attempt in range(attempts):
            try:
                result = self._dispatch(workflow, patched_step, run, state)
                break
            except (NeedsHuman, GotoStep):
                # Control-flow signals are never treated as failures.
                raise
            except StepFailed as exc:
                # Semantic/verification failure → recovery (policy engine).
                result = self._recover(workflow, step, exc.message, run, state, patched_step)
                break
            except TemplateError as exc:
                raise StepFailed(step.id, f"template error: {exc}") from exc
            except Exception as exc:  # noqa: BLE001 - convert adapter errors into step failures
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts - 1:
                    time.sleep(min(2 ** attempt * 0.25, 2.0))
                    continue
                result = self._recover(workflow, step, last_error, run, state, patched_step)
                break

        # Post-conditions gate success (W14), unless the step was skipped.
        if not isinstance(result, SkippedStep) and specs:
            ok, message = evaluate_all(specs, result, run.context)
            if not ok:
                result = self._recover(workflow, step, message, run, state, patched_step)

        if isinstance(result, dict):
            shot = result.get("screenshot") or result.get("data_url")
            if shot:
                try:
                    state.env.evidence(f"{run.run_id}:{step.id}", shot)
                except Exception:  # noqa: BLE001
                    pass

        self._emit_success(step, run, state, result)
        return result

    @staticmethod
    def _success_specs(step: Step) -> list[str]:
        specs = list(step.post) + list(step.success)
        if step.kind == "assert" and step.do:
            specs = [step.do, *specs]
        return specs

    def _emit_success(self, step: Step, run: Run, state: "_State", result: Any) -> None:
        payload = result.to_dict() if isinstance(result, SkippedStep) else result
        state.emitter.emit(
            "step_end",
            step_id=step.id,
            status="skipped" if isinstance(result, SkippedStep) else "ok",
            data={"preview": _preview(payload)},
        )

    # ── error policy engine (P2) ────────────────────────────────────────

    def _recover(
        self,
        workflow: Workflow,
        step: Step,
        error: str,
        run: Run,
        state: "_State",
        patched_step: Step,
    ) -> Any:
        """Route a failure per ``on_error.then``; may return a result or raise.

        Order: locator self-heal (drift) → explicit policy. The default policy
        is ``agent`` when the environment can run an agent, else ``abort``.
        """
        # 1) Locator drift: try an LLM/fallback repair once, then re-dispatch.
        if step.locator and step.kind in ("browser", "app", "computer") and not state.healed.get(step.id):
            repaired = self._try_heal(patched_step, error, run, state)
            if repaired is not None:
                state.healed[step.id] = True
                healed_step = _apply_repair(patched_step, repaired)
                try:
                    result = self._dispatch(workflow, healed_step, run, state)
                    self._record_patch(workflow, step, healed_step, state)
                    return result
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"

        policy = str(step.on_error.get("then") or "").strip().lower()
        if not policy:
            policy = "agent" if state.env.supports_agentic() else "abort"
        if policy == "agent" and not state.env.supports_agentic():
            policy = "abort"

        state.emitter.emit(
            "recover",
            step_id=step.id,
            status=policy,
            message=error,
        )

        if policy in ("abort", "fail"):
            raise StepFailed(step.id, error or "step failed")
        if policy == "skip":
            return SkippedStep(step.id, error)
        if policy == "human":
            raise NeedsHuman(step.id, error or f"step '{step.id}' needs human input")
        if policy.startswith("goto:"):
            raise GotoStep(step.id, policy.split(":", 1)[1].strip())
        if policy == "self_heal":
            raise StepFailed(step.id, error or "step failed")
        # policy == "agent"
        taken = self._takeover(step, error, run, state)
        if taken is not None:
            return taken
        raise NeedsHuman(step.id, f"agent takeover could not complete '{step.id}': {error}")

    def _takeover(self, step: Step, error: str, run: Run, state: "_State") -> dict[str, Any] | None:
        """Hand a step to the agent (full tools); it must self-assess (P3)."""
        if not state.env.supports_agentic():
            return None
        goal = step.goal or f"{step.kind} step '{step.id}'" + (f": {step.do}" if step.do else "")
        specs = self._success_specs(step)
        prompt = _takeover_prompt(step, goal, error, run.context, specs)
        try:
            result = state.env.agentic(prompt, step)
        except Exception as exc:  # noqa: BLE001 - takeover failure falls back to human
            state.emitter.emit("agent_takeover", step_id=step.id, status="failed", message=str(exc))
            return None
        output = ""
        if isinstance(result, dict):
            output = str(result.get("output", ""))
        else:
            output = str(result)
        blocked = "VERDICT: BLOCKED" in output.upper()
        state.emitter.emit(
            "agent_takeover",
            step_id=step.id,
            status="blocked" if blocked else "done",
            message=(output or "")[-400:],
        )
        if blocked:
            return None
        return {"agentic": True, "takeover": True, "output": output, "recovered_from": error}

    def _dispatch(self, workflow: Workflow, step: Step, run: Run, state: "_State") -> Any:
        context = run.context
        if step.kind == "branch":
            condition = resolve_bool(step.when, context, self.secrets)
            chosen = step.then if condition else step.else_
            self._exec_steps(workflow, chosen, run, state)
            return {"branch": condition}
        if step.kind == "parallel":
            return self._run_parallel(workflow, step, run, state)
        if step.kind == "subworkflow":
            return self._run_subworkflow(step, run, state)
        if step.kind == "set":
            return self._run_set(step, run)
        if step.kind == "assert":
            return {"asserted": True}
        if step.kind == "wait":
            return self._run_wait(step)
        return self._dispatch_action(step, run, state)

    def _dispatch_action(self, step: Step, run: Run, state: "_State") -> Any:
        """Dispatch an action step, walking the locator fallback ladder (W12).

        When a GUI step declares multiple locator descriptors, the first that
        *works* wins. A successful fallback is drift: the descriptor is promoted
        and rewritten into the workflow (W15).
        """
        from .model import Locator as _Locator

        parsed = _Locator.from_dict(step.locator) if step.locator else None
        candidates = parsed.candidates() if parsed else []
        if step.kind not in ("browser", "app", "computer") or len(candidates) <= 1:
            return self._dispatch_action_once(step, run, state, step.locator)

        last_error: Exception | None = None
        for index, descriptor in enumerate(candidates):
            try:
                result = self._dispatch_action_once(step, run, state, descriptor)
            except (StepFailed, NeedsHuman):
                raise
            except Exception as exc:  # noqa: BLE001 - try the next descriptor
                last_error = exc
                continue
            if index > 0:
                state.emitter.emit(
                    "self_heal",
                    step_id=step.id,
                    status="drift",
                    message=f"locator fallback #{index} used; canonical locator promoted",
                    data={"descriptor": descriptor},
                )
                promoted = _promote_locator(step.locator, descriptor)
                self._record_patch(state.workflow, step, replace(step, locator=promoted), state)
            return result
        if last_error is not None:
            raise last_error
        raise StepFailed(step.id, "no locator descriptor matched")

    def _dispatch_action_once(
        self, step: Step, run: Run, state: "_State", locator_raw: Any
    ) -> Any:
        context = run.context
        payload = resolve(step.params, context, self.secrets) or {}
        locator = resolve(locator_raw, context, self.secrets) if locator_raw else None
        env = state.env
        kind = step.kind
        action = step.do or str(payload.get("action") or "")

        if kind == "command":
            argv = payload.get("command") or payload.get("run") or step.do
            if isinstance(argv, str):
                argv = _split_command(argv)
            cwd = str(payload.get("cwd") or "")
            timeout = int(payload.get("timeout") or step.timeout)
            return env.command(list(argv), cwd=cwd, timeout=timeout)
        if kind == "tool":
            if not action:
                raise StepFailed(step.id, "tool step requires a tool name in 'do'")
            return env.tool(action, payload)
        if kind == "browser":
            if not action:
                raise StepFailed(step.id, "browser step requires an action in 'do'")
            return env.browser(action, payload, locator)
        if kind in ("app", "computer"):
            if not action:
                raise StepFailed(step.id, "app step requires an action in 'do'")
            return env.app(action, payload, locator)
        if kind == "skill":
            if not action:
                raise StepFailed(step.id, "skill step requires a skill name in 'do'")
            return env.skill(action)
        if kind == "human":
            question = str(payload.get("question") or step.do or f"Approve step '{step.id}'?")
            options = payload.get("options") if isinstance(payload.get("options"), list) else None
            return env.human(step, question, options)
        if kind == "agentic":
            prompt = step.do or str(payload.get("prompt") or "")
            if not prompt:
                raise StepFailed(step.id, "agentic step requires a prompt")
            return env.agentic(prompt, step)
        raise StepFailed(step.id, f"unsupported step kind: {kind}")

    def _run_parallel(self, workflow: Workflow, step: Step, run: Run, state: "_State") -> Any:
        # Children share the run context but execute under the executor lock, so
        # their bindings are written safely and merged after each completes.
        results: dict[str, Any] = {}

        def _run_child(child: Step) -> tuple[str, Any]:
            with state.lock:
                result = self._exec_step(workflow, child, run, state)
            return child.bind, result

        max_workers = int(step.params.get("max_workers", 4) or 4)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 16))) as pool:
            futures = [pool.submit(_run_child, child) for child in step.body]
            for future in futures:
                key, result = future.result()
                results[key] = result
                run.context.setdefault("steps", {})[key] = result
        return {"parallel": results}

    def _run_subworkflow(self, step: Step, run: Run, state: "_State") -> Any:
        name = step.do or str(step.params.get("workflow") or "")
        if not name:
            raise StepFailed(step.id, "subworkflow step requires a workflow name")
        child_wf = self.store.get(name)
        if child_wf is None:
            raise StepFailed(step.id, f"subworkflow not found: {name}")
        inputs = resolve(step.params.get("inputs") or {}, run.context, self.secrets)
        child_run = self.run(child_wf, inputs, env=state.env, trigger="subworkflow", on_patch=state.on_patch)
        if child_run.status != "ok":
            raise StepFailed(step.id, f"subworkflow '{name}' {child_run.status}: {child_run.error}")
        return {"workflow": name, "outputs": child_run.outputs, "run_id": child_run.run_id}

    def _run_set(self, step: Step, run: Run) -> Any:
        context = run.context
        assignments = {k: v for k, v in step.params.items() if k not in ("name",)}
        if "name" in step.params and "value" in step.params:
            assignments = {str(step.params["name"]): step.params["value"]}
        written: dict[str, Any] = {}
        for name, template in assignments.items():
            value = resolve(template, context, self.secrets)
            run.context.setdefault("vars", {})[name] = value
            written[name] = value
        return {"set": written}

    def _run_wait(self, step: Step) -> Any:
        raw = step.params.get("seconds", step.do or 1)
        try:
            seconds = float(resolve(raw, {}, self.secrets))
        except (TypeError, ValueError):
            seconds = 1.0
        seconds = max(0.0, min(seconds, 600.0))
        if seconds:
            time.sleep(seconds)
        return {"waited": seconds}

    # ── recovery ────────────────────────────────────────────────────────

    def _try_heal(self, step: Step, error: str, run: Run, state: "_State") -> dict[str, Any] | None:
        try:
            repaired = state.env.self_heal(step, error, run.context)
        except Exception as exc:  # noqa: BLE001 - healing is best-effort
            logger.warning("self_heal raised for %s: %s", step.id, exc)
            return None
        if repaired:
            state.emitter.emit(
                "self_heal",
                step_id=step.id,
                status="healing",
                message="step repaired after failure",
                data={"fields": list(repaired.keys())},
            )
        return repaired

    def _record_patch(self, workflow: Workflow, old: Step, patched: Step, state: "_State") -> None:
        if state.on_patch is None:
            return
        fields: dict[str, Any] = {}
        if patched.locator != old.locator:
            fields["locator"] = patched.locator
        if patched.params != old.params:
            fields["params"] = patched.params
        if patched.do != old.do:
            fields["do"] = patched.do
        if not fields:
            return
        try:
            state.on_patch(workflow.name, old.id, fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow patch failed for %s/%s: %s", workflow.name, old.id, exc)

    def _request_approval(self, step: Step, run: Run, state: "_State") -> bool:
        question = f"Approve step '{step.id}' ({step.kind}: {step.do or ''})?"
        try:
            decision = state.env.human(step, question, None)
        except NotImplementedError:
            # No human channel: in guarded mode fail closed via NeedsHuman.
            raise NeedsHuman(step.id, f"step '{step.id}' requires human approval")
        if isinstance(decision, bool):
            return decision
        if isinstance(decision, dict):
            if decision.get("approved") is False:
                return False
            return True
        return True

    # ── persistence ─────────────────────────────────────────────────────

    def _checkpoint(self, run: Run) -> None:
        try:
            self.store.save_run(run)
        except Exception:  # noqa: BLE001 - checkpoint failures must not fail a run
            pass


class _State:
    """Per-run mutable executor state (step counter + patch callback)."""

    def __init__(
        self,
        *,
        emitter: RunEventEmitter,
        store: Any,
        env: StepEnvironment,
        on_patch: PatchCallback | None,
        workflow: Workflow,
    ):
        self.emitter = emitter
        self.store = store
        self.env = env
        self.on_patch = on_patch
        self.workflow = workflow
        self.total_steps = 0
        self.healed: dict[str, bool] = {}
        import threading

        self.lock = threading.RLock()

    def guard(self) -> None:
        self.total_steps += 1
        if self.total_steps > MAX_TOTAL_STEPS:
            raise StepFailed("guard", f"run exceeded {MAX_TOTAL_STEPS} steps")


def _order_steps(steps: list[Step]) -> list[Step]:
    """Linearise a sibling list following explicit ``next`` links.

    If no step declares ``next`` (legacy list-based workflows) the list order is
    used unchanged. With explicit links, execution starts from each head (a step
    not targeted by another) and follows the chain; any unreached steps are
    appended in their original order so nothing is silently dropped.
    """
    if not any(getattr(step, "next", "") for step in steps):
        return steps
    by_id = {step.id: step for step in steps}
    targets = {step.next for step in steps if step.next}
    ordered: list[Step] = []
    seen: set[str] = set()
    for step in steps:
        if step.id in targets:
            continue
        cursor: Step | None = step
        while cursor is not None and cursor.id not in seen:
            ordered.append(cursor)
            seen.add(cursor.id)
            cursor = by_id.get(cursor.next) if cursor.next else None
    for step in steps:
        if step.id not in seen:
            ordered.append(step)
    return ordered


def _takeover_prompt(
    step: Step,
    goal: str,
    error: str,
    context: dict[str, Any],
    specs: list[str],
) -> str:
    inputs = context.get("inputs", {})
    try:
        inputs_text = json.dumps(inputs, ensure_ascii=False)[:1500]
    except (TypeError, ValueError):
        inputs_text = str(inputs)[:1500]
    lines = [
        f"A fixed workflow is executing and the step '{step.id}' did not succeed.",
        "Take over this ONE step and complete it, then hand control back.",
        "",
        f"STEP GOAL: {goal}",
        f"STEP KIND: {step.kind}" + (f" / action: {step.do}" if step.do else ""),
    ]
    if error:
        lines.append(f"FAILURE: {error}")
    if step.locator:
        lines.append(f"TARGET LOCATOR: {json.dumps(step.locator, ensure_ascii=False)}")
    if specs:
        lines.append(f"SUCCESS CRITERIA: {'; '.join(specs)}")
    lines += [
        f"CURRENT INPUTS: {inputs_text}",
        "",
        "Use any tools you need. When finished, end your reply with a final line:",
        "VERDICT: DONE   (if the step is complete)",
        "VERDICT: BLOCKED (if you cannot complete it)",
    ]
    return "\n".join(lines)


def _promote_locator(locator: dict[str, Any] | None, descriptor: dict[str, Any]) -> dict[str, Any]:
    from .locators import promote

    return promote(locator, descriptor)


def _apply_repair(step: Step, repaired: dict[str, Any]) -> Step:
    fields: dict[str, Any] = {}
    if "locator" in repaired:
        fields["locator"] = repaired["locator"]
    if "params" in repaired:
        fields["params"] = repaired["params"]
    if "do" in repaired:
        fields["do"] = repaired["do"]
    if not fields:
        return step
    return replace(step, **fields)


def _split_command(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _preview(result: Any, limit: int = RESULT_PREVIEW_CHARS) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(result)
    return text[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
