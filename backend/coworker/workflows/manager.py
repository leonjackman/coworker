"""WorkflowManager: the facade used by the API and the agent tools (W02/W26/W29).

Owns the store, the catalog/registry, the executor and the draft queue. All
mutating operations return plain dicts so both the HTTP layer and tool layer can
render them directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from coworker.logger import get_logger

from .env import StepEnvironment
from .executor import WorkflowExecutor
from .model import (
    Workflow,
    WorkflowParseError,
    WorkflowValidationError,
    Step,
)
from .parser import is_valid_name, parse_workflow, renumber_steps, render_workflow, validate
from .recorder import record_draft
from .registry import WorkflowRegistry
from .store import WorkflowStore

logger = get_logger(__name__)

VALID_PENDING_ACTIONS = {"create", "update"}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class WorkflowManager:
    def __init__(
        self,
        data_dir: Path,
        *,
        secrets: Callable[[str], str | None] | None = None,
        listener: Callable[[Any], None] | None = None,
        roots_provider: Callable[[], list[Path]] | None = None,
    ):
        self.root = Path(data_dir) / "workflows"
        self.store = WorkflowStore(self.root, roots_provider)
        self.registry = WorkflowRegistry(self.store)
        self.executor = WorkflowExecutor(self.store, secrets=secrets, listener=listener)

    # ── catalog ─────────────────────────────────────────────────────────

    def list(self, *, statuses: tuple[str, ...] = ("active",)) -> list[dict[str, Any]]:
        return [w.to_dict(include_steps=False) for w in self.registry.list(statuses=statuses)]

    def get(self, name: str, *, include_steps: bool = True) -> dict[str, Any] | None:
        workflow = self.store.get(name)
        if workflow is None:
            return None
        data = workflow.to_dict(include_steps=include_steps)
        if include_steps:
            data["yaml"] = self.store.read_text(name) or ""
        data["history"] = self.store.history(name)
        return data

    def prompt_block(self) -> str:
        return self.registry.prompt_block()

    # ── mutations ───────────────────────────────────────────────────────

    def create(self, content: str, *, overwrite: bool = False) -> dict[str, Any]:
        workflow = self._parse_or_raise(content)
        if self.store.exists(workflow.name) and not overwrite:
            return {"status": "error", "message": f"workflow already exists: {workflow.name}"}
        from .fingerprint import current_fingerprint

        workflow = Workflow(
            **{**workflow.__dict__, "version": 1 if not overwrite else self.store.next_version(workflow.name),
               "status": "active", "source": "user", "fingerprint": current_fingerprint()}
        )
        saved = self.store.save(workflow, archive=overwrite)
        return {"status": "ok", "workflow": saved.to_dict(include_steps=False)}

    def update(self, name: str, content: str) -> dict[str, Any]:
        existing = self.store.get(name)
        if existing is None:
            return {"status": "error", "message": f"workflow not found: {name}"}
        workflow = self._parse_or_raise(content, name_hint=name)
        # Name changes are allowed only by creating a new workflow.
        if workflow.name != name and self.store.exists(workflow.name):
            return {"status": "error", "message": f"workflow already exists: {workflow.name}"}
        from .fingerprint import current_fingerprint

        workflow = Workflow(
            **{**workflow.__dict__, "version": self.store.next_version(name),
               "status": workflow.status or "active", "source": existing.source,
               "fingerprint": current_fingerprint()}
        )
        saved = self.store.save(workflow)
        if workflow.name != name:
            self.store.delete(name)
        return {"status": "ok", "workflow": saved.to_dict(include_steps=False)}

    def delete(self, name: str) -> dict[str, Any]:
        removed = self.store.delete(name)
        if not removed:
            return {"status": "error", "message": f"workflow not found: {name}"}
        return {"status": "ok", "name": name, "removed": True}

    def list_templates(self) -> list[dict[str, Any]]:
        from .templates import list_templates

        return list_templates()

    def install_template(self, template_id: str, *, overwrite: bool = False) -> dict[str, Any]:
        from .templates import get_template

        from dataclasses import replace

        template = get_template(template_id)
        if template is None:
            return {"status": "error", "message": f"template not found: {template_id}"}
        workflow, diagnostics = parse_workflow(template["yaml"], name_hint=template_id)
        if workflow is None:
            return {"status": "error", "message": "; ".join(diagnostics) or "invalid template"}
        # Normalise node ids to the system scheme (id:1, id:2, …).
        workflow = replace(workflow, steps=renumber_steps(workflow.steps))
        result = self.create(render_workflow(workflow), overwrite=overwrite)
        if result.get("status") == "ok":
            result["template"] = template_id
        return result

    def duplicate(self, name: str, new_name: str = "") -> dict[str, Any]:
        """Copy a workflow under a new name (new, independent definition)."""
        from dataclasses import replace

        from .fingerprint import current_fingerprint

        source = self.store.get(name)
        if source is None:
            return {"status": "error", "message": f"workflow not found: {name}"}
        candidate = (new_name or f"{name}-copy").strip() or f"{name}-copy"
        if candidate == name or not is_valid_name(candidate):
            # Fall back to the default "<name>-copy" (valid for any language).
            candidate = f"{name}-copy"
            if not is_valid_name(candidate):
                candidate = "workflow-copy"
        unique = candidate
        index = 2
        while self.store.exists(unique):
            unique = f"{candidate}-{index}"
            index += 1
        now = _now()
        copy_wf = replace(
            source,
            name=unique,
            version=1,
            status="active",
            source="user",
            fingerprint=current_fingerprint(),
            provenance={**source.provenance, "duplicated_from": name},
            created_at=now,
            updated_at=now,
        )
        saved = self.store.save(copy_wf, archive=False)
        return {"status": "ok", "workflow": saved.to_dict(include_steps=False), "name": unique}

    def export(self, name: str) -> dict[str, Any]:
        text = self.store.read_text(name)
        if text is None:
            return {"status": "error", "message": f"workflow not found: {name}"}
        return {"status": "ok", "name": name, "yaml": text}

    def versions(self, name: str) -> list[dict[str, Any]]:
        return self.store.list_versions(name)

    def version_detail(self, name: str, version: int) -> dict[str, Any]:
        workflow, is_current = self.store.read_version(name, version)
        if workflow is None:
            return {"status": "error", "message": f"no version {version} for {name}"}
        data = workflow.to_dict(include_steps=True)
        data["is_current"] = is_current
        data["yaml"] = render_workflow(workflow)
        return {"status": "ok", "workflow": data}

    def delete_version(self, name: str, version: int) -> dict[str, Any]:
        current = self.store.get(name)
        if current is not None and current.version == version:
            return {"status": "error", "message": "cannot delete the in-use version"}
        removed = self.store.delete_version(name, version)
        if not removed:
            return {"status": "error", "message": f"no version {version} for {name}"}
        return {"status": "ok", "version": version, "removed": True}

    def rollback(self, name: str, version: int) -> dict[str, Any]:
        """Restore a historical version as a NEW version (forward-only history)."""
        from .store import safe_filename

        target = self.store.history_dir / safe_filename(name) / f"v{int(version)}.yaml"
        if not target.is_file():
            return {"status": "error", "message": f"no version {version} for {name}"}
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "message": str(exc)}
        result = self.update(name, content)
        if result.get("status") == "ok":
            result["rolled_back_to"] = int(version)
        return result

    def set_status(self, name: str, status: str) -> dict[str, Any]:
        workflow = self.store.get(name)
        if workflow is None:
            return {"status": "error", "message": f"workflow not found: {name}"}
        from dataclasses import replace

        saved = self.store.save(replace(workflow, status=status))
        return {"status": "ok", "workflow": saved.to_dict(include_steps=False)}

    def render(self, content: str) -> dict[str, Any]:
        result = parse_workflow(content)
        workflow, diagnostics = result
        if workflow is None:
            return {"status": "error", "message": "; ".join(diagnostics), "diagnostics": diagnostics}
        return {
            "status": "ok",
            "yaml": render_workflow(workflow),
            "workflow": workflow.to_dict(include_steps=False),
            "diagnostics": diagnostics,
        }

    def render_steps(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Render a workflow from structured fields (used by the visual editor)."""
        from .parser import _parse_inputs, _parse_steps

        diagnostics: list[str] = []
        steps = _parse_steps(payload.get("steps") or [], diagnostics, "steps")
        workflow = Workflow(
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            steps=steps,
            version=int(payload.get("version") or 1),
            platform=str(payload.get("platform") or ""),
            inputs=_parse_inputs(payload.get("inputs")),
            triggers=[str(t) for t in (payload.get("triggers") or ["manual"])],
            status=str(payload.get("status") or "active"),
            source="user",
        )
        errors = list(diagnostics) + validate(workflow)
        return {
            "status": "ok" if not errors else "error",
            "yaml": render_workflow(workflow),
            "errors": errors,
            "workflow": workflow.to_dict(),
        }

    def validate(self, content: str) -> dict[str, Any]:
        workflow, diagnostics = parse_workflow(content)
        errors = validate(workflow) if workflow is not None else diagnostics
        return {"status": "ok" if not errors else "error", "errors": errors, "valid": not errors}

    # ── drafts ──────────────────────────────────────────────────────────

    def list_pending(self) -> list[dict[str, Any]]:
        return self.store.list_drafts()

    def read_pending(self, name: str) -> str | None:
        return self.store.read_draft(name)

    def stage_draft(
        self,
        name: str,
        content: str,
        *,
        sources: list[str] | None = None,
        action: str = "create",
    ) -> dict[str, Any]:
        workflow, diagnostics = parse_workflow(content, name_hint=name)
        if workflow is None:
            return {"status": "error", "message": "; ".join(diagnostics) or "invalid workflow"}
        errors = validate(workflow)
        if errors:
            return {"status": "error", "message": "; ".join(errors)}
        if action not in VALID_PENDING_ACTIONS:
            action = "create"
        provenance = dict(workflow.provenance)
        # Override (not setdefault): recorded drafts pre-seed action="create",
        # which must not win over an explicit "update".
        provenance["action"] = action
        provenance.setdefault("sources", sources or [])
        from dataclasses import replace

        workflow = replace(workflow, provenance=provenance, status="draft")
        self.store.write_draft(workflow.name, render_workflow(workflow))
        return {"status": "ok", "name": workflow.name, "staged": True, "action": action}

    def update_pending(self, name: str, content: str) -> dict[str, Any]:
        workflow, diagnostics = parse_workflow(content, name_hint=name)
        if workflow is None:
            return {"status": "error", "message": "; ".join(diagnostics)}
        if not self.store.replace_draft_content(name, content):
            return {"status": "error", "message": f"no pending draft: {name}"}
        return {"status": "ok", "name": name}

    def approve_pending(self, name: str) -> dict[str, Any]:
        content = self.store.read_draft(name)
        if content is None:
            return {"status": "error", "message": f"no pending draft: {name}"}
        workflow, diagnostics = parse_workflow(content, name_hint=name)
        if workflow is None:
            return {"status": "error", "message": "; ".join(diagnostics)}
        action = str(workflow.provenance.get("action") or "create")
        existing = self.store.get(workflow.name)
        from dataclasses import replace

        if existing is not None:
            version = self.store.next_version(workflow.name)
            source = existing.source
        else:
            version = workflow.version or 1
            source = "agent"
        from .fingerprint import current_fingerprint

        workflow = replace(
            workflow, version=version, status="active", source=source, fingerprint=current_fingerprint()
        )
        saved = self.store.save(workflow)
        self.store.remove_draft(name)
        if workflow.name != name:
            self.store.remove_draft(workflow.name)
        return {
            "status": "ok",
            "name": workflow.name,
            "approved": True,
            "action": action,
            "workflow": saved.to_dict(include_steps=False),
        }

    def reject_pending(self, name: str) -> dict[str, Any]:
        removed = self.store.remove_draft(name)
        if not removed:
            return {"status": "error", "message": f"no pending draft: {name}"}
        return {"status": "ok", "name": name, "rejected": True}

    # ── runs ────────────────────────────────────────────────────────────

    def run(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
        *,
        env: StepEnvironment | None = None,
        run_id: str | None = None,
        resume: bool = False,
        trigger: str = "manual",
        on_patch: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        workflow = self.store.get(name)
        if workflow is None:
            return {"status": "error", "message": f"workflow not found: {name}"}
        run = self.executor.run(
            workflow,
            inputs or {},
            env=env,
            run_id=run_id,
            resume=resume,
            trigger=trigger,
            on_patch=on_patch or self._default_patch,
        )
        if run.status in ("failed", "needs_human") and trigger != "agent":
            try:
                from coworker.notifications import notify

                notify(
                    self.store.root.parent,
                    kind=f"workflow_{run.status}",
                    title=name,
                    detail=run.error,
                    ref=run.run_id,
                )
            except Exception:  # noqa: BLE001
                pass
        return {"status": run.status, "run": run.to_dict()}

    def resume(
        self,
        run_id: str,
        decisions: dict[str, Any] | None = None,
        *,
        env: StepEnvironment | None = None,
    ) -> dict[str, Any]:
        """Resume a ``needs_human`` run with the human's decisions (W22/W37)."""
        from .env import DecisionEnvironment, StepEnvironment

        run = self.store.load_run(run_id)
        if run is None:
            return {"status": "error", "message": f"no run: {run_id}"}
        workflow = self.store.get(run.workflow)
        if workflow is None:
            return {"status": "error", "message": f"workflow not found: {run.workflow}"}
        base = env or StepEnvironment()
        resumed = self.executor.run(
            workflow,
            run_id=run_id,
            resume=True,
            env=DecisionEnvironment(base, decisions or {}),
            trigger=run.trigger or "manual",
            on_patch=self._default_patch,
        )
        return {"status": resumed.status, "run": resumed.to_dict()}

    def list_runs(self, name: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_runs(workflow=name, limit=limit)

    def read_evidence(self, run_id: str) -> list[dict[str, Any]]:
        from .evidence import read_index

        return read_index(self.store.root.parent, run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.load_run(run_id)
        return run.to_dict() if run else None

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.read_events(run_id)

    def _default_patch(self, workflow_name: str, step_id: str, fields: dict[str, Any]) -> None:
        """Persist a self-heal patch: rewrite the step and bump the version (W15)."""
        workflow = self.store.get(workflow_name)
        if workflow is None:
            return
        new_steps = _patch_steps(workflow.steps, step_id, fields)
        if new_steps is None:
            return
        from dataclasses import replace

        patched = replace(
            workflow,
            steps=new_steps,
            version=self.store.next_version(workflow_name),
            provenance={**workflow.provenance, "healed_step": step_id},
        )
        self.store.save(patched)

    # ── recording ───────────────────────────────────────────────────────

    def stage_recorded(
        self,
        name: str,
        steps: list[dict[str, Any]],
        *,
        description: str,
        inputs: list[dict[str, Any]] | None = None,
        triggers: list[str] | None = None,
        sources: list[str] | None = None,
        action: str = "create",
    ) -> dict[str, Any]:
        draft = record_draft(
            name,
            steps,
            description=description,
            inputs=inputs,
            triggers=triggers,
            sources=sources,
        )
        return self.stage_draft(name, draft, sources=sources, action=action)

    def apply_agent_workflow(
        self,
        action: str,
        name: str,
        steps: list[dict[str, Any]],
        *,
        description: str = "",
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Directly create/update a workflow (used when approval is disabled)."""
        content = record_draft(
            name,
            steps,
            description=description or name,
            sources=sources,
        )
        exists = self.store.exists(name)
        if action == "update" or exists:
            if exists:
                return self.update(name, content)
            return self.create(content)
        return self.create(content)

    # ── helpers ─────────────────────────────────────────────────────────

    def _parse_or_raise(self, content: str, *, name_hint: str = "") -> Workflow:
        workflow, diagnostics = parse_workflow(content, name_hint=name_hint)
        if workflow is None:
            raise WorkflowParseError("; ".join(diagnostics) or "invalid workflow")
        errors = validate(workflow)
        if errors:
            raise WorkflowValidationError("; ".join(errors))
        return workflow


def _patch_steps(steps: list[Step], step_id: str, fields: dict[str, Any]) -> list[Step] | None:
    from dataclasses import replace

    found = False
    out: list[Step] = []
    for step in steps:
        if step.id == step_id:
            found = True
            out.append(replace(step, **{k: v for k, v in fields.items() if k in {"locator", "params", "do"}}))
            continue
        then = _patch_steps(step.then, step_id, fields)
        else_ = _patch_steps(step.else_, step_id, fields)
        body = _patch_steps(step.body, step_id, fields)
        if then is not None or else_ is not None or body is not None:
            found = True
            out.append(
                replace(
                    step,
                    then=then if then is not None else step.then,
                    else_=else_ if else_ is not None else step.else_,
                    body=body if body is not None else step.body,
                )
            )
            continue
        out.append(step)
    return out if found else None
