"""R1: MCP tool calls must go through human-in-the-loop approval.

Covers the risk ladder (read-only / write / destructive x autonomy), the
trusted-server and always-allow escapes, and the interrupt -> stream-event ->
approval-record round trip the frontend card is built from.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.agents import (
    _DynamicInterruptOn,
    command_approval_middleware,
    interrupt_action_kind,
    record_runtime_interrupts,
    stream_event_from_interrupt,
)
from backend.coworker.mcp.mcp_session import mcp_approval_digest
from coworker.workspace import CommandApprovalStore

SERVER_ID = "srv-1"

POLICIES = {
    "peek": {"read_only": True, "annotations": {"destructive": False}},
    "add": {"read_only": False, "annotations": {}},  # undeclared -> write
    "wipe": {"read_only": False, "annotations": {"destructive": True}},
}


def make_policy(trusted: bool = False):
    def policy(name: str):
        base = POLICIES.get(name)
        if base is None:
            return None
        return {
            "tool": name,
            "remote_name": name,
            "server_id": SERVER_ID,
            "server_name": "Alpha",
            "digest": mcp_approval_digest(SERVER_ID, name),
            "trusted": trusted,
            **base,
        }

    return policy


def make_request(tool: str, autonomy: str = "supervised", phase: str = "execute"):
    return SimpleNamespace(
        state={"phase": phase, "work_mode": "build", "autonomy": autonomy},
        tool_call={"name": tool, "args": {"target": "/tmp/x"}, "id": "call-1"},
    )


def hitl_for(store=None, trusted: bool = False):
    return command_approval_middleware(store, make_policy(trusted))[0]


def should_interrupt(hitl, tool: str, autonomy: str = "supervised", phase: str = "execute") -> bool:
    config = hitl.interrupt_on.get(tool)
    if config is None:
        return False
    when = config.get("when")
    return True if when is None else bool(when(make_request(tool, autonomy, phase)))


# ── the dynamic interrupt_on mapping ─────────────────────────────────────


def test_dynamic_mapping_resolves_mcp_tools():
    hitl = hitl_for()
    assert "wipe" in hitl.interrupt_on
    assert hitl.interrupt_on.get("wipe") is not None
    assert hitl.interrupt_on["wipe"]["allowed_decisions"] == ["approve", "reject"]


def test_dynamic_mapping_keeps_static_entries_and_rejects_unknown():
    hitl = hitl_for()
    assert "run_command" in hitl.interrupt_on
    assert "ask_user" in hitl.interrupt_on
    assert "read_file" not in hitl.interrupt_on
    assert hitl.interrupt_on.get("read_file") is None
    with pytest.raises(KeyError):
        hitl.interrupt_on["read_file"]


def test_dynamic_mapping_survives_a_broken_resolver():
    def boom(_name: str):
        raise RuntimeError("resolver exploded")

    mapping = _DynamicInterruptOn({"run_command": {"allowed_decisions": ["approve"]}}, boom)
    assert "run_command" in mapping
    assert mapping.get("whatever") is None


def test_without_a_policy_provider_no_mcp_entries_appear():
    hitl = command_approval_middleware(None, None)[0]
    assert hitl.interrupt_on.get("wipe") is None
    assert "run_command" in hitl.interrupt_on


# ── the risk ladder ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("autonomy", "tool", "expected"),
    [
        # read-only tools never prompt, at any autonomy level
        ("supervised", "peek", False),
        ("guarded", "peek", False),
        ("autonomous", "peek", False),
        # write / undeclared: supervised asks, guarded lets it through
        ("supervised", "add", True),
        ("guarded", "add", False),
        ("autonomous", "add", False),
        # destructive: both supervised and guarded ask
        ("supervised", "wipe", True),
        ("guarded", "wipe", True),
        ("autonomous", "wipe", False),
    ],
)
def test_approval_ladder(autonomy, tool, expected):
    assert should_interrupt(hitl_for(), tool, autonomy) is expected


def test_trusted_server_is_never_prompted():
    hitl = hitl_for(trusted=True)
    for tool in ("peek", "add", "wipe"):
        for autonomy in ("supervised", "guarded", "autonomous"):
            assert should_interrupt(hitl, tool, autonomy) is False


def test_discuss_phase_never_prompts():
    # MCP tools are hidden in discuss; a stray call must not raise an interrupt.
    assert should_interrupt(hitl_for(), "wipe", "supervised", phase="discuss") is False


def test_always_allow_suppresses_future_prompts(tmp_path):
    store = CommandApprovalStore(tmp_path / "approvals.json")
    hitl = hitl_for(store)
    assert should_interrupt(hitl, "wipe", "supervised") is True

    store.always_allow(mcp_approval_digest(SERVER_ID, "wipe"))
    assert should_interrupt(hitl, "wipe", "supervised") is False
    # Scoped to that one tool, not the whole server.
    assert should_interrupt(hitl, "add", "supervised") is True


def test_builtin_tools_keep_their_own_rules():
    hitl = hitl_for()
    # run_command still follows the command rules (supervised only).
    run = SimpleNamespace(
        state={"phase": "execute", "work_mode": "build", "autonomy": "supervised"},
        tool_call={"name": "run_command", "args": {"command": ["git", "status"], "cwd": ""}, "id": "c"},
    )
    assert hitl.interrupt_on["run_command"]["when"](run) is True
    run.state["autonomy"] = "guarded"
    assert hitl.interrupt_on["run_command"]["when"](run) is False


# ── interrupt -> approval record -> stream event ─────────────────────────


class FakeInterrupt:
    def __init__(self, actions):
        self.id = "int-1"
        self.value = {
            "action_requests": actions,
            "review_configs": [{"action_name": a["name"], "allowed_decisions": ["approve", "reject"]} for a in actions],
        }


def test_interrupt_action_kind_classifies_mcp():
    policy = make_policy()
    assert interrupt_action_kind({"name": "ask_user"}, policy) == "question"
    assert interrupt_action_kind({"name": "submit_plan"}, policy) == "plan"
    assert interrupt_action_kind({"name": "wipe"}, policy) == "mcp"
    assert interrupt_action_kind({"name": "run_command"}, policy) == "command"
    # Without a resolver everything non-special stays a command (back-compat).
    assert interrupt_action_kind({"name": "wipe"}) == "command"


def test_mcp_interrupt_produces_a_renderable_approval(tmp_path):
    store = CommandApprovalStore(tmp_path / "approvals.json")
    interrupt = FakeInterrupt([{"name": "wipe", "args": {"target": "/tmp/x"}, "description": "d"}])

    approvals = record_runtime_interrupts([interrupt], store, {"session_id": "s1"}, make_policy())
    assert len(approvals) == 1
    context = approvals[0]["context"]
    assert context["kind"] == "mcp"
    assert context["tool_name"] == "wipe"
    assert context["mcp"]["digest"] == mcp_approval_digest(SERVER_ID, "wipe")
    assert context["mcp"]["server_name"] == "Alpha"

    event = stream_event_from_interrupt(approvals[0])
    assert event["type"] == "approval_required"
    assert event["kind"] == "mcp"
    assert event["tool_name"] == "wipe"
    assert event["tool_args"] == {"target": "/tmp/x"}
    assert event["server_name"] == "Alpha"
    assert event["destructive"] is True
    assert event["read_only"] is False
    # The MCP card has no argv to show.
    assert event["command"] == []


def test_command_interrupt_still_renders_as_a_command(tmp_path):
    store = CommandApprovalStore(tmp_path / "approvals.json")
    interrupt = FakeInterrupt(
        [{"name": "run_command", "args": {"command": ["git", "status"], "cwd": "/w"}, "description": "d"}]
    )
    approvals = record_runtime_interrupts([interrupt], store, {"session_id": "s1"}, make_policy())
    event = stream_event_from_interrupt(approvals[0])
    assert event["type"] == "approval_required"
    assert event["kind"] == "command"
    assert event["command"] == ["git", "status"]
    assert event["cwd"] == "/w"


def test_mixed_interrupt_batch_is_classified_per_action(tmp_path):
    store = CommandApprovalStore(tmp_path / "approvals.json")
    interrupt = FakeInterrupt(
        [
            {"name": "run_command", "args": {"command": ["ls"], "cwd": ""}},
            {"name": "wipe", "args": {"target": "/tmp/y"}},
        ]
    )
    approvals = record_runtime_interrupts([interrupt], store, {"session_id": "s1"}, make_policy())
    kinds = [stream_event_from_interrupt(a).get("kind") for a in approvals]
    assert kinds == ["command", "mcp"]
