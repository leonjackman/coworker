"""Project dashboard: an aggregate, read-only view of one project's
capabilities, served to the dashboard page.

This is Phase 0 of the roadmap that turns Coworker from a coding agent into an
agent that handles real business tasks: it makes a project's files, agents,
tools and capabilities *visible* before they later become *configurable*.

The static builtin-tool catalog below mirrors the tools assembled at runtime
in ``coworker/agent/graph.py#build_workspace_tools`` and the phase/access
classes in ``coworker/agent/core.py``. The catalog is intentionally display
data, kept in one place so the dashboard and the runtime cannot drift silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .org import ORG_MODE_MULTI, ORG_MODE_SINGLE
from .workspace import workspace_git_branch, workspace_git_diff

ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_EXEC = "exec"
ACCESS_ASK = "ask"

# ── Static catalog of tools a project's agents can call ──────────────────────
# ``mode`` (optional) marks tools that only exist in one agent mode.
BUILTIN_TOOLS: list[dict[str, Any]] = [
    # workspace (always available)
    {"name": "search_files", "description": "Search workspace text files.", "group": "workspace", "access": ACCESS_READ},
    {"name": "read_file", "description": "Read a file with a bounded preview.", "group": "workspace", "access": ACCESS_READ},
    {"name": "read_session", "description": "Read a past session transcript.", "group": "workspace", "access": ACCESS_READ},
    {"name": "git_status", "description": "Show the workspace git status.", "group": "workspace", "access": ACCESS_READ},
    {"name": "run_command_status", "description": "Poll a background command.", "group": "workspace", "access": ACCESS_READ},
    {"name": "write_file", "description": "Create or overwrite a file.", "group": "workspace", "access": ACCESS_WRITE},
    {"name": "replace_in_file", "description": "Apply a targeted text replacement.", "group": "workspace", "access": ACCESS_WRITE},
    {"name": "apply_text_edits", "description": "Apply multiple text edits at once.", "group": "workspace", "access": ACCESS_WRITE},
    {"name": "run_command", "description": "Run an allowlisted shell command.", "group": "workspace", "access": ACCESS_EXEC},
    {"name": "ask_user", "description": "Ask the user a clarifying question.", "group": "workspace", "access": ACCESS_ASK},
    # memory
    {"name": "memory", "description": "Write durable notes to agent memory.", "group": "memory", "access": ACCESS_WRITE},
    {"name": "memory_read", "description": "Read agent memory notes.", "group": "memory", "access": ACCESS_READ},
    # team (multi-agent mode only)
    {"name": "delegate_task", "description": "Delegate a task to a team member.", "group": "team", "access": ACCESS_EXEC, "mode": ORG_MODE_MULTI},
    {"name": "delegate_parallel", "description": "Delegate to several members in parallel.", "group": "team", "access": ACCESS_EXEC, "mode": ORG_MODE_MULTI},
    {"name": "create_team_member", "description": "Recruit a new team member.", "group": "team", "access": ACCESS_EXEC, "mode": ORG_MODE_MULTI},
    {"name": "create_team", "description": "Form a new team under the org.", "group": "team", "access": ACCESS_EXEC, "mode": ORG_MODE_MULTI},
    # worker (single-agent mode only)
    {"name": "use_worker", "description": "Spawn a bounded research worker.", "group": "worker", "access": ACCESS_EXEC, "mode": ORG_MODE_SINGLE},
    {"name": "use_workers", "description": "Spawn parallel bounded research workers.", "group": "worker", "access": ACCESS_EXEC, "mode": ORG_MODE_SINGLE},
    # web
    {"name": "web_search", "description": "Search the web.", "group": "web", "access": ACCESS_READ},
    {"name": "web_fetch", "description": "Fetch a web page.", "group": "web", "access": ACCESS_READ},
    # browser
    {"name": "browser", "description": "Drive the desktop browser when connected.", "group": "browser", "access": ACCESS_EXEC},
    # goal (session-scoped)
    {"name": "get_goal", "description": "Read the active session goal.", "group": "goal", "access": ACCESS_READ},
    {"name": "update_goal", "description": "Update the active session goal.", "group": "goal", "access": ACCESS_WRITE},
]


def _agent_card(
    *,
    agent_id: str,
    name: str,
    role: str,
    team: str,
    status: str,
    session_count: int,
    is_default: bool,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": name,
        "role": role,
        "team": team,
        "status": status,
        "session_count": session_count,
        "is_default": is_default,
    }


def build_dashboard_data(
    *,
    project_id: str,
    workspace_controller: Any,
    session_store: Any,
    org_store: Any,
    mcp_manager: Any,
    skill_manager: Any,
    settings: Any,
    default_agent_id: str = "default_agent",
) -> dict[str, Any]:
    """Assemble the dashboard bundle for one project (single HTTP payload).

    Raises ``KeyError`` for an unknown project and ``ValueError`` for a project
    without a usable workspace.
    """
    sessions = session_store.list_sessions(project_id)
    project = workspace_controller.public_project(project_id, len(sessions))

    # ── git status (best-effort; a missing/empty workspace yields no repo) ──
    git: dict[str, Any] = {"git": False, "branch": None, "note": "workspace unavailable"}
    if project.get("workspace_available"):
        try:
            workspace = workspace_controller.workspace_for_project(project_id)
            git = workspace_git_branch(workspace.root)
            diff = workspace_git_diff(workspace.root)
            git.update(
                {
                    "files": diff.get("files", []),
                    "untracked": diff.get("untracked", []),
                    "truncated_diff": diff.get("truncated_diff", False),
                    "note": diff.get("note", ""),
                }
            )
        except (KeyError, ValueError):
            pass

    # ── agents (org roster; single mode falls back to the default agent) ──
    mode = project.get("mode") or ORG_MODE_SINGLE
    sessions_by_agent: dict[str, int] = {}
    for session in sessions:
        agent_id = session.get("agent_id") or default_agent_id
        sessions_by_agent[agent_id] = sessions_by_agent.get(agent_id, 0) + 1

    agents: list[dict[str, Any]] = []
    roster = project.get("roster") or []
    if mode == ORG_MODE_MULTI:
        for entry in roster:
            agent_id = entry.get("id") or default_agent_id
            agents.append(
                _agent_card(
                    agent_id=agent_id,
                    name=entry.get("name") or agent_id,
                    role=entry.get("role") or "",
                    team=entry.get("team") or "",
                    status=entry.get("status") or "active",
                    session_count=sessions_by_agent.get(agent_id, 0),
                    is_default=agent_id == default_agent_id,
                )
            )
    else:
        agents.append(
            _agent_card(
                agent_id=default_agent_id,
                name=default_agent_id,
                role="",
                team="",
                status="active",
                session_count=len(sessions),
                is_default=True,
            )
        )

    # ── capabilities (which optional tool groups are live for this project) ──
    browser_enabled = False
    try:
        from .browser.bridge_client import read_browser_bridge

        browser_enabled = read_browser_bridge(settings.data_dir) is not None
    except Exception:  # noqa: BLE001 - capability probe must never fail the dashboard
        browser_enabled = False
    web_enabled = False
    try:
        from .web import tavily_key_configured

        web_enabled = tavily_key_configured(settings.data_dir)
    except Exception:  # noqa: BLE001
        web_enabled = False
    capabilities = {
        "mode": mode,
        "memory_enabled": bool(getattr(settings, "memory_enabled", True)),
        "web_enabled": web_enabled,
        "browser_enabled": browser_enabled,
    }

    # ── tool catalog filtered by the project's mode ─────────────────────────
    tools = [tool for tool in BUILTIN_TOOLS if tool.get("mode") in (None, mode)]

    # ── mcp servers + skills (global catalogs the project can reach) ────────
    try:
        mcp_servers = mcp_manager.list_servers()
    except Exception:  # noqa: BLE001
        mcp_servers = []
    try:
        skills = [skill.to_dict() for skill in skill_manager.list()]
    except Exception:  # noqa: BLE001
        skills = []

    return {
        "status": "ok",
        "project": project,
        "git": git,
        "agents": agents,
        "capabilities": capabilities,
        "tools": {"builtin": tools, "mcp_servers": mcp_servers, "skills": skills},
        "sessions": sessions[-20:],
    }
