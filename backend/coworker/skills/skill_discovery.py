"""Multi-root skill discovery.

Scans the standard Agent Skills directories plus Coworker's own directories:

- User config: ``~/.agents/skills``, ``~/.coworker/skills``
- Workspace: ``<workspace>/.agents/skills``, ``<workspace>/.coworker/skills``

A directory containing ``SKILL.md`` is a skill root (not recursed further);
directories without one are recursed. Hidden directories, ``node_modules`` and
git-ignored paths are skipped. Same-name collisions keep the first winner.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .skills import (
    MAX_SKILL_FILE_BYTES,
    SkillDiagnostic,
    SkillEntry,
    load_skill_from_file,
)

logger = logging.getLogger(__name__)

AGENTS_SKILLS_DIR = ".agents/skills"
COWORKER_SKILLS_DIR = ".coworker/skills"
SKILL_FILE = "SKILL.md"

# Directories never scanned, even without SKILL.md.
_SKIP_DIR_NAMES = {"node_modules", ".git", "__pycache__", ".venv", "venv"}


@dataclass(frozen=True)
class ScanResult:
    skills: list[SkillEntry]
    diagnostics: list[SkillDiagnostic]


class SkillScanner:
    """Scan skill roots and produce entries + diagnostics."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        user_skills_dir: Path | None = None,
        *,
        max_bytes: int = MAX_SKILL_FILE_BYTES,
    ):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.user_skills_dir = (
            Path(user_skills_dir).resolve() if user_skills_dir else _default_user_skills_dir()
        )
        self.max_bytes = max_bytes

    def roots(self) -> list[tuple[Path, str]]:
        """Return ``(directory, source_label)`` pairs in precedence order.

        Project-level roots precede user-level roots (more specific wins, per
        the Agent Skills convention); duplicate physical directories are
        deduplicated (the first, higher-precedence entry survives).
        """
        candidates: list[tuple[Path, str]] = []
        # Workspace-level (most specific) first.
        if self.workspace_root is not None:
            candidates.append((self.workspace_root / COWORKER_SKILLS_DIR, "coworker-project"))
            candidates.append((self.workspace_root / AGENTS_SKILLS_DIR, "project"))
        # User-level.
        if self.user_skills_dir:
            candidates.append((self.user_skills_dir / COWORKER_SKILLS_DIR, "coworker-user"))
            candidates.append((self.user_skills_dir / AGENTS_SKILLS_DIR, "user"))
        roots: list[tuple[Path, str]] = []
        seen: set[str] = set()
        for root, source in candidates:
            resolved = str(root.resolve()) if root.exists() else str(root.absolute())
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append((root, source))
        return roots

    def scan(self) -> ScanResult:
        all_skills: list[SkillEntry] = []
        diagnostics: list[SkillDiagnostic] = []
        seen_names: set[str] = set()

        for root, source in self.roots():
            if not root.is_dir():
                continue
            try:
                collected, diags = self._scan_tree(root, source)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Skill scan failed for %s: %s", root, exc)
                continue
            diagnostics.extend(diags)
            for skill in collected:
                if skill.name in seen_names:
                    diagnostics.append(
                        SkillDiagnostic(
                            "collision",
                            skill.name,
                            skill.file_path,
                            f"name collision: '{skill.name}' already loaded from another root",
                        )
                    )
                    continue
                seen_names.add(skill.name)
                all_skills.append(skill)

        all_skills.sort(key=lambda s: (s.source, s.name))
        return ScanResult(skills=all_skills, diagnostics=diagnostics)

    def _scan_tree(
        self, directory: Path, source: str
    ) -> tuple[list[SkillEntry], list[SkillDiagnostic]]:
        skills: list[SkillEntry] = []
        diagnostics: list[SkillDiagnostic] = []
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
        for entry in entries:
            if entry.is_symlink():
                try:
                    target = Path(entry.path).resolve()
                except OSError:  # pragma: no cover - broken symlink
                    continue
                if not target.is_file() and not target.is_dir():
                    continue
            if entry.is_file() and entry.name == SKILL_FILE:
                skill_file = Path(entry.path)
                skill, diags = load_skill_from_file(
                    skill_file, source, max_bytes=self.max_bytes
                )
                diagnostics.extend(diags)
                if skill is not None:
                    skills.append(skill)
                # A directory containing SKILL.md is a skill root: do not recurse.
                return skills, diagnostics
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in _SKIP_DIR_NAMES:
                continue
            sub_skills, sub_diags = self._scan_tree(Path(entry.path), source)
            skills.extend(sub_skills)
            diagnostics.extend(sub_diags)
        return skills, diagnostics


def _default_user_skills_dir() -> Path | None:
    home = Path.home()
    try:
        return home
    except Exception:  # pragma: no cover
        return None
