"""System-prompt construction for the Coworker agent.

This is the "ground truth the model sees before doing anything": it exposes the
workspace (root + a bounded directory tree) so the model never has to guess
paths, lists the tools it can actually call so it never hallucinates tools, and
carries the Codex-style behaviour guidance that keeps it exploring instead of
spinning.

Layered with the memory block (injected separately by ``MemoryMiddleware`` as a
leading ``<memory>`` section) — this module owns the workspace + tooling +
behaviour context that follows it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Directory-tree rendering bounds: keep the injected context small even on huge
# repos. opencode caps the skill list at ~18k chars; we cap the workspace tree
# harder because it is a fixed preamble, not a per-skill on-demand read.
MAX_TREE_DEPTH = 3
# Bounded workspace tree (P3): TOTAL entries across the whole rendered tree.
# The layout is a hint, not a snapshot — the model is told to list directories
# before reading. Keep it small so a huge repo cannot make the resident tree a
# context bomb.
MAX_TREE_ENTRIES = 60
MAX_TREE_CHARS = 4_000
# SIBLING cap (per directory, distinct from the total MAX_TREE_ENTRIES): limits
# the entries shown under ONE directory, else a 10k-file flat repo floods the
# tree even when the total-entry cap is small.
MAX_DIR_ENTRIES = 60

_IGNORED_TOP_LEVEL = {
    ".git",
    ".coworker",
    ".coworker-tool-output",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    "release",
    "landing",
}


def _default_ignored(name: str) -> bool:
    return name in _IGNORED_TOP_LEVEL or name.startswith(".")


def build_workspace_tree(workspace: Any, *, max_depth: int = MAX_TREE_DEPTH, max_entries: int = MAX_TREE_ENTRIES, max_chars: int = MAX_TREE_CHARS) -> str:
    """Render a bounded tree of the workspace so the model knows its layout.

    Deeply nested toolchains (node_modules, .git, build output) are skipped;
    the tree is truncated past ``max_depth`` / ``max_entries`` / ``max_chars``
    with an explicit note so the model knows it is partial.
    """
    try:
        root = Path(workspace.root)
    except Exception:  # noqa: BLE001
        return ""
    lines: list[str] = []
    entry_count = 0
    char_count = 0

    def _emit(indent: str, name: str, is_dir: bool) -> bool:
        nonlocal entry_count, char_count
        if entry_count >= max_entries:
            return False
        suffix = "/" if is_dir else ""
        line = f"{indent}{name}{suffix}"
        if char_count + len(line) > max_chars:
            return False
        lines.append(line)
        entry_count += 1
        char_count += len(line)
        return True

    def _walk(path: Path, depth: int, prefix: str) -> None:
        nonlocal entry_count, char_count
        if depth > max_depth:
            return
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        shown = 0
        for child in children:
            if _default_ignored(child.name):
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if not _emit(prefix + "  ", child.name, is_dir):
                continue
            shown += 1
            if shown > MAX_DIR_ENTRIES:
                _emit(prefix + "  ", f"... (+{MAX_DIR_ENTRIES} more entries)", False)
                break
            if is_dir:
                _walk(child, depth + 1, prefix + "  ")

    try:
        children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return ""
    for child in children:
        if _default_ignored(child.name):
            continue
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if not _emit("", child.name, is_dir):
            break
        if is_dir:
            _walk(child, 1, "  ")
    if entry_count >= max_entries:
        lines.append(f"(tree truncated at {max_entries} entries; use run_command `find`/`rg --files` for the full layout)")
    return "\n".join(lines)


def build_workspace_context(workspace: Any) -> str:
    """Render the ``## Workspace`` section injected into the system prompt."""
    try:
        root = str(Path(workspace.root))
    except Exception:  # noqa: BLE001
        root = ""
    tree = build_workspace_tree(workspace)
    lines = [
        "## Workspace",
        "",
    ]
    if root:
        lines.append(f"The current project root is: ``{root}``")
        lines.append(
            "All tool paths are RELATIVE to this root (e.g. ``backend/main.py``). "
            "Never guess a path — list a directory first (``list_directory``/``run_command ls``) "
            "before reading files."
        )
    if tree:
        lines.append("")
        lines.append("Project layout (partial, bounded):")
        lines.append("```")
        lines.append(tree)
        lines.append("```")
    return "\n".join(lines)


def build_project_context_md(workspace_root: str | Path) -> str:
    """Generate the default ``CONTEXT.md`` body for a newly created project.

    Written into ``BASE/PROJECT/CONTEXT.md`` at project creation so the project
    memory (which is injected to the agent) always carries the project's STABLE
    identity (name + root path). The directory tree is deliberately NOT snapshotted
    here — it changes continuously during development, so a static tree would go
    stale. The live project layout is injected per-request via the ``## Workspace``
    system-prompt section (see ``build_workspace_context``).
    """
    root = Path(workspace_root)
    name = root.name
    return "\n".join(
        [
            "# 项目背景与约束",
            "",
            "（由系统生成与维护 — 记录项目的高层级背景、约束与上下文）",
            "",
            "## 项目信息",
            "",
            f"- **项目名**: {name}",
            f"- **项目根路径**: `{root}`",
            "",
            "## 开发提示",
            "",
            "- 项目目录结构会随开发持续变化，请以当前实时结构为准（用工具列出目录），"
            "不要依赖本文件中的静态路径。",
            "- 如需补充项目的技术栈、运行/测试指令、约定等稳定信息，请在此文件维护。",
        ]
    )




def build_cw_system_prompt() -> str:
    """Assemble the behaviour-only base system prompt for the Coworker agent.

    Codex-style behaviour guidance, adapted and compressed for Coworker:
    * Always ground yourself in the real workspace (never guess paths).
    * Keep going until the task is resolved; do not fabricate results.
    * Never hallucinate tools — use only the ones provided to you.
    * On tool failure, change strategy instead of retrying the same call.
    * Communicate like a concise teammate (Chinese by default).

    The workspace layout, MCP attribution, memory index and skills catalog are
    composed by ``SystemAssembler`` (behaviour first, dynamic last — B2); this
    function only provides the behaviour core so it is never duplicated.
    """
    behaviour = (
        "You are Coworker, a local coding assistant.\n"
        "Work until the user's request is genuinely resolved; do NOT stop early "
        "and NEVER fabricate results, file contents, or tool outputs. If you are "
        "not sure, verify with a tool before claiming anything.\n"
        "\n"
        "## Working method\n"
        "- Explore before you act: list the project layout first, read the files "
        "you actually need, then make a plan.\n"
        "- Prefer focused, surgical edits; fix the root cause, not surface symptoms.\n"
        "- Run or build to validate your work when practical.\n"
        "- Keep the user informed with short progress notes before big actions.\n"
        "\n"
        "## Tool discipline\n"
        "- Use ONLY the tools provided to you. Never invent a tool name (e.g. "
        "there is no ``list_directory`` — use ``run_command ls``).\n"
        "- If a tool call fails, do NOT re-run the exact same call. Analyze the "
        "error and change approach (narrow the scope, pick a different tool, or "
        "summarize and answer directly).\n"
        "- Read large files page by page: ``read_file`` returns a bounded window; "
        "follow the ``next_offset`` hint to continue.\n"
        "- Truncated command output is saved to disk — use the returned path to "
        "view the full output instead of re-running the command.\n"
        "\n"
        "## Parallelism\n"
        "- For independent research/analysis tasks, delegate to sub-agent workers "
        "in PARALLEL: call ``use_workers`` with all tasks at once (or issue "
        "multiple ``use_worker`` calls in one response) instead of one at a time.\n"
    )
    return behaviour
