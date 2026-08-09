"""SkillManager: catalog, runtime overrides, and JSON config persistence.

Skills themselves live on disk (files are the source of truth). This manager
owns discovery and the small set of runtime overrides that must survive
restarts: which skills are disabled and per-skill permissions.

Config file layout (``data_dir/skills_config.json``):

.. code-block:: json

    {
      "disabled": ["skill-a"],
      "permissions": {"skill-b": "ask"}
    }
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from .skill_discovery import ScanResult, SkillScanner
from .skills import SkillDiagnostic, SkillEntry

logger = logging.getLogger(__name__)

SKILLS_CONFIG_FILENAME = "skills_config.json"

_ALLOWED_PERMISSIONS = {"allow", "deny", "ask"}


class SkillManager:
    """Discover skills and expose catalog + runtime overrides."""

    def __init__(
        self,
        data_dir: Path | None = None,
        workspace_root: Path | None = None,
        *,
        scanner: SkillScanner | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else Path.cwd()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.scanner = scanner or SkillScanner(workspace_root=workspace_root)
        self.config_path = self.data_dir / SKILLS_CONFIG_FILENAME
        self._lock = threading.Lock()
        self._config: dict[str, Any] = {"disabled": [], "permissions": {}}
        self._cached: ScanResult | None = None
        self._load_config()

    # -- config ---------------------------------------------------------

    def _load_config(self) -> None:
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("config root must be an object")
            disabled = data.get("disabled", [])
            permissions = data.get("permissions", {})
            self._config = {
                "disabled": [str(x) for x in disabled if isinstance(x, str)],
                "permissions": {
                    str(k): str(v)
                    for k, v in permissions.items()
                    if isinstance(v, str) and v in _ALLOWED_PERMISSIONS
                },
            }
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 - a broken config must not crash startup
            logger.warning("Failed to read skills config %s: %s", self.config_path, exc)

    def _save_config(self) -> None:
        try:
            self.config_path.write_text(
                json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Failed to write skills config: %s", exc)

    # -- discovery ------------------------------------------------------

    def refresh(self) -> ScanResult:
        """Re-scan skill roots and rebuild the catalog."""
        with self._lock:
            result = self.scanner.scan()
            disabled = set(self._config.get("disabled", []))
            permissions = self._config.get("permissions", {})
            skills: list[SkillEntry] = []
            for skill in result.skills:
                skills.append(
                    SkillEntry(
                        name=skill.name,
                        description=skill.description,
                        file_path=skill.file_path,
                        base_dir=skill.base_dir,
                        source=skill.source,
                        version=skill.version,
                        disable_model_invocation=skill.disable_model_invocation,
                        enabled=skill.name not in disabled,
                    )
                )
            self._cached = ScanResult(skills=skills, diagnostics=result.diagnostics)
            return self._cached

    def _ensure_fresh(self) -> ScanResult:
        if self._cached is None:
            self.refresh()
        assert self._cached is not None
        return self._cached

    def list(self, *, enabled_only: bool = False) -> list[SkillEntry]:
        result = self._ensure_fresh()
        skills = result.skills
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return skills

    def get(self, name: str) -> SkillEntry | None:
        return next((s for s in self.list() if s.name == name), None)

    def diagnostics(self) -> list[SkillDiagnostic]:
        return list(self._ensure_fresh().diagnostics)

    # -- runtime overrides ----------------------------------------------

    def set_enabled(self, name: str, enabled: bool) -> SkillEntry | None:
        skill = self.get(name)
        if skill is None:
            return None
        with self._lock:
            disabled = set(self._config.get("disabled", []))
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
            self._config["disabled"] = sorted(disabled)
            self._save_config()
        updated = self._apply_overrides(skill)
        self._update_cached(updated)
        return updated

    def set_permission(self, name: str, permission: str) -> SkillEntry | None:
        if permission not in _ALLOWED_PERMISSIONS:
            raise ValueError(f"permission must be one of {sorted(_ALLOWED_PERMISSIONS)}")
        skill = self.get(name)
        if skill is None:
            return None
        with self._lock:
            permissions = dict(self._config.get("permissions", {}))
            if permission == "allow":
                permissions.pop(name, None)
            else:
                permissions[name] = permission
            self._config["permissions"] = permissions
            self._save_config()
        updated = self._apply_overrides(skill)
        self._update_cached(updated)
        return updated

    def _update_cached(self, skill: SkillEntry) -> None:
        """Replace a skill in the cached catalog after a toggle."""
        if self._cached is None:
            return
        replaced = False
        skills: list[SkillEntry] = []
        for existing in self._cached.skills:
            if existing.name == skill.name:
                skills.append(skill)
                replaced = True
            else:
                skills.append(existing)
        if not replaced:
            skills.append(skill)
        self._cached = ScanResult(skills=skills, diagnostics=self._cached.diagnostics)

    def _apply_overrides(self, skill: SkillEntry) -> SkillEntry:
        disabled = set(self._config.get("disabled", []))
        return SkillEntry(
            name=skill.name,
            description=skill.description,
            file_path=skill.file_path,
            base_dir=skill.base_dir,
            source=skill.source,
            version=skill.version,
            disable_model_invocation=skill.disable_model_invocation,
            enabled=skill.name not in disabled,
        )

    def injection_list(self) -> list[SkillEntry]:
        """Skills eligible for system-prompt injection (enabled + allowed)."""
        permissions = self._config.get("permissions", {})
        skills = []
        for skill in self.list(enabled_only=True):
            perm = permissions.get(skill.name, "allow")
            if perm == "deny":
                continue
            skills.append(skill)
        return skills

    def read_body(self, name: str) -> tuple[str, str] | None:
        """Return ``(body_markdown, base_dir)`` for a skill, or ``None``.

        The body is read on demand when the model (or a /skill command) loads
        the skill, and by the management UI for preview. Disabled skills are
        still readable here (management preview); agent-facing injection is
        gated separately by :meth:`injection_list` and the /skill command
        checks ``enabled`` before injecting.
        """
        skill = self.get(name)
        if skill is None:
            return None
        try:
            content = skill.file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        from .skills import parse_frontmatter

        _, body = parse_frontmatter(content)
        return body, str(skill.base_dir)
