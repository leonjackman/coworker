"""Node contract: every declared kind/action is well-formed and Studio-readable.

This is the "single source of truth" guard (P4): the backend registry defines the
node contract, and the frontend fallback catalog must be a subset of it.
"""

from __future__ import annotations

import re
from pathlib import Path

from coworker.workflows.capabilities import SUCCESS_RULES, CapabilityRegistry
from coworker.workflows.model import VALID_KINDS

_PARAM_TYPES = {"string", "number", "boolean", "list", "object", "textarea", "csv", "app"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ACTIONS_TS = _REPO_ROOT / "frontend" / "src" / "components" / "workflows" / "actions.ts"


def test_every_kind_is_a_valid_model_kind():
    reg = CapabilityRegistry.declared()
    for kind in reg.kinds:
        assert kind in VALID_KINDS, f"registry declares unknown kind: {kind}"


def test_every_action_has_a_wellformed_contract():
    reg = CapabilityRegistry.declared()
    for kind, kspec in reg.kinds.items():
        for action in kspec.actions:
            ref = f"{kind}.{action.name}"
            assert action.target, f"{ref}: missing target"
            assert action.success in SUCCESS_RULES, f"{ref}: bad success rule '{action.success}'"
            names = [p.name for p in action.params]
            assert len(names) == len(set(names)), f"{ref}: duplicate param names"
            for param in action.params:
                assert param.type in _PARAM_TYPES, f"{ref}.{param.name}: bad type '{param.type}'"
            for output in action.outputs:
                assert isinstance(output, str) and output, f"{ref}: bad output {output!r}"
            if action.locator is not None:
                # x/y are implicit cross-action targets (a coords descriptor can
                # fill any click-like action); everything else must be a param.
                implicit = {"x", "y"}
                for descriptor_key, targets in action.locator.maps.items():
                    assert descriptor_key in action.locator.keys, f"{ref}: map key '{descriptor_key}' not in locator keys"
                    for target in targets:
                        assert target in names or target in implicit, f"{ref}: locator maps to unknown param '{target}'"


def test_browser_click_and_type_are_verified():
    reg = CapabilityRegistry.declared()
    for action in ("click", "click_selector", "click_text"):
        assert reg.action("browser", action).success == "observable_change"


def test_frontend_fallback_catalog_is_subset_of_registry():
    """The Studio's static fallback actions must all exist in the backend
    registry (drift guard); the registry is authoritative."""
    text = _ACTIONS_TS.read_text(encoding="utf-8")
    static_actions = set(re.findall(r"action:\s*'([a-z_]+)'", text))
    assert static_actions, "failed to parse actions.ts"

    reg = CapabilityRegistry.declared()
    backend_actions = {a.name for k in reg.kinds.values() for a in k.actions}
    # Native kinds (set/wait/assert/…) use their kind name as the "action".
    backend_actions |= set(reg.kinds.keys())
    missing = sorted(static_actions - backend_actions)
    assert not missing, f"frontend actions absent from backend registry: {missing}"
