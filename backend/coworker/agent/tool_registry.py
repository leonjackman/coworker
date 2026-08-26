"""Authoritative tool catalogue for Coworker's system prompt.

Mirrors the "tool registry" pattern from ARKS (``catalog.py``) and openclaw
(``tool-catalog.ts``): every tool the agent can call is described once, grouped
by section, and injected into the system prompt so the model knows exactly what
it can do and never hallucinates a tool name.

The catalogue is a *description source* only — actual availability is enforced
physically by ``PhaseToolGateMiddleware``. ``build_tool_context`` (in
``system_prompt.py``) merges this with the live tool list so only tools that
actually exist and are currently allowed show up.
"""

from __future__ import annotations

# Sections (order matters — rendered in this order).
SECTION_ORDER = ["fs", "runtime", "web", "memory", "collaboration", "skills", "misc"]

TOOL_REGISTRY: dict[str, dict[str, str]] = {
    # ── Filesystem ─────────────────────────────────────────────
    "search_files": {
        "section": "fs",
        "summary": "Search UTF-8 text files in the workspace by query/path.",
    },
    "read_file": {
        "section": "fs",
        "summary": "Read a text file as a bounded line window; page with offset.",
    },
    "write_file": {
        "section": "fs",
        "summary": "Create or overwrite a UTF-8 text file.",
    },
    "replace_in_file": {
        "section": "fs",
        "summary": "Replace exact text in a file (single or all occurrences).",
    },
    "apply_text_edits": {
        "section": "fs",
        "summary": "Apply a list of precise text edits to files.",
    },
    "git_status": {
        "section": "fs",
        "summary": "Inspect git status/branch/diff of the workspace.",
    },
    # ── Runtime ────────────────────────────────────────────────
    "run_command": {
        "section": "runtime",
        "summary": "Run a shell command in the workspace (allowed commands only).",
    },
    # ── Web ────────────────────────────────────────────────────
    "web_search": {
        "section": "web",
        "summary": "Search the web for up-to-date information.",
    },
    "web_fetch": {
        "section": "web",
        "summary": "Fetch and read a web page.",
    },
    "browser": {
        "section": "web",
        "summary": "Drive a headless browser for interactive web tasks.",
    },
    # ── Memory ─────────────────────────────────────────────────
    "memory": {
        "section": "memory",
        "summary": "Write durable long-term facts (user/project/agent memory).",
    },
    "memory_read": {
        "section": "memory",
        "summary": "Read long-term memory on demand.",
    },
    # ── Collaboration ──────────────────────────────────────────
    "ask_user": {
        "section": "collaboration",
        "summary": "Ask the user a clarifying question.",
    },
    "delegate_task": {
        "section": "collaboration",
        "summary": "Delegate a single research/analysis task to a sub-agent worker.",
    },
    "delegate_parallel": {
        "section": "collaboration",
        "summary": "Delegate several independent tasks to workers in parallel.",
    },
    "use_worker": {
        "section": "collaboration",
        "summary": "Spawn one sub-agent worker for an independent task.",
    },
    "use_workers": {
        "section": "collaboration",
        "summary": "Spawn multiple sub-agent workers in parallel for independent tasks.",
    },
    "create_team": {
        "section": "collaboration",
        "summary": "Create a multi-agent team.",
    },
    "create_team_member": {
        "section": "collaboration",
        "summary": "Add a member to a multi-agent team.",
    },
    # ── Skills ─────────────────────────────────────────────────
    "install_skill": {
        "section": "skills",
        "summary": "Install a skill from the market/catalog.",
    },
    "load_skill": {
        "section": "skills",
        "summary": "Load a skill's SKILL.md to follow its instructions.",
    },
    # ── Misc / orchestration ───────────────────────────────────
    "write_todos": {
        "section": "misc",
        "summary": "Create/update the visible task checklist (use for planning).",
    },
    "read_session": {
        "section": "misc",
        "summary": "Read a past conversation session the user referenced.",
    },
    "get_goal": {
        "section": "misc",
        "summary": "Read the current persistent goal.",
    },
    "update_goal": {
        "section": "misc",
        "summary": "Mark the current goal complete or blocked.",
    },
}

# Tools not in the catalogue (MCP tools, plugin tools, dynamically registered)
# are rendered generically when present in the live tool list.
SECTION_LABELS = {
    "fs": "Filesystem",
    "runtime": "Runtime",
    "web": "Web",
    "memory": "Memory",
    "collaboration": "Collaboration",
    "skills": "Skills",
    "misc": "Orchestration",
}


def section_for(tool_name: str) -> str:
    """Return the registry section for a tool name ('' when unknown)."""
    entry = TOOL_REGISTRY.get(tool_name)
    return (entry or {}).get("section", "")


def summary_for(tool_name: str) -> str:
    """Return the one-line summary for a tool name ('' when unknown)."""
    entry = TOOL_REGISTRY.get(tool_name)
    return (entry or {}).get("summary", "")
