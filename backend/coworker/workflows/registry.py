"""Workflow catalog and agent-facing system-prompt injection (W30 support).

Workflows are advertised to the model the way skills are: name + description +
triggers only, so the model can *choose to run an existing workflow* instead of
re-deriving the procedure. Bodies (steps) load on demand via the workflow tool.
"""

from __future__ import annotations

from typing import Any

from .model import Workflow
from .store import WorkflowStore

WORKFLOW_CATALOG_MAX_TOKENS = 1200
WORKFLOW_DESCRIPTION_CLIP_CHARS = 160


class WorkflowRegistry:
    def __init__(self, store: WorkflowStore):
        self.store = store

    def list(self, *, statuses: tuple[str, ...] = ("active",)) -> list[Workflow]:
        return [w for w in self.store.list_active() if w.status in statuses]

    def get(self, name: str) -> Workflow | None:
        return self.store.get(name)

    def injection_list(self) -> list[Workflow]:
        return self.list(statuses=("active",))

    def prompt_block(self) -> str:
        workflows = self.injection_list()
        if not workflows:
            return ""
        header = [
            "\n\nThe following workflows are saved, deterministic procedures you can run "
            "instead of re-doing the steps by hand. Use the `workflow` tool with action "
            "`run` (name + inputs) to execute one, or action `get` to inspect its steps. "
            "Prefer running an existing workflow over improvising when its description "
            "matches the task.",
            "",
            "<available_workflows>",
        ]
        lines = list(header)
        for wf in workflows:
            desc = " ".join(wf.description.split())[:WORKFLOW_DESCRIPTION_CLIP_CHARS]
            triggers = ",".join(wf.triggers) if wf.triggers else "manual"
            inputs = ",".join(wf.input_names)
            lines.append("  <workflow>")
            lines.append(f"    <name>{_esc(wf.name)}</name>")
            lines.append(f"    <description>{_esc(desc)}</description>")
            lines.append(f"    <triggers>{_esc(triggers)}</triggers>")
            if inputs:
                lines.append(f"    <inputs>{_esc(inputs)}</inputs>")
            lines.append(f"    <steps>{len(wf.steps)}</steps>")
            lines.append("  </workflow>")
        lines.append("</available_workflows>")
        rendered = "\n".join(lines)
        try:
            from coworker.context import estimate_text_tokens

            if estimate_text_tokens(rendered) > WORKFLOW_CATALOG_MAX_TOKENS:
                # Trim to the first N workflows that fit, then note truncation.
                kept: list[str] = list(header)
                for wf in workflows:
                    candidate = kept + [
                        "  <workflow>",
                        f"    <name>{_esc(wf.name)}</name>",
                        f"    <description>{_esc(wf.description[:WORKFLOW_DESCRIPTION_CLIP_CHARS])}</description>",
                        "  </workflow>",
                    ]
                    candidate.append("</available_workflows>")
                    if estimate_text_tokens("\n".join(candidate)) > WORKFLOW_CATALOG_MAX_TOKENS:
                        break
                    kept = candidate[:-1]
                kept.append("</available_workflows>")
                kept.append("[workflow catalog truncated to fit context]")
                return "\n".join(kept)
        except Exception:  # noqa: BLE001 - token estimate is best-effort
            pass
        return rendered


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
