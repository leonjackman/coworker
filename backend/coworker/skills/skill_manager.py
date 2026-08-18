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
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .skill_discovery import ScanResult, SkillScanner
from .skills import SkillDiagnostic, SkillEntry

from coworker.logger import get_logger
logger = get_logger(__name__)

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
                        commands=skill.commands,
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
            commands=skill.commands,
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

    def read_command_body(self, name: str, command: str) -> tuple[str, str] | None:
        """Return ``(body_markdown, base_dir)`` for a sub-command of a skill.

        The command's instructions are read from the file declared in its
        ``commands`` entry (relative to the package ``base_dir``; default
        ``commands/<name>.md``). Returns ``None`` if the skill or command is
        unknown.
        """
        skill = self.get(name)
        if skill is None:
            return None
        cmd = next((c for c in skill.commands if c.name == command), None)
        if cmd is None:
            return None
        base_dir = Path(skill.base_dir)
        path = (base_dir / cmd.file).resolve()
        # Defence-in-depth: a command file must stay inside its skill package.
        try:
            if path != base_dir.resolve() and not path.is_relative_to(base_dir.resolve()):
                return None
        except OSError:
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        from .skills import parse_frontmatter

        _, body = parse_frontmatter(content)
        return (body or content), str(skill.base_dir)

    # -- deletion -------------------------------------------------------

    def delete_skill(self, name: str) -> bool:
        """Uninstall a skill: remove its directory from disk and refresh.

        Returns ``True`` if the skill existed and was removed, ``False`` if no
        skill with that name is in the catalog. Refuses (``ValueError``) to
        delete anything that is not a recognised skill package directory — a
        defensive guard against path traversal / accidental deletion of
        unrelated data. Removing a skill also cleans up any stale runtime
        overrides (disabled / permission entries) that reference it.
        """
        skill = self.get(name)
        if skill is None:
            return False

        base_dir = Path(skill.base_dir).resolve()
        if not base_dir.is_dir():
            raise ValueError(f"Skill '{name}' directory not found: {base_dir}")

        # Only delete directories that live under a known scan root.
        allowed_prefixes = {
            str(r.resolve()) for r, _ in self.scanner.roots() if r is not None
        }
        inside_root = any(
            str(base_dir) == prefix or str(base_dir).startswith(prefix + os.sep)
            for prefix in allowed_prefixes
        )
        if not inside_root:
            raise ValueError(
                f"Refusing to delete '{base_dir}': not inside a known skill root"
            )

        # Require a SKILL.md to be present before nuking the directory.
        if not Path(skill.file_path).is_file() and not (base_dir / "SKILL.md").is_file():
            raise ValueError(f"Refusing to delete '{base_dir}': no SKILL.md present")

        shutil.rmtree(base_dir)

        # Drop stale overrides that reference the now-deleted skill.
        with self._lock:
            disabled = [n for n in self._config.get("disabled", []) if n != name]
            permissions = {
                k: v for k, v in self._config.get("permissions", {}).items() if k != name
            }
            changed = False
            if disabled != self._config.get("disabled", []):
                self._config["disabled"] = disabled
                changed = True
            if permissions != self._config.get("permissions", {}):
                self._config["permissions"] = permissions
                changed = True
            if changed:
                self._save_config()

        self.refresh()
        logger.info("Deleted skill '%s' from %s", name, base_dir)
        return True

