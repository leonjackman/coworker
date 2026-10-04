"""Contract drift guard.

`backend/coworker/computer/actions.py` is the SINGLE source for computer
actions. This test fails if the JS script kernel / host router drift from it,
so "the docs promise a function that does not exist" (and vice-versa) cannot
ship silently.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.computer.actions import (  # noqa: E402
    COMPUTER_ACTIONS,
    SEMANTIC_SHORTCUTS,
    contract_dump,
)

_ROOT = Path(__file__).resolve().parents[2]
_KERNEL = _ROOT / "electron" / "computer-repl-kernel.js"
_HOST = _ROOT / "electron" / "desktop-controller.js"


def _kernel_methods() -> set[str]:
    text = _KERNEL.read_text(encoding="utf-8")
    names = set(re.findall(r"async\s+(\w+)\s*\(", text))
    names.discard("function")
    return names


def _host_routes() -> set[str]:
    text = _HOST.read_text(encoding="utf-8")
    start = text.index("async _replCall(")
    end = text.index("_scriptInstance()", start)
    return set(re.findall(r"case\s+'([\w_]+)'", text[start:end]))


def _kernel_emitted_rpcs() -> set[str]:
    text = _KERNEL.read_text(encoding="utf-8")
    return set(re.findall(r"(?:rpc|app)\(\s*'([\w_]+)'", text))


def test_contract_dump_is_json_serializable():
    dump = contract_dump()
    json.dumps(dump)  # must not raise
    assert dump["shortcuts"] == list(SEMANTIC_SHORTCUTS)
    assert {a["name"] for a in dump["actions"]} == {a.name for a in COMPUTER_ACTIONS}


def test_every_script_binding_exists_in_kernel():
    kernel = _kernel_methods()
    missing = [a.name for a in COMPUTER_ACTIONS if a.script and a.script not in kernel]
    assert not missing, f"actions declare script bindings that the kernel lacks: {missing}"


def test_every_emitted_rpc_is_routed_by_host():
    routes = _host_routes()
    missing = sorted(r for r in _kernel_emitted_rpcs() if r not in routes)
    assert not missing, f"kernel emits RPCs the host cannot route: {missing}"


def test_required_and_requires_any_are_sane():
    for a in COMPUTER_ACTIONS:
        names = {p.name for p in a.params}
        for req in a.requires_any:
            assert req in names, f"{a.name}: requires_any '{req}' is not a declared param"
        # A required param must be declared exactly once and not also optional.
        assert len(names) == len(a.params), f"{a.name}: duplicate param names"


def test_param_map_targets_declared_params():
    for a in COMPUTER_ACTIONS:
        names = {p.name for p in a.params}
        for src in a.param_map:
            assert src in names, f"{a.name}: param_map key '{src}' is not a param"
