"""Agent core: shared types, tool-argument schemas, message/context helpers.

The base of the ``coworker.agent`` package — everything else imports from here
and nothing imports back into it, so the import DAG stays acyclic:

    providers.py ← agent/model_defaults.py ← agent/core.py ← {prompts, middleware, graph} ← runtime.py

Holds the former ``coworker/agents.py`` core that is shared by the middleware,
graph builder, runtimes and worker sub-agents: the ``Language`` / ``WorkMode`` /
``Phase`` / ``Autonomy`` types, the Pydantic tool-argument schemas, the message-
chunk event conversion, context budgeting and the checkpoint helpers.
"""

import asyncio
import json
import operator
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path

from ..logger import get_logger

logger = get_logger(__name__)

from typing import Annotated, Any, Literal
from typing_extensions import NotRequired

from langchain.agents.middleware.types import AgentState
from pydantic import BaseModel, Field

from .types import AgentMode, Autonomy, Language, Phase, VALID_LANGUAGES, WorkMode

# SINGLE SOURCE of truth for command timeouts (the executor owns them).
from ..workspace import DEFAULT_COMMAND_TIMEOUT_SECONDS, MAX_COMMAND_TIMEOUT_SECONDS


MAX_ATTACHMENT_CHARS = 120_000
# Per-call char budget for a referenced-session page (read_session). Each page
# is bounded so a whole referenced transcript is reachable via offset paging —
# the OLD single 60k head-cap silently dropped the session's TAIL (newest
# decisions) and had no way to page further back.
MAX_REFERENCE_SESSION_PAGE_CHARS = 20_000
# Default page size (messages) per read_session call.
DEFAULT_REFERENCE_PAGE_SIZE = 20

# O1: tool outputs are persisted in FULL up to this fuse (truncation to the
# model happens at replay time via ``truncate_to_token_budget``). Real outputs
# are source-bounded already (read ≤READ_FILE_MAX_CHARS, run_command externalizes
# >MAX_COMMAND_OUTPUT_CHARS), so this only guards pathological tools.
TOOL_OUTPUT_PERSIST_MAX_CHARS = 128_000
# Display cap for the LIVE ``tool_end`` SSE event / ``done.parts`` — the frontend
# bubble renders this, so a 128k output would flood it. The FULL output rides
# alongside as ``output_full`` and is what gets persisted + replayed to the model
# (O1's "full at persist, truncate at replay" contract is unchanged; only the
# wire/display size is bounded back to the pre-O1 preview).
TOOL_OUTPUT_DISPLAY_MAX_CHARS = 2_000

PLAN_MARKER = "[CW-PLAN]"


class CoworkerAgentState(AgentState[Any]):
    work_mode: NotRequired[str]
    language: NotRequired[str]
    phase: NotRequired[str]
    autonomy: NotRequired[str]
    todos: NotRequired[list[Any]]
    # Session id injected via graph inputs so the steer middleware can resolve
    # which session's interjection inbox to drain at each model-call boundary
    # (the middleware's Runtime has no access to the run config metadata).
    session_id: NotRequired[str]
    # Cumulative count of context compressions (trim or summarize) for this
    # session. Lives in state (not on the middleware) because the middleware is
    # rebuilt every turn; the checkpoint persists this so the "已精简" badge is
    # cumulative across turns — see B6.
    context_compact_count: NotRequired[Annotated[int, operator.add]]
    # Last compaction summary body (state-persisted so the streaming runtime can
    # strip a model echo of the injected summary from a LATER turn's reply).
    context_summary: NotRequired[str]
    # Fingerprints of segments already summarized, persisted in state so the
    # loop guard survives middleware rebuilds across turns (the middleware is
    # rebuilt every turn; a per-instance set would reset the guard each turn).
    context_summarized_fingerprints: NotRequired[list[str]]
    # W2/N1: explicit reason the turn's tool loop stopped (single source of
    # truth for the value set — see LOOP_REASON_* constants below). Surfaced on
    # the done event so the UI/continuation knows exactly WHY the loop ended.
    loop_reason: NotRequired[str]


# Single source of truth for the loop_reason value set (referenced by the loop
# guard, the runtime done event and mirrored in frontend types.ts).
LOOP_REASON_TOOL_CALLS = "tool_calls"
LOOP_REASON_REPEATED = "repeated"
LOOP_REASON_DEGENERATE = "degenerate"
LOOP_REASON_OVERFLOW = "overflow"
LOOP_REASON_HITL = "hitl"
LOOP_REASON_STEP_CAP = "step_cap"
# IdleLoopMiddleware: "idle" = the soft "seems stuck" warning (non-terminal),
# "idle_hard" = the terminal hard stop (tools stripped, summary forced).
LOOP_REASON_IDLE = "idle"
LOOP_REASON_IDLE_HARD = "idle_hard"
LOOP_REASON_FINAL = "final"


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
    offset: int = Field(
        default=1,
        description="Line number to start reading from (1-indexed). Use the 'next_offset' hint "
        "returned by a truncated read to page through a large file.",
    )
    limit: int = Field(
        default=200,
        description="Maximum number of lines to read (defaults to 200). Reads are capped at a few "
        "hundred lines so a large file never floods the context; page with offset to continue.",
    )


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


class SkillManageArgs(BaseModel):
    action: Literal["create", "patch", "edit", "delete"] = Field(
        description=(
            "create = author a NEW skill (staged as a draft for approval); "
            "patch = targeted fix of an existing skill (old_string -> new_string); "
            "edit = full rewrite of an existing skill; delete = remove a skill entirely. "
            "Every create/patch/edit write is staged for human approval — it never "
            "takes effect until the user approves it."
        )
    )
    name: str = Field(
        description="Skill slug/identifier (lowercase letters, digits, hyphens)."
    )
    content: str | None = Field(
        default=None,
        description="Required for create/edit: the full SKILL.md content, including YAML frontmatter "
        "(name + description) and the four sections When to Use / Procedure / Pitfalls / Verification.",
    )
    old_string: str | None = Field(
        default=None,
        description="Required for patch: exact text to replace in the existing skill body.",
    )
    new_string: str | None = Field(
        default=None,
        description="Required for patch: the replacement text.",
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
    command: str | list[str] = Field(
        description=(
            "Command to run. Pass either an argv array, for example ['npm', 'run', 'build'], "
            "OR a plain shell command string, for example 'npm run build'. The backend normalizes "
            "strings into argv automatically, so a string is always safe."
        )
    )
    cwd: str = Field(default="", description="Optional workspace-relative working directory.")
    timeout_seconds: int = Field(default=DEFAULT_COMMAND_TIMEOUT_SECONDS, ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS, description="Command timeout in seconds. Use background=true for long-running builds.")


# Default single-agent id — SINGLE SOURCE of truth lives in the memory layout
# (the agent folder name). Re-exported here so the runtime/workers import it
# from the agent core as before.

# NOTE: NO tool-loop step cap is configured (recursion_limit is left to
# create_agent's built-in 9_999 absolute bound). Runaway loops are governed by
# the loop guards: RepeatedToolCall (consecutive identical calls), degenerate
# text detection, and IdleLoopMiddleware (progress-aware stuck detection with a
# sliding 10-in-20 window). See LOOP_REASON_* below.



class CommandStatusArgs(BaseModel):
    job_id: str = Field(description="The job_id returned by run_command(background=true).")


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


class ManageGoalArgs(BaseModel):
    status: Literal["complete", "blocked"] = Field(
        description=(
            "Mark the active session goal complete or blocked. You may ONLY set "
            '"complete" or "blocked" — pausing/resuming/budget are user- or '
            'system-controlled. "complete" requires a strict requirement-by-requirement '
            "audit against authoritative current-state evidence. "
            '"blocked" requires the SAME blocking condition to repeat for at least '
            "three consecutive goal turns."
        )
    )


class GetGoalArgs(BaseModel):
    """No arguments — reads the active session goal (objective, status, budget, usage)."""


def _resolve_project_memory_dir(project_store: Any | None, workspace_root: str) -> str:
    """Resolve the memory_dir for a workspace root, or ``""`` if unknown.

    The memory library is central (not under the workspace), so the project
    must be looked up by its workspace path. Returns ``""`` for the default
    (non-project) workspace — memory then degrades to system-level only.
    """
    if project_store is None or not workspace_root:
        return ""
    try:
        project = project_store.load().find_by_workspace_path(str(Path(workspace_root).resolve()))
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
    agent's ``BASE/`` (``MEMORY.md`` or a named sibling file) or its
    ``SESSIONS/`` folder (session notes, e.g. ``SESSIONS/2026-08-19.md``);
    system scope is limited to the three system-default files. Everything
    else — project ``BASE`` files, ``BASE/PROJECT``, other agents, user root
    files — stays read-only for agents.
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
    if name.startswith("SESSIONS/"):
        filename = name[len("SESSIONS/"):].strip()
        if not filename or "/" in filename or "\\" in filename:
            return False, "SESSIONS name must be a single file name (no nested folders)."
        if not (filename.endswith(".md") or filename.endswith(".markdown")):
            return False, "SESSIONS files must be Markdown (.md / .markdown)."
        agent_dir = posixpath.dirname(base)
        return True, f"{agent_dir}/SESSIONS/{filename}"
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
    offset: int = Field(
        default=0,
        ge=0,
        description="Skip this many messages from the END of the session before reading. offset=0 returns the MOST RECENT page; use the returned next_offset/hint to page back through older messages until next_offset=0 (full transcript reachable).",
    )
    page_size: int = Field(
        default=DEFAULT_REFERENCE_PAGE_SIZE,
        ge=1,
        le=100,
        description="Max messages per page (also bounded by a per-call char budget).",
    )


def build_referenced_session_page(
    session: Any,
    *,
    offset: int = 0,
    page_size: int = DEFAULT_REFERENCE_PAGE_SIZE,
    max_chars: int = MAX_REFERENCE_SESSION_PAGE_CHARS,
) -> dict[str, Any]:
    """Build ONE readable page of a referenced session, newest-first.

    Paging anchors at the END of the transcript (offset counts messages back
    from the most recent) so the first page carries the newest decisions —
    the part a follow-up agent usually needs — and ``next_offset`` lets it walk
    back through older history until the WHOLE session is reachable. This
    replaces the old head-cap behaviour (kept only the oldest ~60k chars and
    silently dropped the tail with no way to page).
    """
    raw: list[dict[str, Any]] = []
    for message in getattr(session, "messages", None) or []:
        role = getattr(message, "role", "") or ""
        content = getattr(message, "content", "") or ""
        if role not in {"user", "assistant"} or not content:
            continue
        if role == "user":
            content = format_user_message(
                content,
                getattr(message, "attachments", None),
                getattr(message, "references", None),
            )
        raw.append({"role": role, "content": content})

    total = len(raw)
    page_size = max(1, min(int(page_size or DEFAULT_REFERENCE_PAGE_SIZE), 200))
    offset = max(0, int(offset or 0))
    newest = total - 1 - offset  # index of the newest message this page may return

    hint_no_more = f"(No earlier messages; you have read the whole session of {total}.)"
    if newest < 0 or not raw:
        return {
            "session_id": getattr(session, "id", ""),
            "title": getattr(session, "title", "") or "",
            "total_messages": total,
            "message_count": 0,
            "messages": [],
            "total_chars": 0,
            "offset": offset,
            "truncated": False,
            "next_offset": 0,
            "hint": hint_no_more if raw else "(Empty session.)",
        }

    collected: list[dict[str, Any]] = []
    used = 0
    idx = newest
    while idx >= 0 and len(collected) < page_size:
        cost = _content_chars(raw[idx]["content"])
        if collected and used + cost > max_chars:
            break
        collected.append(raw[idx])
        used += cost
        idx -= 1
    collected.reverse()  # keep chronological order within the page

    earlier_remain = idx >= 0
    next_offset = (total - 1 - idx) if earlier_remain else 0
    returned_first = idx + 1
    returned_last = newest
    if earlier_remain:
        hint = (
            f"(Returned messages {returned_first + 1}..{returned_last + 1} of {total}. "
            f"Call read_session with offset={next_offset} to read the next-earlier page; "
            f"use offset=0 for the most recent page.)"
        )
    else:
        hint = hint_no_more
    return {
        "session_id": getattr(session, "id", ""),
        "title": getattr(session, "title", "") or "",
        "total_messages": total,
        "message_count": len(collected),
        "messages": collected,
        "total_chars": used,
        "offset": offset,
        "truncated": earlier_remain,
        "next_offset": next_offset,
        "hint": hint,
    }


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
    # Accept any supported UI language (the agent mirrors the user's message
    # language, so we no longer force zh/en). Unknown values fall back to zh.
    if language in VALID_LANGUAGES:
        return language  # type: ignore[return-value]
    return "zh"




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
        # NO explicit recursion_limit: runaways are governed by the loop guards
        # (RepeatedToolCall / degenerate / IdleLoopMiddleware); create_agent's
        # built-in 9_999 stays as the absolute bound.
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
            # Persist ~1 checkpoint per turn (only at exit / on interrupt) instead
            # of after every superstep. The checkpoint DB is disposable per-turn,
            # so mid-run checkpoints would only be garbage that grows the file and
            # lengthens the SQLite write-lock hold. exit-durability still writes
            # the interrupt checkpoint durably, so HITL resume keeps working.
            "__pregel_durability": "exit",
        },
    }


_shared_checkpointer: Any = None
_shared_checkpointer_init: Any = None


async def _get_shared_checkpointer(checkpoints_dir: Any) -> Any:
    """Lazily create the single process-wide JSON-file checkpoint saver.

    All checkpoint I/O (writes, deletes, reads) goes through ONE saver whose
    ``asyncio.Lock`` serializes the per-session file operations. Each session
    keeps its OWN ``checkpoints/<session_id>.json`` file written atomically
    (temp + rename), so there is no shared SQLite file to lock and different
    sessions never contend. With ``durability="exit"`` each turn writes ~1
    checkpoint, and the file is deleted when the turn ends — the checkpoint DB
    stays a tiny, disposable per-turn cache.
    """
    global _shared_checkpointer, _shared_checkpointer_init
    if _shared_checkpointer is None:
        if _shared_checkpointer_init is None:
            _shared_checkpointer_init = asyncio.Lock()
        async with _shared_checkpointer_init:
            if _shared_checkpointer is None:
                from coworker.checkpoint_store import JsonFileCheckpointSaver

                _shared_checkpointer = JsonFileCheckpointSaver(Path(checkpoints_dir))
    return _shared_checkpointer


def _open_checkpointer(checkpoints_dir: Any):
    """Yield the single process-wide JSON-file checkpoint saver.

    Every stream shares the one ``JsonFileCheckpointSaver`` (serialized by its
    ``asyncio.Lock``). Checkpoints are stored as one atomic JSON file per
    session under ``checkpoints/<session_id>.json`` — the per-session-file
    persistence model (cf. cline), so the SQLite write-lock / busy_timeout /
    "database is locked" failure modes are physically impossible. The checkpoint
    is disposable per turn: each turn starts fresh from session history and
    deletes its thread when it ends.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _open():
        saver = await _get_shared_checkpointer(checkpoints_dir)
        yield saver

    return _open()





_CHANGE_TOOL_NAMES = {"write_file", "replace_in_file", "apply_text_edits"}

# Upper bound for built tool descriptions (P2). Tool schemas ride on EVERY
# request, so a long description is paid token-by-token on every model call of
# every turn. Mainstream coding agents keep descriptions short (~100-400 chars)
# and move long guidance into memory/skill files instead.
MAX_TOOL_DESCRIPTION_CHARS = 650

# Tool sets for phase-driven tool gating (see PhaseToolGateMiddleware).
_READ_ONLY_TOOLS = {"search_files", "read_file", "read_session", "memory_read", "load_skill", "git_status", "web_search", "web_fetch", "browser", "get_goal", "run_command_status"}
_PLAN_TOOLS = {"ask_user"}
_MEMORY_TOOLS = {"memory"}
_EXEC_TOOLS = {"run_command", "install_skill", "skill_manage", "delegate_task", "delegate_parallel", "create_team_member", "create_team", "use_worker", "use_workers", "update_goal"}

# 子代理（worker）工具集在构造期就排除的委派/spawn 工具。把这些工具塞给子代理，
# 会允许 worker 无限嵌套 spawn 更多 worker/team（单 agent 模式没有 org.max_depth
# 约束）。见 build_workspace_tools 中 UseWorkerTool 的 tools= 传参。
_CHILD_EXCLUDED_TOOLS = {"use_worker", "use_workers", "delegate_task", "delegate_parallel", "create_team_member", "create_team"}





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
    plan text emitted through the plan events (checked against ALL plan
    fragments, longest first — D5).
    """
    if not content:
        return content
    plan_texts: list[str] = []
    for part in parts:
        if part.get("type") in ("plan_end", "plan") and part.get("content"):
            t = str(part["content"])
            if t not in plan_texts:
                plan_texts.append(t)
    if not plan_texts:
        return content
    # Longest plan text first (a later full plan is more authoritative).
    for plan_text in sorted(plan_texts, key=len, reverse=True):
        if content.startswith(plan_text):
            return content[len(plan_text):].lstrip("\n")
    stripped = content.lstrip("\n")
    if stripped.startswith(PLAN_MARKER):
        stripped = stripped[len(PLAN_MARKER):].lstrip("\n")
        for plan_text in plan_texts:
            if stripped.startswith(plan_text):
                return stripped[len(plan_text):].lstrip("\n")
    return content


def _clean_final_content(content: str, parts: list[dict[str, Any]], summary: str) -> str:
    """D5: one combined final-content cleanup — strip any leaked plan segment
    AND a verbatim echo of the injected compaction summary (single pass).
    """
    content = _strip_plan_leak(content, parts)
    if content and summary:
        s = summary.strip()
        if len(s) >= 20 and s in content:
            content = content.replace(s, "").strip()
    return content


_WRITE_ARG_PATH_KEYS = ("file_path", "path", "target")


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


# Reserved ``tool_state`` key: maps a tool call's streaming ``index`` to its
# ``id``. LangGraph's ``tool_call_chunks`` carry the id/name only on the FIRST
# chunk of a call; continuation chunks (incremental args) reuse the same index
# with an empty id. Without this map they would be dropped and tool input would
# stay a partial JSON fragment (e.g. ``{"command":``) for the whole turn.
_TOOL_INDEX_MAP_KEY = "__cw_tool_index_map__"


def _message_chunk_events(
    msg: Any,
    content_parts: list[str],
    tool_state: dict[str, dict[str, Any]],
    parts: list[dict[str, Any]],
    session_id: str = "",
    real_file_changes: Callable[[str, dict[str, dict[str, Any]], str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Convert a LangGraph ``messages`` stream chunk into SSE events.

    Shared by the main agent stream and worker sub-agent streams so both emit
    the identical ``delta`` / ``reasoning_delta`` / ``tool_start`` /
    ``tool_delta`` / ``tool_end`` vocabulary. ``real_file_changes`` is an
    optional hook for the main runtime to claim real file changes; worker runs
    fall back to the static estimator.
    """
    from langchain_core.messages import AIMessageChunk, ToolMessage

    if real_file_changes is None:
        # Worker runs use the static estimator. It takes (tool_name, input_raw),
        # so adapt the shared (tc_id, tool_state, session_id) call signature.
        def _estimate_fallback(tc_id: str, tool_state: dict[str, dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
            st = tool_state.get(tc_id) or {}
            return _estimate_file_changes(str(st.get("name") or ""), str(st.get("input") or ""))

        real_file_changes = _estimate_fallback  # type: ignore[assignment]

    events: list[dict[str, Any]] = []

    if isinstance(msg, AIMessageChunk):
        reasoning = _extract_reasoning_from_chunk(msg)
        if reasoning:
            parts.append({"type": "reasoning_delta", "content": reasoning})
            events.append({"type": "reasoning_delta", "content": reasoning})

        text = getattr(msg, "content", "") or ""
        if isinstance(text, str) and text:
            content_parts.append(text)
            parts.append({"type": "delta", "content": text})
            events.append({"type": "delta", "content": text})

        tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
        for tc in tool_call_chunks:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id") or ""
            tc_name = tc.get("name") or ""
            tc_args = tc.get("args") or ""
            tc_index = tc.get("index")

            if not tc_id:
                # Continuation chunk: args stream incrementally, only the first
                # chunk of a tool call carries the id. Route by index to the
                # tool it belongs to. Fallback: when the index is unknown (some
                # providers emit the first chunk without an index, so its index
                # was never registered), route by NAME to the most recent
                # RUNNING tool with that name — this fixes the empty-input
                # capture for the single-tool common case (continuation args
                # were being silently dropped, persisting tool parts with no
                # input and losing the edit/audit trail).
                idx_map = tool_state.get(_TOOL_INDEX_MAP_KEY) or {}
                if tc_index is not None and tc_index in idx_map:
                    tc_id = idx_map[tc_index]
                    tc_name = tool_state.get(tc_id, {}).get("name", "") or tc_name
                else:
                    running = [
                        k for k, v in tool_state.items()
                        if k != _TOOL_INDEX_MAP_KEY and v.get("status") == "running"
                        and (v.get("name") == tc_name or not tc_name)
                    ]
                    if not running:
                        continue
                    if len(running) == 1:
                        tc_id = running[0]
                    else:
                        named = [k for k in running if tool_state[k].get("name") == tc_name]
                        if not named:
                            continue
                        tc_id = max(named, key=lambda k: tool_state[k].get("started_at", 0))
                    tc_name = tool_state.get(tc_id, {}).get("name", "") or tc_name

            if tc_name == "write_todos":
                if tc_id in tool_state:
                    tool_state.pop(tc_id, None)
                    parts[:] = [
                        p for p in parts
                        if not (p.get("type") == "tool_start" and p.get("id") == tc_id)
                    ]
                continue

            if tc_id not in tool_state:
                # Start with the first args chunk so tool_state["input"] (used
                # by tool_end / real_file_changes) reflects the full accumulated
                # args — otherwise the leading fragment is lost.
                tool_state[tc_id] = {"name": tc_name or "", "input": tc_args, "status": "running", "started_at": time.time()}
                if tc_index is not None:
                    tool_state.setdefault(_TOOL_INDEX_MAP_KEY, {})[tc_index] = tc_id
                parts.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
                events.append({"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_args})
            else:
                tool_state[tc_id]["input"] = tool_state[tc_id].get("input", "") + tc_args
                if tc_name:
                    tool_state[tc_id]["name"] = tc_name
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
        if msg_name == "write_todos":
            if tc_id and tc_id in tool_state:
                tool_state.pop(tc_id, None)
                parts[:] = [p for p in parts if not (p.get("type") == "tool_start" and p.get("id") == tc_id)]
            return events
        tool_status = "success" if (getattr(msg, "status", "") or "success") == "success" else "error"
        if tc_id in tool_state:
            # Tool finished: drop its index mapping so a stale continuation chunk
            # can never be mis-routed to this (now complete) tool.
            idx_map = tool_state.get(_TOOL_INDEX_MAP_KEY)
            if idx_map:
                for idx, mapped in list(idx_map.items()):
                    if mapped == tc_id:
                        idx_map.pop(idx, None)
            tool_state[tc_id]["status"] = tool_status
            # O1: persist the FULL (fused) output in ``output_full`` — truncation
            # to the model happens at replay time via truncate_to_token_budget.
            # The wire/display ``output`` stays at the pre-O1 preview cap so the
            # frontend bubble never floods; the full copy is what gets persisted
            # and replayed (frontend-backend size contract preserved).
            tool_state[tc_id]["output"] = str(content)[:TOOL_OUTPUT_PERSIST_MAX_CHARS]
            started_at = tool_state[tc_id].get("started_at")
            duration_ms = round((time.time() - started_at) * 1000) if started_at else None
            files = real_file_changes(tc_id, tool_state, session_id)
            part: dict[str, Any] = {
                "type": "tool_end",
                "id": tc_id,
                "name": msg_name,
                "output": str(content)[:TOOL_OUTPUT_DISPLAY_MAX_CHARS],
                "output_full": str(content)[:TOOL_OUTPUT_PERSIST_MAX_CHARS],
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
                "output": str(content)[:TOOL_OUTPUT_DISPLAY_MAX_CHARS],
                "output_full": str(content)[:TOOL_OUTPUT_PERSIST_MAX_CHARS],
                "status": tool_status,
            }
            parts.append(part)
            events.append(part)

    return events


def _merge_event_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    pending_text: list[str] = []
    # D3: id → merged-index map so tool_delta/tool_end resolve the running tool
    # in O(1) instead of a linear scan of `merged` per chunk (O(n²) on long
    # streamed tool inputs).
    tool_index: dict[str, int] = {}

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
            idx = tool_index.get(part.get("id", ""))
            if idx is not None and idx < len(merged):
                existing_tool = merged[idx]
                existing_tool["input"] = (existing_tool.get("input", "") or "") + (part.get("input") or "")
            elif merged and merged[-1].get("type") == "tool_start":
                merged[-1]["type"] = "tool"
                merged[-1]["input"] = (merged[-1].get("input", "") or "") + (part.get("input") or "")
        elif part.get("type") == "tool_start":
            merged.append({"type": "tool", "id": part.get("id", ""), "name": part.get("name", ""), "status": "running", "input": part.get("input", "")})
            tool_index[str(part.get("id", ""))] = len(merged) - 1
        elif part.get("type") == "tool_end":
            idx = tool_index.get(part.get("id", ""))
            existing_tool = merged[idx] if idx is not None and idx < len(merged) else None
            if existing_tool:
                existing_tool["status"] = "success" if part.get("status") == "success" else "error"
                if part.get("output") is not None:
                    existing_tool["output"] = part["output"]
                # O1: carry the full output through to the persisted/merged part
                # (display stays at the preview cap on ``output``).
                if part.get("output_full") is not None:
                    existing_tool["output_full"] = part["output_full"]
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
                        **({"output_full": part["output_full"]} if part.get("output_full") is not None else {}),
                        **({"duration_ms": part["duration_ms"]} if part.get("duration_ms") is not None else {}),
                        **({"files": part["files"]} if part.get("files") is not None else {}),
                    }
                )
        elif part.get("type") in ("delegate_start", "delegate_progress", "delegate_end"):
            run_id = str(part.get("worker_run_id") or "")
            if not run_id:
                # Legacy frames without a run id still coalesce by target.
                targets = part.get("to") or part.get("from") or ""
                run_id = "::".join(targets) if isinstance(targets, list) else str(targets)
            existing = next((p for p in merged if p.get("type") == "agent" and p.get("worker_run_id") == run_id), None)
            if existing is None:
                existing = {
                    "type": "agent",
                    "worker_run_id": run_id,
                    "from": part.get("from") or "",
                    "to": part.get("to") or "",
                    "task": part.get("task"),
                    "status": "running",
                    "parallel": bool(part.get("parallel")),
                }
                merged.append(existing)
            if part.get("type") == "delegate_start":
                if part.get("task") is not None:
                    existing["task"] = part["task"]
                if part.get("to") is not None:
                    existing["to"] = part["to"]
                if part.get("from") is not None:
                    existing["from"] = part["from"]
                existing["status"] = "running"
                existing["parallel"] = bool(part.get("parallel"))
            elif part.get("type") == "delegate_progress":
                if part.get("status") == "error" or part.get("error"):
                    existing["status"] = "error"
                if part.get("error"):
                    existing["error"] = part["error"]
                if part.get("chars") is not None:
                    existing["chars"] = part["chars"]
            elif part.get("type") == "delegate_end":
                existing["status"] = "error" if part.get("error") else "done"
                if part.get("error"):
                    existing["error"] = part["error"]
                if part.get("chars") is not None:
                    existing["chars"] = part["chars"]
                if part.get("failed") is not None:
                    existing["failed"] = part["failed"]
        else:
            merged.append(part)

    _flush_text()
    for item in merged:
        if item.get("type") == "reasoning":
            item["heading"] = _reasoning_heading(item.get("content", ""))
            item["done"] = True
    return merged


def generate_title(first_user_message: str, assistant_response: str = "", language: Language = "zh") -> str:
    """Generate a session title by RULE only (N4) — no model chat call.

    Previously this ran a full chat completion on the default provider's model
    for every new session (a disproportionate cost for a 20-char label). The
    rule-based title is deterministic, instant and free; the assistant response
    is accepted but unused to keep the call signature stable.
    """
    from .prompts import _default_title_from_message

    title = _default_title_from_message(first_user_message)
    return title




# 每个 prompt 允许的最大 image_url 区块数。对齐 vLLM 服务端
# --limit-mm-per-prompt.image（常见值 5）；固定常数，不暴露为设置。超过时只保留
# 最近的图（保证当前这轮附件在内），旧图以文字注记取代，避免把整段视觉历史无上限
# 塞进每个 prompt 而撞上服务端的图数上限（400 At most N image(s)…）。
MAX_IMAGES_PER_PROMPT = 5


def prepare_agent_messages(
    messages: list[dict[str, Any]],
    max_images: int | None = None,
) -> list[dict[str, Any]]:
    """为模型准备会话历史。

    多模态 ``list[dict]`` 内容原样透传（交给 LangChain 转成 ``image_url`` 块）。
    为避免冲破服务端的每 prompt 图数上限，当图片总数超过 ``max_images`` 时只保留
    最近的图片（当前这轮的附件一定在内），被丢弃的旧图以一段文字注记取代。

    ``_parts_to_conversation`` 会把持久化的 parts 重建为标准的
    ``assistant(tool_calls) → tool(result)`` 序列；这里必须原样放行 ``role="tool"``
    与 assistant 的 ``tool_calls`` 键，否则跨轮历史只剩纯文字叙述——模型会模仿
    「只叙述不执行工具」的空转/降智模式（曾观测到连续多轮输出
    「先查看當前狀態，再一次性 commit：」等承诺句后停止）。LangChain 的
    ``convert_to_messages`` 会把放行的消息转成 ``ToolMessage`` / 带 tool_calls 的
    ``AIMessage``。
    """
    limit = int(max_images) if max_images and max_images > 0 else MAX_IMAGES_PER_PROMPT
    prepared: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content")
        if role == "tool":
            if content is None:
                continue
            prepared.append(
                {
                    "role": "tool",
                    "tool_call_id": str(message.get("tool_call_id") or ""),
                    "content": str(content),
                }
            )
            continue
        if role not in {"user", "assistant", "system"}:
            continue
        # 纯 tool_calls 的 assistant 消息 content 可为 None（无叙述时），必须保留。
        if content is None and not (role == "assistant" and message.get("tool_calls")):
            continue
        entry: dict[str, Any] = {
            "role": role,
            "content": content if isinstance(content, list) else str(content),
        }
        if role == "assistant" and message.get("tool_calls"):
            entry["tool_calls"] = message["tool_calls"]
        prepared.append(entry)
    if not prepared:
        prepared.append({"role": "user", "content": ""})
        return prepared
    _cap_images_in_prepared(prepared, limit)
    return prepared


def _cap_images_in_prepared(prepared: list[dict[str, Any]], max_images: int) -> None:
    """原地收敛：仅保留最近的 ``max_images`` 个 image_url 区块。

    最旧的图被丢弃；每条被丢弃图片的讯息会补一段合并的文字注记。当前轮（最后）
    的讯息优先保留，因此当前轮附件一定在内。不修改调用方的原始 content 列表。
    """
    positions: list[tuple[int, int]] = []
    for mi, msg in enumerate(prepared):
        content = msg.get("content")
        if isinstance(content, list):
            for bi, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "image_url":
                    positions.append((mi, bi))
    if len(positions) <= max_images:
        return
    drop = len(positions) - max_images
    dropped = set(positions[:drop])  # 按时间顺序 → 最旧在前
    out: list[dict[str, Any]] = []
    for mi, msg in enumerate(prepared):
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        kept: list[dict[str, Any]] = []
        msg_dropped = 0
        for bi, block in enumerate(content):
            if (mi, bi) in dropped:
                msg_dropped += 1
                continue
            kept.append(block)
        if msg_dropped:
            note = f"(已省略 {msg_dropped} 张较早的截图/图片，以符合模型每 prompt 的图片上限)"
            kept.insert(0, {"type": "text", "text": note})
        out.append({**msg, "content": kept})
    prepared[:] = out


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
    inline_attachments: bool = True,
    vision: bool = True,
) -> str | list[dict[str, Any]]:
    """把用户文本、引用、附件拼成发给 LLM 的内容。

    设计原则（产品决策）：附件透传给 LLM，由模型自行决定是否受理；客户端如实呈现
    模型的回复即可。**唯一的例外是按提供商的 ``vision`` 能力闸控图片附件**：提供者
    勾选了「多模态（视觉）」时图片以 ``image_url`` 原生转发；未勾选（该模型不支持
    视觉）时不把图片字节发给模型，改为文字说明，与浏览器截图「无视觉模型存盘回退」
    的策略一致——否则一张图会撞上 API 的图片拒绝或让无视觉模型「幻觉式看图」。

    ``max_attachment_bytes`` 来自设置页的「文件体积上限」（前端换算成字节后随请求
    传入）。超过该体积的二进制附件不内联字节，仅在提示词中如实说明「未转发」，
    作为防 OOM 的安全网；模型仍可在回复中说明自己无法处理该文件。

    ``inline_attachments``（R2 源頭修復）：附件全文**只在首次提供的那一輪**内联
    （``True``）；歷史重放時傳 ``False``，把附件渲染為緊湊 stub（opencode
    stripMedia/compaction 佔位符），避免「附件全文持久化 + 每轮重放重發」的永久
    成本。模型仍可要求用户重新附上，或用文件工具按需读取工作区内的文件。

    ``vision``（提供商的视觉能力，热更新）：为 ``False`` 时，内联图片附件不进
    ``image_url`` 块而是以文字注记替代；非图片附件不受影响。

    返回：
    - ``str``：无附件且无引用时，保持纯文本（向后兼容历史消息）。
    - ``list[dict]``（多模态）：含附件/引用时。文本进 ``text`` 块；图片进
      ``image_url`` 块（``vision=True``）或以文字注记替代（``vision=False``）；
      其它二进制把 base64 data URL 一并带进 ``text`` 块，模型自行决定是否解析。
      超体积的二进制不内联字节，仅在文本中如实说明。
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
    image_attachments = [
        attachment
        for attachment in attachments or []
        if str(attachment.get("type") or "").startswith("image/")
    ]
    gate_images = (not vision) and bool(image_attachments)
    if attachments:
        if inline_attachments:
            if gate_images:
                header = (
                    "The user attached images, but this model is configured WITHOUT vision "
                    "capability, so the images are NOT shown to you. Do NOT claim you can see "
                    "them; tell the user to paste the relevant text (or rely on file tools) "
                    "when you need their content:"
                    if not text
                    else "Images attached below are NOT shown to you (this model has no vision); "
                    "other attached files are forwarded. Do NOT claim you can see the images:"
                )
            else:
                header = (
                    "The user attached the following files; forward all of them to the model "
                    "and let it decide whether to use each:"
                    if not text
                    else "Attached files (all forwarded; the model decides whether to use each):"
                )
        else:
            header = (
                "Earlier attachments are referenced below; their full contents were "
                "provided when first attached and are NOT repeated here. Ask the user to "
                "re-attach a file (or read it with the file tools if it lives in the "
                "workspace) when you need its content again:"
            )
        text = f"{text}\n\n{header}" if text else header

    if text:
        blocks.append({"type": "text", "text": text})

    for attachment in attachments or []:
        name = str(attachment.get("name") or "attachment")
        size = int(attachment.get("size") or 0)
        kind = str(attachment.get("type") or "file")
        content = attachment.get("content")
        exceeds_limit = bool(attachment.get("tooLarge")) or size > max_attachment_bytes

        # R2: history replay renders compact stubs, never the raw bytes.
        if not inline_attachments:
            if kind.startswith("image/"):
                stub = f"[Image: {name} ({size} bytes, {kind})]"
            elif attachment.get("binary") or exceeds_limit:
                stub = f"[Binary attachment: {name} ({size} bytes, {kind})]"
            else:
                stub = f"[Attachment: {name} ({size} bytes, {kind})]"
            blocks.append({"type": "text", "text": f"\n- {stub}"})
            continue

        # 超过体积上限的二进制附件：不内联字节，如实说明（前端已拦截添加，
        # 这里作为后端兜底，覆盖 web/直接 API 等不经过前端拦截的路径）。
        if isinstance(content, str) and content and not exceeds_limit:
            if kind.startswith("image/"):
                if vision:
                    blocks.append({"type": "image_url", "image_url": {"url": content}})
                else:
                    blocks.append(
                        {
                            "type": "text",
                            "text": f"\n- {name} ({kind}, {size} bytes): image NOT forwarded — this provider "
                            "is configured without vision capability.",
                        }
                    )
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



# ---------------------------------------------------------------------------
# NormalizeMessagesMiddleware – keeps provider-safe message ordering.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# CoworkerSummarizationMiddleware – bounds the model context for long sessions.
# ---------------------------------------------------------------------------

# Rolling char budget for the message list fed to the model on each call. Oldest
# messages are dropped first (the first system message is always kept). The
# checkpoint still holds full history; this only bounds what the model sees and
# what gets replayed into the checkpoint.
# Fraction of ``window − max_output`` used as the resident-message budget.
# 0.75 was over-conservative (openqueue compacts at ``window − reserved``, codex
# at 90%): with token-accurate measurement + calibration the extra margin is
# unnecessary and caused premature trimming (V1). 0.9 keeps a safety edge for
# estimation error without starving the resident window.
CONTEXT_SAFETY_FACTOR = 0.9
# Compaction keeps a FIXED small recent-window of raw messages (opencode-aligned;
# opencode uses DEFAULT_KEEP_TOKENS=8000). The compacted resident set is then
# roughly ``recent + summary`` instead of the old ``budget × 0.6`` (≈118k for a
# 256k window) which left the conversation near the ceiling after every compact.


def context_budget_chars(context_window_tokens: int, max_output_tokens: int = 0) -> int:
    """Convert a model's token context window into the resident-message budget.

    ``budget = (window − max_output) × safety × chars_per_token``; a floor keeps
    tiny local models usable (avoids a budget so small every turn trims
    immediately). The output reservation matters: providers reserve
    ``max_output`` tokens from the window, so budgeting against the raw window
    leaves zero real margin (see :func:`coworker.context.effective_input_limit`).

    T1: the char budget is a DISPLAY mirror — every decision (trim/compact/
    guard/truncate) uses ``context_budget_tokens``. It derives from the SAME
    Latin chars/token constant as the estimator so the legacy char meter
    fallback never disagrees with the token meter.
    """
    from ..context import LATIN_CHARS_PER_TOKEN, effective_input_limit

    if not context_window_tokens or context_window_tokens <= 0:
        context_window_tokens = 128_000
    limit = effective_input_limit(context_window_tokens, max_output_tokens)
    return max(20_000, int(limit * CONTEXT_SAFETY_FACTOR * LATIN_CHARS_PER_TOKEN))


def context_budget_tokens(context_window_tokens: int, max_output_tokens: int = 0) -> int:
    """Token-space resident-message budget (``(window − max_output) × safety``).

    The trim/compact meter runs in tokens (CJK-aware via :func:`_estimate_tokens`)
    because providers count tokens, not characters — a char budget at a flat
    chars/token ratio badly under-counts Chinese content. ``max_output_tokens``
    is subtracted FIRST: vLLM/OpenAI-family servers enforce
    ``input + max_tokens ≤ window``, so the input ceiling is
    ``window − max_output`` — budgeting against the raw window silently spends
    the reserved output tokens and dies one token past the real limit.
    """
    from ..context import effective_input_limit

    if not context_window_tokens or context_window_tokens <= 0:
        context_window_tokens = 128_000
    limit = effective_input_limit(context_window_tokens, max_output_tokens)
    return max(5_000, int(limit * CONTEXT_SAFETY_FACTOR))


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


# Provider "per-prompt image limit" error signatures → automatic single retry
# with a reduced image set (so a single oversized multimodal prompt doesn't
# surface the raw 400 to the user).
IMAGE_LIMIT_PATTERNS = (
    "image(s) may be provided",
    "limit_mm_per_prompt",
    "too many images",
    "maximum number of images",
)


def is_image_limit_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    text = str(exc).lower()
    return any(pattern in text for pattern in IMAGE_LIMIT_PATTERNS)


def is_provider_bad_request(exc: BaseException | None) -> bool:
    """True for a provider ``400 BadRequest`` (request-format rejection).

    Matches the openai SDK's ``BadRequestError`` (status 400) and its
    "Error code: 400 - {...}" message. These are deterministic request-format
    issues (e.g. "System message must be at the beginning"), not agent stalls:
    the user can retry / edit / regenerate, so the goal must NOT be marked
    ``blocked`` on them (otherwise the GoalCard stays stuck in the stalled
    state even after a successful retry).
    """
    if exc is None:
        return False
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            if int(status) == 400:
                return True
        except (TypeError, ValueError):
            pass
    text = str(exc).lower()
    if "badrequesterror" in type(exc).__name__.lower():
        return True
    return text.startswith("error code: 400") or "error code: 400 -" in text


def _runtime_context_budget(provider: Any, model_override: str | None = None) -> tuple[int, int, str, str | None, int]:
    """Resolve ``(budget_chars, window_tokens, source, warning, max_output_tokens)``.

    The window resolution is model-aware: ``model_override`` (the model chosen for
    this turn) takes precedence over the provider's stored default model, so
    switching models mid-conversation recomputes the budget from the new model's
    context window — see B7. ``source`` is one of user/table/discovered/default.
    ``warning`` is a human-readable note (e.g. an untrusted oversized window or a
    server-reported cap) surfaced to the UI, or ``None``. ``max_output_tokens``
    is the per-request output reservation (0 ⇒ DEFAULT_MAX_OUTPUT_TOKENS) that
    every budget must subtract from the window.
    """
    try:
        from ..providers import DEFAULT_MAX_OUTPUT_TOKENS, ProviderManager

        max_output = int(getattr(provider, "max_output_tokens", 0) or 0)
        if max_output <= 0:
            max_output = DEFAULT_MAX_OUTPUT_TOKENS
        window, source, warning = ProviderManager._resolve_context_window_full(provider, model=model_override)
        return context_budget_chars(window, max_output), window, source, warning, max_output
    except Exception:  # noqa: BLE001 - a failed resolve must never break a turn
        from ..providers import DEFAULT_MAX_OUTPUT_TOKENS

        return context_budget_chars(128_000, DEFAULT_MAX_OUTPUT_TOKENS), 128_000, "default", None, DEFAULT_MAX_OUTPUT_TOKENS


def _message_text(msg: Any) -> str:
    """Extract all textual content from a message (incl. tool calls/results).

    Used for BOTH the character-based trim budget and the token estimate, so the
    context-budget meter and the actual trimming agree on message "size". Tool
    calls / tool results previously counted as zero chars and could silently push
    a tool-heavy turn past the window — see B3. Media blocks (image/audio) are
    NOT text — they are counted separately at a per-item vision cost.
    """
    from ..context import message_text

    return message_text(msg)


def _msg_chars(msg: Any) -> int:
    return len(_message_text(msg))


def _msg_tokens(msg: Any) -> int:
    """Calibrated-free token estimate for a message (CJK + base64 aware).

    Media blocks count at the per-image vision cost instead of zero (the old
    behaviour let image-bearing messages budget as empty while the provider
    charged real vision tokens).
    """
    from ..context import message_tokens

    return message_tokens(msg)


def _estimate_tokens(text: str) -> int:
    """Content-class aware token estimate (Latin ~3.8 chars/token, CJK ~0.6
    tokens/char, base64/data-URL runs at their true ~1.4 chars/token density).

    Delegates to :func:`coworker.context.estimate_text_tokens`, the single base
    estimator every meter/budget/guard shares. A flat chars/token rate badly
    under-counted dense payloads (screenshots smuggled in as base64 text
    tokenize ~2.8x denser than prose) — that under-count let a browser-heavy
    turn blow the provider window while the meter showed 50%.
    """
    from ..context import estimate_text_tokens

    return estimate_text_tokens(text)


def _normalize_usage(usage: dict[str, Any]) -> tuple[int, int]:
    """Normalize a provider usage dict into (prompt_tokens, completion_tokens).

    langchain-core 1.x stores usage on messages as a ``UsageMetadata`` TypedDict
    with ``input_tokens`` / ``output_tokens`` / ``total_tokens``, while raw
    OpenAI-compatible responses (and older langchain) use ``prompt_tokens`` /
    ``completion_tokens``. Accept both so usage accounting never silently reads
    zeros.
    """
    return (
        int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    )


def _normalize_usage_total(usage: dict[str, Any]) -> tuple[int, int]:
    """Cache-inclusive usage normalization (T3).

    The calibration fold must measure the TRUE input size — cached tokens still
    occupy the window. Providers report cache separately as
    ``input_token_details.cache_read`` (Anthropic) or
    ``prompt_tokens_details.cached_tokens`` (OpenAI); sum them in so cache-heavy
    turns do not drag the calibration factor down (which would under-count
    non-cached requests and risk an overflow).
    """
    p, c = _normalize_usage(usage)
    details = usage.get("input_token_details") or usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cache_read = int(details.get("cache_read") or details.get("cached_tokens") or 0)
    else:
        cache_read = 0
    return p + max(0, cache_read), c



# ---------------------------------------------------------------------------
# ContextGuardMiddleware – final-request boundary guard.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Agent builder – single create_agent graph (official langchain idiom).
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Concrete runtimes
# ---------------------------------------------------------------------------



