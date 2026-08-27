"""Phase-gated tool visibility + tool-call gate middleware.

Splits the old three-role middleware: tool VISIBILITY and the defense-in-depth
tool-call GATE live here; the system-prompt composition moved to
``SystemAssembler`` (single fragment assembler with a total budget). This
matches mainstream architecture — codex (``ToolRouter.model_visible_specs``
visibility + ``ToolOrchestrator`` gating; prompt via context contributors) and
opencode (``SessionTools.resolve`` visibility + ``permission.ask`` gating;
prompt via ``SystemPrompt`` service).
"""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import ToolMessage

from ...logger import get_logger
from ..core import (
    CoworkerAgentState,
    _CHANGE_TOOL_NAMES,
    _EXEC_TOOLS,
    _MEMORY_TOOLS,
    _PLAN_TOOLS,
    _READ_ONLY_TOOLS,
    normalize_autonomy,
    normalize_phase,
    normalize_work_mode,
)

logger = get_logger(__name__)


class PhaseToolGateMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Dynamic tool selection based on the agent's phase and autonomy.

    Uses the official ``wrap_model_call`` + ``request.override`` pattern so the
    model only ever sees the tools the current phase allows:

    * ``discuss`` (plan mode before approval): read-only + ``ask_user`` +
      the agent cannot touch the filesystem, but can still write long-term
      memory (the ``memory`` tool) — durable facts belong in planning too.
    * ``execute``: read + write + ``run_command``; ``ask_user`` stays available
      unless autonomy is ``autonomous`` (physical removal — the model cannot
      interrupt the user at all in full-autonomy mode).

    The phase/autonomy-aware system prompt is composed by ``SystemAssembler``.

    MCP tools are treated as execute-phase tools: they are allowed (and only
    visible) while ``phase == "execute"``. A provider callable supplies the
    currently-connected MCP tool names so the gate can tell them apart from
    unknown tool calls without coupling this module to the MCP layer.
    """

    def __init__(self, mcp_tool_names_provider: Callable[[], set[str]] | None = None, workspace: Any | None = None):
        self.mcp_tool_names_provider = mcp_tool_names_provider
        self.workspace = workspace

    def _allowed_tools(self, state: CoworkerAgentState) -> set[str]:
        work_mode = normalize_work_mode(state.get("work_mode"))
        phase = normalize_phase(state.get("phase"), work_mode)
        autonomy = normalize_autonomy(state.get("autonomy"))
        allowed = set(_READ_ONLY_TOOLS) | _MEMORY_TOOLS
        if phase == "discuss":
            allowed |= _PLAN_TOOLS
            # use_worker 在讨论（只读）阶段也开放：worker 以只读模式运行（与主
            # agent 一致），专注研究/分析，不改动文件系统。
            allowed |= {"use_worker"}
        else:
            allowed |= _CHANGE_TOOL_NAMES | _EXEC_TOOLS
            if autonomy != "autonomous":
                allowed |= {"ask_user"}
            if self.mcp_tool_names_provider is not None:
                try:
                    allowed |= self.mcp_tool_names_provider()
                except Exception:  # noqa: BLE001 - a broken provider must not gate tools
                    pass
        # Task-list management is available in EVERY phase and mode (build/plan/
        # chat): write_todos only writes graph state, never files, so it stays
        # safe in the read-only discuss phase too.
        allowed.add("write_todos")
        return allowed

    def _overrides(self, request: Any) -> dict[str, Any]:
        state = request.state
        allowed = self._allowed_tools(state)
        tools = [tool for tool in request.tools if getattr(tool, "name", "") in allowed]
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        # 记录当前 phase，供 use_worker 工具在执行时判断 worker 是否只读。
        if self.workspace is not None:
            try:
                setattr(self.workspace, "_current_phase", phase)
            except Exception:  # noqa: BLE001 - phase tracking must never gate tools
                pass
        # Visibility only: the phase-filtered schemas are what the model sees.
        # The system prompt (workspace/phase/memory/skills) is composed by
        # SystemAssembler — no duplicate tool catalogue here (mainstream).
        return {"tools": tools}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request.override(**self._overrides(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(**self._overrides(request)))

    def _tool_name(self, request: Any) -> str:
        tool_call = getattr(request, "tool_call", None)
        if isinstance(tool_call, dict):
            return str(tool_call.get("name") or "")
        return str(getattr(tool_call, "name", "") or "")

    def _blocked_tool_message(self, request: Any) -> Any:
        tool_name = self._tool_name(request)
        registered: set[str] = set()
        if self.workspace is not None:
            try:
                registered = set(getattr(self.workspace, "_registered_tool_names", set()) or set())
            except Exception:  # noqa: BLE001 - a broken registry must not crash a turn
                registered = set()
        if registered and tool_name and tool_name not in registered:
            # The model hallucinated a tool name (e.g. list_directory). Tell it the
            # tool does not exist and list what IS available so it can self-correct
            # instead of believing the tool is phase-gated and retrying in vain.
            names = sorted(registered)
            sample = ", ".join(names[:12])
            more = f" … +{len(names) - 12} more" if len(names) > 12 else ""
            content = (
                f"Tool '{tool_name}' does not exist. Only these tools are available: "
                f"{sample}{more}. Use the exact tool names above; do not invent tools."
            )
        else:
            content = (
                f"Tool '{tool_name}' is not available in the current phase/autonomy. "
                "It was skipped."
            )
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call.get("id", "unknown"),
            status="error",
        )

    def _outside_scope(self, request: Any) -> bool:
        tool_name = self._tool_name(request)
        if not tool_name:
            return False
        return tool_name not in self._allowed_tools(request.state)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        # Defense in depth: a tool call that the current phase/autonomy does not
        # allow must never run. Resolve it with an error ToolMessage so the call
        # is closed (avoids a dangling tool_call without a ToolMessage in the
        # checkpoint history, which providers reject on the next turn).
        if self._outside_scope(request):
            return self._blocked_tool_message(request)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self._outside_scope(request):
            return self._blocked_tool_message(request)
        return await handler(request)
