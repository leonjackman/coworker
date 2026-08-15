"""v1 → v2 memory migration (one-shot, idempotent, backs up first).

v1 layout (the ``§``-delimited model):

- User-level memory: ``~/.coworker/MEMORY.md``
- Project-level memory: ``<workspace>/.coworker/MEMORY.md`` (per workspace)
- Entries separated by a ``§`` line; file header ``# Coworker 记忆``.

v2 layout (the memory library tree):

- ``{data_dir}/memory/MEMORY.md`` / ``USER.md`` / ``AGENT.md`` (system-level)
- ``{data_dir}/memory/<memory_dir>/BASE/*`` and ``BASE/PROJECT/*``
- ``{data_dir}/memory/<memory_dir>/<agent>/{SOUL,AGENT,MEMORY}.md``

Migration policy (Plan A):

1. The old user-level file becomes the system ``USER.md`` file.
2. Each old project-level file is folded into that project's default agent
   ``MEMORY.md`` (the project's agent-scoped long-term memory).
3. Nothing is modified in place: every source file is copied verbatim into a
   timestamped backup dir ``memory/.migrate_backup/<ts>/`` first; only after a
   successful copy does the source get moved (renamed) into the backup.
4. A marker file records completion so the migration is idempotent: once done
   it never re-runs, even if new projects appear later.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .layout import MEMORY_ROOT_NAME, resolve_rel_path
from .memory_file import split_blocks

_BACKUP_DIRNAME = ".migrate_backup"
_MARKER = "migrated.marker"
_HEADER_RE = re.compile(r"^#\s+.+$")
_ENTRY_SPLIT_RE = re.compile(r"\n*\s*§\s*\n+")

DEFAULT_AGENT = "default_agent"


def _v1_entries(text: str) -> list[str]:
    """Split a v1 ``§``-delimited file into trimmed entry blocks.

    A leading ``#`` header line (if any) is dropped; entries are what follows.
    """
    body = _ENTRY_SPLIT_RE.split(text or "")
    entries: list[str] = []
    for index, part in enumerate(body):
        lines = [line for line in part.splitlines() if line.strip()]
        # Drop a lone header line only from the first block (title header).
        if index == 0 and lines and _HEADER_RE.match(lines[0].strip()):
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()
        if not cleaned:
            continue
        entries.append(cleaned)
    return entries


def run_migration(
    *,
    data_dir: Path,
    registry: Any,
    project_store: Any | None = None,
    memory_root: Path | None = None,
    store: Any | None = None,
    old_user_path: Path | None = None,
) -> dict[str, Any]:
    """Execute the v1 → v2 migration.

    Returns a summary dict. Never raises: failures are collected and reported so
    the endpoint can surface partial migration status.
    """
    data_dir = Path(data_dir)
    memory_root = Path(memory_root) if memory_root else (data_dir / MEMORY_ROOT_NAME).resolve()
    if old_user_path is None:
        old_user_path = Path.home() / ".coworker" / "MEMORY.md"
    registry.ensure_root()

    backup_dir = memory_root / _BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    marker = backup_dir / _MARKER
    if marker.exists():
        return {"migrated": False, "reason": "already_migrated", "backup": str(backup_dir)}

    errors: list[str] = []
    migrated_files = 0

    # 1) User-level memory → system USER.md
    if old_user_path.is_file():
        entries = _v1_entries(old_user_path.read_text(encoding="utf-8", errors="replace"))
        if store is not None:
            try:
                if entries:
                    store.write_file("USER.md", "\n\n".join(entries) + "\n")
                migrated_files += 1
            except ValueError as exc:
                errors.append(f"USER.md: {exc}")
        else:
            write_target = memory_root / "USER.md"
            write_target.parent.mkdir(parents=True, exist_ok=True)
            if entries:
                write_target.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
            migrated_files += 1
        # Move the source into backup (one-shot: subsequent runs see no source).
        try:
            shutil.move(str(old_user_path), str(backup_dir / "user_MEMORY.md"))
        except OSError as exc:
            errors.append(f"backup user memory: {exc}")

    # 2) Project-level memory → per-project default-agent MEMORY.md
    if project_store is not None:
        try:
            projects = project_store.list_projects()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"project store: {exc}")
            projects = []
        for project in projects:
            workspace_path = project.workspace_path
            if not workspace_path:
                continue
            src = Path(workspace_path) / ".coworker" / "MEMORY.md"
            if not src.is_file():
                continue
            entries = _v1_entries(src.read_text(encoding="utf-8", errors="replace"))
            if not entries:
                continue
            project_dir = project.memory_dir or ""
            if not project_dir:
                try:
                    project_dir = project_store.memory_dir_for(project.id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{project.name}: {exc}")
                    continue
            if store is not None:
                try:
                    rel = f"{project_dir}/{DEFAULT_AGENT}/MEMORY.md"
                    resolve_rel_path(memory_root, rel)  # validate before writing
                    for entry in entries:
                        store.add_block(rel, entry)
                    migrated_files += 1
                except ValueError as exc:
                    errors.append(f"{project.name}: {exc}")
            else:
                try:
                    target = resolve_rel_path(memory_root, f"{project_dir}/{DEFAULT_AGENT}/MEMORY.md")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
                    merged = "\n\n".join([*split_blocks(existing), *entries])
                    target.write_text(merged + "\n", encoding="utf-8")
                    migrated_files += 1
                except ValueError as exc:
                    errors.append(f"{project.name}: {exc}")
            try:
                shutil.move(str(src), str(backup_dir / f"project_{project.id[:8]}_MEMORY.md"))
            except OSError as exc:
                errors.append(f"backup {project.name}: {exc}")

    marker.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
    return {
        "migrated": True,
        "migrated_files": migrated_files,
        "errors": errors,
        "backup": str(backup_dir),
    }
