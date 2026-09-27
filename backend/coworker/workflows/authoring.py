"""The single workflow authoring pipeline.

Every path that turns a proposed procedure into a stored workflow — the agent
``workflow`` tool, the post-turn review, the recorder/from-session endpoint, a
template install — funnels through :func:`store` so validation is applied exactly
once and an invalid workflow can never reach the store. The candidate steps are
normalized/generalized by :mod:`recorder`, which is itself registry-driven so the
produced steps are grounded in capabilities that exist.
"""

from __future__ import annotations

from typing import Any

from .recorder import record_draft


def build_draft(
    name: str,
    steps: list[dict[str, Any]],
    *,
    description: str,
    inputs: list[dict[str, Any]] | None = None,
    triggers: list[str] | None = None,
    sources: list[str] | None = None,
) -> str:
    """Render candidate steps into canonical workflow YAML."""
    return record_draft(
        name, steps, description=description, inputs=inputs, triggers=triggers, sources=sources
    )


def store(
    manager: Any,
    *,
    name: str,
    steps: list[dict[str, Any]],
    description: str,
    action: str = "create",
    sources: list[str] | None = None,
    inputs: list[dict[str, Any]] | None = None,
    approval_required: bool = True,
) -> dict[str, Any]:
    """Validate a proposal and either stage it (draft) or apply it (active).

    Returns the manager result dict; on validation failure it returns an error
    with structured ``diagnostics`` and NOTHING is written.
    """
    draft = build_draft(
        name, steps, description=description, inputs=inputs, sources=sources
    )
    check = manager.validate(draft)
    if not check.get("valid"):
        return {
            "status": "error",
            "message": "; ".join(check.get("errors") or ["invalid workflow"]),
            "diagnostics": check.get("diagnostics") or [],
        }
    if approval_required:
        return manager.stage_draft(
            name, draft, sources=sources, action="update" if action == "update" else "create"
        )
    exists = manager.store.exists(name)
    if action == "update" or exists:
        if exists:
            return manager.update(name, draft)
        return manager.create(draft)
    return manager.create(draft)
