"""MCP middleware for the Coworker agent graph.

Attaches dispatch tools from the persistent :class:`McpSessionManager` to the
model call, registers them with the agent's ``ToolNode`` when they are already
loaded, and dynamically resolves MCP tool calls at execution time (the pattern
the langchain agent factory documents for middleware-added tools).

MCP tools are only exposed in the **execute** phase. In ``discuss`` (plan
mode) they are hidden entirely, matching the product decision that planning is
read-only and never touches external services.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from .mcp_session import McpSessionManager

from coworker.logger import get_logger
logger = get_logger(__name__)

# Cap on how much of a tool result is copied into the audit trail.
_AUDIT_PREVIEW_CHARS = 400


def _phase_is_discuss(state: Any) -> bool:
    """Mirror of ``agents.normalize_phase`` (avoid a circular import)."""
    phase = str((state or {}).get("phase") or "")
    if phase in ("discuss", "execute"):
        return phase == "discuss"
    work_mode = str((state or {}).get("work_mode") or "build")
    return work_mode == "plan"


def _tool_name(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


class McpToolMiddleware(AgentMiddleware):

    def __init__(self, session_manager: McpSessionManager, audit_path: Path | None = None):
        self.mcp_manager = session_manager.mcp_manager
        self.session_manager = session_manager
        self.audit_path = Path(audit_path) if audit_path is not None else None
        # Opportunistic ToolNode registration: populated once sessions are
        # connected. Execution does not depend on this -- the dynamic
        # ``wrap_tool_call`` path below resolves unregistered MCP tools.
        self.tools: list[Any] = list(session_manager.all_tools())
        self._servers: list[dict[str, Any]] = []

    def tool_policy(self, name: str) -> dict[str, Any] | None:
        """Approval metadata for an MCP tool name (``None`` for builtins)."""
        try:
            return self.session_manager.tool_policy(name)
        except Exception:  # noqa: BLE001 - approval checks must never crash a run
            logger.debug("MCP tool policy lookup failed for %r", name)
            return None

    def _refresh_tools(self) -> None:
        self._servers = []
        try:
            self._servers = self.mcp_manager.list_runtime_configs(enabled_only=True)
        except Exception as exc:  # noqa: BLE001 - config problems must not break chat
            logger.warning("Failed to read MCP config: %s", exc)
        self.tools = list(self.session_manager.all_tools())

    def tool_names(self) -> set[str]:
        """Names of currently-connected MCP tools (for the phase gate)."""
        return self.session_manager.tool_names()

    def _server_names(self) -> dict[str, str]:
        return {
            str(server.get("id") or "").strip(): str(server.get("name") or server.get("id") or "")
            for server in getattr(self, "_servers", [])
            if str(server.get("id") or "").strip()
        }

    def _mcp_summary(self) -> str | None:
        """One line per enabled server with the tools the model can call."""
        tools = self.tools
        if not tools:
            return None
        names = self._server_names()
        if not names:
            return None

        by_server: dict[str, list[str]] = {}
        for tool in tools:
            metadata = getattr(tool, "metadata", None)
            server_id = metadata.get("coworker_server") if isinstance(metadata, dict) else None
            tool_name = getattr(tool, "name", None)
            if server_id and tool_name:
                by_server.setdefault(str(server_id), []).append(str(tool_name))

        if not by_server:
            return None

        lines = []
        for server_id, name in names.items():
            server_tools = sorted(by_server.get(server_id) or [])
            if not server_tools:
                continue
            lines.append(f"- {name}: {', '.join(server_tools)}")
        if not lines:
            return None
        try:
            conflicts = self.session_manager.list_conflicts()
        except Exception:  # noqa: BLE001
            conflicts = {}
        if conflicts:
            lines.append(
                "- Note: tools named "
                + ", ".join(sorted(conflicts))
                + " exist on several servers and are exposed with a "
                "`<server>__<tool>` prefix. Use the prefixed name shown above."
            )
        return "\n".join(lines)

    def _overrides(self, request: Any) -> dict[str, Any]:
        logger.info("MCP _overrides START request_id=%s", id(request))
        logger.info("MCP _overrides _connecting=%d _servers=%d", len(self.session_manager._connecting), len(self.session_manager._servers))
        logger.info("MCP _overrides: calling ensure_connected")
        self.session_manager.ensure_connected(enable_browser_flow=False)
        self._refresh_tools()
        tools = self.tools
        if not tools:
            return {}

        if _phase_is_discuss(getattr(request, "state", None)):
            logger.debug("MCP tools hidden in discuss phase")
            return {}

        existing = list(getattr(request, "tools", None) or [])
        taken = {getattr(tool, "name", None) for tool in existing}

        additions = []
        for t in tools:
            name = getattr(t, "name", None)
            if name in taken:
                logger.debug("MCP tool %r already exposed (registered or colliding)", name)
                continue
            taken.add(name)
            additions.append(t)

        if not additions:
            return {}

        # The attribution section is composed by SystemAssembler as a proper
        # fragment (after behaviour/phase/capabilities, hidden in discuss) — the
        # behaviour core must stay FIRST (B2; codex developer-instructions-first,
        # opencode instructions→mcp). Prepending it here pushed it above the
        # behaviour core and diluted it.
        return {"tools": existing + additions}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if overrides:
            return handler(request.override(**overrides))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # Streaming/resume run the graph asynchronously (`astream`/`ainvoke`).
        # The initial MCP connect can block for seconds, so run it off the
        # event loop via `to_thread`; `_overrides` itself is idempotent.
        _t0 = time.monotonic()
        logger.info("MCP awrap_model_call START request_id=%s", id(request))
        overrides = await asyncio.to_thread(self._overrides, request)
        _t1 = time.monotonic()
        logger.info("MCP awrap_model_call to_thread(self._overrides) took %.2fs", _t1 - _t0)
        if overrides:
            return await handler(request.override(**overrides))
        return await handler(request)

    def _resolve_tool(self, request: Any) -> Any:
        name = _tool_name(request)
        if not name:
            return None
        for tool in self.session_manager.all_tools():
            if getattr(tool, "name", None) == name:
                return tool
        return None

    def _prepare(self, request: Any) -> tuple[Any, dict[str, Any] | None]:
        """Resolve an unregistered MCP tool and look up its policy.

        ``request.tool`` is already set for tools the ``ToolNode`` knows about
        (``self.tools`` was populated at graph build time). Tools that
        connected later are not registered, so they are resolved dynamically
        here -- the pattern the langchain agent factory documents for
        middleware-provided tools.
        """
        name = _tool_name(request)
        policy = self.tool_policy(name) if name else None
        if request.tool is None:
            tool = self._resolve_tool(request)
            if tool is not None:
                request = request.override(tool=tool)
        return request, policy

    # ── audit ────────────────────────────────────────────────────────────

    @staticmethod
    def _redact_args(args: Any) -> Any:
        sensitive = ("key", "token", "secret", "password", "authorization", "credential")
        if isinstance(args, dict):
            out: dict[str, Any] = {}
            for key, value in args.items():
                if any(word in str(key).lower() for word in sensitive):
                    out[str(key)] = "***"
                else:
                    out[str(key)] = McpToolMiddleware._redact_args(value)
            return out
        if isinstance(args, list):
            return [McpToolMiddleware._redact_args(item) for item in args]
        if isinstance(args, str) and len(args) > _AUDIT_PREVIEW_CHARS:
            return args[:_AUDIT_PREVIEW_CHARS] + "..."
        return args

    @staticmethod
    def _result_preview(result: Any) -> str:
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        text = str(content or "")
        return text[:_AUDIT_PREVIEW_CHARS] + ("..." if len(text) > _AUDIT_PREVIEW_CHARS else "")

    def _audit(
        self,
        policy: dict[str, Any],
        request: Any,
        status: str,
        started: float,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        """Record one MCP tool call in the shared tool audit trail.

        MCP calls run outside the workspace sandbox, so they are exactly the
        calls that most need a trace. Failures here are swallowed: auditing must
        never change the outcome of the call it is describing.
        """
        if self.audit_path is None:
            return
        try:
            from ..workspace import append_tool_audit

            tool_call = getattr(request, "tool_call", None)
            args = tool_call.get("args") if isinstance(tool_call, dict) else None
            details: dict[str, Any] = {
                "tool": policy.get("tool"),
                "remote_name": policy.get("remote_name"),
                "server_id": policy.get("server_id"),
                "server_name": policy.get("server_name"),
                "read_only": policy.get("read_only"),
                "trusted": policy.get("trusted"),
                "args": self._redact_args(args or {}),
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
            if error is not None:
                details["error"] = str(error)[:_AUDIT_PREVIEW_CHARS]
            elif result is not None:
                details["result_preview"] = self._result_preview(result)
                if getattr(result, "status", None) == "error":
                    status = "error"
            append_tool_audit(self.audit_path, "mcp_tool_call", status, details)
        except Exception:  # noqa: BLE001 - audit is best-effort
            logger.debug("MCP audit write failed for %s", policy.get("tool"))

    # ── execution ────────────────────────────────────────────────────────

    # MCP servers run outside the workspace sandbox and their results were
    # previously UNCAPPED — one chatty server reply could overflow the model
    # context on its own. Same bound as the browser tool output.
    _RESULT_MAX_CHARS = 50_000
    _RESULT_TRUNCATION_NOTE = "\n[content truncated by Coworker to fit context]"

    @classmethod
    def _guard_result(cls, result: Any) -> Any:
        """Bound and scrub an MCP tool result before it enters the context.

        Text parts are scrubbed of base64/data-URL blobs and capped; native
        image blocks ride through untouched (they are counted at vision cost,
        not as text). Non-ToolMessage results pass through unchanged.
        """
        content = getattr(result, "content", None)
        if content is None:
            return result
        try:
            from coworker.context import scrub_text

            if isinstance(content, str):
                scrubbed, _ = scrub_text(content)
                if len(scrubbed) > cls._RESULT_MAX_CHARS:
                    scrubbed = scrubbed[: cls._RESULT_MAX_CHARS] + cls._RESULT_TRUNCATION_NOTE
                if scrubbed != content:
                    return result.model_copy(update={"content": scrubbed})
                return result
            if isinstance(content, list):
                budget = cls._RESULT_MAX_CHARS
                new_content: list[Any] = []
                changed = False
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text") or ""
                        scrubbed, n = scrub_text(text)
                        if len(scrubbed) > budget:
                            scrubbed = scrubbed[:budget] + cls._RESULT_TRUNCATION_NOTE
                        budget = max(0, budget - len(scrubbed))
                        if scrubbed != text:
                            changed = True
                        new_content.append({**part, "text": scrubbed})
                    else:
                        new_content.append(part)
                if changed:
                    return result.model_copy(update={"content": new_content})
                return result
        except Exception:  # noqa: BLE001 - guarding must never break a tool call
            logger.debug("MCP result guard skipped", exc_info=True)
        return result

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        request, policy = self._prepare(request)
        if policy is None:
            return handler(request)
        started = time.monotonic()
        try:
            result = handler(request)
        except BaseException as exc:
            self._audit(policy, request, "error", started, error=exc)
            raise
        self._audit(policy, request, "ok", started, result=result)
        return self._guard_result(result)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        request, policy = self._prepare(request)
        if policy is None:
            return await handler(request)
        started = time.monotonic()
        try:
            result = await handler(request)
        except BaseException as exc:
            self._audit(policy, request, "error", started, error=exc)
            raise
        self._audit(policy, request, "ok", started, result=result)
        return self._guard_result(result)
