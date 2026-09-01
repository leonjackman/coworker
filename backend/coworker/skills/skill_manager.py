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

from .skill_discovery import AGENTS_SKILLS_DIR, ScanResult, SkillScanner
from .skills import SkillDiagnostic, SkillEntry, set_frontmatter_value

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
                        provenance=skill.provenance,
                        status=skill.status,
                        sources=list(skill.sources),
                        created_at=skill.created_at,
                        bundle=skill.bundle,
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
            provenance=skill.provenance,
            status=skill.status,
            sources=list(skill.sources),
            created_at=skill.created_at,
            bundle=skill.bundle,
        )

    def injection_list(self) -> list[SkillEntry]:
        """Skills eligible for system-prompt injection.

        Only **active** skills are injected — drafts awaiting approval are
        never surfaced to the agent. Filtered further by enabled + permission.
        """
        permissions = self._config.get("permissions", {})
        skills = []
        for skill in self.list(enabled_only=True):
            if skill.status != "active":
                continue
            perm = permissions.get(skill.name, "allow")
            if perm == "deny":
                continue
            skills.append(skill)
        return skills

    def pending(self) -> list[SkillEntry]:
        """Draft skills awaiting approval (self-calibration queue)."""
        return [s for s in self.list() if s.status == "draft"]

    def set_status(self, name: str, status: str) -> SkillEntry | None:
        """Flip a skill between ``active`` and ``draft`` (approve/reopen).

        Rewrites the ``status`` key in the SKILL.md frontmatter and refreshes
        the catalog. Returns the updated entry, or ``None`` if unknown.
        """
        if status not in ("active", "draft"):
            raise ValueError("status must be 'active' or 'draft'")
        skill = self.get(name)
        if skill is None:
            return None
        try:
            content = skill.file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"unreadable skill: {exc}") from exc
        updated = set_frontmatter_value(content, "status", status)
        if updated == content:
            return skill
        try:
            skill.file_path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"unable to write skill: {exc}") from exc
        self.refresh()
        return self.get(name)

    # -- self-calibration draft queue -----------------------------------
    #
    # Agent-written skills (self-calibration) are staged under
    # ``<user_skills>/.pending/<name>/SKILL.md`` — a hidden directory the
    # scanner ignores — so a draft can never be injected into the agent's
    # context before a human approves it. Approving a NEW skill moves it into
    # the main skills dir; approving a REPLACEMENT overwrites the existing
    # active skill's SKILL.md.

    @property
    def _pending_root(self) -> Path:
        return self.scanner.user_skills_dir / AGENTS_SKILLS_DIR / ".pending"

    def _pending_skill_file(self, name: str) -> Path:
        return self._pending_root / name / "SKILL.md"

    @property
    def _user_skills_dir(self) -> Path:
        return self.scanner.user_skills_dir / AGENTS_SKILLS_DIR

    def _normalize_agent_draft(self, content: str, *, sources: list[str] | None = None) -> str:
        """Force self-calibration metadata onto a staged SKILL.md."""
        from datetime import datetime, timezone

        from .skills import parse_frontmatter, set_frontmatter_list

        out = set_frontmatter_value(content, "status", "draft")
        out = set_frontmatter_value(out, "provenance", "agent")
        out = set_frontmatter_value(out, "bundle", "agent-learned")
        if sources:
            out = set_frontmatter_list(out, "sources", sources)
        fm, _ = parse_frontmatter(out)
        existing_created = fm.get("created_at") if isinstance(fm.get("created_at"), str) else ""
        if not existing_created.strip():
            out = set_frontmatter_value(
                out, "created_at", datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        return out

    def _write_pending(
        self,
        name: str,
        content: str,
        *,
        require_existing: bool,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        if not name or not name.strip():
            return {"status": "error", "message": "Skill name is required"}
        if not content or not content.strip():
            return {"status": "error", "message": "Skill content (SKILL.md) is required"}
        from .skills import parse_frontmatter, validate_description, validate_name

        frontmatter, _ = parse_frontmatter(content)
        resolved = name.strip()
        if isinstance(frontmatter.get("name"), str):
            resolved = frontmatter["name"].strip() or resolved
        desc = frontmatter.get("description") if isinstance(frontmatter.get("description"), str) else ""
        problems = validate_name(resolved) + validate_description(desc)
        if problems:
            return {"status": "error", "message": "; ".join(problems)}

        existing = self.get(resolved)
        if require_existing and existing is None:
            return {"status": "error", "message": f"skill not found: {resolved}"}
        if not require_existing and existing is not None:
            return {"status": "error", "message": f"skill already exists: {resolved}"}

        normalized = self._normalize_agent_draft(content, sources=sources)
        target = self._pending_skill_file(resolved)
        # The exists() check and the write must be atomic: two background reviews
        # (e.g. an original turn + a regenerate) could otherwise both pass the
        # check and clobber each other's draft. self._lock is never held while
        # calling self.get()/refresh() (non-reentrant), so only the critical
        # section goes under it.
        with self._lock:
            if target.exists():
                return {"status": "error", "message": f"a pending draft already exists: {resolved}"}
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(normalized, encoding="utf-8")
            except OSError as exc:
                return {"status": "error", "message": f"unable to stage draft: {exc}"}
        return {"status": "ok", "name": resolved, "staged": True}

    def stage_skill_draft(self, name: str, content: str, *, sources: list[str] | None = None) -> dict[str, Any]:
        """Stage a NEW skill (agent-created) as a pending draft for approval."""
        return self._write_pending(name, content, require_existing=False, sources=sources)

    def stage_skill_replacement(
        self, name: str, content: str, *, sources: list[str] | None = None
    ) -> dict[str, Any]:
        """Stage a full replacement of an EXISTING skill (edit) for approval."""
        return self._write_pending(name, content, require_existing=True, sources=sources)

    def stage_skill_patch(
        self,
        name: str,
        old_string: str,
        new_string: str,
        *,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Propose a targeted patch to an existing active skill (staged)."""
        skill = self.get(name)
        if skill is None:
            return {"status": "error", "message": f"skill not found: {name}"}
        try:
            base = skill.file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"status": "error", "message": f"unreadable skill: {exc}"}
        if not old_string or old_string not in base:
            return {"status": "error", "message": "old_string was not found in the skill body"}
        proposed = base.replace(old_string, new_string, 1)
        return self._write_pending(name, proposed, require_existing=True, sources=sources)

    def pending(self) -> list[dict[str, Any]]:
        """List pending drafts (the self-calibration review queue)."""
        root = self._pending_root
        if not root.is_dir():
            return []
        drafts: list[dict[str, Any]] = []
        from .skills import parse_frontmatter

        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                content = skill_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _ = parse_frontmatter(content)
            name = fm.get("name") if isinstance(fm.get("name"), str) else child.name
            description = fm.get("description") if isinstance(fm.get("description"), str) else ""
            version = fm.get("version") if isinstance(fm.get("version"), str) else ""
            raw_sources = fm.get("sources")
            sources = [s for s in raw_sources if isinstance(s, str)] if isinstance(raw_sources, list) else []
            created_at = fm.get("created_at") if isinstance(fm.get("created_at"), str) else ""
            bundle = fm.get("bundle") if isinstance(fm.get("bundle"), str) else "agent-learned"
            drafts.append(
                {
                    "name": name,
                    "description": description,
                    "version": version,
                    "file_path": str(skill_file),
                    "provenance": "agent",
                    "status": "draft",
                    "sources": sources,
                    "created_at": created_at,
                    "bundle": bundle,
                }
            )
        return drafts

    def read_pending(self, name: str) -> str | None:
        """Full SKILL.md content of a pending draft (or ``None``)."""
        skill_file = self._pending_skill_file(name)
        if not skill_file.is_file():
            return None
        try:
            return skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def update_pending(self, name: str, content: str) -> dict[str, Any]:
        """Overwrite a pending draft (edit-before-approve)."""
        skill_file = self._pending_skill_file(name)
        if not skill_file.is_file():
            return {"status": "error", "message": f"no pending draft: {name}"}
        if not content or not content.strip():
            return {"status": "error", "message": "Skill content (SKILL.md) is required"}
        from .skills import parse_frontmatter, validate_description, validate_name

        fm, _ = parse_frontmatter(content)
        resolved = name.strip()
        if isinstance(fm.get("name"), str):
            resolved = fm["name"].strip() or resolved
        desc = fm.get("description") if isinstance(fm.get("description"), str) else ""
        problems = validate_name(resolved) + validate_description(desc)
        if problems:
            return {"status": "error", "message": "; ".join(problems)}
        normalized = self._normalize_agent_draft(content)
        try:
            skill_file.write_text(normalized, encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "message": f"unable to update draft: {exc}"}
        return {"status": "ok", "name": name, "updated": True}

    def approve_pending(self, name: str) -> dict[str, Any]:
        """Approve a draft: activate a NEW skill or apply a replacement."""
        src = self._pending_skill_file(name)
        if not src.is_file():
            return {"status": "error", "message": f"no pending draft: {name}"}
        try:
            content = src.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"status": "error", "message": f"unreadable draft: {exc}"}
        content = set_frontmatter_value(content, "status", "active")
        existing = self.get(name)
        try:
            if existing is None:
                target = self._user_skills_dir / name
                if target.exists():
                    return {"status": "error", "message": f"target skill directory already exists: {name}"}
                target.mkdir(parents=True, exist_ok=True)
                (target / "SKILL.md").write_text(content, encoding="utf-8")
            else:
                existing.file_path.write_text(content, encoding="utf-8")
            shutil.rmtree(src.parent)
        except OSError as exc:
            return {"status": "error", "message": f"unable to approve draft: {exc}"}
        self.refresh()
        return {"status": "ok", "name": name, "approved": True}

    def reject_pending(self, name: str) -> dict[str, Any]:
        """Reject a draft (delete it from the queue)."""
        src = self._pending_skill_file(name)
        if not src.is_file():
            return {"status": "error", "message": f"no pending draft: {name}"}
        try:
            shutil.rmtree(src.parent)
        except OSError as exc:
            return {"status": "error", "message": f"unable to reject draft: {exc}"}
        return {"status": "ok", "name": name, "rejected": True}

    def skill_manage(
        self,
        action: str,
        name: str,
        *,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Orchestrator for the agent's ``skill_manage`` tool.

        ``create`` stages a new skill; ``edit``/``patch`` stage a replacement of
        an existing skill; ``delete`` removes it immediately (HITL-gated). Every
        write is staged as a draft awaiting approval — the agent never enables a
        skill on its own.
        """
        action = (action or "").lower()
        if action == "create":
            if not content:
                return {"status": "error", "message": "content (SKILL.md) is required for create"}
            return self.stage_skill_draft(name, content, sources=sources)
        if action == "edit":
            if not content:
                return {"status": "error", "message": "content (SKILL.md) is required for edit"}
            return self.stage_skill_replacement(name, content, sources=sources)
        if action == "patch":
            return self.stage_skill_patch(name, old_string or "", new_string or "", sources=sources)
        if action == "delete":
            if self.get(name) is None:
                return {"status": "error", "message": f"skill not found: {name}"}
            try:
                self.delete_skill(name)
            except ValueError as exc:
                return {"status": "error", "message": str(exc)}
            return {"status": "ok", "name": name, "deleted": True}
        return {"status": "error", "message": f"unknown skill_manage action: {action}"}

    def apply_agent_skill(
        self,
        action: str,
        name: str,
        content: str,
        *,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write an agent-produced skill DIRECTLY as active (no draft queue).

        Used when the user disables "require approval" (Hermes-style free write)
        for both the review loop and the in-conversation ``skill_manage`` tool.
        ``action`` is ``create`` (new skill) or ``update`` (overwrite existing).
        """
        from .skills import parse_frontmatter, validate_description, validate_name

        if action not in ("create", "update"):
            return {"status": "error", "message": f"unsupported apply action: {action}"}
        if not name or not name.strip():
            return {"status": "error", "message": "Skill name is required"}
        if not content or not content.strip():
            return {"status": "error", "message": "Skill content (SKILL.md) is required"}

        frontmatter, _ = parse_frontmatter(content)
        resolved = name.strip()
        if isinstance(frontmatter.get("name"), str):
            resolved = frontmatter["name"].strip() or resolved
        desc = frontmatter.get("description") if isinstance(frontmatter.get("description"), str) else ""
        problems = validate_name(resolved) + validate_description(desc)
        if problems:
            return {"status": "error", "message": "; ".join(problems)}

        existing = self.get(resolved)
        if action == "create" and existing is not None:
            return {"status": "error", "message": f"skill already exists: {resolved}"}
        if action == "update" and existing is None:
            return {"status": "error", "message": f"skill not found: {resolved}"}

        from .skills import set_frontmatter_list, set_frontmatter_value

        out = set_frontmatter_value(content, "status", "active")
        out = set_frontmatter_value(out, "provenance", "agent")
        out = set_frontmatter_value(out, "bundle", "agent-learned")
        if sources:
            out = set_frontmatter_list(out, "sources", sources)
        try:
            with self._lock:
                if existing is None:
                    target = self._user_skills_dir / resolved
                    if target.exists():
                        return {"status": "error", "message": f"target skill directory already exists: {resolved}"}
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "SKILL.md").write_text(out, encoding="utf-8")
                else:
                    existing.file_path.write_text(out, encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "message": f"unable to apply skill: {exc}"}
        self.refresh()
        return {"status": "ok", "name": resolved, "applied": True, "action": action}

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

