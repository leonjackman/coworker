import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)


def _llm_stream_chunk_timeout() -> float:
    """Timeout (seconds) for how long the LLM stream may pause between chunks.

    LangChain's default is 120s; slow / concurrent local providers can exceed
    that and get their reply truncated. Configurable via env; default 300s.
    """
    try:
        return float(os.environ.get("COWORKER_LLM_STREAM_CHUNK_TIMEOUT_S", "300.0"))
    except (TypeError, ValueError):
        return 300.0


from typing import Any, Literal
from typing_extensions import NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, Runtime
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from .checkpoints import CheckpointManager
from .changes import ChangeStore
from .config import BackendSettings
from .mcp.mcp import McpManager
from .providers import ProviderEntry, ProviderManager
from .sessions import SessionStore
from .traces import AGENT_TRACE_FILENAME, AgentTraceStore
from .workspace import (
    COMMAND_APPROVAL_FILENAME,
    READ_ONLY_COMMANDS,
    TOOL_AUDIT_FILENAME,
    CommandApprovalStore,
    Workspace,
    fingerprint_path_for,
    workspace_git_branch,
    workspace_git_diff,
)

AgentMode = Literal["single"]
Language = Literal["zh", "en"]
WorkMode = Literal["plan", "build"]
Phase = Literal["discuss", "execute"]
Autonomy = Literal["supervised", "guarded", "autonomous"]


SYSTEM_PROMPT = (
    "You are Coworker, a local coding assistant. "
    "Use workspace tools only when they are needed and keep answers concise."
)
def _title_system_prompt(language: Language) -> str:
    """Title-generation prompt. Titles follow the system UI language (the same
    rule as chat replies), not the language of the user's message."""
    return (
        "You are a thread title generator. Output ONLY the title string. Nothing else. No code fences, no quotes, no explanation."
        "Rules:"
        " - The input is the first exchange of a conversation: the user's message and the AI's reply."
        " - Summarize the exchange into a short title that captures the main topic, question, or task."
        f" - Reply in {language_name(language)}."
        " - Title must be a complete meaningful phrase."
        " - Never include tool names like read tool, bash tool, edit tool."
        " - Focus on the main topic, question, or task."
        " - Keep exact: technical terms, numbers, filenames, HTTP codes."
        " - Remove generic words: the, this, my, a, an."
        " - Never respond to questions—just generate a title for the conversation."
        " - For short or conversational messages (hello, lol, what's up, hey): generate a brief friendly title like 'Quick introduction', 'Brief check-in', 'Light chat', etc."
        " - The title must be a single line, 3-40 characters, no explanations."
    )
MAX_ATTACHMENT_CHARS = 120_000
MAX_REFERENCE_SESSION_CHARS = 60_000

PLAN_MARKER = "[CW-PLAN]"


class CoworkerAgentState(AgentState[Any]):
    work_mode: NotRequired[str]
    language: NotRequired[str]
    phase: NotRequired[str]
    autonomy: NotRequired[str]
    goal_mode: NotRequired[bool]
    goal_text: NotRequired[str]
    goal_done: NotRequired[bool]
    goal_nudge: NotRequired[str]
    todos: NotRequired[list[Any]]


@dataclass(frozen=True)
class AgentReply:
    content: str
    mode: AgentMode
    provider: str
    parts: list[dict[str, Any]] | None = None


class SearchFilesArgs(BaseModel):
    query: str = Field(min_length=1, description="Text to search for in UTF-8 workspace files.")
    path: str = Field(default="", description="Optional workspace-relative file or directory to search.")
    max_results: int = Field(default=80, ge=1, le=80, description="Maximum number of matches to return.")


class ReadFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")


class WriteFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to write.")
    content: str = Field(description="Full UTF-8 file content to write.")


class InstallSkillArgs(BaseModel):
    name: str = Field(description="Skill slug/identifier (lowercase letters, digits, hyphens). Becomes the install directory name under ~/.agents/skills.")
    content: str = Field(description="Full SKILL.md file content, including YAML frontmatter (name + description).")
    commands: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional sub-commands to expose as direct /<command> entries. Each item is "
        "{name, description, body}. Each body is written to commands/<name>.md and listed in the "
        "root SKILL.md frontmatter so the skill shows individual commands in the chat menu.",
    )


class LoadSkillArgs(BaseModel):
    name: str = Field(
        description="The exact skill <name> shown in <available_skills> whose SKILL.md body you want to load."
    )


class GitStatusArgs(BaseModel):
    """No arguments — inspects the workspace git repository."""


class ReplaceInFileArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence when true; otherwise exactly one occurrence is required.")


class TextEditArgs(BaseModel):
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence of old_text for this edit.")


class ApplyTextEditsArgs(BaseModel):
    file_path: str = Field(description="Workspace-relative UTF-8 text file path.")
    edits: list[TextEditArgs] = Field(description="Ordered exact text edits. All edits must validate before the file is written.")


class RunCommandArgs(BaseModel):
    command: list[str] = Field(description="Command argv array, for example ['npm', 'run', 'build']. Shell strings are not accepted.")
    cwd: str = Field(default="", description="Optional workspace-relative working directory.")
    timeout_seconds: int = Field(default=20, ge=1, le=60, description="Command timeout in seconds.")


DEFAULT_AGENT_NAME = "default_agent"


class DelegateTaskArgs(BaseModel):
    agent: str = Field(description="The team member id to delegate to (see the roster in your context).")
    task: str = Field(description="What you want that member to do, as a self-contained instruction.")
    context: str = Field(default="", description="Optional relevant context (file paths, prior findings, constraints) to hand over.")


class DelegateTaskItem(BaseModel):
    agent: str = Field(description="The team member id to delegate to.")
    task: str = Field(description="What you want that member to do.")
    context: str = Field(default="", description="Optional relevant context.")


class DelegateParallelArgs(BaseModel):
    tasks: list[DelegateTaskItem] = Field(description="List of independent delegation tasks to run concurrently.")
    max_concurrent: int = Field(default=3, ge=1, le=8, description="Concurrency cap.")


class CreateTeamMemberArgs(BaseModel):
    name: str = Field(description="New member id (lowercase, no spaces). This becomes their memory directory name.")
    role: str = Field(description="Their role on the team, e.g. 'frontend developer'.")
    description: str = Field(default="", description="One-line description of their responsibilities.")
    superior: str = Field(default="", description="The member they report to. Leave empty to report to the user.")


class CreateTeamArgs(BaseModel):
    id: str = Field(description="New team id (lowercase, no spaces).")
    name: str = Field(description="Display name for the team / department.")
    lead: str = Field(default="", description="The team lead's agent id (must already be a member).")
    parent_team_id: str = Field(default="", description="Optional parent team id for nested departments.")


def _resolve_project_memory_dir(project_store: Any | None, workspace_root: str) -> str:
    """Resolve the memory_dir for a workspace root, or ``""`` if unknown.

    The memory library is central (not under the workspace), so the project
    must be looked up by its workspace path. Returns ``""`` for the default
    (non-project) workspace — memory then degrades to system-level only.
    """
    if project_store is None or not workspace_root:
        return ""
    try:
        project = project_store.find_by_workspace_path(str(Path(workspace_root).resolve()))
    except Exception:  # noqa: BLE001 - a lookup hiccup must not break chat
        return ""
    if project is None:
        return ""
    try:
        if not project.memory_dir:
            project.memory_dir = project_store.memory_dir_for(project.id)
    except Exception:  # noqa: BLE001 - defensive
        return project.memory_dir or ""
    return project.memory_dir or ""


class MemoryArgs(BaseModel):
    """Long-term memory write (agent scope). Only available during execution."""
    action: Literal["add", "replace", "remove"] = Field(
        description="add appends a new memory; replace swaps every entry containing 'target'; remove deletes every entry containing 'target'."
    )
    content: str = Field(
        min_length=1,
        description="Memory text. For 'replace' this is the new text; for 'add' it is the entry to append; ignored for 'remove'.",
    )
    target: str = Field(
        default="",
        description="For 'replace'/'remove', a substring that identifies the entry to change. Leave empty for 'add'.",
    )
    scope: Literal["agent", "system"] = Field(
        default="agent",
        description="agent writes to your own agent BASE/ memory (default). system writes to a system-level default file (MEMORY.md / USER.md / AGENT.md).",
    )
    name: str = Field(
        default="",
        description="File name within the target scope. Empty in agent scope = your MEMORY.md; otherwise a .md file in your own agent BASE/. In system scope must be MEMORY.md, USER.md or AGENT.md.",
    )


class MemoryReadArgs(BaseModel):
    """Read a long-term memory file on demand (agent scope)."""
    file: str = Field(
        description="Memory-root-relative path of the file to read, e.g. '<project>/<agent>/SESSIONS/<name>.md' or '<project>/<agent>/BASE/RULES.md'. Use this to review session records or topic files that are not injected every turn.",
    )


def _looks_like_raw_paste(text: str) -> bool:
    """Heuristic guard against dumping raw conversation into long-term memory.

    A refined memory fact is short and compact. Anything very long that is
    heavily quoted or spans many lines is almost certainly a raw paste the
    agent should first distill into a takeaway.
    """
    stripped = (text or "").strip()
    if len(stripped) < 400:
        return False
    quote_chars = sum(1 for c in stripped if c in '>“”"\'`')
    newline_count = stripped.count("\n")
    # A long block that is (a) 3+ lines and quoted, or (b) unusually verbose.
    if newline_count >= 3 and quote_chars >= 2:
        return True
    if len(stripped) >= 1200:
        return True
    return False


def _resolve_memory_target(memory_rel: str, scope: str, name: str) -> tuple[bool, str]:
    """Resolve the memory file ``rel`` for the memory tool write target.

    Returns ``(ok, rel_or_error)``. Agent scope stays inside the current
    agent's ``BASE/`` (``MEMORY.md`` or a named sibling file); system scope is
    limited to the three system-default files. Everything else — project
    ``BASE`` files, ``BASE/PROJECT``, other agents, user root files — stays
    read-only for agents.
    """
    import posixpath

    from coworker.memory.layout import SYSTEM_FILES

    scope = scope or "agent"
    name = (name or "").strip()
    if scope == "system":
        if name not in SYSTEM_FILES:
            return False, "System memory scope only allows MEMORY.md, USER.md or AGENT.md."
        return True, name
    base = posixpath.dirname(memory_rel)
    if not name:
        return True, memory_rel
    if "/" in name or "\\" in name:
        return False, "Agent memory name must be a single file name (no folders)."
    if not (name.endswith(".md") or name.endswith(".markdown")):
        return False, "Agent memory files must be Markdown (.md / .markdown)."
    return True, f"{base}/{name}"


class AskUserOption(BaseModel):
    label: str = Field(description="Display text (1-5 words, concise).")
    description: str = Field(default="", description="Optional explanation of the choice.")


class AskUserArgs(BaseModel):
    question: str = Field(description="Complete question for the user.")
    options: list[AskUserOption] = Field(description="Available choices, each with a label and optional description.")
    multiple: bool = Field(default=False, description="Allow selecting multiple choices.")
    header: str = Field(default="", description="Very short label (max 30 chars) for the prompt.")


class ReadSessionArgs(BaseModel):
    session_id: str = Field(description="The id of a session the user explicitly referenced in this conversation (pasted session id).")
    max_messages: int = Field(default=0, ge=0, le=50, description="Optional cap on how many recent messages to read (0 = no cap).")


class AgentRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    def _next_turn_index(self, session_id: str) -> int:
        if getattr(self, "change_store", None) is None:
            return 1
        try:
            return self.change_store.next_turn_index(session_id)
        except Exception:
            return 1

    @abstractmethod
    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        raise NotImplementedError


class AgentStreamRuntime(ABC):
    mode: AgentMode
    owns_runtime_messages = False

    def _next_turn_index(self, session_id: str) -> int:
        if self.change_store is None:
            return 1
        try:
            return self.change_store.next_turn_index(session_id)
        except Exception:
            return 1

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError


def language_name(language: Language) -> str:
    return "Chinese" if language == "zh" else "English"

def normalize_work_mode(work_mode: str | None) -> WorkMode:
    return "plan" if work_mode == "plan" else "build"


def normalize_autonomy(autonomy: str | None) -> Autonomy:
    if autonomy in ("supervised", "guarded", "autonomous"):
        return autonomy
    return "guarded"


def normalize_phase(phase: str | None, work_mode: str | None = None) -> Phase:
    if phase in ("discuss", "execute"):
        return phase
    # A fresh task in plan mode starts by discussing; build mode executes.
    return "discuss" if normalize_work_mode(work_mode) == "plan" else "execute"


def normalize_language(language: Any) -> Language:
    return "en" if language == "en" else "zh"


def phase_system_prompt(language: Language, phase: Phase, autonomy: Autonomy) -> str:
    """Phase/autonomy-aware system instruction.

    The active phase decides which tools the model sees (via
    ``PhaseToolGateMiddleware``); this prompt only sets the behavioural
    contract for that phase.
    """
    lang_line = f"Reply in {language_name(language)}."
    todo_hint = (
        "Break your work into a visible task list: call write_todos with the concrete steps "
        "you intend to take, then call write_todos again as each step completes to update its "
        "status. Keep the checklist in sync with your actual progress."
    )
    if phase == "discuss":
        return (
            f"{lang_line}\n"
            "You are planning (read-only). Use the read-only tools to research the workspace and "
            "gather context (auditing, investigating, or breaking down the task). Use write_todos "
            "to present the plan as a checklist of what you will do. "
            "You may write durable long-term facts with the memory tool (user preferences, project "
            "conventions) — those are welcome even during planning. "
            "Do NOT modify files or run commands — execution is deferred until the user switches "
            "to build mode. Finish by summarizing your findings and the planned steps."
        )
    if autonomy == "autonomous":
        return (
            f"{lang_line}\n"
            "You are executing with full autonomy. You may read, edit files and run workspace "
            "commands. Do not ask the user anything — make reasonable decisions and complete the "
            "task to the best of your ability. " + todo_hint
        )
    return (
        f"{lang_line}\n"
        "You are executing. You may read, edit files and run workspace commands. Only call "
        "ask_user when you are genuinely blocked and need a decision to continue; otherwise make "
        "reasonable assumptions and proceed autonomously. " + todo_hint
    )


def agent_run_config(
    *,
    session_id: str,
    provider: str,
    model: str,
    language: Language,
    work_mode: WorkMode,
    autonomy: Autonomy,
    streaming: bool,
) -> dict[str, Any]:
    return {
        "run_name": "coworker_agent" + ("_stream" if streaming else ""),
        "tags": [
            "coworker",
            "agent",
            f"work:{work_mode}",
            f"autonomy:{autonomy}",
            "streaming" if streaming else "non-streaming",
        ],
        "metadata": {
            "coworker.session_id": session_id,
            "coworker.provider": provider,
            "coworker.model": model,
            "coworker.language": language,
            "coworker.work_mode": work_mode,
            "coworker.autonomy": autonomy,
            "coworker.streaming": streaming,
        },
        "configurable": {
            "thread_id": session_id,
        },
    }


def _open_checkpointer(checkpoint_path: Any):
    """Return a per-stream AsyncSqliteSaver connection for the checkpoint.

    Every stream (agent run) gets its OWN sqlite connection for the duration of
    the run, so concurrent sessions never serialize on a single process-wide
    connection. WAL mode allows concurrent readers plus brief writer locks, and
    ``busy_timeout`` makes a writer wait (up to 30s) instead of failing with
    ``database is locked``. The checkpoint DB is tiny (pruned) so write
    contention is negligible; this keeps different sessions' agent runs isolated
    from one another.
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _open():
        conn = await aiosqlite.connect(str(checkpoint_path), timeout=30.0)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=30000")
            await conn.execute("PRAGMA synchronous=NORMAL")
            # Do NOT set auto_vacuum per connection: it is a persistent DB-file
            # property and re-applying it while another writer holds the lock
            # raises "database is locked" (the checkpoint manager already
            # guarantees INCREMENTAL mode, so this is redundant anyway).
            yield AsyncSqliteSaver(conn)
        finally:
            await conn.close()

    return _open()



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
    def read_file(file_path: str) -> str:
        """Read a UTF-8 text file from the configured workspace."""
        try:
            return workspace.read_text(file_path)
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
    def run_command(command: list[str], cwd: str = "", timeout_seconds: int = 20) -> str:
        """Run an allowlisted command in the workspace after runtime policy approval.

        The result is JSON with ``return_code`` (0 = success), ``stdout``,
        ``stderr`` and ``timed_out``. A non-zero ``return_code`` means the
        command FAILED — never blindly re-run the exact same command; adjust
        the path/scope first or use a different tool. Note that searches can
        report "Permission denied" for unreadable directories even when the
        search itself worked: narrow the search path instead of retrying the
        whole tree with the same command.
        """
        try:
            # Runtime policy approval (HITL) is owned by HumanInTheLoopMiddleware;
            # this tool call is not the sync bottom-panel approval flow.
            result = workspace.run_command(command, cwd, timeout_seconds, audit_context)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "run_command")

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
                from .skills.skill_market import SkillMarketManager

                mkt = SkillMarketManager(_Path.home())
            else:
                mkt = skill_market_manager
            result = mkt.install_from_content(name, content, commands=commands)
            if result.get("status") == "ok" and skill_manager is not None:
                try:
                    skill_manager.refresh()
                except Exception:  # refresh must not mask the install result
                    import logging

                    logging.getLogger(__name__).exception("Failed to refresh skill catalog after install")
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
          your ``MEMORY.md`` or, via ``name``, another ``.md`` file there.
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
        """Ask the user a question with selectable options when you need a decision or clarification."""
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

    tools = [search_files, read_file, ask_user, replace_in_file, apply_text_edits, write_file, run_command, install_skill, load_skill, git_status]
    if readonly:
        # Reviewer/auditor sub-agents get no workspace mutation tools.
        tools = [search_files, read_file, git_status]
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
    return tools


_CHANGE_TOOL_NAMES = {"write_file", "replace_in_file", "apply_text_edits"}

# Tool sets for phase-driven tool gating (see PhaseToolGateMiddleware).
_READ_ONLY_TOOLS = {"search_files", "read_file", "read_session", "memory_read", "load_skill", "git_status"}
_PLAN_TOOLS = {"ask_user"}
_MEMORY_TOOLS = {"memory"}
_EXEC_TOOLS = {"run_command", "install_skill", "delegate_task", "delegate_parallel", "create_team_member", "create_team"}


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


def _is_external_path_candidate(file_path: str, workspace_root: Path) -> bool:
    """Lightweight: resolve path against workspace_root, check containment.
    
    Returns True if path is likely outside, False if inside or undeterminable.
    """
    if not file_path or not file_path.strip():
        return False
    path_str = file_path.strip()
    if path_str.startswith(("/", "~")):
        try:
            resolved = Path(path_str).expanduser().resolve()
            if resolved != workspace_root and workspace_root not in resolved.parents:
                return True
        except (OSError, ValueError):
            return False
    try:
        resolved = (workspace_root / path_str).resolve()
        if resolved != workspace_root and workspace_root not in resolved.parents:
            return True
    except (OSError, ValueError):
        return False
    return False


class _DynamicInterruptOn(dict):
    """``interrupt_on`` mapping that resolves MCP tool names on demand.

    ``HumanInTheLoopMiddleware`` looks its config up by tool name at interrupt
    time (``self.interrupt_on.get(name)`` / ``[name]``), so tool names that are
    only known once an MCP server connects can be resolved lazily here instead
    of being frozen into a static dict at graph-build time.
    """

    def __init__(self, static: dict[str, Any], resolver: Callable[[str], Any | None]):
        super().__init__(static)
        self._resolver = resolver

    def _resolve(self, key: Any) -> Any | None:
        if not isinstance(key, str) or not key:
            return None
        try:
            return self._resolver(key)
        except Exception:  # noqa: BLE001 - approval lookup must never break a run
            return None

    def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        resolved = self._resolve(key)
        return default if resolved is None else resolved

    def __missing__(self, key: Any) -> Any:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        return resolved

    def __contains__(self, key: Any) -> bool:  # type: ignore[override]
        return dict.__contains__(self, key) or self._resolve(key) is not None


def _mcp_interrupt_description(tool_call: Any, state: Any, runtime: Any) -> str:
    """Human-readable description for an MCP tool approval request."""
    name = str((tool_call or {}).get("name") or "")
    args = (tool_call or {}).get("args")
    try:
        rendered = json.dumps(args, ensure_ascii=False, default=str)[:600]
    except Exception:  # noqa: BLE001
        rendered = str(args)[:600]
    return (
        "Coworker needs approval before calling an external MCP tool.\n\n"
        f"Tool: {name}\nArgs: {rendered}"
    )


def command_approval_middleware(
    approval_store: CommandApprovalStore | None = None,
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
    workspace: Any | None = None,  # NEW: for external write detection in guarded mode
) -> list[Any]:
    """Always-mounted HITL middleware; approval decisions live in ``when``
    predicates that read phase/autonomy from agent state.

    * ``run_command`` / write tools: interrupt only in ``execute`` phase with
      ``supervised`` autonomy. ``guarded`` runs allowlisted commands inside the
      workspace automatically (Codex ``on-request``); ``autonomous`` never asks.
    * ``ask_user``: always interrupts — the tool is only reachable when the
      phase gate exposes it, so this is decoupled from the permission switch
      (fixes D3: full access no longer kills the question capability).
    * MCP tools (resolved dynamically through ``mcp_policy``): MCP calls leave
      the workspace sandbox entirely, so they get their own risk ladder derived
      from the server's ``ToolAnnotations``:

      =============  ==========  ==================  ================
      autonomy       read-only   write / undeclared  destructive
      =============  ==========  ==================  ================
      supervised     auto        ask                 ask
      guarded        auto        auto                ask
      autonomous     auto        auto                auto
      =============  ==========  ==================  ================

      A server the user marked ``trusted`` is exempt at every level (that is
      the entire meaning of the trust toggle), and "always allow" adds the
      individual tool to the approval allowlist.
    """
    workspace_root = workspace.root if workspace is not None else None
    from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware

    def _is_read_only_command(command_list: list[str]) -> bool:
        if not command_list:
            return False
        return Path(command_list[0]).name in READ_ONLY_COMMANDS

    def _needs_command_approval(req: Any) -> bool:
        state = req.state
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        if phase != "execute":
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy in ("guarded", "autonomous"):
            return False
        # read-only commands in supervised → direct pass (ls, cat, head, etc.)
        tool_input = req.tool_call.get("args", {}) if isinstance(req.tool_call, dict) else {}
        command_val = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if isinstance(command_val, list):
            _parts = command_val if command_val else []
        elif isinstance(command_val, str):
            _parts = command_val.split() if command_val else []
        else:
            _parts = []
        return not _is_read_only_command(_parts)

    def _needs_write_approval(req: Any) -> bool:
        state = req.state
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        if phase != "execute":
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy == "autonomous":
            return False
        if autonomy == "supervised":
            return True
        if autonomy == "guarded" and workspace_root is not None:
            tool_args = req.tool_call.get("args", {}) if isinstance(req.tool_call, dict) else {}
            file_path = str(tool_args.get("file_path", "") or "") if isinstance(tool_args, dict) else ""
            return file_path and _is_external_path_candidate(file_path, workspace_root)
        return False

    def _needs_mcp_approval(req: Any) -> bool:
        state = req.state
        if normalize_phase(state.get("phase"), state.get("work_mode")) != "execute":
            return False
        policy = _mcp_policy_for(req.tool_call)
        if policy is None:
            return False
        if policy.get("trusted") or policy.get("read_only"):
            return False
        autonomy = normalize_autonomy(state.get("autonomy"))
        if autonomy == "autonomous":
            return False
        if autonomy == "guarded":
            annotations = policy.get("annotations") or {}
            if annotations.get("destructive") is not True:
                return False
        return not (policy.get("digest") and approval_store is not None and approval_store.is_always_allowed(policy["digest"]))

    def _needs_sensitive_approval(req: Any) -> bool:
        state = req.state
        autonomy = normalize_autonomy(state.get("autonomy"))
        # memory + install_skill: HITL for supervised + guarded, direct pass for
        # autonomous. No phase gate here: memory may be written from any phase
        # (planning included); install_skill stays execute-only via the phase gate.
        return autonomy != "autonomous"

    def _needs_ask_user(req: Any) -> bool:
        state = req.state
        return not (normalize_phase(state.get("phase"), state.get("work_mode")) == "execute" and normalize_autonomy(state.get("autonomy")) == "autonomous")

    write_configs: dict[str, Any] = {}
    for tool_name in ("write_file", "replace_in_file", "apply_text_edits"):
        write_configs[tool_name] = {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to modify a file.",
            "when": _needs_write_approval,
        }

    static_configs: dict[str, Any] = {**write_configs,
        "run_command": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker needs approval before running this workspace command.",
            "when": _needs_command_approval,
        },
        "memory": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to update its long-term memory for this project.",
            "when": _needs_sensitive_approval,
        },
        "install_skill": {
            "allowed_decisions": ["approve", "reject"],
            "description": "Coworker wants to install a new skill. Installing persists across "
            "sessions and injects the skill's instructions into future conversations.",
            "when": _needs_sensitive_approval,
        },
        "ask_user": {
            "allowed_decisions": ["respond", "reject"],
            "description": "Coworker asks the user a question that needs an answer.",
            "when": _needs_ask_user,
        },
    }

    def _mcp_policy_for(tool_call: Any) -> dict[str, Any] | None:
        if mcp_policy is None:
            return None
        name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
        if not name:
            return None
        try:
            return mcp_policy(str(name))
        except Exception:  # noqa: BLE001 - a broken policy lookup must not break the run
            return None

    def resolve_mcp_config(name: str) -> dict[str, Any] | None:
        if mcp_policy is None:
            return None
        try:
            if mcp_policy(name) is None:
                return None
        except Exception:  # noqa: BLE001
            return None
        return {
            "allowed_decisions": ["approve", "reject"],
            "description": _mcp_interrupt_description,
            "when": _needs_mcp_approval,
        }

    hitl = HumanInTheLoopMiddleware(interrupt_on=static_configs)
    if mcp_policy is not None:
        hitl.interrupt_on = _DynamicInterruptOn(hitl.interrupt_on, resolve_mcp_config)
    return [hitl]


def interrupt_payload(interrupt: Any) -> dict[str, Any]:
    value = getattr(interrupt, "value", None)
    return value if isinstance(value, dict) else {"value": value}


def interrupt_id(interrupt: Any) -> str:
    return str(getattr(interrupt, "id", "") or "")


def interrupt_action_requests(value: dict[str, Any]) -> list[dict[str, Any]]:
    action_requests = value.get("action_requests") if isinstance(value, dict) else None
    if not isinstance(action_requests, list):
        return []
    return [action for action in action_requests if isinstance(action, dict)]


def interrupt_action_kind(
    action: dict[str, Any],
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
) -> str:
    name = str(action.get("name") or "")
    if name == "ask_user":
        return "question"
    if mcp_policy is not None and name:
        try:
            if mcp_policy(name) is not None:
                return "mcp"
        except Exception:
            logger.exception("mcp_policy lookup failed for name=%r", name)
    return "command"


def _json_safe(value: Any) -> Any:
    if isinstance(value, AskUserOption):
        return value.model_dump()
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def interrupt_command_details(value: dict[str, Any]) -> tuple[list[str], str, int]:
    action_requests = value.get("action_requests") if isinstance(value, dict) else None
    action = action_requests[0] if isinstance(action_requests, list) and action_requests else {}
    args = action.get("args") if isinstance(action, dict) else {}
    command = args.get("command") if isinstance(args, dict) else None
    cwd = args.get("cwd") if isinstance(args, dict) else ""
    timeout_seconds = args.get("timeout_seconds") if isinstance(args, dict) else 20
    safe_command = command if isinstance(command, list) and all(isinstance(part, str) for part in command) else []
    return safe_command, str(cwd or ""), int(timeout_seconds or 20)


def _mcp_context(policy: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-safe MCP descriptor stored on the approval record.

    ``digest`` is what "always allow" writes to the approval allowlist, so it
    has to survive the round-trip through the store.
    """
    if not policy:
        return {}
    return {
        "mcp": {
            "server_id": str(policy.get("server_id") or ""),
            "server_name": str(policy.get("server_name") or ""),
            "remote_name": str(policy.get("remote_name") or ""),
            "digest": str(policy.get("digest") or ""),
            "read_only": bool(policy.get("read_only")),
            "trusted": bool(policy.get("trusted")),
            "annotations": _json_safe(policy.get("annotations") or {}),
        }
    }


def mcp_policy_resolver(session_manager: Any | None) -> Callable[[str], dict[str, Any] | None] | None:
    """``tool_policy`` accessor for a session manager (``None`` when absent)."""
    if session_manager is None:
        return None
    resolver = getattr(session_manager, "tool_policy", None)
    return resolver if callable(resolver) else None


def record_runtime_interrupts(
    interrupts: Iterable[Any],
    approval_store: CommandApprovalStore,
    context: dict[str, Any],
    mcp_policy: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = []
    for interrupt in interrupts:
        value = interrupt_payload(interrupt)
        current_interrupt_id = interrupt_id(interrupt)
        actions = interrupt_action_requests(value)
        if not actions:
            command, cwd, timeout_seconds = interrupt_command_details(value)
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, 0, "command", command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": 0, "hitl_request": _json_safe(value)},
            )
            approvals.append(approval)
            continue
        for action_index, action in enumerate(actions):
            args = action.get("args") if isinstance(action, dict) else {}
            args = args if isinstance(args, dict) else {}
            args = {
                key: ([item.model_dump() if isinstance(item, AskUserOption) else item for item in value] if key == "options" and isinstance(value, list) else value)
                for key, value in args.items()
            }
            kind = interrupt_action_kind(action, mcp_policy)
            policy: dict[str, Any] | None = None
            if kind in ("question", "mcp"):
                command, cwd, timeout_seconds = [], "", 20
                if kind == "mcp" and mcp_policy is not None:
                    try:
                        policy = mcp_policy(str(action.get("name") or ""))
                    except Exception:  # noqa: BLE001
                        policy = None
            else:
                command, cwd, timeout_seconds = interrupt_command_details({"action_requests": [action]})
            approval = approval_store.request_runtime_interrupt(
                current_interrupt_id, action_index, kind, command, cwd, timeout_seconds,
                {**context, "source": "agent_langgraph_hitl", "interrupt_id": current_interrupt_id, "action_index": action_index, "action_count": len(actions), "tool_name": str(action.get("name") or ""), "action_args": args, **_mcp_context(policy), "hitl_request": _json_safe(value)},
            )
            approvals.append(approval)
    return approvals


def stream_event_from_interrupt(approval: dict[str, Any]) -> dict[str, Any]:
    context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
    kind = str(context.get("kind") or "command")
    base = {
        "approval_id": approval.get("id", ""),
        "approval_status": approval.get("status", "pending"),
        "session_id": str(context.get("session_id") or ""),
    }
    if kind == "question":
        args = context.get("action_args") if isinstance(context.get("action_args"), dict) else {}
        options = args.get("options") if isinstance(args.get("options"), list) else []
        return {
            **base, "type": "question_required",
            "question": str(args.get("question") or ""),
            "header": str(args.get("header") or ""),
            "options": options,
            "multiple": bool(args.get("multiple")),
        }
    if kind == "mcp":
        args = context.get("action_args") if isinstance(context.get("action_args"), dict) else {}
        mcp = context.get("mcp") if isinstance(context.get("mcp"), dict) else {}
        annotations = mcp.get("annotations") if isinstance(mcp.get("annotations"), dict) else {}
        return {
            **base,
            "type": "approval_required",
            "kind": "mcp",
            "command": [],
            "cwd": "",
            "tool_name": str(context.get("tool_name") or ""),
            "tool_args": _json_safe(args),
            "server_name": str(mcp.get("server_name") or ""),
            "server_id": str(mcp.get("server_id") or ""),
            "remote_name": str(mcp.get("remote_name") or ""),
            "read_only": bool(mcp.get("read_only")),
            "destructive": annotations.get("destructive") is True,
        }
    return {**base, "type": "approval_required", "kind": "command", "command": approval.get("command", []), "cwd": approval.get("cwd", "")}


def trace_context(
    *, session_id: str, provider: str, provider_id: str, model: str,
    language: Language, work_mode: WorkMode, autonomy: Autonomy, streaming: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id, "provider": provider, "provider_id": provider_id, "model": model,
        "language": language, "work_mode": work_mode, "autonomy": autonomy, "streaming": streaming,
    }


def coerce_message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


def _extract_reasoning_from_chunk(chunk: Any) -> str | None:
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    raw = additional_kwargs.get("reasoning")
    if not isinstance(raw, str) or not raw.strip():
        raw = additional_kwargs.get("reasoning_content")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _reasoning_heading(text: str) -> str:
    """Extract a short summary heading from reasoning text.

    Prefers the first markdown heading, then the first ``**bold**`` segment.
    """
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip()[:80]
    match = re.search(r"\*\*([^*]+)\*\*", text)
    if match:
        return match.group(1).strip()[:80]
    return ""


def _strip_plan_leak(content: str, parts: list[dict[str, Any]]) -> str:
    """Remove a leaked internal plan-marker segment from streamed content.

    ``PlanGateMiddleware`` injects the planner output as an assistant message
    (``[CW-PLAN]`` + plan text) so the model can use it as guidance. Some
    providers/graph modes re-stream that injected message as ordinary content
    deltas, duplicating the plan that was already delivered through the
    ``plan_*`` events. This strips that leading segment when it matches the
    plan text emitted through the plan events.
    """
    if not content:
        return content
    plan_text = ""
    for part in parts:
        if part.get("type") == "plan_end" and part.get("content"):
            plan_text = str(part["content"])
            break
        if part.get("type") == "plan" and part.get("content"):
            plan_text = str(part["content"])
            break
    if not plan_text:
        return content

    if content.startswith(plan_text):
        return content[len(plan_text):].lstrip("\n")
    stripped = content.lstrip("\n")
    if stripped.startswith(PLAN_MARKER):
        stripped = stripped[len(PLAN_MARKER):].lstrip("\n")
        if stripped.startswith(plan_text):
            return stripped[len(plan_text):].lstrip("\n")
    return content


_WRITE_ARG_PATH_KEYS = ("file_path", "path", "target")


def _estimate_file_changes(tool_name: str, input_raw: str) -> list[dict[str, Any]]:
    """Best-effort summary of files touched by a write/edit tool call.

    Returns a list of ``{path, kind, added, removed}`` dicts derived from the
    tool input arguments. Values are line-count estimates, not exact diffs.
    """
    if not input_raw:
        return []
    try:
        args = json.loads(input_raw)
    except Exception:
        return []
    if not isinstance(args, dict):
        return []

    def _count_lines(text: str) -> int:
        if not text:
            return 0
        return max(text.rstrip("\n").count("\n") + 1, 1)

    if tool_name == "write_file":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        content = str(args.get("content") or "")
        if path:
            return [{"path": path, "kind": "write", "added": _count_lines(content), "removed": 0}]

    if tool_name == "replace_in_file":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        occurrences = 1 if not args.get("replace_all") else max(int(args.get("occurrences") or 1), 1)
        if path:
            removed = occurrences * max(_count_lines(old_text) - 1, 0)
            added = occurrences * max(_count_lines(new_text) - 1, 0)
            return [{"path": path, "kind": "edit", "added": added, "removed": removed}]

    if tool_name == "apply_text_edits":
        path = next((str(args[k]) for k in _WRITE_ARG_PATH_KEYS if args.get(k)), "")
        edits = args.get("edits")
        if path and isinstance(edits, list):
            added = 0
            removed = 0
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                old_lines = _count_lines(str(edit.get("old_text") or ""))
                new_lines = _count_lines(str(edit.get("new_text") or ""))
                removed += max(old_lines - 1, 0)
                added += max(new_lines - 1, 0)
            return [{"path": path, "kind": "edit", "added": added, "removed": removed}]

    return []


def _terminate_stray_tools(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defensive: guarantee every ``tool_start`` has a terminal ``tool_end``
    before the turn's parts are merged/persisted. A tool that never produced a
    ToolMessage (interrupted before execution) would otherwise be persisted as
    ``status: running`` and render an endless spinner in the UI."""
    unresolved: set[str] = set()
    for part in parts:
        ptype = part.get("type")
        if ptype == "tool_start":
            unresolved.add(str(part.get("id", "")))
        elif ptype == "tool_end":
            unresolved.discard(str(part.get("id", "")))
    if not unresolved:
        return parts
    terminated = list(parts)
    for tc_id in unresolved:
        terminated.append({"type": "tool_end", "id": tc_id, "output": "", "status": "error"})
    return terminated


def _merge_event_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    pending_text: list[str] = []

    def _flush_text() -> None:
        if pending_text:
            merged.append({"type": "text", "content": "".join(pending_text)})
            pending_text.clear()

    for part in parts:
        if part.get("type") == "delta":
            pending_text.append(str(part.get("content") or ""))
            continue
        # 到达非文本边界（工具/推理/计划）时，先把累积的文本拍成一个 text part，
        # 让 text 与 tool 在数组中按流式到达顺序交错排列。
        _flush_text()
        if part.get("type") == "reasoning_delta":
            if merged and merged[-1].get("type") == "reasoning":
                merged[-1]["content"] += part["content"]
            else:
                merged.append({"type": "reasoning", "content": part["content"]})
        elif part.get("type") == "plan_delta":
            if merged and merged[-1].get("type") == "plan":
                merged[-1]["content"] += part["content"]
            elif merged and merged[-1].get("type") == "plan_end":
                merged[-1]["content"] = part["content"]
                merged[-1]["type"] = "plan"
            else:
                merged.append({"type": "plan", "content": part["content"]})
        elif part.get("type") == "plan_start":
            continue
        elif part.get("type") == "plan_end":
            if merged and merged[-1].get("type") == "plan":
                merged[-1]["content"] = part["content"] or merged[-1].get("content", "")
            else:
                merged.append({"type": "plan", "content": part.get("content", "")})
        elif part.get("type") == "tool_delta":
            existing_tool = next((p for p in merged if p.get("type") == "tool" and p.get("id") == part.get("id")), None)
            if existing_tool:
                existing_tool["input"] = (existing_tool.get("input", "") or "") + (part.get("input") or "")
            elif merged and merged[-1].get("type") == "tool_start":
                merged[-1]["type"] = "tool"
                merged[-1]["input"] = (merged[-1].get("input", "") or "") + (part.get("input") or "")
        elif part.get("type") == "tool_start":
            merged.append({"type": "tool", "id": part.get("id", ""), "name": part.get("name", ""), "status": "running", "input": part.get("input", "")})
        elif part.get("type") == "tool_end":
            existing_tool = next((p for p in merged if p.get("type") == "tool" and p.get("id") == part.get("id")), None)
            if existing_tool:
                existing_tool["status"] = "success" if part.get("status") == "success" else "error"
                if part.get("output") is not None:
                    existing_tool["output"] = part["output"]
                if part.get("duration_ms") is not None:
                    existing_tool["duration_ms"] = part["duration_ms"]
                if part.get("files") is not None:
                    existing_tool["files"] = part["files"]
            else:
                merged.append(
                    {
                        "type": "tool",
                        "id": part.get("id", ""),
                        "name": part.get("name", ""),
                        "status": "success" if part.get("status") == "success" else "error",
                        "input": "",
                        "output": part.get("output"),
                        **({"duration_ms": part["duration_ms"]} if part.get("duration_ms") is not None else {}),
                        **({"files": part["files"]} if part.get("files") is not None else {}),
                    }
                )
        else:
            merged.append(part)

    _flush_text()
    for item in merged:
        if item.get("type") == "reasoning":
            item["heading"] = _reasoning_heading(item.get("content", ""))
            item["done"] = True
    return merged


def generate_title(first_user_message: str, assistant_response: str = "", language: Language = "zh") -> str:
    from langchain_openai import ChatOpenAI
    if assistant_response and assistant_response.strip():
        conversation = f"用户: {first_user_message}\n\nAI: {assistant_response}"
    else:
        conversation = first_user_message
    title_prompt = _title_system_prompt(language)
    try:
        from .config import load_settings
        from .providers import ProviderManager
        settings = load_settings()
        provider_manager = ProviderManager(settings.data_dir / "providers.json", settings.data_dir)
        dp = provider_manager.default_provider()
        if dp and dp.api_key and (dp.base_url or dp.provider_type):
            llm = ChatOpenAI(model=dp.model, temperature=0, api_key=dp.api_key, base_url=dp.base_url or None)
            response = llm.invoke([
                {"role": "system", "content": title_prompt},
                {"role": "user", "content": conversation},
            ])
            title = coerce_message_content(response).strip().strip('"').strip("'")
            if title and 3 <= len(title) <= 50:
                return title
    except Exception:
        pass
    return _default_title_from_message(first_user_message)


def _default_title_from_message(user_message: str) -> str:
    text = user_message.strip()
    if len(text) <= 20:
        return text
    return text[:20].rstrip()[:20]


def prepare_agent_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content")
        if role not in {"user", "assistant", "system"} or content is None:
            continue
        # 多模态内容（list[dict]）原样透传，交给 LangChain 的 message_from_dict
        # 转成带 image_url 块的 HumanMessage；其余统一转字符串。
        prepared.append(
            {"role": role, "content": content if isinstance(content, list) else str(content)}
        )
    if not prepared:
        prepared.append({"role": "user", "content": ""})
    return prepared


def _content_chars(content: Any) -> int:
    """多模态内容（list[dict]）按文本块长度计；其余按字符串长度计。"""
    if isinstance(content, list):
        return sum(len(part.get("text", "")) for part in content if isinstance(part, dict))
    return len(content or "")


def format_user_message(
    message: str,
    attachments: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    max_attachment_bytes: int = 25 * 1024 * 1024,
) -> str | list[dict[str, Any]]:
    """把用户文本、引用、附件拼成发给 LLM 的内容。

    设计原则（产品决策）：**不网关、全部透传**——前端发来的所有附件都原样转发给
    LLM，由模型自行决定是否受理；客户端如实呈现模型的回复即可。

    ``max_attachment_bytes`` 来自设置页的「文件体积上限」（前端换算成字节后随请求
    传入）。超过该体积的二进制附件不内联字节，仅在提示词中如实说明「未转发」，
    作为防 OOM 的安全网；模型仍可在回复中说明自己无法处理该文件。

    返回：
    - ``str``：无附件且无引用时，保持纯文本（向后兼容历史消息）。
    - ``list[dict]``（多模态）：含附件/引用时。文本进 ``text`` 块；图片进
      ``image_url`` 块；其它二进制把 base64 data URL 一并带进 ``text`` 块，模型
      自行决定是否解析。超体积的二进制不内联字节，仅在文本中如实说明。
    """
    blocks: list[dict[str, Any]] = []
    if max_attachment_bytes is None:
        max_attachment_bytes = 25 * 1024 * 1024

    if references:
        ref_lines = ["Referenced sessions (readable via the read_session tool):"]
        for reference in references:
            ref_id = str(reference.get("id") or "")
            ref_title = str(reference.get("title") or ref_id)
            ref_lines.append(f"- {ref_title} (session id: {ref_id})")
        blocks.append({"type": "text", "text": "\n".join(ref_lines)})

    text = (message or "").strip()
    if attachments:
        header = (
            "The user attached the following files; forward all of them to the model "
            "and let it decide whether to use each:"
            if not text
            else "Attached files (all forwarded; the model decides whether to use each):"
        )
        text = f"{text}\n\n{header}" if text else header

    if text:
        blocks.append({"type": "text", "text": text})

    for attachment in attachments or []:
        name = str(attachment.get("name") or "attachment")
        size = int(attachment.get("size") or 0)
        kind = str(attachment.get("type") or "file")
        content = attachment.get("content")
        # 超过体积上限的二进制附件：不内联字节，如实说明（前端已拦截添加，
        # 这里作为后端兜底，覆盖 web/直接 API 等不经过前端拦截的路径）。
        exceeds_limit = bool(attachment.get("tooLarge")) or size > max_attachment_bytes
        if isinstance(content, str) and content and not exceeds_limit:
            if kind.startswith("image/"):
                blocks.append({"type": "image_url", "image_url": {"url": content}})
            else:
                safe = content[:MAX_ATTACHMENT_CHARS]
                truncated = bool(attachment.get("truncated")) or len(content) > MAX_ATTACHMENT_CHARS
                note = "\n[Attachment truncated by Coworker.]" if truncated else ""
                blocks.append(
                    {
                        "type": "text",
                        "text": f"\n--- {name} ({kind}, {size} bytes) ---\n{safe}{note}\n--- end {name} ---",
                    }
                )
        elif attachment.get("binary"):
            if exceeds_limit:
                blocks.append(
                    {
                        "type": "text",
                        "text": f"\n- {name} ({kind}, {size} bytes): binary attachment exceeds the size limit "
                        f"({max_attachment_bytes // (1024 * 1024)} MB); bytes were NOT forwarded.",
                    }
                )
            else:
                blocks.append(
                    {
                        "type": "text",
                        "text": f"\n- {name} ({kind}, {size} bytes): binary attachment; raw bytes were forwarded "
                        "but this model may not be able to parse them.",
                    }
                )
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": f"\n- {name} ({kind}, {size} bytes): no readable content included.",
                }
            )

    if not blocks:
        return message or ""
    return blocks


# ---------------------------------------------------------------------------
# Reasoning-preserving ChatOpenAI adapter
# ---------------------------------------------------------------------------

class ReasonPreservingChatOpenAI:
    """Factory that returns a :class:`ChatOpenAI` subclass which persists
    ``reasoning_content`` in ``additional_kwargs`` for OpenAI-compatible
    providers (DeepSeek, vLLM, Ollama, local proxy).

    Needed because the base ``langchain-openai`` class deliberately discards
    non‑standard delta fields in ``_convert_delta_to_message_chunk``.
    """

    @staticmethod
    def create(model: str, temperature: float, api_key: str, base_url: str | None) -> Any:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import AIMessageChunk

        _original = ChatOpenAI._convert_chunk_to_generation_chunk

        def _patched_convert(self: Any, chunk: dict, default_chunk_class: Any, base_generation_info: Any | None = None) -> Any:
            gen_chunk = _original(self, chunk, default_chunk_class, base_generation_info)
            if gen_chunk is None or gen_chunk.message is None:
                return gen_chunk
            msg = gen_chunk.message
            if isinstance(msg, AIMessageChunk):
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    additional = dict(getattr(msg, "additional_kwargs", {}) or {})
                    existing = additional.get("reasoning", "")
                    additional["reasoning"] = reasoning
                    object.__setattr__(msg, "additional_kwargs", additional)
            return gen_chunk

        # Patch at the class level so bind() / model_copy() clones inherit it
        ChatOpenAI._convert_chunk_to_generation_chunk = _patched_convert

        return ChatOpenAI(
            model=model, temperature=temperature, api_key=api_key, base_url=base_url,
            # LangChain's built-in retry covers transient 5xx / connection
            # resets (default max_retries=2 with exponential backoff); a local
            # provider blip no longer fails the whole turn.
            max_retries=2,
            # Long-thinking / slow local providers (vLLM, Ollama) can pause
            # between chunks for well over langchain's 120s default; a fired
            # stream_chunk_timeout truncates the reply mid-generation. Use a
            # generous, configurable timeout so concurrent or slow tasks are not
            # killed just because the next token took a while.
            stream_chunk_timeout=_llm_stream_chunk_timeout(),
        )


# ---------------------------------------------------------------------------
# NormalizeMessagesMiddleware – keeps provider-safe message ordering.
# ---------------------------------------------------------------------------

class NormalizeMessagesMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Ensures no ``system`` message ends up in a non‑first position of the
    message list passed to the model.

    Some providers (e.g. Qwen3.6 / vLLM) reject any request where a system
    message is not the very first message. Historical checkpoints created
    before the plan marker fix can contain a residual ``SystemMessage``
    (``[CW-PLAN]``) in the middle of the conversation, which would trigger a
    400 on resume. This middleware downgrades such misplaced system messages
    to ``human`` (content preserved) right before each model call.
    """

    def _normalize(self, state: CoworkerAgentState) -> list[Any] | None:
        from langchain_core.messages import HumanMessage

        messages = state.get("messages", [])
        if not messages:
            return None

        changed = False
        normalized: list[Any] = []
        for index, msg in enumerate(messages):
            msg_type = getattr(msg, "type", None)
            if msg_type == "system" and index > 0:
                normalized.append(HumanMessage(content=msg.content, id=getattr(msg, "id", None), additional_kwargs=msg.additional_kwargs or {}))
                changed = True
            else:
                normalized.append(msg)

        return normalized if changed else None

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}


# ---------------------------------------------------------------------------
# ContextWindowMiddleware – bounds the model context for very long sessions.
# ---------------------------------------------------------------------------

# Rolling char budget for the message list fed to the model on each call. Oldest
# messages are dropped first (the first system message is always kept). The
# checkpoint still holds full history; this only bounds what the model sees and
# what gets replayed into the checkpoint.
# Default budget when a provider's context window cannot be resolved. Derived
# from the default context window (128k tokens) with the safety factor applied.
DEFAULT_CONTEXT_WINDOW_CHARS = 128_000
CONTEXT_SAFETY_FACTOR = 0.75
CHARS_PER_TOKEN = 3.5
KEEP_RECENT_FACTOR = 0.6


def context_budget_chars(context_window_tokens: int) -> int:
    """Convert a model's token context window into the resident-message budget.

    ``budget = window × safety × chars_per_token``; a floor keeps tiny local
    models usable (avoids a budget so small every turn trims immediately).
    """
    if not context_window_tokens or context_window_tokens <= 0:
        context_window_tokens = 128_000
    return max(20_000, int(context_window_tokens * CONTEXT_SAFETY_FACTOR * CHARS_PER_TOKEN))


# Provider "context overflow" error signatures that trigger an automatic
# compaction + single retry (OpenClaw-style recovery).
CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "maximum context",
    "context_length_exceeded",
    "context_window_exceeded",
    "too many tokens",
    "request too large",
    "this model's maximum context",
    "exceeds the maximum context",
    "the number of tokens in the prompt",
)


def is_context_overflow_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    text = str(exc).lower()
    return any(pattern in text for pattern in CONTEXT_OVERFLOW_PATTERNS)


def _runtime_context_budget(provider: Any, model_override: str | None = None) -> int:
    """Resolve the runtime context budget (chars) from a provider entry.

    Uses the provider's context window resolution (user override > table >
    local discovery > default) and converts it to a character budget.
    """
    try:
        from .providers import ProviderManager

        window, _ = ProviderManager.resolve_context_window(provider)
        return context_budget_chars(window)
    except Exception:  # noqa: BLE001 - a failed resolve must never break a turn
        return DEFAULT_CONTEXT_WINDOW_CHARS


def _msg_chars(msg: Any) -> int:
    try:
        content = msg.content
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return 0


def _truncate_message(msg: Any, budget: int) -> Any:
    """Return a copy of ``msg`` with string content truncated to ``budget``.

    Only safe for plain-text user/system messages (not tool calls / tool results,
    which must stay intact for pairing). Falls back to the original message when
    the content is not trimmable.
    """
    try:
        content = msg.content
    except Exception:  # noqa: BLE001
        return msg
    if isinstance(content, str) and len(content) > budget:
        from langchain_core.messages import HumanMessage

        if getattr(msg, "type", "") in ("human", "system"):
            return HumanMessage(
                content=content[:budget] + "\n[content truncated by Coworker to fit context]",
                id=getattr(msg, "id", None),
            )
        return msg
    return msg


class ContextWindowMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Trim the model-bound message list to the runtime context budget.

    Long sessions otherwise replay the entire history on every model call (and
    grow the checkpoint), eventually exceeding the provider's context window and
    failing the turn with a 400. Dropping the OLDEST messages (keeping the first
    system message + the most recent exchanges) is a safe rolling window.

    The budget is derived at runtime from the provider's model context window
    (see :func:`context_budget_chars`). The trim must be expressed with
    ``RemoveMessage``: the ``messages`` state channel uses the ``add_messages``
    reducer, so merely returning a shorter list merges by message id and does NOT
    delete the trimmed messages. Tool messages are also kept paired with their
    triggering AIMessage so the provider never sees an orphaned ToolMessage.
    """

    def __init__(self, budget_chars: int = DEFAULT_CONTEXT_WINDOW_CHARS, llm: Any | None = None):
        self.budget_chars = max(20_000, int(budget_chars or DEFAULT_CONTEXT_WINDOW_CHARS))
        # Optional model used for summary compaction. When set, over-budget turns
        # summarize the OLDEST segment instead of dropping it entirely; when unset
        # (or on summarization failure) the plain rolling trim is used.
        self.llm = llm
        self._summarized_segments: set[str] = set()

    def _trim(self, state: CoworkerAgentState) -> list[Any] | None:
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        messages = state.get("messages", [])
        if not messages:
            return None
        total = sum(_msg_chars(m) for m in messages)
        if total <= self.budget_chars:
            return None
        # Keep the first message (system prompt) and then the most recent tail
        # (oldest-first drop). Oversized user/system content is truncated instead
        # of dropped so the model still sees the user's current input; oversized
        # tool/AI messages are dropped (cannot be truncated without breaking
        # tool-call pairing).
        head: list[Any] = []
        budget = self.budget_chars
        for msg in messages[:1]:
            chars = _msg_chars(msg)
            if chars > budget:
                msg = _truncate_message(msg, budget)
            head.append(msg)
            budget -= _msg_chars(msg)

        kept_tail: list[Any] = []
        for msg in reversed(messages[1:]):
            chars = _msg_chars(msg)
            if chars >= self.budget_chars:
                # Oversized message: truncate user/system, drop tool/AI.
                if getattr(msg, "type", "") in ("human", "system", "user"):
                    kept_tail.append(_truncate_message(msg, self.budget_chars))
                    budget = 0
                    break
                continue
            if budget - chars < 0:
                break
            kept_tail.append(msg)
            budget -= chars

        kept_tail.reverse()
        # Drop any leading ToolMessage whose triggering AIMessage landed in the
        # trimmed gap (a ToolMessage is always preceded by its AIMessage in the
        # list; keeping it alone would 400 the provider).
        while kept_tail and getattr(kept_tail[0], "type", "") == "tool":
            kept_tail.pop(0)

        kept = head + kept_tail
        if len(kept) == len(messages):
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept]}

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._trim(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        # Summary compaction: when the LLM is available and we are over budget,
        # summarize the OLDEST segment (keeping the recent tail intact) instead of
        # dropping it. Falls back to the plain rolling trim on any failure.
        if self.llm is not None:
            try:
                compacted = await self._compact(state)
                if compacted is not None:
                    return compacted
            except Exception:  # noqa: BLE001 - compaction must never break a turn
                logger.warning("context compaction failed; falling back to trim", exc_info=True)
        return self._trim(state)

    async def _compact(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        """Replace the oldest over-budget segment with a model-generated summary.

        Keeps the first system message and the ``keep_recent`` tail untouched;
        the middle (oldest) messages are summarized into a single system message
        carrying the key facts (paths, commands, ports, decisions). Never raises:
        any failure returns ``None`` so the caller falls back to rolling trim.
        """
        from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

        from langchain_core.messages import SystemMessage

        messages = state.get("messages", [])
        if len(messages) < 4:
            return None
        total = sum(_msg_chars(m) for m in messages)
        if total <= self.budget_chars:
            return None

        keep_recent = max(8_000, int(self.budget_chars * KEEP_RECENT_FACTOR))
        recent: list[Any] = []
        recent_budget = keep_recent
        for msg in reversed(messages[1:]):
            chars = _msg_chars(msg)
            if recent_budget - chars < 0 and recent:
                break
            recent.append(msg)
            recent_budget -= chars
        recent.reverse()
        if len(recent) >= len(messages) - 1:
            return None

        # The oldest segment to summarize = everything between the first message
        # (system) and the recent tail.
        old_count = len(messages) - 1 - len(recent)
        if old_count < 2:
            return None

        old_messages = messages[1 : 1 + old_count]
        fingerprint = "|".join(getattr(m, "id", "") or "" for m in old_messages)
        if fingerprint in self._summarized_segments:
            # Already summarized this exact segment on a prior turn: do not loop.
            return None
        self._summarized_segments.add(fingerprint)
        if len(self._summarized_segments) > 64:
            self._summarized_segments.clear()

        summary = await self._summarize_segment(old_messages)
        if not summary:
            return None

        kept = [messages[0], SystemMessage(content=f"[Earlier conversation summary] {summary}", id="__compaction__"), *recent]
        # Memory-flush reminder: tell the model the oldest history was compacted
        # and it should persist any still-relevant facts into long-term memory so
        # they survive beyond this session (ties into the auto-memory pipeline).
        kept.append(
            SystemMessage(
                content=(
                    "Note: the oldest part of this conversation was summarized "
                    "above to keep the context compact. If any durable fact in it "
                    "still matters for future sessions, persist it via the memory "
                    "tool now."
                ),
                id="__compaction_flush__",
            )
        )
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept]}

    async def _summarize_segment(self, messages: list[Any]) -> str:
        """Ask the model to condense the oldest messages into a concise summary."""
        from langchain_core.messages import SystemMessage, HumanMessage

        transcript = []
        for msg in messages:
            role = getattr(msg, "type", "")
            text = _msg_chars(msg)
            if text <= 0:
                continue
            role_label = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}.get(role, role)
            transcript.append(f"{role_label}: {text}")
        transcript_text = "\n".join(transcript)
        if not transcript_text.strip():
            return ""
        # Cap the transcript fed to the summarizer so a pathological segment
        # cannot blow up the compaction call itself.
        if len(transcript_text) > 40_000:
            transcript_text = transcript_text[-40_000:]
        system = SystemMessage(
            content=(
                "You are summarizing the OLDEST part of a working session so the "
                "agent can continue with a compact context. Preserve every durable "
                "fact that still matters: file paths, command lines, ports, IDs, "
                "user preferences, decisions and their rationale. Drop transient "
                "noise. Return ONLY a dense factual summary, ~150-250 words, no "
                "preamble."
            )
        )
        try:
            response = await self.llm.ainvoke([system, HumanMessage(content=transcript_text)])
            text = str(getattr(response, "content", "") or response or "")
            text = text.strip()
            if len(text) > 2500:
                text = text[:2500]
            return text
        except Exception:  # noqa: BLE001 - summarization is best-effort
            logger.warning("context summarization failed", exc_info=True)
            return ""


# ---------------------------------------------------------------------------
# ToolCallCleanerMiddleware – drops empty/invalid tool calls before execution.
# ---------------------------------------------------------------------------


class ToolCallCleanerMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Removes tool calls that the provider emitted without a tool name.

    Some OpenAI-compatible streaming servers (e.g. vLLM with Qwen3.6) can emit
    a parallel tool call whose delta never carries a ``name``, leaving an empty
    ``{"name": "", "args": {}}`` entry in the assistant message. LangChain keeps
    such entries in ``tool_calls``; executing them fails with an invalid-tool
    error, and the corrupted entry is then replayed to the provider on the next
    model call, producing a 400 (``Extra data``). This middleware strips these
    empty tool calls right after the model call so they never reach the tool
    executor or the provider.
    """

    def _clean(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        replacements: list[Any] = []
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                continue
            invalid = [t for t in tool_calls if not (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            if not invalid:
                continue
            from langchain_core.messages import AIMessage
            valid = [t for t in tool_calls if (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))]
            replacements.append(AIMessage(
                content=getattr(msg, "content", None) or "",
                tool_calls=valid,
                id=getattr(msg, "id", None),
                additional_kwargs=getattr(msg, "additional_kwargs", None) or {},
            ))
        if not replacements:
            return None
        return {"messages": replacements}

    def after_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)

    async def aafter_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._clean(state)


# ---------------------------------------------------------------------------
# PhaseToolGateMiddleware – phase-driven tool gating (Codex-style autonomy).
# ---------------------------------------------------------------------------

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

    The phase/autonomy-aware system prompt is also injected here so there is a
    single prompt source (fixing the previous double-injection).

    MCP tools are treated as execute-phase tools: they are allowed (and only
    visible) while ``phase == "execute"``. A provider callable supplies the
    currently-connected MCP tool names so the gate can tell them apart from
    unknown tool calls without coupling this module to the MCP layer.
    """

    def __init__(self, mcp_tool_names_provider: Callable[[], set[str]] | None = None):
        self.mcp_tool_names_provider = mcp_tool_names_provider

    def _allowed_tools(self, state: CoworkerAgentState) -> set[str]:
        work_mode = normalize_work_mode(state.get("work_mode"))
        phase = normalize_phase(state.get("phase"), work_mode)
        autonomy = normalize_autonomy(state.get("autonomy"))
        allowed = set(_READ_ONLY_TOOLS) | _MEMORY_TOOLS
        if phase == "discuss":
            allowed |= _PLAN_TOOLS
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
        # goal): write_todos only writes graph state, never files, so it stays
        # safe in the read-only discuss phase too.
        allowed.add("write_todos")
        if state.get("goal_mode"):
            allowed |= {"finalize_goal", "write_todos"}
        return allowed

    def _overrides(self, request: Any) -> dict[str, Any]:
        state = request.state
        allowed = self._allowed_tools(state)
        tools = [tool for tool in request.tools if getattr(tool, "name", "") in allowed]
        language = normalize_language(state.get("language"))
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        autonomy = normalize_autonomy(state.get("autonomy"))
        return {
            "tools": tools,
            "system_message": SystemMessage(phase_system_prompt(language, phase, autonomy)),
        }

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
        from langchain_core.messages import ToolMessage
        tool_name = self._tool_name(request)
        return ToolMessage(
            content=f"Tool '{tool_name}' is not available in the current phase/autonomy. It was skipped.",
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


# ---------------------------------------------------------------------------
# GoalModeMiddleware – autonomous /goal loop (Codex Goal mode).
# ---------------------------------------------------------------------------

GOAL_MARKER = "[CW-GOAL]"

# Max forced-continuation nudges before declaring a goal stalled. Shared by
# GoalModeMiddleware (docstring reference) and the goal loop in
# OpenAICompatibleStreamRuntime, which previously referenced `self.MAX_FORCE`
# on a class that never defined it (AttributeError on every stall).
GOAL_MAX_FORCE = 3


class FinalizeGoalArgs(BaseModel):
    achieved: bool = Field(description="True only when the goal is fully achieved and verified.")
    progress: str = Field(default="", description="1-2 sentence status of the current round.")
    verification: str = Field(default="", description="Evidence (tests/commands that passed) when achieved=True.")


def _goal_tools() -> list[Any]:
    from langchain_core.tools import tool

    @tool(args_schema=FinalizeGoalArgs)
    def finalize_goal(achieved: bool, progress: str = "", verification: str = "") -> str:
        """Declare the current goal round complete.

        Call ``finalize_goal(achieved=True, verification=...)`` ONLY when the goal
        is fully achieved and verified (tests/checks passed). Call
        ``finalize_goal(achieved=False, progress=...)`` after finishing a work chunk
        when more work remains — the system continues the goal automatically.
        Do NOT give a final answer without calling this tool first.
        """
        return json.dumps(
            {"achieved": bool(achieved), "progress": str(progress)[:500], "verification": str(verification)[:500]},
            ensure_ascii=False,
        )

    return [finalize_goal]


def goal_system_prompt(language: Language, goal_text: str, autonomy: Autonomy = "autonomous") -> str:
    lang_line = f"Reply in {language_name(language)}."
    if autonomy == "autonomous":
        mode_line = (
            "You are working toward a persistent goal in fully autonomous mode: do not "
            "ask the user anything, make reasonable decisions and keep working."
        )
    elif autonomy == "supervised":
        mode_line = (
            "You are working toward a persistent goal in supervised mode: each write or "
            "command may require the user's approval before it runs. Ask for approval only "
            "when needed."
        )
    else:
        mode_line = (
            "You are working toward a persistent goal in guarded mode: work freely inside "
            "the workspace. You may call ask_user only when you are genuinely blocked and "
            "need a decision; otherwise proceed autonomously."
        )
    return (
        f"{lang_line}\n"
        f"{mode_line}\n"
        f"GOAL: {goal_text}\n\n"
        "Work continuously toward the goal.\n"
        "1. Break the work into concrete todos with `write_todos`, marking each "
        "in_progress/completed as you go.\n"
        "2. Research, edit files and run workspace commands. Use `run_command` to run "
        "tests/checks that prove real progress.\n"
        "3. After a work chunk, if more remains, call "
        "`finalize_goal(achieved=false, progress=<1-2 sentence status>)` — the system "
        "starts the next round automatically.\n"
        "4. When the goal is FULLY achieved and verified, you MUST call "
        "`finalize_goal(achieved=true, verification=<evidence: tests/commands that passed>)`.\n"
        "5. CRITICAL: A plain-text answer does NOT complete the goal. The ONLY way to "
        "finish is to call `finalize_goal(achieved=true, ...)`. Do NOT stop with a "
        "text summary before calling it. If you are truly blocked, call "
        "`finalize_goal(achieved=false, progress=<blocker>)`."
    )


class GoalModeMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Autonomous goal loop (Codex ``/goal``).

    * Injects the goal system prompt and exposes ``finalize_goal``.
    * Guards against premature conclusions: ``wrap_model_call`` re-invokes the
      model (up to ``MAX_FORCE`` times) with a continuation nudge whenever it
      returns plain text without having called ``finalize_goal`` this round, so
      the loop keeps working instead of stopping early.
    """

    MAX_FORCE = GOAL_MAX_FORCE

    def __init__(self, language: Language = "en"):
        self.language = language
        self._finalize_tool = _goal_tools()[0]

    def _active(self, state: CoworkerAgentState) -> bool:
        return bool(state.get("goal_mode"))

    def _finalize_called(self, state: CoworkerAgentState) -> bool:
        from langchain_core.messages import AIMessage, ToolMessage
        finalized: set[str] = set()
        for msg in state.get("messages", []):
            if isinstance(msg, AIMessage):
                for tc in getattr(msg, "tool_calls", None) or []:
                    if tc.get("name") == "finalize_goal" and tc.get("id"):
                        finalized.add(tc["id"])
        for msg in state.get("messages", []):
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None) in finalized:
                return True
        return False

    def _overrides(self, request: Any) -> dict[str, Any]:
        tools = list(request.tools)
        if not any(getattr(t, "name", "") == "finalize_goal" for t in tools):
            tools.append(self._finalize_tool)
        goal_text = str(request.state.get("goal_text") or "")
        autonomy = normalize_autonomy(request.state.get("autonomy"))
        nudge = request.state.get("goal_nudge")
        prompt = goal_system_prompt(self.language, goal_text, autonomy)
        if nudge:
            prompt = f"{prompt}\n\n[Coworker] {str(nudge)[:1000]}"
        return {
            "tools": tools,
            "system_message": SystemMessage(prompt),
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._active(request.state):
            return handler(request)
        return handler(request.override(**self._overrides(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._active(request.state):
            return await handler(request)
        return await handler(request.override(**self._overrides(request)))


class RepeatedToolCallMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Stop models from blindly repeating the same failing tool call.

    ``create_agent`` hard-codes ``recursion_limit: 9_999``, so a model that
    re-emits one failing call (e.g. a ``find`` blocked by permissions) loops
    effectively forever. This middleware counts CONSECUTIVE identical tool
    calls already in the run history; when the warning threshold is crossed it
    tells the model to change approach, and when the hard cap is crossed it
    strips every tool for the next model call so the model MUST reply with a
    text-only final answer (the same "last step" mechanism opencode uses).

    Only the trailing run of identical (name + canonicalized args) calls counts,
    so ordinary long tasks are unaffected. Mounted last (innermost) so its
    overrides are applied after PhaseToolGateMiddleware / SkillMiddleware /
    MemoryMiddleware.
    """

    def __init__(self, warn_after: int = 2, stop_after: int = 4) -> None:
        self.warn_after = max(1, int(warn_after))
        self.stop_after = max(self.warn_after + 1, int(stop_after))

    @staticmethod
    def _call_key(tool_call: Any) -> tuple[str, str]:
        name = tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
        try:
            canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            canonical = str(args)
        return str(name), canonical

    def _consecutive_repeats(self, messages: list[Any]) -> tuple[int, str, str]:
        """Return (count, name, last_result) for the trailing run of identical
        tool calls. ``count`` is how many identical calls are already in the
        history (0 = none)."""
        count = 0
        name = ""
        prev_key: tuple[str, str] | None = None
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                i -= 1
                continue
            if isinstance(msg, AIMessage):
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    break
                key = self._call_key(calls[-1])
                if prev_key is None:
                    prev_key = key
                    count = 1
                elif key == prev_key:
                    count += 1
                else:
                    break
            else:
                break
            i -= 1
        if prev_key is not None:
            name = prev_key[0]
        last_result = ""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage) and (getattr(msg, "name", "") or "") == name:
                last_result = str(getattr(msg, "content", ""))[:200]
                break
        return count, name, last_result

    def _overrides(self, request: Any) -> dict[str, Any]:
        count, name, last_result = self._consecutive_repeats(list(request.messages or []))
        if count < self.warn_after:
            return {}
        if count >= self.stop_after:
            msg = (
                f"STOP. You have already run '{name}' {count} times and it keeps "
                f"failing. Do NOT make any more tool calls. Provide your final "
                f"answer as plain text now and explain what went wrong."
            )
            return {"tools": [], "messages": [*request.messages, HumanMessage(content=msg)]}
        last_line = f" Last result: {last_result}" if last_result else ""
        msg = (
            f"WARNING: You have already run '{name}' {count} times in a row and it "
            f"has not succeeded. Do NOT repeat the exact same call.{last_line} "
            f"Change approach (different path, different tool, narrower scope) "
            f"or answer directly."
        )
        return {"messages": [*request.messages, HumanMessage(content=msg)]}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return handler(request)
        return handler(request.override(**overrides))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        overrides = self._overrides(request)
        if not overrides:
            return await handler(request)
        return await handler(request.override(**overrides))


# ---------------------------------------------------------------------------
# Agent builder – single create_agent graph (official langchain idiom).
# ---------------------------------------------------------------------------

def build_coworker_agent_graph(
    llm: Any,
    tools: list[Any],
    work_mode: WorkMode,
    language: Language,
    autonomy: Autonomy = "guarded",
    checkpointer: Any | None = None,
    approval_store: CommandApprovalStore | None = None,
    goal_mode: bool = False,
    data_dir: Path | None = None,
    mcp_session_manager: Any | None = None,
    skill_manager: Any | None = None,
    memory_manager: Any | None = None,
    workspace: Any | None = None,  # NEW: for external write HITL bridge
    context_budget: int = DEFAULT_CONTEXT_WINDOW_CHARS,
) -> Any:
    """Compile the Coworker agent as a single ``create_agent`` graph.

    The middleware chain implements the Codex-style two-axis model:

    * ``PhaseToolGateMiddleware`` filters the tool set each model call based on
      the current ``phase``/``autonomy`` (physical enforcement, not prompt).
    * ``GoalModeMiddleware`` drives the autonomous /goal loop when ``goal_mode``
      is active (goal prompt + finalize_goal/continue_goal + premature-end guard).
    * ``TodoListMiddleware`` (always mounted) exposes ``write_todos`` in every
      mode so the agent can break its task into a visible checklist that the UI
      renders as the TodoBlock card.
    * ``HumanInTheLoopMiddleware`` (always mounted) interrupts commands/writes
      only in ``execute`` + ``supervised``, and ``ask_user`` regardless.
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware.todo import TodoListMiddleware

    from .mcp.mcp_middleware import McpToolMiddleware

    if mcp_session_manager is None:
        from .mcp.mcp_session import McpSessionManager

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

    phase_gate = PhaseToolGateMiddleware()
    context_middleware = ContextWindowMiddleware(context_budget, llm=llm)
    middleware: list[Any] = [
        NormalizeMessagesMiddleware(),
        context_middleware,
        ToolCallCleanerMiddleware(),
        phase_gate,
        GoalModeMiddleware(language),
        # Task-list management in EVERY mode (build / plan / goal): the agent
        # breaks its work into a `write_todos` checklist and keeps it updated as
        # it completes each step. Read-only-safe (writes graph state only).
        TodoListMiddleware(),
    ]
    if goal_mode:
        # Register the goal-completion tool in the graph's tool registry so the
        # tool node can execute it (the middleware also exposes it to the model).
        tools = [*tools, _goal_tools()[0]]
    middleware.extend(command_approval_middleware(approval_store, mcp_middleware.tool_policy, workspace=workspace))

    middleware.append(mcp_middleware)
    phase_gate.mcp_tool_names_provider = mcp_middleware.tool_names    # Skills: inject the catalog (name+description+location) into the system
    # prompt; the full SKILL.md body loads on demand via read_file.
    if skill_manager is not None:
        from .skills.skill_middleware import SkillMiddleware

        middleware.append(SkillMiddleware(skill_manager))

    # Memory: inject long-term memory into every phase (planning needs the
    # user's background facts most of all). Mounted after skills so the memory
    # section lands before the skills catalog in the system prompt. Writes are
    # gated separately by the phase gate + HITL middleware via the `memory`
    # tool.
    if memory_manager is not None:
        from .memory.memory_middleware import MemoryMiddleware

        try:
            middleware.append(MemoryMiddleware(memory_manager))
        except Exception as exc:  # noqa: BLE001 - a broken memory middleware must not break chat
            logger.warning("Memory middleware unavailable: %s", exc)

    # Loop guard (innermost): the model must never re-run the same failing
    # tool call forever. create_agent's default recursion_limit (9_999) makes
    # an unguarded loop effectively infinite, so cap identical consecutive
    # calls here and force a text-only final turn on the hard cap.
    middleware.append(RepeatedToolCallMiddleware())

    system_prompt = (
        f"You are Coworker, a local coding assistant. Reply in {language_name(language)}. "
        "Use workspace tools only when they are needed and keep answers concise. "
        "If a tool call fails, do NOT re-run the exact same call; analyze the error and "
        "change approach (narrow the scope, pick a different tool) or summarize and answer directly."
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
    except Exception:  # noqa: BLE001 - best-effort hook
        pass
    return graph


# ---------------------------------------------------------------------------
# Concrete runtimes
# ---------------------------------------------------------------------------

class SimulatedSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {autonomy}\n\n你说：{message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {autonomy}\n\nYou said: {message}"
            )
        return AgentReply(content=content, mode=self.mode, provider="simulated")


class OpenAICompatibleSingleAgentRuntime(AgentRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpointer: Any, provider: ProviderEntry, model_override: str | None = None, change_store: ChangeStore | None = None, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None, data_dir: Path | None = None, mcp_session_manager: Any | None = None, skill_manager: Any | None = None, memory_manager: Any | None = None, project_store: Any | None = None, agent: str = DEFAULT_AGENT_NAME):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpointer = checkpointer
        self.change_store = change_store
        self.session_store = session_store
        self.referenced_sessions = set(referenced_sessions or set())
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.context_budget_chars = _runtime_context_budget(provider, model_override)
        self.agent = agent or DEFAULT_AGENT_NAME

    @property
    def _memory(self) -> tuple[Any | None, Any | None, str]:
        """Return ``(project_scoped_manager, memory_store, agent_memory_rel)``."""
        if self.memory_manager is None or not getattr(self.memory_manager, "enabled", False):
            return None, None, ""
        project_dir = _resolve_project_memory_dir(self.project_store, str(self.workspace.root))
        view = self.memory_manager.for_project(project_dir, self.agent)
        agent_rel = ""
        if project_dir:
            agent_rel = f"{project_dir}/{self.agent}/BASE/MEMORY.md"
        return view, getattr(view, "store", None), agent_rel

    def _nudge_memory(self, session_id: str) -> None:
        """Phase 2: one call per settled turn; never blocks or raises."""
        try:
            if self.memory_manager is not None:
                self.memory_manager.after_turn(session_id, workspace_root=self.workspace.root)
        except Exception:  # noqa: BLE001 - a memory hiccup must never break a turn
            logger.warning("memory nudge failed", exc_info=True)

    def _build_delegator(self, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy):
        """Return a team Delegator when the project org is multi-agent, else None."""
        try:
            from .delegation import Delegator

            org_store = getattr(self.memory_manager, "org_store", None)
            if org_store is None or not getattr(self.memory_manager, "enabled", False):
                return None
            project_dir = _resolve_project_memory_dir(self.project_store, str(self.workspace.root))
            if not project_dir or not org_store.exists(project_dir):
                return None
            org = org_store.load(project_dir)
            if getattr(org, "mode", "multi") != "multi":
                return None
            if not org_store.is_active(org, self.agent):
                return None
            return Delegator(
                org_store=org_store,
                memory_manager=self.memory_manager,
                project_store=self.project_store,
                workspace=self.workspace,
                caller_agent=self.agent,
                project_dir=project_dir,
                language=language,
                work_mode=work_mode,
                autonomy=autonomy,
                session_id=session_id,
                provider_name=self.provider_name,
                model_name=self.model_name,
                llm=self.llm,
                trace_store=self.trace_store,
                approval_store=self.approval_store,
                change_store=self.change_store,
                session_store=self.session_store,
                data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                emit=self._delegation_event,
            )
        except Exception:  # noqa: BLE001 - delegation must never break a turn
            logger.warning("delegation disabled", exc_info=True)
            return None

    def _delegation_event(self, event: dict[str, Any]) -> None:
        """Sink for delegation SSE frames (no-op in the sync path)."""
        return

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    def run(self, message: str, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AgentReply:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=False,
        )
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "run"})
        turn_index = self._next_turn_index(session_id)
        self._nudge_memory(session_id)
        memory_view, memory_store, memory_rel = self._memory
        delegator = self._build_delegator(session_id, language, work_mode, autonomy)
        graph = build_coworker_agent_graph(
            self.llm,
            build_workspace_tools(
                self.workspace, audit_context, change_store=self.change_store, turn_index=turn_index,
                session_store=self.session_store, referenced_sessions=self.referenced_sessions,
                skill_manager=self.skill_manager,
                memory_store=memory_store,
                memory_rel=memory_rel,
                delegator=delegator,
                caller_agent=self.agent,
            ),
            work_mode=work_mode,
            language=language,
            autonomy=autonomy,
            checkpointer=self.checkpointer,
            approval_store=self.approval_store,
            data_dir=self.data_dir,
            mcp_session_manager=self.mcp_session_manager,
            skill_manager=self.skill_manager,
            memory_manager=memory_view,
            workspace=self.workspace,
            context_budget=self.context_budget_chars,
        )
        try:
            result = graph.invoke(
                {
                    "messages": prepare_agent_messages([{"role": "user", "content": message}]),
                    "work_mode": work_mode,
                    "language": language,
                    "phase": normalize_phase(None, work_mode),
                    "autonomy": autonomy,
                },
                config=agent_run_config(
                    session_id=session_id, provider=self.provider_name, model=self.model_name,
                    language=language, work_mode=work_mode, autonomy=autonomy, streaming=False,
                ),
            )
        except Exception as exc:
            self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
            raise
        if "__interrupt__" in result:
            approvals = record_runtime_interrupts(
                result["__interrupt__"], self.approval_store,
                {**audit_context, "language": language, "work_mode": work_mode, "autonomy": autonomy, "referenced_sessions": list(self.referenced_sessions)},
                mcp_policy_resolver(self.mcp_session_manager),
            )
            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals]})
            approval_ids = ", ".join(str(a.get("id", "")) for a in approvals)
            content = f"Command approval required: {approval_ids}" if language == "en" else f"命令需要审批：{approval_ids}"
            return AgentReply(content=content, mode=self.mode, provider=self.provider_name)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        content = coerce_message_content(messages[-1]) if messages else ""
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(content)})
        return AgentReply(content=content, mode=self.mode, provider=self.provider_name)


class OpenAICompatibleStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"
    owns_runtime_messages = True

    def __init__(self, workspace: Workspace, approval_store: CommandApprovalStore, trace_store: AgentTraceStore, checkpoint_path: Path, provider: ProviderEntry, model_override: str | None = None, change_store: ChangeStore | None = None, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None, data_dir: Path | None = None, mcp_session_manager: Any | None = None, skill_manager: Any | None = None, memory_manager: Any | None = None, project_store: Any | None = None, agent: str = DEFAULT_AGENT_NAME):
        llm_cls = ReasonPreservingChatOpenAI.create
        self.provider_id = provider.id
        self.provider_name = provider.name
        self.model_name = model_override or provider.model
        self.llm = llm_cls(model=self.model_name, temperature=0, api_key=provider.api_key or os.getenv("OPENAI_API_KEY") or "not-needed", base_url=self._openai_compatible_base_url(provider))
        self.workspace = workspace
        self.approval_store = approval_store
        self.trace_store = trace_store
        self.checkpoint_path = checkpoint_path
        self.change_store = change_store
        self.session_store = session_store
        self.referenced_sessions = set(referenced_sessions or set())
        self.data_dir = data_dir
        self.mcp_session_manager = mcp_session_manager
        self.skill_manager = skill_manager
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.agent = agent or DEFAULT_AGENT_NAME
        self._delegation_buffer: list[dict[str, Any]] = []
        self.context_budget_chars = _runtime_context_budget(provider, model_override)
    @property
    def _memory(self) -> tuple[Any | None, Any | None, str]:
        """Return ``(project_scoped_manager, memory_store, agent_memory_rel)``."""
        if self.memory_manager is None or not getattr(self.memory_manager, "enabled", False):
            return None, None, ""
        project_dir = _resolve_project_memory_dir(self.project_store, str(self.workspace.root))
        view = self.memory_manager.for_project(project_dir, self.agent)
        agent_rel = ""
        if project_dir:
            agent_rel = f"{project_dir}/{self.agent}/BASE/MEMORY.md"
        return view, getattr(view, "store", None), agent_rel

    def _nudge_memory(self, session_id: str) -> None:
        """Phase 2: one call per settled turn; never blocks or raises."""
        try:
            if self.memory_manager is not None:
                self.memory_manager.after_turn(session_id, workspace_root=self.workspace.root)
        except Exception:  # noqa: BLE001 - a memory hiccup must never break a turn
            logger.warning("memory nudge failed", exc_info=True)

    def _build_delegator(self, session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy):
        """Return a team Delegator when the project org is multi-agent, else None."""
        try:
            from .delegation import Delegator

            org_store = getattr(self.memory_manager, "org_store", None)
            if org_store is None or not getattr(self.memory_manager, "enabled", False):
                return None
            project_dir = _resolve_project_memory_dir(self.project_store, str(self.workspace.root))
            if not project_dir or not org_store.exists(project_dir):
                return None
            org = org_store.load(project_dir)
            if getattr(org, "mode", "multi") != "multi":
                return None
            if not org_store.is_active(org, self.agent):
                return None
            return Delegator(
                org_store=org_store,
                memory_manager=self.memory_manager,
                project_store=self.project_store,
                workspace=self.workspace,
                caller_agent=self.agent,
                project_dir=project_dir,
                language=language,
                work_mode=work_mode,
                autonomy=autonomy,
                session_id=session_id,
                provider_name=self.provider_name,
                model_name=self.model_name,
                llm=self.llm,
                trace_store=self.trace_store,
                approval_store=self.approval_store,
                change_store=self.change_store,
                session_store=self.session_store,
                data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                emit=self._delegation_event,
            )
        except Exception:  # noqa: BLE001 - delegation must never break a turn
            logger.warning("delegation disabled", exc_info=True)
            return None

    def _delegation_event(self, event: dict[str, Any]) -> None:
        """Buffer a delegation SSE frame for the streaming loop to drain."""
        try:
            self._delegation_buffer.append(event)
        except Exception:  # noqa: BLE001 - never break on buffer append
            pass

    def _drain_delegation_events(self) -> list[dict[str, Any]]:
        try:
            events = list(self._delegation_buffer)
            self._delegation_buffer.clear()
            return events
        except Exception:  # noqa: BLE001
            return []

    async def _force_compact(self, graph: Any, inputs: dict[str, Any], config: Any) -> None:
        """Halve the context budget on the middleware and nudge the checkpoint
        so the overflow retry sends a strictly smaller request.

        The middleware's ``budget_chars`` is mutable and read on every
        ``abefore_model``; halving it guarantees the retried turn trims harder.
        """
        try:
            middleware = getattr(graph, "_cw_context_middleware", None)
            if middleware is not None and hasattr(middleware, "budget_chars"):
                middleware.budget_chars = max(20_000, int(middleware.budget_chars * 0.5))
            logger.info("forced context compaction for overflow retry (budget halved)")
        except Exception:  # noqa: BLE001 - best-effort
            logger.warning("overflow compaction failed", exc_info=True)

    @staticmethod
    def _openai_compatible_base_url(provider: ProviderEntry) -> str:
        base_url = provider.base_url.rstrip("/")
        if provider.provider_type == "ollama" and not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    async def stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=False):
            yield event

    async def stream_rerun(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Re-run the agent from a full message history (rollback/regenerate/edit).

        Unlike ``stream``, this treats the given messages as the complete initial
        state (no checkpoint append). The session checkpoint must already have
        been reset by the caller so the history is rebuilt from scratch.
        """
        async for event in self._stream(messages, session_id, language, work_mode, autonomy, rerun=True):
            yield event

    async def _stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy, *, rerun: bool,
        goal_mode: bool = False, goal_text: str = "", goal_continue: bool = False, _nudge: str = "", _cancel_event: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        interrupt_context = {**audit_context, "language": language, "work_mode": work_mode, "autonomy": autonomy, "referenced_sessions": list(self.referenced_sessions)}
        self.trace_store.record("agent_activity", "start", current_trace_context, {"activity": "rerun" if rerun else "stream"})
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": self.provider_name, "model": self.model_name}
        yield {"type": "stage", "name": "executing", "status": "running"}

        if goal_continue:
            # Round 2+ of a goal: continue the thread from the checkpoint without
            # adding a new user message.
            prepared_messages = []
        else:
            prepared_messages = prepare_agent_messages(messages)
        turn_index = self._next_turn_index(session_id)
        self._nudge_memory(session_id)
        memory_view, memory_store, memory_rel = self._memory
        delegator = self._build_delegator(session_id, language, work_mode, autonomy)

        async with _open_checkpointer(self.checkpoint_path) as checkpointer:
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(
                    self.workspace, audit_context, change_store=self.change_store, turn_index=turn_index,
                    session_store=self.session_store, referenced_sessions=self.referenced_sessions,
                    skill_manager=self.skill_manager,
                    memory_store=memory_store,
                    memory_rel=memory_rel,
                    delegator=delegator,
                    caller_agent=self.agent,
                ),
                work_mode=work_mode, language=language, autonomy=autonomy,
                checkpointer=checkpointer, approval_store=self.approval_store,
                goal_mode=goal_mode, data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                memory_manager=memory_view,
                workspace=self.workspace,
                context_budget=self.context_budget_chars,
            )

            inputs = {
                "messages": prepared_messages,
                "work_mode": work_mode,
                "language": language,
                "phase": normalize_phase(None, work_mode),
                "autonomy": autonomy,
            }
            if goal_mode:
                inputs["goal_mode"] = True
                inputs["goal_text"] = goal_text
                # A stale goal_nudge left in the checkpoint from a previous
                # force retry must not re-inject into later rounds. Clear it
                # unless THIS call is the force retry carrying a fresh nudge.
                inputs["goal_nudge"] = _nudge or ""
            config = agent_run_config(
                session_id=session_id, provider=self.provider_name, model=self.model_name,
                language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
            )

            content_parts: list[str] = []
            tool_state: dict[str, dict[str, Any]] = {}
            parts: list[dict[str, Any]] = []

            # Overflow recovery: run the stream, and if the provider rejects the
            # request for being too long before anything was emitted, force a
            # tighter budget (compact once) and retry the graph a single time.
            for _attempt in range(2):
                try:
                    async for stream_mode, chunk in graph.astream(inputs, config=config, stream_mode=["messages", "custom", "updates"]):
                        if stream_mode == "messages":
                            msg, _meta = chunk
                            try:
                                for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                    yield event
                            except GeneratorExit:
                                raise
                            except Exception:
                                # The stream must keep going (the chunk is non-fatal),
                                # but never swallow it silently — a missing tool card /
                                # text segment would otherwise be undiagnosable.
                                logger.exception("Failed to emit message-chunk event")
                        elif stream_mode == "custom":
                            if isinstance(chunk, dict):
                                event_type = chunk.get("type", "")
                                if event_type in ("plan_start", "plan_delta", "plan_end"):
                                    parts.append(chunk)
                                    yield chunk
                        elif stream_mode == "updates":
                            if "__interrupt__" in chunk:
                                approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, interrupt_context, mcp_policy_resolver(self.mcp_session_manager))
                                self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals]})
                                for approval in approvals:
                                    event = stream_event_from_interrupt(approval)
                                    yield event
                                return
                            # write_todos updates the todo list via a Command state update.
                            for node_update in chunk.values():
                                if isinstance(node_update, dict) and "todos" in node_update:
                                    yield {"type": "todos", "todos": node_update.get("todos") or []}
                            # Drain any delegation SSE frames buffered by the delegate tools.
                            for delegate_event in self._drain_delegation_events():
                                yield delegate_event
                            if _cancel_event and _cancel_event.is_set():
                                raise asyncio.CancelledError("Goal cancelled mid-stream")
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if (
                        _attempt == 0
                        and not content_parts
                        and not parts
                        and is_context_overflow_error(exc)
                    ):
                        logger.warning("context overflow; compacting and retrying once: %s", str(exc)[:200])
                        await self._force_compact(graph, inputs, config)
                        continue
                    self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400]})
                    raise

        final_content = "".join(content_parts)
        final_content = _strip_plan_leak(final_content, parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content)})
        merged_parts = _merge_event_parts(_terminate_stray_tools(parts))
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": merged_parts}

    async def goal_stream(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        language: Language,
        work_mode: WorkMode,
        autonomy: Autonomy,
        goal_text: str = "",
        goal_continue_first: bool = False,
        _cancel_event: Any = None,
        goal_stream_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Autonomous /goal loop (Codex Goal mode): rounds until the agent calls
        ``finalize_goal(achieved=True)`` or the goal is paused.

        Each round is one agent turn (model → tools → … → finalize_goal or a
        status). Pause is honoured at round boundaries: the current round finishes,
        then the loop stops; ``/goal/resume`` re-enters with ``goal_continue_first``.
        """
        if self.session_store is not None:
            try:
                session_state = self.session_store.require(session_id)
                goal_text = str(session_state.goal_text or goal_text)
                # Preserve 0 (unlimited); only fall back to 50 when unset (None).
                goal_max_rounds = 50 if session_state.goal_max_rounds is None else int(session_state.goal_max_rounds)
                if session_state.goal_interrupted:
                    yield {"type": "goal_done", "round": 0, "goal": goal_text, "content": "", "reason": "interrupted"}
                    return
                if session_state.goal_done:
                    # Already achieved — nothing left to run.
                    yield {"type": "goal_done", "round": 0, "goal": goal_text, "content": "", "already": True}
                    return
            except Exception:
                pass
        last_checkpoint: dict[str, Any] | None = None
        last_todos: list[Any] = []
        round_no = 0
        yield {"type": "goal_start", "goal": goal_text, "session_id": session_id}
        while True:
            round_no += 1
            # Read session state: goal_just_edited flag, goal_text, stop request, max_rounds.
            session_state: Any = None
            if self.session_store is not None:
                try:
                    session_state = self.session_store.require(session_id)
                    goal_text = str(session_state.goal_text or goal_text)
                    if session_state.goal_just_edited:
                        session_state.goal_just_edited = False
                        self.session_store.save(session_state)
                        yield {"type": "goal_edited", "round": round_no, "goal": goal_text, "stream_id": goal_stream_id}
                    if session_state.goal_stopped:
                        yield {
                            "type": "goal_done", "round": round_no, "goal": goal_text,
                            "content": "", "reason": "stopped",
                        }
                        return
                    # Preserve 0 (unlimited); only fall back to 50 when unset (None).
                    goal_max_rounds = 50 if session_state.goal_max_rounds is None else int(session_state.goal_max_rounds)
                except Exception:
                    # Failed to read session state: fail safe to the default cap
                    # rather than 0, which would otherwise mean "unlimited".
                    goal_max_rounds = 50
            else:
                goal_max_rounds = 50
            is_first = round_no == 1
            # Max rounds guard: stop when we exceed goal_max_rounds (0 = no limit).
            if goal_max_rounds > 0 and round_no > goal_max_rounds:
                yield {
                    "type": "goal_done", "round": round_no, "goal": goal_text,
                    "content": "", "reason": "max_rounds_exceeded",
                }
                return
            yield {"type": "goal_round", "round": round_no, "goal": goal_text, "status": "running"}
            round_had_tools = False
            round_had_interrupt = False
            try:
                async with asyncio.timeout(600):  # 5 min per turn
                    async for event in self._stream(
                        messages if (is_first and not goal_continue_first) else [],
                        # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                        goal_mode=True, goal_text=goal_text,
                        goal_continue=(not is_first or goal_continue_first),
                        _cancel_event=_cancel_event,
                    ):
                        etype = event.get("type", "")
                        if etype == "goal_checkpoint":
                            last_checkpoint = event
                        elif etype == "todos":
                            last_todos = event.get("todos") or []
                        elif etype == "tool_start":
                            round_had_tools = True
                        elif etype == "approval_required":
                            round_had_interrupt = True
                        yield event
            except asyncio.TimeoutError:
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                return
            except asyncio.CancelledError:
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                return
            # Round boundary decision.
            paused = False
            if self.session_store is not None:
                try:
                    paused = bool(self.session_store.require(session_id).goal_paused)
                except Exception:
                    paused = False
            achieved = bool(last_checkpoint and last_checkpoint.get("achieved"))
            if round_had_interrupt and not achieved:
                # A human-in-the-loop interrupt (plan / command approval) fired
                # during this goal round. Pause the loop cleanly instead of
                # re-entering and re-triggering the same interrupt repeatedly
                # until the round cap — the user approves, then resumes from the
                # checkpoint.
                if self.session_store is not None:
                    try:
                        self.session_store.update_goal(session_id, goal_paused=True)
                    except Exception:
                        pass
                yield {"type": "goal_paused", "round": round_no, "goal": goal_text}
                return
            if self.session_store is not None:
                try:
                    self.session_store.update_goal(
                        session_id, goal_done=achieved, goal_todos=list(last_todos or []),
                    )
                except Exception:
                    pass
            if achieved:
                yield {
                    "type": "goal_done", "round": round_no, "goal": goal_text,
                    "content": str((last_checkpoint or {}).get("progress", "") or ""),
                    "verification": str((last_checkpoint or {}).get("verification", "") or ""),
                }
                return
            if paused:
                yield {"type": "goal_paused", "round": round_no, "goal": goal_text}
                return
            if last_checkpoint is None and not round_had_tools:
                # The agent concluded with plain text without calling finalize_goal
                # and made no tool calls this round — it is stalling, not working.
                # Apply force-loop nudges up to MAX_FORCE times before giving up.
                if self.session_store is not None:
                    try:
                        session_state = self.session_store.require(session_id)
                        goal_force = int(session_state.goal_force_count or 0)
                    except Exception:
                        goal_force = 0
                else:
                    goal_force = 0
                if goal_force < GOAL_MAX_FORCE:
                    # Yield a force_continue event to inform the client, then
                    # re-invoke _stream with a nudge appended to the user message.
                    yield {"type": "goal_force", "round": round_no, "reason": "agent_without_tools", "count": goal_force}
                    goal_force += 1
                    # Persist the incremented force count so it survives at round boundary
                    if self.session_store is not None:
                        try:
                            self.session_store.update_goal(session_id, goal_force_count=goal_force)
                        except Exception:
                            pass
                    # Reset checkpoint state for the forced continuation round.
                    last_checkpoint = None
                    last_todos = []
                    round_had_tools = False
                    # Re-invoke _stream with a nudge appended via state.
                    nudge = (
                        "\n\n[Coworker] You gave a plain-text answer without calling "
                        "`finalize_goal` or using any tools. Keep working toward the goal "
                        "using your tools (read_file, search_files, write_file, run_command, etc.) "
                        "and call finalize_goal when done."
                    )
                    try:
                        async with asyncio.timeout(5 * 60):  # 5 min per turn
                            async for event in self._stream(
                                # Force retry always CONTINUES the existing thread:
                                # the round's user message was already added by its
                                # first _stream attempt, so re-adding it here would
                                # duplicate the user message in the checkpoint.
                                [],
                                # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                                goal_mode=True, goal_text=goal_text,
                                goal_continue=True,
                                _nudge=nudge,
                                _cancel_event=_cancel_event,
                            ):
                                etype = event.get("type", "")
                                if etype == "goal_checkpoint":
                                    last_checkpoint = event
                                elif etype == "todos":
                                    last_todos = event.get("todos") or []
                                elif etype == "tool_start":
                                    round_had_tools = True
                                yield event
                    except asyncio.TimeoutError:
                        yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                        return
                    except asyncio.CancelledError:
                        yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                        return
                    # Check again after the forced continuation.
                    if last_checkpoint is not None:
                        continue
                    # Second attempt also stalled — check force count again.
                    if self.session_store is not None:
                        try:
                            session_state = self.session_store.require(session_id)
                            goal_force = int(session_state.goal_force_count or 0)
                        except Exception:
                            goal_force = 0
                    if goal_force < GOAL_MAX_FORCE:
                        goal_force += 1
                        yield {"type": "goal_force", "round": round_no, "reason": "agent_without_tools", "count": goal_force}
                        last_checkpoint = None
                        last_todos = []
                        round_had_tools = False
                        if self.session_store is not None:
                            try:
                                self.session_store.update_goal(session_id, goal_force_count=goal_force)
                            except Exception:
                                pass
                        try:
                            async with asyncio.timeout(600):  # 5 min per turn
                                async for event in self._stream(
                                    # Force retry continues the existing thread (see above).
                                    [],
                                    # A goal is an autonomous "do the work" loop: always run in
                        # the execute phase so write/run tools are available,
                        # regardless of the session's plan/build toggle.
                        session_id, language, "build", autonomy, rerun=False,
                                    goal_mode=True, goal_text=goal_text,
                                    goal_continue=True,
                                    _nudge=nudge,
                                    _cancel_event=_cancel_event,
                                ):
                                    etype = event.get("type", "")
                                    if etype == "goal_checkpoint":
                                        last_checkpoint = event
                                    elif etype == "todos":
                                        last_todos = event.get("todos") or []
                                    elif etype == "tool_start":
                                        round_had_tools = True
                                    yield event
                        except asyncio.CancelledError:
                            yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "stopped"}
                            return
                        except asyncio.TimeoutError:
                            yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "reason": "timeout"}
                            return
                    if last_checkpoint is not None:
                        continue
                # Force exhausted or goal_force >= MAX_FORCE — stop with stall signal.
                yield {"type": "goal_done", "round": round_no, "goal": goal_text, "content": "", "stalled": True}
                return

    def _handle_message_chunk(
        self, msg: Any, content_parts: list[str], tool_state: dict[str, dict[str, Any]], parts: list[dict[str, Any]], session_id: str = "",
    ) -> list[dict[str, Any]]:
        from langchain_core.messages import AIMessageChunk, ToolMessage

        events: list[dict[str, Any]] = []

        if isinstance(msg, AIMessageChunk):
            reasoning = _extract_reasoning_from_chunk(msg)
            if reasoning:
                parts.append({"type": "reasoning_delta", "content": reasoning})
                events.append({"type": "reasoning_delta", "content": reasoning})

            text = getattr(msg, "content", "") or ""
            if isinstance(text, str) and text:
                content_parts.append(text)
                # 文本 delta 也进入 parts 列表，_merge_event_parts 会把它按到达顺序
                # 合并成交错排列的 text parts，从而在持久化/重载后保留 text 与 tool
                # 的相对顺序（与 Anthropic content blocks 模型一致）。
                parts.append({"type": "delta", "content": text})
                events.append({"type": "delta", "content": text})

            tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
            for tc in tool_call_chunks:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                tc_name = tc.get("name") or ""
                tc_args = tc.get("args") or ""

                if not tc_id:
                    continue

                if tc_name in ("finalize_goal", "write_todos"):
                    # finalize_goal/write_todos are goal-loop / task-list controls
                    # rendered via goal_checkpoint/todos events, not tool cards.
                    continue

                if tc_id not in tool_state:
                    tool_state[tc_id] = {"name": tc_name or "", "input": "", "status": "running", "started_at": time.time()}
                    parts.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                    events.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                else:
                    tool_state[tc_id]["input"] = tool_state[tc_id].get("input", "") + tc_args
                    if tc_name:
                        tool_state[tc_id]["name"] = tc_name
                        # 名字可能在后续流式 chunk 才到达，回填已推送的 tool_start part，
                        # 避免持久化后 tool 名称为空（显示 "Used tool:"）。
                        for existing_part in parts:
                            if existing_part.get("type") == "tool_start" and existing_part.get("id") == tc_id:
                                existing_part["name"] = tc_name
                                break
                    part = {"type": "tool_delta", "id": tc_id, "input": tc_args}
                    parts.append(part)
                    events.append(part)

        elif isinstance(msg, ToolMessage):
            msg_name = getattr(msg, "name", "") or ""
            tc_id = getattr(msg, "tool_call_id", "") or ""
            content = getattr(msg, "content", "") or ""
            if msg_name == "finalize_goal":
                # Emit a goal-checkpoint event for the round orchestrator / UI.
                try:
                    checkpoint = json.loads(content) if content else {}
                except (TypeError, ValueError):
                    checkpoint = {}
                events.append({
                    "type": "goal_checkpoint",
                    "achieved": bool(checkpoint.get("achieved", False)),
                    "progress": str(checkpoint.get("progress", "") or "")[:500],
                    "verification": str(checkpoint.get("verification", "") or "")[:500],
                })
                return events
            if msg_name == "write_todos":
                # Todo progress is streamed via the `todos` event; no tool card.
                return events
            # The real outcome: HITL rejections/errors carry status "error",
            # only genuine completions are "success".
            tool_status = "success" if (getattr(msg, "status", "") or "success") == "success" else "error"
            if tc_id in tool_state:
                tool_state[tc_id]["status"] = tool_status
                tool_state[tc_id]["output"] = str(content)[:2000]
                started_at = tool_state[tc_id].get("started_at")
                duration_ms = round((time.time() - started_at) * 1000) if started_at else None
                files = self._real_file_changes(tc_id, tool_state, session_id)
                # Backfill the authoritative full args so the UI can replace any
                # streamed fragments (which may be dropped/incomplete mid-stream)
                # with the final, valid JSON the tool actually executed.
                part: dict[str, Any] = {
                    "type": "tool_end",
                    "id": tc_id,
                    "output": str(content)[:2000],
                    "status": tool_status,
                    "input": str(tool_state[tc_id].get("input") or ""),
                }
                if duration_ms is not None:
                    part["duration_ms"] = duration_ms
                if files:
                    part["files"] = files
                parts.append(part)
                events.append(part)
            elif tc_id:
                part = {
                    "type": "tool_end",
                    "id": tc_id,
                    "name": getattr(msg, "name", "") or "",
                    "output": str(content)[:2000],
                    "status": tool_status,
                }
                parts.append(part)
                events.append(part)

        return events

    def _real_file_changes(self, tc_id: str, tool_state: dict[str, dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
        state = tool_state.get(tc_id) or {}
        tool_name = str(state.get("name") or "")
        input_raw = str(state.get("input") or "")
        if tool_name in _CHANGE_TOOL_NAMES and self.change_store is not None and session_id:
            raw_path = _path_from_tool_input(tool_name, input_raw)
            if raw_path:
                normalized = self.workspace.normalize_rel_path(raw_path)
                change = self.change_store.match_and_claim(session_id, tool_name, normalized)
                if change is not None:
                    return [_change_to_public(change)]
        return _estimate_file_changes(tool_name, input_raw)

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted turn, yielding progress events in real time.

        Every decision (approve / reject / continue_discuss / respond) resumes
        the SAME graph execution via ``Command(resume=...)`` — the official
        LangGraph HITL contract. The middleware synthesizes the corresponding
        ToolMessage (reject -> error feedback, respond -> human answer, plan
        approve -> execute transition), so the model always gets to continue
        instead of the turn being hard-terminated (fixes D4/D5).
        """
        from langgraph.types import Command

        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        session_id = str(context.get("session_id") or "")
        language = normalize_language(context.get("language"))
        work_mode = normalize_work_mode(str(context.get("work_mode") or "build"))
        autonomy = normalize_autonomy(context.get("autonomy"))
        audit_context = {
            "session_id": session_id, "provider": self.provider_name, "provider_id": self.provider_id,
            "model": self.model_name, "workspace_path": str(self.workspace.root),
        }
        current_trace_context = trace_context(
            session_id=session_id, provider=self.provider_name, provider_id=self.provider_id,
            model=self.model_name, language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )
        content_parts: list[str] = []
        parts: list[dict[str, Any]] = []
        decision_types = ", ".join(str(item.get("type")) for item in decisions)
        self.trace_store.record("agent_activity", "resolved", current_trace_context, {"approval_id": approval.get("id", ""), "decisions": decision_types})

        # If HITL approved write operations, mark the workspace so that
        # resolve_write_path() will accept external paths during the resumed run.
        approved_approvals = self.approval_store.list()
        for approval_rec in approved_approvals:
            decision = approval_rec.get("decision") or {}
            if decision.get("type") == "approve":
                tool_name = str(approval_rec.get("tool_name", ""))
                if tool_name in ("write_file", "replace_in_file", "apply_text_edits", "run_command"):
                    self.workspace._allow_external_write = True
                    break

        config = agent_run_config(
            session_id=session_id, provider=self.provider_name, model=self.model_name,
            language=language, work_mode=work_mode, autonomy=autonomy, streaming=True,
        )

        async with _open_checkpointer(self.checkpoint_path) as checkpointer:
            self._nudge_memory(session_id)
            memory_view, memory_store, memory_rel = self._memory
            graph = build_coworker_agent_graph(
                self.llm, build_workspace_tools(
                        self.workspace, audit_context, change_store=self.change_store,
                        session_store=self.session_store, referenced_sessions=self.referenced_sessions,
                        skill_manager=self.skill_manager,
                        memory_store=memory_store,
                        memory_rel=memory_rel,
                    ),
                work_mode=work_mode, language=language, autonomy=autonomy,
                checkpointer=checkpointer, approval_store=self.approval_store,
                data_dir=self.data_dir,
                mcp_session_manager=self.mcp_session_manager,
                skill_manager=self.skill_manager,
                memory_manager=memory_view,
                workspace=self.workspace,
                context_budget=self.context_budget_chars,
            )
            interrupt_id = str(context.get("interrupt_id") or "")
            # If a question was rejected, stop the turn immediately instead of
            # re-entering the agent graph.
            if any(d.get("type") == "_stop_turn" for d in decisions):
                mode = work_mode
                if not mode:
                    mode = normalize_work_mode("build") if work_mode is None else work_mode
                yield {
                    "type": "done",
                    "content": "",
                    "mode": mode.value if hasattr(mode, "value") else str(mode),
                    "autonomy": autonomy or "guarded",
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "parts": [],
                }
                return
            resume_map: dict[str, Any] = {interrupt_id: {"decisions": decisions}} if interrupt_id else {"decisions": decisions}
            tool_state: dict[str, dict[str, Any]] = {}
            try:
                async for stream_mode, chunk in graph.astream(Command(resume=resume_map), config=config, stream_mode=["messages", "custom", "updates"]):
                    if stream_mode == "messages":
                        msg, _meta = chunk
                        try:
                            for event in self._handle_message_chunk(msg, content_parts, tool_state, parts, session_id):
                                yield event
                        except GeneratorExit:
                            raise
                        except Exception:
                            # The stream must keep going (the chunk is non-fatal),
                            # but never swallow it silently — a missing tool card /
                            # text segment would otherwise be undiagnosable.
                            logger.exception("Failed to emit message-chunk event")
                    elif stream_mode == "custom":
                        if isinstance(chunk, dict):
                            event_type = chunk.get("type", "")
                            if event_type in ("plan_start", "plan_delta", "plan_end"):
                                parts.append(chunk)
                                yield chunk
                    elif stream_mode == "updates":
                        if "__interrupt__" in chunk:
                            approvals = record_runtime_interrupts(chunk["__interrupt__"], self.approval_store, context, mcp_policy_resolver(self.mcp_session_manager))
                            self.trace_store.record("agent_activity", "pending", current_trace_context, {"approval_ids": [a.get("id", "") for a in approvals], "resumed": True})
                            for item in approvals:
                                event = stream_event_from_interrupt(item)
                                yield event
                            continue
            except Exception as exc:
                self.trace_store.record("agent_activity", "error", current_trace_context, {"error": str(exc)[:400], "resumed": True})
                raise
            finally:
                # Reset so the flag cannot leak into subsequent turns or agent runs.
                self.workspace._allow_external_write = False

        final_content = "".join(content_parts)
        final_content = _strip_plan_leak(final_content, parts)
        self.trace_store.record("agent_activity", "done", current_trace_context, {"content_chars": len(final_content), "resumed": True})
        yield {"type": "stage", "name": "finalizing", "status": "done"}
        yield {"type": "done", "content": final_content, "mode": self.mode, "provider": self.provider_name, "model": self.model_name, "parts": _merge_event_parts(_terminate_stray_tools(parts))}
        return


class SimulatedStreamRuntime(AgentStreamRuntime):
    mode: AgentMode = "single"

    def __init__(self, settings: BackendSettings, workspace: Workspace, session_store: SessionStore | None = None, referenced_sessions: set[str] | None = None):
        self.settings = settings
        self.workspace = workspace

    async def stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def stream_rerun(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._stream(messages, session_id, language, work_mode, autonomy):
            yield event

    async def _stream(self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy) -> AsyncGenerator[dict[str, Any], None]:
        user_message = messages[-1]["content"] if messages else ""
        if isinstance(user_message, list):
            user_message = " ".join(
                part.get("text", "")
                for part in user_message
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if language == "zh":
            content = (
                "Coworker 正在以模拟提供商模式运行。\n\n"
                f"工作区：{self.workspace.root}\n会话：{session_id}\n\n"
                f"模式：{work_mode} / {autonomy}\n\n你说：{user_message}"
            )
        else:
            content = (
                "Coworker is running in simulated provider mode.\n\n"
                f"Workspace: {self.workspace.root}\nSession: {session_id}\n\n"
                f"Mode: {work_mode} / {autonomy}\n\nYou said: {user_message}"
            )
        yield {"type": "start", "session_id": session_id, "mode": self.mode, "provider": "simulated", "model": ""}
        for chunk in content:
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "content": content, "mode": self.mode, "provider": "simulated", "model": ""}


class AgentRuntimeRegistry:
    def __init__(self, settings: BackendSettings, session_store: SessionStore | None = None, mcp_session_manager: Any | None = None, skill_manager: Any | None = None, memory_manager: Any | None = None, project_store: Any | None = None):
        from langgraph.checkpoint.sqlite import SqliteSaver

        self.settings = settings
        self.session_store = session_store
        self.skill_manager = skill_manager
        self.memory_manager = memory_manager
        self.project_store = project_store
        self.default_workspace = Workspace(
            settings.workspace_dir,
            settings.data_dir / TOOL_AUDIT_FILENAME,
            fingerprint_path_for(settings.data_dir, settings.workspace_dir),
        )
        self.approval_store = CommandApprovalStore(settings.data_dir / COMMAND_APPROVAL_FILENAME)
        self.trace_store = AgentTraceStore(settings.data_dir / AGENT_TRACE_FILENAME)
        self.change_store = ChangeStore(settings.data_dir)
        self.provider_manager = ProviderManager(settings.data_dir / "providers.json", settings.data_dir)
        self.mcp_manager = McpManager(settings.data_dir / "mcp_servers.json")
        self.mcp_session_manager = mcp_session_manager
        self.checkpoint_path = settings.data_dir / "runtime_checkpoints.sqlite"
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_conn = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False, timeout=30.0)
        self.checkpoint_conn.execute("PRAGMA journal_mode=WAL")
        # Set auto_vacuum=FULL so SQLite automatically recycles free pages on commit.
        # Must VACUUM after setting auto_vacuum — only VACUUM writes the PRAGMA
        # value into the DB-file header for an existing database.
        self.checkpoint_conn.execute("PRAGMA auto_vacuum=FULL")
        self.checkpoint_conn.execute("VACUUM")
        self.checkpoint_conn.commit()
        self.checkpoint_conn.execute("PRAGMA busy_timeout=30000")
        self.checkpoint_conn.execute("PRAGMA synchronous=NORMAL")
        self.checkpointer = SqliteSaver(self.checkpoint_conn)
        self.checkpoint_manager = CheckpointManager(
            self.checkpoint_path,
            sessions_dir=settings.data_dir / "sessions",
            cap_per_session=settings.checkpoint_cap_per_session,
            max_bytes_per_thread=settings.checkpoint_max_bytes_per_thread,
        )

    def _open_sync_checkpointer(self):
        # A fresh synchronous connection per call, committed and closed, so it
        # never holds a lingering lock that contends with the async saver used
        # during streaming/resume. ``auto_vacuum`` is a persistent DB property
        # (already set by the checkpoint manager at startup) and cannot be
        # re-applied per connection — doing so raises "database is locked" when
        # the async saver is actively writing.
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(str(self.checkpoint_path), check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return SqliteSaver(conn), conn

    def has_runtime_checkpoint(self, session_id: str) -> bool:
        try:
            saver, conn = self._open_sync_checkpointer()
        except sqlite3.OperationalError:
            # The async saver holds the write lock (a stream just ended). Be
            # conservative and assume a checkpoint exists: chat_stream then keeps
            # just the new user message and continues from the checkpoint instead
            # of re-adding the FULL history (which would duplicate context).
            return True
        try:
            return saver.get({"configurable": {"thread_id": session_id}}) is not None
        except sqlite3.OperationalError:
            return True
        finally:
            try:
                conn.commit()
            finally:
                conn.close()

    def forget_runtime_checkpoint(self, session_id: str) -> None:
        # Delegate to the manager so the delete also reclaims disk space.
        self.checkpoint_manager.delete_thread(session_id)

    def _provider_for_request(self, provider_id: str | None, model: str | None) -> ProviderEntry | None:
        if provider_id:
            config = self.provider_manager.load()
            provider = config.find_enabled(provider_id)
            if not provider:
                raise RuntimeError(f"Provider {provider_id} is not enabled or not found")
            return replace(provider, model=model or provider.model)
        provider = self.provider_manager.default_provider()
        if provider and model:
            return replace(provider, model=model)
        return provider

    def _workspace_or_default(self, workspace: Workspace | None = None) -> Workspace:
        return workspace or self.default_workspace

    def _create_single_agent(self, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None, agent: str | None = None) -> AgentRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if provider:
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, provider, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir, mcp_session_manager=self.mcp_session_manager, skill_manager=self.skill_manager, memory_manager=self.memory_manager, project_store=self.project_store, agent=agent or DEFAULT_AGENT_NAME)
        if self.settings.agent_provider == "openai":
            env_provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
            return OpenAICompatibleSingleAgentRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpointer, env_provider, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir, mcp_session_manager=self.mcp_session_manager, skill_manager=self.skill_manager, memory_manager=self.memory_manager, project_store=self.project_store, agent=agent or DEFAULT_AGENT_NAME)
        if self.settings.agent_provider == "simulated":
            return SimulatedSingleAgentRuntime(self.settings, selected_workspace, session_store=self.session_store, referenced_sessions=referenced_sessions)
        raise RuntimeError(f"Unsupported COWORKER_AGENT_PROVIDER: {self.settings.agent_provider}")

    def get_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None, agent: str | None = None) -> AgentRuntime:
        if mode == "single":
            return self._create_single_agent(provider_id, model, workspace, referenced_sessions, agent)
        raise RuntimeError(f"Unsupported agent mode: {mode}")

    def list_agent_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.trace_store.list(limit)

    def get_stream_runtime(self, mode: AgentMode, provider_id: str | None = None, model: str | None = None, workspace: Workspace | None = None, referenced_sessions: set[str] | None = None, agent: str | None = None) -> AgentStreamRuntime:
        selected_workspace = self._workspace_or_default(workspace)
        provider = self._provider_for_request(provider_id, model)
        if not provider and self.settings.agent_provider == "openai":
            provider = ProviderEntry(id="env-openai", name="Environment OpenAI", provider_type="openai", base_url=os.getenv("COWORKER_OPENAI_BASE_URL", "https://api.openai.com/v1"), api_key=os.getenv("OPENAI_API_KEY", ""), model=self.settings.openai_model, enabled=True)
        if not provider:
            if self.settings.agent_provider == "simulated":
                return SimulatedStreamRuntime(self.settings, selected_workspace, session_store=self.session_store, referenced_sessions=referenced_sessions)
            raise RuntimeError("No provider configured for streaming. Add a provider in Settings first.")
        if mode == "single":
            return OpenAICompatibleStreamRuntime(selected_workspace, self.approval_store, self.trace_store, self.checkpoint_path, provider, model, change_store=self.change_store, session_store=self.session_store, referenced_sessions=referenced_sessions, data_dir=self.settings.data_dir, mcp_session_manager=self.mcp_session_manager, skill_manager=self.skill_manager, memory_manager=self.memory_manager, project_store=self.project_store, agent=agent or DEFAULT_AGENT_NAME)
        raise RuntimeError(f"Unsupported agent mode for streaming: {mode}")

    async def resume_interrupt(self, approval: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Resume an interrupted agent turn (HITL approval) using the stream runtime.

        The approval context carries the provider id, workspace path, and session
        metadata so the same graph can be rebuilt against the existing checkpoint.
        Events are forwarded in real time from the runtime generator.
        """
        context = approval.get("context") if isinstance(approval.get("context"), dict) else {}
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME, fingerprint_path_for(self.settings.data_dir, Path(str(workspace_path))))
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        runtime = self.get_stream_runtime("single", provider_id or None, model or None, workspace, referenced_sessions=referenced_sessions)
        async for event in runtime.resume_interrupt(approval, decisions):
            yield event

    def _stream_runtime_from_context(self, context: dict[str, Any]) -> AgentStreamRuntime:
        provider_id = str(context.get("provider_id") or "")
        model = str(context.get("model") or "")
        workspace_path = context.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            workspace = Workspace(Path(str(workspace_path)), self.settings.data_dir / TOOL_AUDIT_FILENAME, fingerprint_path_for(self.settings.data_dir, Path(str(workspace_path))))
        referenced_sessions = set(str(item) for item in (context.get("referenced_sessions") or []))
        return self.get_stream_runtime("single", provider_id or None, model or None, workspace, referenced_sessions=referenced_sessions, agent=str(context.get("agent") or "") or None)

    async def rerun_stream(
        self, messages: list[dict[str, Any]], session_id: str, language: Language, work_mode: WorkMode, autonomy: Autonomy,
        provider_id: str | None = None, model: str | None = None, referenced_sessions: set[str] | None = None,
        workspace_path: str | None = None, agent: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Reset the session checkpoint and re-run the agent from full history."""
        # The checkpoint delete touches SQLite; run it off the event loop so a
        # transient writer lock can never stall every other request in the app.
        await asyncio.to_thread(self.forget_runtime_checkpoint, session_id)
        context = {
            "provider_id": provider_id or "",
            "model": model or "",
            "referenced_sessions": list(referenced_sessions or []),
            # The project workspace must be threaded through or regenerate/edit
            # would run against the DEFAULT workspace and write files in the
            # wrong place (compare resume_interrupt, which passes it).
            "workspace_path": workspace_path,
            "agent": agent or "",
        }
        runtime = self._stream_runtime_from_context(context)
        async for event in runtime.stream_rerun(messages, session_id, language, work_mode, autonomy):
            yield event
