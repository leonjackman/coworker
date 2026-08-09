"""End-to-end graph tests: MCP tool calls must round-trip through HITL (R1).

These build the *real* ``create_agent`` graph with a *real* stdio MCP server
subprocess and a scripted chat model, so the assertions cover the actual
middleware wiring (``_DynamicInterruptOn`` -> ``HumanInTheLoopMiddleware`` ->
``McpToolMiddleware``) rather than a reimplementation of it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from coworker.agents import (
    build_coworker_agent_graph,
    mcp_policy_resolver,
    record_runtime_interrupts,
    stream_event_from_interrupt,
)
from coworker.workspace import COMMAND_APPROVAL_FILENAME, CommandApprovalStore


class ScriptedChatModel(BaseChatModel):
    """Chat model that replays a fixed script of AI messages."""

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    bound_tools: list[list[str]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        # create_agent binds the (middleware-augmented) tool list; the script
        # is fixed so binding is a no-op that keeps the same instance. The
        # names are recorded so tests can assert what the model was offered.
        names = []
        for tool in tools or []:
            name = getattr(tool, "name", None)
            if name is None and isinstance(tool, dict):
                name = tool.get("name") or (tool.get("function") or {}).get("name")
            if name:
                names.append(str(name))
        self.bound_tools.append(names)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def ai_tool_call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}],
    )


def build_graph(
    sessions: Any,
    tmp_path: Path,
    script: list[AIMessage],
    approval_store: CommandApprovalStore | None = None,
) -> tuple[Any, CommandApprovalStore, ScriptedChatModel]:
    store = approval_store or CommandApprovalStore(tmp_path / COMMAND_APPROVAL_FILENAME)
    model = ScriptedChatModel(script=list(script), calls=[], bound_tools=[])
    graph = build_coworker_agent_graph(
        llm=model,
        tools=[],
        work_mode="build",
        language="en",
        autonomy="supervised",
        checkpointer=InMemorySaver(),
        approval_store=store,
        data_dir=tmp_path,
        mcp_session_manager=sessions,
    )
    return graph, store, model


def run(graph: Any, state: dict[str, Any], thread: str) -> dict[str, Any]:
    return graph.invoke(state, config={"configurable": {"thread_id": thread}})


def execute_state(text: str = "go", autonomy: str = "supervised") -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=text)],
        "phase": "execute",
        "autonomy": autonomy,
        "work_mode": "build",
        "language": "en",
    }


def interrupts_of(result: dict[str, Any]) -> list[Any]:
    return list(result.get("__interrupt__") or [])


def tool_messages(result: dict[str, Any]) -> list[BaseMessage]:
    return [m for m in result["messages"] if m.__class__.__name__ == "ToolMessage"]


# ── the destructive tool must interrupt, and resume must execute it ─────────


def test_destructive_mcp_tool_interrupts_then_executes(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")]
    )

    first = run(graph, execute_state(), "t-wipe")
    pending = interrupts_of(first)
    assert pending, "destructive MCP tool must raise a HITL interrupt in supervised mode"

    resumed = graph.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config={"configurable": {"thread_id": "t-wipe"}},
    )
    assert not interrupts_of(resumed)
    texts = [str(m.content) for m in tool_messages(resumed)]
    assert any("wiped:db" in t for t in texts), texts


def test_reject_decision_skips_execution(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")]
    )

    run(graph, execute_state(), "t-reject")
    resumed = graph.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "no"}]}),
        config={"configurable": {"thread_id": "t-reject"}},
    )
    texts = [str(m.content) for m in tool_messages(resumed)]
    assert not any("wiped:db" in t for t in texts), texts


# ── the risk ladder, exercised through the compiled graph ───────────────────


def test_read_only_mcp_tool_runs_without_interrupt(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("peek", {"text": "hi"}), AIMessage(content="ok")]
    )

    result = run(graph, execute_state(), "t-peek")
    assert not interrupts_of(result), "read-only MCP tools must never prompt"
    texts = [str(m.content) for m in tool_messages(result)]
    assert any("peek:hi" in t for t in texts), texts


def test_undeclared_tool_interrupts_in_supervised(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("add", {"a": 1, "b": 2}), AIMessage(content="ok")]
    )
    assert interrupts_of(run(graph, execute_state(), "t-add-sup"))


def test_undeclared_tool_auto_runs_in_guarded(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("add", {"a": 1, "b": 2}), AIMessage(content="ok")]
    )

    result = run(graph, execute_state(autonomy="guarded"), "t-add-guarded")
    assert not interrupts_of(result)
    assert any("3" in str(m.content) for m in tool_messages(result))


def test_destructive_tool_still_interrupts_in_guarded(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")]
    )
    assert interrupts_of(run(graph, execute_state(autonomy="guarded"), "t-wipe-guarded"))


def test_autonomous_never_interrupts(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")]
    )

    result = run(graph, execute_state(autonomy="autonomous"), "t-wipe-auto")
    assert not interrupts_of(result)
    assert any("wiped:db" in str(m.content) for m in tool_messages(result))


# ── interrupt payload -> stream event, through the real graph ───────────────


def test_graph_interrupt_maps_to_mcp_stream_event(single_server, tmp_path: Path):
    sessions, server_id, _marker = single_server
    graph, store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")]
    )

    pending = interrupts_of(run(graph, execute_state(), "t-event"))
    approvals = record_runtime_interrupts(
        pending,
        store,
        {"session_id": "s1"},
        mcp_policy_resolver(sessions),
    )
    assert approvals, "the interrupt must be recorded as an approval request"

    event = stream_event_from_interrupt(approvals[0])
    assert event["type"] == "approval_required"
    assert event["kind"] == "mcp"
    assert event["tool_name"] == "wipe"
    assert event["tool_args"] == {"target": "db"}
    assert event["server_id"] == server_id
    assert event["remote_name"] == "wipe"
    assert event["destructive"] is True
    assert event["read_only"] is False
    assert event["command"] == []


def test_always_allow_digest_suppresses_next_prompt(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    store = CommandApprovalStore(tmp_path / COMMAND_APPROVAL_FILENAME)

    graph_a, _s, _m = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "db"}), AIMessage(content="ok")], store
    )
    pending = interrupts_of(run(graph_a, execute_state(), "t-always-1"))
    approvals = record_runtime_interrupts(pending, store, {}, mcp_policy_resolver(sessions))
    digest = approvals[0]["context"]["mcp"]["digest"]
    store.always_allow(digest)

    graph_b, _s2, _m2 = build_graph(
        sessions, tmp_path, [ai_tool_call("wipe", {"target": "x"}), AIMessage(content="ok")], store
    )
    result = run(graph_b, execute_state(), "t-always-2")
    assert not interrupts_of(result), "an always-allowed MCP tool must not prompt again"
    assert any("wiped:x" in str(m.content) for m in tool_messages(result))

    # ...and the allowance is scoped to that one tool, not the whole server.
    graph_c, _s3, _m3 = build_graph(
        sessions, tmp_path, [ai_tool_call("add", {"a": 1, "b": 1}), AIMessage(content="ok")], store
    )
    assert interrupts_of(run(graph_c, execute_state(), "t-always-3"))


# ── discuss phase hides MCP tools entirely ──────────────────────────────────


def test_discuss_phase_hides_mcp_tools(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, model = build_graph(sessions, tmp_path, [AIMessage(content="thinking")])

    state = execute_state()
    state["phase"] = "discuss"
    result = run(graph, state, "t-discuss")
    assert not interrupts_of(result)

    offered = {name for batch in model.bound_tools for name in batch}
    assert not ({"peek", "wipe", "add"} & offered), f"MCP tools leaked into discuss: {offered}"


def test_execute_phase_exposes_mcp_tools(single_server, tmp_path: Path):
    sessions, _server_id, _marker = single_server
    graph, _store, model = build_graph(sessions, tmp_path, [AIMessage(content="ok")])

    run(graph, execute_state(), "t-exposed")
    offered = {name for batch in model.bound_tools for name in batch}
    assert {"peek", "wipe", "add", "alpha_only"} <= offered, offered


# ── the audit log is written for MCP calls ──────────────────────────────────


def test_mcp_call_is_audited(single_server, tmp_path: Path):
    from coworker.workspace import TOOL_AUDIT_FILENAME

    sessions, _server_id, _marker = single_server
    graph, _store, _model = build_graph(
        sessions, tmp_path, [ai_tool_call("peek", {"text": "hi"}), AIMessage(content="ok")]
    )
    run(graph, execute_state(), "t-audit")

    audit = tmp_path / TOOL_AUDIT_FILENAME
    assert audit.exists(), "MCP tool calls must be appended to the audit log"
    body = audit.read_text(encoding="utf-8")
    assert "peek" in body
