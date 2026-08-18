"""Team delegation: run a bounded sub-agent turn for another team member.

The supervisor pattern is implemented via tools (the LangGraph-recommended
approach): the calling agent invokes ``delegate_task`` / ``delegate_parallel``,
which execute a bounded, nested single-turn graph for the target member using
that member's own memory view and a reduced tool set. The target's result is
returned to the caller as plain text; only the top-level agent talks to the
user (single-response principle).

Boundaries enforced here:

- target must be an active member of the project's org;
- target must not be the caller itself;
- hierarchy depth must not exceed ``org.max_depth``;
- a target whose ``role`` hints reviewer/auditor/qa runs read-only;
- nested execution uses a fresh checkpoint thread id so it never pollutes the
  caller's session checkpoint.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from coworker.logger import get_logger
logger = get_logger(__name__)


class DelegationError(ValueError):
    """Raised when a delegation request violates org/boundary rules."""


class Delegator:
    """Executes bounded sub-agent turns for other team members.

    Built by the runtime per request; passed into ``build_workspace_tools`` so
    the ``delegate_*`` tools close over it.
    """

    def __init__(
        self,
        *,
        org_store: Any,
        memory_manager: Any,
        project_store: Any,
        workspace: Any,
        caller_agent: str,
        project_dir: str,
        language: Any,
        work_mode: Any,
        autonomy: Any,
        session_id: str,
        provider_name: str,
        model_name: str,
        llm: Any,
        trace_store: Any,
        approval_store: Any,
        change_store: Any | None = None,
        session_store: Any | None = None,
        data_dir: Any | None = None,
        mcp_session_manager: Any | None = None,
        skill_manager: Any | None = None,
        emit: Any | None = None,  # optional callback for delegation SSE frames
    ):
        self.org_store = org_store
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.workspace = workspace
        self.caller_agent = caller_agent
        self.project_dir = project_dir
        self.language = language
        self.work_mode = work_mode
        self.autonomy = autonomy
        self.session_id = session_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.llm = llm
        self.trace_store = trace_store
        self.approval_store = approval_store
        self.change_store = change_store
        self.session_store = session_store
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.emit = emit or (lambda event: None)
        self._lock = threading.Lock()

    # -- validation --------------------------------------------------------

    def _resolve_target(self, agent: str) -> Any:
        org = self.org_store.load(self.project_dir)
        target = next((a for a in org.agents if a.id == agent), None)
        if target is None:
            raise DelegationError(f"agent {agent!r} is not a member of this project")
        if target.status != "active":
            raise DelegationError(f"agent {agent!r} is not active")
        if agent == self.caller_agent:
            raise DelegationError("cannot delegate to yourself")
        depth = self.org_store.agents_depth(org, agent)
        if depth > org.max_depth:
            raise DelegationError(
                f"agent {agent!r} is at depth {depth}, exceeding max_depth {org.max_depth}"
            )
        return org, target

    @staticmethod
    def _readonly_role(role: str) -> bool:
        hints = ("review", "audit", "qa", "critic", "checker", "审核", "审阅", "检查")
        return any(h in (role or "").lower() for h in hints)

    # -- execution ---------------------------------------------------------

    def delegate(self, agent: str, task: str, context: str = "") -> str:
        """Run one bounded sub-agent turn for ``agent`` and return its result."""
        try:
            org, target = self._resolve_target(agent)
        except DelegationError as exc:
            return f"Delegation rejected: {exc}"
        readonly = self._readonly_role(target.role)
        try:
            self.emit(
                {
                    "type": "delegate_start",
                    "from": self.caller_agent,
                    "to": agent,
                    "task": task[:200],
                }
            )
            result = self._run_sub_turn(target.id, task, context, readonly)
            self.emit(
                {
                    "type": "delegate_end",
                    "from": agent,
                    "to": self.caller_agent,
                    "ok": True,
                    "chars": len(result),
                }
            )
            return result
        except Exception as exc:  # noqa: BLE001 - delegation failure must not kill the caller
            logger.warning("delegate_task failed for %s: %s", agent, exc, exc_info=True)
            self.emit(
                {
                    "type": "delegate_end",
                    "from": agent,
                    "to": self.caller_agent,
                    "ok": False,
                    "error": str(exc)[:200],
                }
            )
            return f"Delegation to {agent} failed: {exc}"

    def delegate_parallel(
        self, tasks: list[dict[str, Any]], max_concurrent: int = 3
    ) -> str:
        """Run several bounded sub-agent turns concurrently; return per-agent results.

        ``tasks`` is a list of ``{agent, task, context}``. Individual failures
        are isolated (that agent's result carries the error); the call itself
        never raises. Concurrency is capped at ``min(max_concurrent, len(tasks))``.
        """
        import asyncio

        try:
            org = self.org_store.load(self.project_dir)
            cap = getattr(org, "max_concurrent", None) or 3
            limit = max(1, min(max_concurrent, cap, len(tasks)))
        except Exception:  # noqa: BLE001
            limit = max(1, min(max_concurrent, len(tasks)))
        if limit < 1:
            return json.dumps({"error": "no tasks"}, ensure_ascii=False)
        try:
            self.emit(
                {
                    "type": "delegate_start",
                    "from": self.caller_agent,
                    "to": [t.get("agent", "") for t in tasks],
                    "parallel": True,
                    "task": "并行委托",
                }
            )
        except Exception:  # noqa: BLE001
            pass

        def _run_one(item: dict[str, Any]) -> dict[str, Any]:
            agent = str(item.get("agent", ""))
            task = str(item.get("task", ""))
            context = str(item.get("context", ""))
            try:
                org, target = self._resolve_target(agent)
                readonly = self._readonly_role(target.role)
                result = self._run_sub_turn(target.id, task, context, readonly)
                self.emit(
                    {
                        "type": "delegate_progress",
                        "from": agent,
                        "to": self.caller_agent,
                        "status": "done",
                        "chars": len(result),
                    }
                )
                return {"agent": agent, "ok": True, "result": result}
            except Exception as exc:  # noqa: BLE001 - per-agent isolation
                logger.warning("delegate_parallel task for %s failed: %s", agent, exc)
                self.emit(
                    {
                        "type": "delegate_progress",
                        "from": agent,
                        "to": self.caller_agent,
                        "status": "error",
                        "error": str(exc)[:200],
                    }
                )
                return {"agent": agent, "ok": False, "error": str(exc)}

        # Run concurrently via a thread pool (the delegate tools are sync).
        from concurrent.futures import ThreadPoolExecutor

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=limit) as pool:
            futures = [pool.submit(_run_one, item) for item in tasks]
            for future in futures:
                results.append(future.result())
        try:
            self.emit(
                {
                    "type": "delegate_end",
                    "from": [t.get("agent", "") for t in tasks],
                    "to": self.caller_agent,
                    "parallel": True,
                    "ok": sum(1 for r in results if r.get("ok")),
                    "failed": [r["agent"] for r in results if not r.get("ok")],
                }
            )
        except Exception:  # noqa: BLE001
            pass
        return json.dumps(results, ensure_ascii=False)

    # -- team creation -----------------------------------------------------

    def create_agent(self, name: str, role: str, description: str, superior: str) -> str:
        """Create a new team member (subject to org.allow_agent_creation)."""
        from .memory.memory_manager import DEFAULT_AGENT
        from .org import AGENT_STATUS_ACTIVE, OrgAgent

        org = self.org_store.load(self.project_dir)
        if not getattr(org, "allow_agent_creation", True):
            return "项目已禁止 agent 自建成员（allow_agent_creation=false）"
        if any(a.id == name for a in org.agents):
            return f"agent {name!r} already exists"
        if superior and superior not in {a.id for a in org.agents}:
            return f"superior {superior!r} is not a member"
        try:
            self.org_store.upsert_agent(
                self.project_dir,
                OrgAgent(
                    id=name, name=name, role=role, description=description,
                    parent=superior or "", team_id="",
                    status=AGENT_STATUS_ACTIVE,
                ),
            )
            from .memory.registry import MemoryRegistry

            registry = MemoryRegistry(self.memory_manager.data_dir)
            project_path = registry.ensure_project(self.project_dir)
            registry.ensure_agent(project_path, name)
            return f"Created team member {name!r} (role: {role})"
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_team_member failed: %s", exc, exc_info=True)
            return f"Failed to create member: {exc}"

    def create_team(self, team_id: str, name: str, lead: str, parent_team_id: str) -> str:
        """Create a new team/department (subject to org.allow_agent_creation)."""
        from .org import AGENT_STATUS_ACTIVE, OrgTeam

        org = self.org_store.load(self.project_dir)
        if not getattr(org, "allow_agent_creation", True):
            return "项目已禁止 agent 自建团队（allow_agent_creation=false）"
        if any(t.id == team_id for t in org.teams):
            return f"team {team_id!r} already exists"
        if lead and lead not in {a.id for a in org.agents}:
            return f"lead {lead!r} is not a member"
        if parent_team_id and parent_team_id not in {t.id for t in org.teams}:
            return f"parent team {parent_team_id!r} does not exist"
        try:
            self.org_store.upsert_team(
                self.project_dir,
                OrgTeam(
                    id=team_id, name=name, lead=lead,
                    parent_team_id=parent_team_id, status=AGENT_STATUS_ACTIVE,
                ),
            )
            from .memory.registry import MemoryRegistry

            registry = MemoryRegistry(self.memory_manager.data_dir)
            registry.ensure_project(self.project_dir)
            team_dir = self.memory_manager.root / self.project_dir / "teams" / team_id
            team_dir.mkdir(parents=True, exist_ok=True)
            for fname in ("GOALS.md", "CONTEXT.md", "MEMORY.md"):
                path = team_dir / fname
                if not path.exists():
                    path.write_text(f"# {fname}\n\n（{name} 部门记忆）\n", encoding="utf-8")
            return f"Created team {name!r} (id: {team_id})"
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_team failed: %s", exc, exc_info=True)
            return f"Failed to create team: {exc}"

    # -- nested graph ------------------------------------------------------

    def _run_sub_turn(self, agent: str, task: str, context: str, readonly: bool) -> str:
        """Build and run a bounded nested graph for ``agent``; return final text."""
        from .agents import (
            agent_run_config,
            build_coworker_agent_graph,
            build_workspace_tools,
            coerce_message_content,
            prepare_agent_messages,
        )
        from .memory.memory_manager import DEFAULT_AGENT
        from .org import AGENT_STATUS_ACTIVE

        _ = DEFAULT_AGENT  # (referenced for clarity in the thread id below)

        project_dir = self.project_dir or ""
        view = self.memory_manager.for_project(project_dir, agent)
        memory_store = getattr(view, "store", None)
        memory_rel = f"{project_dir}/{agent}/BASE/MEMORY.md" if project_dir else ""
        org = self.org_store.load(project_dir) if project_dir else None
        depth = self.org_store.agents_depth(org, agent) if org else 1
        max_depth = getattr(org, "max_depth", 3) if org else 3
        # Sub-agents may themselves delegate only while depth allows.
        allow_delegate = depth < max_depth

        audit_context = {
            "session_id": f"{self.session_id}::delegate::{agent}",
            "provider": self.provider_name,
            "provider_id": self.provider_name,
            "model": self.model_name,
            "workspace_path": str(self.workspace.root),
            "delegated_to": agent,
        }
        tools = build_workspace_tools(
            self.workspace,
            audit_context,
            change_store=self.change_store,
            turn_index=1,
            session_store=self.session_store,
            referenced_sessions=set(),
            skill_manager=self.skill_manager,
            memory_store=memory_store,
            memory_rel=memory_rel,
            delegator=self if allow_delegate else None,
            caller_agent=agent,
            readonly=readonly,
        )
        graph = build_coworker_agent_graph(
            self.llm,
            tools,
            work_mode=self.work_mode,
            language=self.language,
            autonomy="autonomous" if readonly else self.autonomy,
            checkpointer=None,
            approval_store=self.approval_store,
            data_dir=self.data_dir,
            mcp_session_manager=self.mcp_session_manager,
            skill_manager=self.skill_manager,
            memory_manager=view,
            workspace=self.workspace,
        )
        hierarchy = self._hierarchy_prompt(agent)
        messages = prepare_agent_messages(
            [{"role": "user", "content": f"{hierarchy}\n\n任务：{task}\n\n{context}".strip()}]
        )
        result = graph.invoke(
            {
                "messages": messages,
                "work_mode": self.work_mode,
                "language": self.language,
                "phase": "execute" if not readonly else "discuss",
                "autonomy": self.autonomy,
            },
            config=agent_run_config(
                session_id=f"{self.session_id}::delegate::{agent}::{uuid.uuid4().hex[:8]}",
                provider=self.provider_name,
                model=self.model_name,
                language=self.language,
                work_mode=self.work_mode,
                autonomy=self.autonomy,
                streaming=False,
            ),
        )
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        if "__interrupt__" in result:
            return "（委托任务需要审批，已中止）"
        return coerce_message_content(msgs[-1]) if msgs else "（子 agent 无输出）"

    def _hierarchy_prompt(self, agent: str) -> str:
        """The system-style context block telling the target who it is."""
        org = self.org_store.load(self.project_dir) if self.project_dir else None
        name = agent
        role = ""
        parent = ""
        team = ""
        if org:
            target = next((a for a in org.agents if a.id == agent), None)
            if target:
                name = target.name or agent
                role = target.role
                parent = target.parent or "（用户）"
                if target.team_id:
                    t = next((t for t in org.teams if t.id == target.team_id), None)
                    team = t.name if t else target.team_id
        lines = [
            f"你是 {name}" + (f"（{role}）" if role else ""),
            f"你隶属项目团队" + (f"，部门 {team}" if team else ""),
            f"你的上级/委派者是 {parent}。",
            "你只处理本次委派的任务，完成后将结果汇报给委派者，不要直接向用户汇报。",
            "只在本任务边界内行动，不要越权。",
        ]
        return "\n".join(lines)

