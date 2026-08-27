"""Agent tool set and graph builder.

Extracted from the former monolithic ``coworker/agents.py``:

* :func:`build_workspace_tools` — the full workspace tool set (search/read/
  write/command/skill/memory/session/delegation/worker tools);
* :func:`build_coworker_agent_graph` — compiles the single ``create_agent``
  graph with the middleware chain.

Depends on ``agent.core`` (args models / shared helpers), ``agent.middleware``,
``agent.prompts`` and ``agent.model_defaults`` — never on ``agent.runtime``.
"""

import json
from pathlib import Path
from typing import Any

from langchain.agents.middleware import ClearToolUsesEdit, ContextEditingMiddleware

from ..changes import ChangeStore
from ..goal_feature import goal_feature
from ..logger import get_logger
from ..mcp.mcp import McpManager
from ..sessions import SessionStore
from ..workspace import (
    READ_FILE_MAX_CHARS,
    TOOL_AUDIT_FILENAME,
    CommandApprovalStore,
    Workspace,
    workspace_git_branch,
    workspace_git_diff,
)
from .core import (
    Autonomy,
    Language,
    MAX_REFERENCE_SESSION_CHARS,
    WorkMode,
    AskUserArgs,
    AskUserOption,
    ApplyTextEditsArgs,
    CreateTeamArgs,
    CreateTeamMemberArgs,
    CoworkerAgentState,
    DelegateParallelArgs,
    DelegateTaskArgs,
    DelegateTaskItem,
    GetGoalArgs,
    GitStatusArgs,
    InstallSkillArgs,
    LoadSkillArgs,
    ManageGoalArgs,
    MemoryArgs,
    MemoryReadArgs,
    ReadFileArgs,
    ReadSessionArgs,
    ReplaceInFileArgs,
    RunCommandArgs,
    SearchFilesArgs,
    TextEditArgs,
    WriteFileArgs,
    _CHILD_EXCLUDED_TOOLS,
    _WRITE_ARG_PATH_KEYS,
    _content_chars,
    _looks_like_raw_paste,
    _resolve_memory_target,
    context_budget_tokens,
    format_user_message,
)
from .middleware import (
    ContextGuardMiddleware,
    CoworkerSummarizationMiddleware,
    NormalizeMessagesMiddleware,
    PhaseToolGateMiddleware,
    RepeatedToolCallMiddleware,
    StallRetryMiddleware,
    SteerInjectionMiddleware,
    ToolCallCleanerMiddleware,
    _summarizer_candidates,
    command_approval_middleware,
)
logger = get_logger(__name__)


def build_workspace_tools(
    workspace: Workspace,
    audit_context: dict[str, Any] | None = None,
    change_store: ChangeStore | None = None,
    turn_index: int = 1,
    session_store: SessionStore | None = None,
    referenced_sessions: set[str] | None = None,
    skill_manager: Any | None = None,
    skill_market_manager: Any | None = None,
    memory_store: Any | None = None,
    memory_rel: str = "",
    delegator: Any | None = None,
    caller_agent: str = "",
    readonly: bool = False,
    web_tools: list | None = None,
    browser_tool: Any | None = None,
    # WorkerAgent 集成（单 agent 模式）
    use_worker_enabled: bool = False,
    language: str = "zh",
    max_concurrent: int = 4,
    worker_llm: Any | None = None,
    worker_session_id: str = "",
    worker_work_mode: str = "build",
    worker_autonomy: str = "guarded",
    worker_provider_name: str = "",
    worker_approval_store: Any | None = None,
    worker_data_dir: Any | None = None,
    worker_mcp_session_manager: Any | None = None,
    delegation_emit: Any | None = None,  # optional callback for use_worker SSE frames
    worker_bus: Any | None = None,  # WorkerEventBus for worker sub-agent internal streams
    worker_context_window_tokens: int = 0,
    worker_max_output_tokens: int = 0,
    worker_calibration_key: str = "",
    session_id: str = "",
    goal_emit: Any | None = None,  # optional callback for goal_updated SSE frames
) -> list[Any]:
    from pathlib import Path as _Path

    from langchain_core.tools import tool

    def _error_result(error: Exception, operation: str) -> str:
        details = {"error": str(error)[:500], "operation": operation}
        return json.dumps(details, ensure_ascii=False)

    @tool(args_schema=SearchFilesArgs)
    def search_files(query: str, path: str = "", max_results: int = 80) -> str:
        """Search UTF-8 workspace text files."""
        try:
            result = workspace.search_text(query, path, max_results)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "search_files")

    @tool(args_schema=ReadFileArgs)
    def read_file(file_path: str, offset: int = 1, limit: int = 200) -> str:
        """Read a text file from the workspace (binary files return a hint; large
        files are read as a bounded line window so a single call never floods the
        model context). Use the returned ``next_offset``/``hint`` to page further
        into a large file instead of dumping the whole thing at once."""
        try:
            preview = workspace.read_preview(
                file_path,
                max_chars=READ_FILE_MAX_CHARS,
                offset=offset,
                limit=limit,
            )
            if preview.get("binary"):
                return json.dumps(
                    {
                        "binary": True,
                        "size": preview.get("size", 0),
                        "hint": "Binary file — open it in the file panel; its raw bytes are not readable as text.",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(preview, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "read_file")

    @tool(args_schema=WriteFileArgs)
    def write_file(file_path: str, content: str) -> str:
        """Write a full UTF-8 text file."""
        try:
            workspace.write_text(file_path, content, audit_context, change_store, turn_index)
            return f"Wrote {file_path}"
        except Exception as exc:
            return _error_result(exc, "write_file")

    @tool(args_schema=ReplaceInFileArgs)
    def replace_in_file(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        """Replace exact text in a UTF-8 workspace file."""
        try:
            result = workspace.replace_text(file_path, old_text, new_text, replace_all, audit_context, change_store, turn_index)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "replace_in_file")

    @tool(args_schema=ApplyTextEditsArgs)
    def apply_text_edits(file_path: str, edits: list[TextEditArgs]) -> str:
        """Apply multiple exact text edits to one UTF-8 workspace file atomically."""
        try:
            result = workspace.apply_text_edits(
                file_path,
                [edit.model_dump() if isinstance(edit, TextEditArgs) else edit for edit in edits],
                audit_context,
                change_store,
                turn_index,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "apply_text_edits")

    @tool(args_schema=RunCommandArgs)
    def run_command(command: str | list[str], cwd: str = "", timeout_seconds: int = 20) -> str:
        """Run an allowlisted command in the workspace after runtime policy approval.

        The result is JSON with ``return_code`` (0 = success), ``stdout``,
        ``stderr`` and ``timed_out``. A non-zero ``return_code`` means the
        command FAILED — never blindly re-run the exact same command; adjust
        the path/scope first or use a different tool. Note that searches can
        report "Permission denied" for unreadable directories even when the
        search itself worked: narrow the search path instead of retrying the
        whole tree with the same command.

        ``command`` may be an argv array or a plain shell string — the backend
        normalizes strings into argv (shlex) automatically.
        """
        import shlex as _shlex

        if isinstance(command, str):
            try:
                command = _shlex.split(command)
            except ValueError:
                command = [command]
        try:
            # Runtime policy approval (HITL) is owned by HumanInTheLoopMiddleware;
            # this tool call is not the sync bottom-panel approval flow.
            result = workspace.run_command(command, cwd, timeout_seconds, audit_context)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "run_command")

    # Platform-aware command vocabulary: tell the model which OS it is on and
    # which commands are actually allowlisted, so it stops guessing `ls` on
    # Windows or `dir` on Unix.
    from .. import platform as _platform

    run_command.description = (
        run_command.description
        + "\n\n"
        + _platform.platform_hint()
        + " "
        + _platform.command_hint()
    )

    @tool(args_schema=InstallSkillArgs)
    def install_skill(name: str, content: str, commands: list[dict[str, str]] | None = None) -> str:
        """Install a NEW skill by writing its SKILL.md directly into the user skills
        directory (~/.agents/skills/<name>/SKILL.md) and refreshing the catalog.

        Use this (and ONLY this) to add a brand-new skill from chat — do NOT use
        write_file or run_command for that, because the install path lives outside
        the workspace sandbox and those tools will be denied. The provided ``content``
        must be the complete SKILL.md text including a YAML frontmatter with ``name``
        and ``description``. When ``commands`` is provided, each sub-command is written
        to commands/<name>.md and listed in the root frontmatter, so the skill exposes
        direct /<command> entries. On success the skill becomes available immediately:
        it shows up in the settings "Installed Skills" list and as a ``/skill <name>``
        command (or ``/<command>`` when it declares sub-commands).
        """
        try:
            if skill_market_manager is None:
                from ..skills.skill_market import SkillMarketManager

                mkt = SkillMarketManager(_Path.home())
            else:
                mkt = skill_market_manager
            result = mkt.install_from_content(name, content, commands=commands)
            if result.get("status") == "ok" and skill_manager is not None:
                try:
                    skill_manager.refresh()
                except Exception:  # refresh must not mask the install result
                    logger.exception("Failed to refresh skill catalog after install")
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "install_skill")

    @tool(args_schema=LoadSkillArgs)
    def load_skill(name: str) -> str:
        """Load a skill's SKILL.md body so you can follow its instructions.

        Only the skill catalog (name + description) is in context by default;
        skill bodies live outside the workspace sandbox, so read_file cannot
        load them. Use this tool with the exact skill <name> from <available_skills>.
        """
        try:
            if skill_manager is None:
                return _error_result(ValueError("skill system unavailable"), "load_skill")
            loaded = skill_manager.read_body(name)
            if loaded is None:
                return json.dumps({"status": "error", "error": f"skill not found: {name}"}, ensure_ascii=False)
            body, base_dir = loaded
            if body and len(body) > 120_000:
                body = body[:120_000] + "\n[truncated]"
            cmds = []
            skill = skill_manager.get(name)
            if skill is not None:
                cmds = [c.name for c in skill.commands]
            return json.dumps(
                {"status": "ok", "name": name, "base_dir": base_dir, "commands": cmds, "body": body},
                ensure_ascii=False,
            )
        except Exception as exc:
            return _error_result(exc, "load_skill")

    @tool(args_schema=GitStatusArgs)
    def git_status() -> str:
        """Inspect the workspace git repository: current branch, working-tree
        status (modified/untracked files) and a diff summary. Returns
        ``{"git": false}`` when the workspace is not a git repository. Use this
        before making changes so you know what has already been modified, and
        after changes to verify what you touched."""
        try:
            import subprocess as _subprocess
            from pathlib import Path as _Path

            root = _Path(str(workspace.root))
            branch = workspace_git_branch(root)
            diff = workspace_git_diff(root)
            result: dict[str, Any] = {
                "git": bool(branch.get("is_repo", False) or diff.get("git", False)),
                "workspace": str(workspace.root),
                "branch": branch.get("branch"),
            }
            if not result["git"]:
                return json.dumps({**result, "note": "not a git repository"}, ensure_ascii=False)
            # git status --short --branch, bounded
            try:
                proc = _subprocess.run(
                    ["git", "-C", str(root), "status", "--short", "--branch", "--untracked-files=normal"],
                    capture_output=True, text=True, timeout=10,
                )
                lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
                result["status"] = lines[:80]
                result["status_truncated"] = len(lines) > 80
            except Exception:  # noqa: BLE001
                result["status"] = []
            result["diff_files"] = diff.get("files", [])
            result["untracked"] = diff.get("untracked", [])
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "git_status")

    @tool(args_schema=MemoryArgs)
    def memory(action: str, content: str = "", target: str = "", scope: str = "agent", name: str = "") -> str:
        """Write a long-term memory entry.

        Long-term memory persists across sessions and is injected into every
        conversation. Use it for stable facts: user preferences, project
        conventions, ports / commands that are always true, decisions with
        lasting consequences. Do NOT store anything transient (a one-off error,
        an exploratory guess) — that belongs in the conversation instead.

        Write each entry as a CONDENSED, self-contained fact in your own words:
        distill the durable takeaway rather than quoting messages or pasting raw
        text. Keep entries concise (a sentence or two, ideally under ~200 chars)
        and merge related facts into what it makes sense to remember.

        - ``add``: appends a new entry.
        - ``replace``: replaces every entry containing ``target`` (substring)
          with ``content``. Use it to update stale or outgrown entries.
        - ``remove``: deletes every entry containing ``target``.

        Your ``MEMORY.md`` is a CURATED INDEX of durable facts, kept concise by
        automatic consolidation. When you have a lot of detail to preserve,
        write it to a separate topic file via ``name`` (e.g. ``name="RULES.md"``)
        instead of dumping it into ``MEMORY.md``. Before writing, check whether
        the fact already exists or overlaps an existing entry — prefer
        ``replace``/merge over blind appends. Never paste long raw text: only
        refined takeaways belong in memory.

        Scope rules:
        - ``scope="agent"`` (default) writes to your own agent ``BASE/`` — either
          your ``MEMORY.md`` or, via ``name``, another ``.md`` file there. Use
          ``name="SESSIONS/2026-08-19.md"`` to write a dated session note (the
          system also appends automatic session notes there each day).
        - ``scope="system"`` writes to one of the system-default files only:
          ``MEMORY.md``, ``USER.md`` or ``AGENT.md`` (pass it via ``name``).
          Use this for durable global facts about the user.

        You cannot write to project ``BASE`` user files, ``BASE/PROJECT``,
        another agent's memory, or user-created root files — those are
        read-only for you. In supervised mode a write pauses for the user's
        confirmation. Failed writes (duplicate / target not found) return an
        error message and change nothing.
        """
        try:
            if memory_store is None or not memory_rel:
                return _error_result(RuntimeError("memory is not available"), "memory")
            if action in ("add", "replace") and _looks_like_raw_paste(content):
                return (
                    "Memory write rejected: this looks like pasted raw text "
                    "(too long / heavily quoted). Distill the durable takeaway "
                    "into a concise fact in your own words before saving it."
                )
            ok, rel = _resolve_memory_target(memory_rel, scope, name)
            if not ok:
                return f"Memory write rejected: {rel}"
            if action == "add":
                blocks = memory_store.add_block(rel, content)
            elif action == "replace":
                blocks = memory_store.replace_block(rel, target, content)
            elif action == "remove":
                blocks = memory_store.remove_block(rel, target)
            else:
                return f"Unsupported memory action: {action}"
            return f"Memory updated. {len(blocks)} entries now."
        except Exception as exc:
            return _error_result(exc, "memory")

    @tool(args_schema=MemoryReadArgs)
    def memory_read(file: str) -> str:
        """Read a long-term memory file on demand (agent scope).

        Your ``SESSIONS/*`` records and extra topic files are NOT injected into
        every conversation to keep the prompt compact. When you need to recall
        what happened in an earlier session or review a topic file, read it here
        with its memory-root-relative ``file`` path (e.g.
        ``<project>/<agent>/SESSIONS/2026-08-17.md``). Returns the file content,
        or an error if the path is missing or outside the memory root.
        """
        try:
            if memory_store is None or not memory_rel:
                return _error_result(RuntimeError("memory is not available"), "memory_read")
            memory = memory_store.read_file(file.strip())
            content = memory.content or ""
            if not content.strip():
                return f"(empty memory file: {file})"
            return content
        except Exception as exc:
            return _error_result(exc, "memory_read")

    @tool(args_schema=AskUserArgs)
    def ask_user(question: str, options: list[dict[str, str]], multiple: bool = False, header: str = "") -> str:
        """Ask the user a question with selectable options when you need a decision or clarification.

        ``question`` MUST be a concrete, non-empty question written for the user
        (e.g. "Which fix do you want first?"). Never call this with an empty or
        placeholder question. ``options`` may be empty for a free-form reply, but
        prefer 2-4 clear choices when they exist."""
        question = (question or "").strip()
        if not question:
            return json.dumps(
                {"error": "question_required", "message": "ask_user requires a concrete non-empty 'question'. Restate the actual decision you need from the user and call again."},
                ensure_ascii=False,
            )
        normalized_options = [
            item.model_dump() if isinstance(item, AskUserOption) else item
            for item in options
        ]
        result = {
            "question": question,
            "options": normalized_options,
            "multiple": multiple,
            "header": header,
            "status": "awaiting_user",
        }
        return json.dumps(result, ensure_ascii=False)

    allowed_references = referenced_sessions or set()

    @tool(args_schema=ReadSessionArgs)
    def read_session(session_id: str, max_messages: int = 0) -> str:
        """Read a conversation session that the user explicitly referenced in this chat (they pasted its session id). Use it to recall prior decisions, code, or context from another session. Only sessions the user pasted into this conversation can be read; anything else is rejected."""
        if session_store is None:
            return json.dumps({"error": "unavailable", "message": "Session reading is not available in this runtime."}, ensure_ascii=False)
        if session_id not in allowed_references:
            return json.dumps(
                {"error": "not_authorized", "message": f"Session {session_id} was not referenced by the user in this conversation. Ask the user to paste that session's id into the chat."},
                ensure_ascii=False,
            )
        try:
            session = session_store.load(session_id)
        except Exception as exc:
            return _error_result(exc, "read_session")
        if session is None:
            return json.dumps({"error": "not_found", "message": f"Session {session_id} does not exist."}, ensure_ascii=False)
        messages: list[dict[str, Any]] = []
        for message in session.messages:
            if message.role not in {"user", "assistant"} or not message.content:
                continue
            if message.role == "user":
                content = format_user_message(message.content, message.attachments, message.references)
            else:
                content = message.content
            messages.append({"role": message.role, "content": content})
        if max_messages > 0:
            messages = messages[-max_messages:]
        capped: list[dict[str, Any]] = []
        total = 0
        truncated = False
        for message in messages:
            total += _content_chars(message["content"])
            if total > MAX_REFERENCE_SESSION_CHARS:
                truncated = True
                break
            capped.append(message)
        return json.dumps(
            {
                "session_id": session.id,
                "title": session.title,
                "message_count": len(session.messages),
                "messages": capped,
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    @tool(args_schema=DelegateTaskArgs)
    def delegate_task(agent: str, task: str, context: str = "") -> str:
        """Delegate a bounded task to another team member and get back their result.

        Use when a task fits a teammate's role better (code review, research,
        testing, a subsystem you don't own). The member runs independently with
        their own memory and returns their final answer to you; you integrate
        it and report to the user. Do NOT delegate work you should just do
        yourself, and never delegate to yourself.
        """
        if delegator is None:
            return "Team delegation is not available in this project (single-agent mode)."
        return delegator.delegate(agent, task, context)

    @tool(args_schema=DelegateParallelArgs)
    def delegate_parallel(tasks: list[DelegateTaskItem], max_concurrent: int = 3) -> str:
        """Delegate several independent tasks to different team members concurrently.

        Each task runs in parallel and results come back as a JSON list of
        ``{agent, ok, result|error}``. Use for fan-out work (e.g. review several
        files at once). Do NOT use for dependent tasks.
        """
        if delegator is None:
            return "Team delegation is not available in this project (single-agent mode)."
        payload = [item.model_dump() if isinstance(item, DelegateTaskItem) else item for item in tasks]
        return delegator.delegate_parallel(payload, max_concurrent)

    @tool(args_schema=CreateTeamMemberArgs)
    def create_team_member(name: str, role: str, description: str = "", superior: str = "") -> str:
        """Create a new team member under the current project (team formation).

        Use when the user asks you to build a team / add a colleague, or when
        you need a specialist whose role you can't cover. The new member gets
        their own identity + memory and appears in the project's team roster.
        This app does NOT configure agents via an agents.yaml file — creating a
        colleague is done with THIS tool, so do not search the filesystem for
        agent config files.
        """
        if delegator is None:
            return "Team management is not available in this project (single-agent mode)."
        return delegator.create_agent(name, role, description, superior)

    @tool(args_schema=CreateTeamArgs)
    def create_team(id: str, name: str, lead: str = "", parent_team_id: str = "") -> str:
        """Create a new team/department to organize existing team members.

        A team groups members under a shared name and shared team memory. Give
        it a stable id (lowercase, no spaces) and a display name; optionally a
        lead member and a parent team for nested departments.
        """
        if delegator is None:
            return "Team management is not available in this project (single-agent mode)."
        return delegator.create_team(id, name, lead, parent_team_id)

    @tool(args_schema=ManageGoalArgs)
    def update_goal(status: str) -> str:
        """Declare the active session goal complete or blocked.

        You may ONLY set "complete" or "blocked". Pausing/resuming the goal and
        its token budget are user- or system-controlled — never call this tool
        to change them.

        "complete" is a strict claim: the FULL objective is finished and
        verified requirement-by-requirement against authoritative current-state
        evidence (files, command output, tests, rendered artifacts, runtime
        behavior). Never mark complete merely because work is hard, slow, the
        budget is nearly exhausted, or you are stopping work.

        "blocked" is only for a true impasse: the SAME blocking condition has
        repeated for at least three consecutive goal turns. Never use it because
        the work is hard, uncertain, or would benefit from clarification.

        The system enforces this: calling "blocked" before the goal has run
        three turns is REJECTED and the goal stays active — you must keep
        working. Only after the third goal turn will "blocked" be accepted.
        """
        if session_store is None or not session_id:
            return json.dumps({"error": "goal store unavailable"}, ensure_ascii=False)
        goal = session_store.get_goal(session_id)
        if goal is None:
            return json.dumps({"error": "当前没有 active 目标"}, ensure_ascii=False)
        if goal.status not in {"active", "budget_limited"}:
            return json.dumps({"error": f"goal status is {goal.status}, cannot update"}, ensure_ascii=False)
        if status == "blocked" and goal.round < 2:
            # 引擎侧 blocked audit：须连续 ≥3 轮同一阻塞才可 blocked（round 0/1/2）。
            # 过早 blocked 会被拒绝，目标保持 active，要求模型继续推进。
            return json.dumps(
                {
                    "error": (
                        "blocked 仅允许在「同一阻塞连续出现 ≥3 轮」后声明（当前第 "
                        f"{goal.round + 1} 轮）。请继续基于实际当前状态推进目标，"
                        "不要因工作困难/进度慢/需要澄清而 blocked。若确有同一阻塞，"
                        "持续尝试到第 3 轮后再 update_goal(status=\"blocked\")。"
                    )
                },
                ensure_ascii=False,
            )
        updated = session_store.update_goal_status(session_id, status)
        if goal_emit is not None:
            try:
                goal_emit({"type": "goal_updated", "session_id": session_id, "goal": updated.to_dict()})
            except Exception:  # noqa: BLE001 - never break the tool on a publish hiccup
                pass
        return json.dumps({"status": "ok", "goal_status": status}, ensure_ascii=False)

    @tool(args_schema=GetGoalArgs)
    def get_goal() -> str:
        """Read the active session goal (objective, status, token budget, usage).

        Read-only: use it to re-check the exact objective, token budget and how
        many tokens/seconds have been spent before deciding whether the goal is
        actually complete.
        """
        if session_store is None or not session_id:
            return json.dumps({"error": "goal store unavailable"}, ensure_ascii=False)
        goal = session_store.get_goal(session_id)
        if goal is None:
            return json.dumps({"goal": None}, ensure_ascii=False)
        return json.dumps(goal.to_dict(), ensure_ascii=False)

    tools = [search_files, read_file, ask_user, replace_in_file, apply_text_edits, write_file, run_command, install_skill, load_skill, git_status]
    if readonly:
        # Reviewer/auditor sub-agents get no workspace mutation tools.
        tools = [search_files, read_file, git_status]
    if web_tools:
        # Web tools are read-only network reads, open to the main agent and all
        # sub-agents regardless of autonomy/phase (see _READ_ONLY_TOOLS).
        tools.extend(web_tools)
    if browser_tool is not None:
        # Embedded-browser tool (desktop only): drives the visible right-panel
        # browser. Mounted only when the Electron bridge is registered.
        tools.append(browser_tool)
    if memory_store is not None and memory_rel:
        tools.append(memory_read)
        if not readonly:
            tools.append(memory)
    if session_store is not None:
        tools.append(read_session)
    if delegator is not None:
        tools.append(delegate_task)
        tools.append(delegate_parallel)
        tools.append(create_team_member)
        tools.append(create_team)

    # Goal tools（对齐 codex spec.rs）：仅当 session 有 active/budget_limited 目标时
    # 可见。update_goal 只能声明 complete/blocked（pause/resume/budget 归用户/系统）；
    # get_goal 只读查询。用 factory 延迟闭包，避免在定义处绑定外部可变 state。
    # 整个 goal 能力被关闭（Settings toggle / COWORKER_GOAL_ENABLED=0 bypass）时
    # 一律不挂载，模型看不到这两个工具。
    if session_store is not None and session_id and goal_feature.is_enabled():
        _active_goal = None
        try:
            _active_goal = session_store.get_goal(session_id)
        except Exception:  # noqa: BLE001 - a goal probe must never break tool building
            _active_goal = None
        if _active_goal is not None and _active_goal.status in {"active", "budget_limited"}:
            tools.append(update_goal)
            tools.append(get_goal)
    # WorkerAgent 集成（单 agent 模式）：use_worker 是只读-safe 的，
    # 但 worker 本身可以有写权限（由 WorkerConfig 控制），所以 use_worker tool 始终可挂载。
    if use_worker_enabled and worker_llm is not None:
        from coworker.workers.worker_tool import UseWorkerTool

        # 根源性护栏：在构造期就为子代理准备干净的工具集——排除所有委派/spawn
        # 工具并拷贝成独立列表。否则 worker 继承同一可变 tools 列表（下面 append
        # 的 use_worker 会泄漏进去），可无限嵌套 spawn 子代理（单 agent 模式无
        # org.max_depth 约束）。worker 只做一次性研究/分析，不需要再委派。
        worker_tools = [
            tool
            for tool in tools
            if getattr(tool, "name", "") not in _CHILD_EXCLUDED_TOOLS
        ]
        worker_tool = UseWorkerTool(
            llm=worker_llm,
            workspace=workspace,
            tools=worker_tools,
            approval_store=worker_approval_store,
            change_store=change_store,
            session_store=session_store,
            data_dir=worker_data_dir,
            mcp_session_manager=worker_mcp_session_manager or None,
            skill_manager=skill_manager,
            provider_name=worker_provider_name,
            session_id=worker_session_id,
            work_mode=worker_work_mode,
            autonomy=worker_autonomy,
            language=language,
            max_concurrent=max_concurrent,
            delegation_emit=delegation_emit,
            worker_bus=worker_bus,
            depth=0,
            context_window_tokens=worker_context_window_tokens,
            max_output_tokens=worker_max_output_tokens,
            calibration_key=worker_calibration_key,
        )
        tools.extend(worker_tool.create_tools())
    # Record the full registered tool-name set on the workspace so the phase gate
    # can tell the model a hallucinated tool (e.g. list_directory) does not exist
    # instead of a misleading "not available in the current phase/autonomy".
    if workspace is not None:
        try:
            workspace._registered_tool_names = {getattr(t, "name", "") for t in tools if getattr(t, "name", "")}
        except Exception:  # noqa: BLE001 - never break tool building
            pass
    return tools


def _path_from_tool_input(tool_name: str, input_raw: str) -> str:
    if not input_raw:
        return ""
    try:
        args = json.loads(input_raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(args, dict):
        return ""
    return next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")


def _change_to_public(change: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {
        "path": change.get("file_path", ""),
        "kind": change.get("kind", "edit"),
        "added": int(change.get("added") or 0),
        "removed": int(change.get("removed") or 0),
        "truncated": bool(change.get("truncated")),
        "too_large": bool(change.get("too_large")),
    }
    if change.get("hunks"):
        public["hunks"] = change["hunks"]
    return public


def build_coworker_agent_graph(
    llm: Any,
    tools: list[Any],
    work_mode: WorkMode,
    language: Language,
    autonomy: Autonomy = "guarded",
    checkpointer: Any | None = None,
    approval_store: CommandApprovalStore | None = None,
    data_dir: Path | None = None,
    mcp_session_manager: Any | None = None,
    skill_manager: Any | None = None,
    memory_manager: Any | None = None,
    workspace: Any | None = None,  # NEW: for external write HITL bridge
    context_budget: int | None = None,
    context_window_tokens: int = 0,
    context_window_source: str = "default",
    context_window_warning: str | None = None,
    web_capability: str = "",
    browser_capability: str = "",
    max_output_tokens: int = 0,
    calibration_key: str = "",
    steer_emit: Any | None = None,  # interjection (插話) live-emit callback
) -> Any:
    """Compile the Coworker agent as a single ``create_agent`` graph.

    The middleware chain implements the Codex-style two-axis model:

    * ``PhaseToolGateMiddleware`` filters the tool set each model call based on
      the current ``phase``/``autonomy`` (physical enforcement, not prompt).
    * ``TodoListMiddleware`` (always mounted) exposes ``write_todos`` in every
      mode so the agent can break its task into a visible checklist that the UI
      renders as the TodoBlock card.
    * ``HumanInTheLoopMiddleware`` (always mounted) interrupts commands/writes
      only in ``execute`` + ``supervised``, and ``ask_user`` regardless.
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware.todo import TodoListMiddleware

    from ..mcp.mcp_middleware import McpToolMiddleware

    if mcp_session_manager is None:
        from ..mcp.mcp_session import McpSessionManager

        _mcp_manager = McpManager(
            Path(data_dir if data_dir is not None else Path.cwd()) / "mcp_servers.json",
        )
        mcp_session_manager = McpSessionManager(
            Path(data_dir if data_dir is not None else Path.cwd()), _mcp_manager
        )
        mcp_session_manager.start()

    # Built before the middleware list so the HITL middleware can resolve MCP
    # approval policies through it (MCP tool names are only known at runtime).
    mcp_middleware = McpToolMiddleware(
        mcp_session_manager,
        audit_path=(Path(data_dir) / TOOL_AUDIT_FILENAME) if data_dir is not None else None,
    )

    from .. import platform as _platform

    phase_gate = PhaseToolGateMiddleware(
        "\n\n".join(part for part in (_platform.platform_hint(), web_capability, browser_capability) if part),
        workspace=workspace,
    )
    # Output reservation: 0 means "unset" upstream, but the LLM call itself
    # always sends DEFAULT_MAX_OUTPUT_TOKENS — budget against what really goes
    # over the wire, not the raw zero.
    from ..providers import DEFAULT_MAX_OUTPUT_TOKENS as _DEFAULT_MAX_OUTPUT

    effective_max_output = max_output_tokens if max_output_tokens > 0 else _DEFAULT_MAX_OUTPUT
    # Closed-loop tokenizer calibration (actual usage / raw estimate), shared by
    # the summarization meter and the pre-send guard.
    from ..context import get_calibration_store

    calibration_store = get_calibration_store(data_dir) if data_dir is not None else None

    # Cheap per-call layer: clear stale tool results (Anthropic-style context
    # editing) so the model never pays for long-dead tool output. Transient —
    # the UI/session history is untouched (two-layer storage). The SAME edit
    # instance also feeds the summarization middleware's prune-aware trigger.
    # Trigger lives on the EFFECTIVE budget ((window − max_output) × safety),
    # so the reservation can never be double-spent.
    tool_edit = ClearToolUsesEdit(
        trigger=int(context_budget_tokens(context_window_tokens or 128_000, effective_max_output) * 0.75),
        keep=3,
        placeholder="[cleared]",
        exclude_tools=("write_todos", "memory", "memory_read", "ask_user"),
    )
    context_middleware = CoworkerSummarizationMiddleware(
        context_budget,
        llm=llm,
        summarizer_candidates=_summarizer_candidates(data_dir, llm),
        language=language,
        context_window_tokens=context_window_tokens,
        context_window_source=context_window_source,
        context_window_warning=context_window_warning,
        tool_edit=tool_edit,
        max_output_tokens=effective_max_output,
        calibration_store=calibration_store,
        calibration_key=calibration_key,
    )
    middleware: list[Any] = [
        NormalizeMessagesMiddleware(),
        StallRetryMiddleware(),
        context_middleware,
        ToolCallCleanerMiddleware(),
        phase_gate,
        # Task-list management in EVERY mode (build / plan / chat): the agent
        # breaks its work into a `write_todos` checklist and keeps it updated as
        # it completes each step. Read-only-safe (writes graph state only).
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[tool_edit]),
    ]
    middleware.extend(command_approval_middleware(approval_store, mcp_middleware.tool_policy, workspace=workspace))

    middleware.append(mcp_middleware)
    phase_gate.mcp_tool_names_provider = mcp_middleware.tool_names    # Skills: inject the catalog (name+description+location) into the system
    # prompt; the full SKILL.md body loads on demand via read_file.
    if skill_manager is not None:
        from ..skills.skill_middleware import SkillMiddleware

        middleware.append(SkillMiddleware(skill_manager))

    # Memory: inject long-term memory into every phase (planning needs the
    # user's background facts most of all). Mounted after skills so the memory
    # section lands before the skills catalog in the system prompt. Writes are
    # gated separately by the phase gate + HITL middleware via the `memory`
    # tool.
    if memory_manager is not None:
        from ..memory.memory_middleware import MemoryMiddleware

        try:
            middleware.append(MemoryMiddleware(memory_manager))
        except Exception as exc:  # noqa: BLE001 - a broken memory middleware must not break chat
            logger.warning("Memory middleware unavailable: %s", exc)

    # Loop guard: the model must never re-run the same failing tool call
    # forever. create_agent's default recursion_limit (9_999) makes an
    # unguarded loop effectively infinite, so cap identical consecutive calls
    # here and force a text-only final turn on the hard cap.
    middleware.append(RepeatedToolCallMiddleware())

    # Interjection (插話) steering: drains the per-session steer inbox at every
    # model-call boundary and folds pending user messages into the next request
    # — WITHOUT aborting the in-flight stream. Mounted AFTER compaction (so the
    # steer survives any trim) and BEFORE the context guard (so the guard
    # measures the full final request including injected steers).
    middleware.append(SteerInjectionMiddleware(steer_emit=steer_emit))

    # Context guard (INNERMOST — last in the chain, so it measures the request
    # after every other middleware's overrides): calibrated measurement of the
    # FULL final request against ``window − max_output`` with staged reductions.
    # The window falls back to 128k for runtimes that never resolved one (the
    # same fallback every other budget uses), so the guard is always armed.
    context_guard = ContextGuardMiddleware(
        window_tokens=context_window_tokens or 128_000,
        max_output_tokens=effective_max_output,
        calibration_store=calibration_store,
        calibration_key=calibration_key,
        mcp_tool_names_provider=mcp_middleware.tool_names,
        window_source=context_window_source,
        window_warning=context_window_warning,
    )
    middleware.append(context_guard)

    from .system_prompt import build_cw_system_prompt

    system_prompt = build_cw_system_prompt(
        tools=tools,
        workspace=workspace,
        work_mode=work_mode,
        language=language,
        include_workspace=True,
    )

    kwargs: dict[str, Any] = {
        "model": llm,
        "tools": tools,
        "system_prompt": system_prompt,
        "middleware": middleware,
        "state_schema": CoworkerAgentState,
        "name": "coworker_agent",
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    graph = create_agent(**kwargs)
    # Expose the context middleware on the compiled graph so a runtime can
    # tighten the budget when the provider rejects an oversized request.
    try:
        setattr(graph, "_cw_context_middleware", context_middleware)
        setattr(graph, "_cw_context_guard", context_guard)
    except Exception:  # noqa: BLE001 - best-effort hook
        pass
    return graph