"""Memory library layout and path resolution.

The memory library lives under ``{DATA_DIR}/memory/`` and is organized as a
directory tree (multi-agent capable):

.. code-block:: text

    {memory_root}/
    ├── MEMORY.md / USER.md / AGENT.md      # system-level (user-maintained)
    ├── <memory_dir>/                        # one dir per project (timestamp id)
    │   ├── BASE/                            # user-maintained project facts
    │   │   ├── project.md / game_rule.md / …
    │   │   └── PROJECT/                     # system-generated project context
    │   │       ├── goals.md / context.md
    │   └── <agent>/                         # one dir per agent (auto-created)
    │       ├── BASE/                        # agent identity + long-term memory
    │       │   └── SOUL.md / AGENT.md / MEMORY.md
    │       └── SESSIONS/*.md

All files are plain Markdown (human-readable/editable). ``rel`` paths used by
the API are always validated against the memory root so callers cannot escape
it via ``..`` segments.
"""

from __future__ import annotations

import re
from pathlib import Path

MEMORY_ROOT_NAME = "memory"

# System-level files, in injection precedence order.
SYSTEM_FILES = ("MEMORY.md", "USER.md", "AGENT.md")

# Project-level directories.
BASE_DIR = "BASE"
PROJECT_SUBDIR = "PROJECT"
SESSIONS_DIR = "SESSIONS"

# Agent-level subdirectory holding the core files.
AGENT_BASE_DIR = BASE_DIR

# Agent core files, in injection precedence order.
AGENT_CORE_FILES = ("SOUL.md", "AGENT.md", "MEMORY.md")

# Default BASE files created for a new project (user-maintained).
DEFAULT_BASE_FILES = ("project.md", "game_rule.md", "BASE.md", "clean_code_rule.md")

# Skeleton content for system-generated PROJECT context files.
PROJECT_SKELETON = {
    "goals.md": "# 项目高层级目标\n\n（由系统生成与维护 — 记录项目的高层级目标）\n",
    "context.md": "# 项目背景与约束\n\n（由系统生成与维护 — 记录项目的高层级背景、约束与上下文）\n",
}

AGENT_SKELETON = {
    "SOUL.md": "# SOUL\n\n（agent 的灵魂文件：人格、语气、核心行为）\n",
    "AGENT.md": "# AGENT\n\n（agent 的工作模式：擅长领域、工具偏好）\n",
    "MEMORY.md": "# MEMORY\n\n（agent 的长期记忆，由 agent 在对话中写入）\n",
}

# A project memory directory is a second-resolution timestamp.
_DIRNAME_RE = re.compile(r"[^\w.-]+")
_SAFE_NAME_RE = re.compile(r"^[\w.-]+$")


def memory_dir_from_created_at(created_at: str) -> str:
    """Build the project memory directory name from an ISO ``created_at``.

    Returns ``%Y%m%d%H%M%S`` (e.g. ``20260701112359``). Uniqueness is handled
    by the caller appending ``_2/_3`` suffixes on collision; second-resolution
    timestamps satisfy the single-user desktop workflow.
    """
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y%m%d%H%M%S")
    return dt.strftime("%Y%m%d%H%M%S")


def sanitize_name(name: str, max_len: int = 64) -> str:
    """Sanitize a user-provided name into a safe directory/file segment."""
    cleaned = _DIRNAME_RE.sub("_", (name or "").strip())
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_len].rstrip(".")


def ensure_unique_dirname(root: Path, base: str) -> Path:
    """Return a non-colliding directory path ``base``, ``base_2``, ``base_3``…"""
    path = root / base
    suffix = 2
    while path.exists():
        path = root / f"{base}_{suffix}"
        suffix += 1
    return path


def resolve_rel_path(root: Path, rel: str) -> Path:
    """Resolve ``rel`` relative to ``root``, rejecting any escape attempt.

    ``rel`` uses ``/`` separators (platform-neutral). The resolved path must
    stay inside ``root``; otherwise a ``MemoryError`` is raised. Symlinks are
    resolved so a caller cannot ``..``-out then back in via a link.
    """
    if not rel or not rel.strip():
        raise ValueError("path is required")
    cleaned = rel.strip().lstrip("/\\")
    root_resolved = root.resolve()
    candidate = (root_resolved / cleaned).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes the memory root: {rel!r}") from exc
    return candidate
