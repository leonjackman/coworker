"""Versioned workflow storage, draft queue and run history (W02).

Layout under ``<data_dir>/workflows``::

    <name>.yaml                     current (active) definition
    .history/<name>/v<N>.yaml       immutable version snapshots
    .drafts/<name>.yaml             agent/recorded drafts awaiting approval
    .runs/<run_id>.json             run checkpoint (for resume)
    .runs/<run_id>.events.jsonl     run event stream

YAML on disk is the source of truth; the store never rewrites a file it did not
parse.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from typing import Any

from coworker.atomicio import atomic_write_text
from coworker.logger import get_logger

from .model import Run, RunEvent, Workflow, WorkflowParseError
from .parser import load_workflow_file, parse_workflow, render_workflow

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ASCII_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def safe_filename(name: str) -> str:
    """Map a (possibly non-ASCII) workflow name to a filesystem-safe stem.

    ASCII slug names are kept verbatim (backward compatible); anything else
    (e.g. Chinese) becomes a short deterministic hash so the real name never
    touches the filesystem and can contain no path separators.
    """
    if _ASCII_SAFE_RE.match(name or ""):
        return name
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:20]
    return f"wf-{digest}"


class WorkflowStore:
    """Disk-backed workflow catalog + drafts + runs.

    Workflows live in the primary ``root`` (user scope). ``roots_provider``
    optionally returns extra roots (e.g. per-project ``.coworker/workflows``)
    that are also scanned; a workflow is read from and written back to whichever
    root already holds it (project scope wins if it was installed there).
    """

    def __init__(self, root: Path, roots_provider: Callable[[], list[Path]] | None = None):
        self.root = Path(root)
        self._roots_provider = roots_provider
        self.drafts_dir = self.root / ".drafts"
        self.history_dir = self.root / ".history"
        self.runs_dir = self.root / ".runs"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ── active workflows ────────────────────────────────────────────────

    def _extra_roots(self) -> list[Path]:
        if self._roots_provider is None:
            return []
        try:
            roots = [Path(p) for p in (self._roots_provider() or []) if p]
        except Exception:  # noqa: BLE001
            return []
        out: list[Path] = []
        for root in roots:
            if root != self.root and root not in out:
                out.append(root)
        return out

    def path_for(self, name: str) -> Path:
        return self.root / f"{safe_filename(name)}.yaml"

    def _find_path(self, name: str) -> Path | None:
        stem = safe_filename(name)
        for root in [self.root, *self._extra_roots()]:
            candidate = root / f"{stem}.yaml"
            if candidate.is_file():
                return candidate
        return None

    def exists(self, name: str) -> bool:
        return self._find_path(name) is not None

    def read_text(self, name: str) -> str | None:
        # Search every root so project-scoped workflows resolve too.
        path = self._find_path(name)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def list_active(self, source: str = "user") -> list[Workflow]:
        workflows: list[Workflow] = []
        seen: set[str] = set()
        for root in [self.root, *self._extra_roots()]:
            for path in sorted(root.glob("*.yaml")):
                workflow, _ = load_workflow_file(path, source)
                if workflow is not None and workflow.name not in seen:
                    seen.add(workflow.name)
                    workflows.append(workflow)
        return workflows

    def get(self, name: str, source: str = "user") -> Workflow | None:
        path = self._find_path(name)
        if path is None:
            return None
        workflow, _ = load_workflow_file(path, source)
        return workflow

    def save(self, workflow: Workflow, *, archive: bool = True) -> Workflow:
        """Persist a workflow; snapshots the previous version when archiving.

        Writes back to the root that already holds the workflow (so a
        project-scoped workflow stays in its project), else the primary root.
        """
        with self._lock:
            existing = self._find_path(workflow.name)
            path = existing or self.path_for(workflow.name)
            if archive and path.is_file():
                previous = self.get(workflow.name)
                if previous is not None:
                    self._snapshot(previous)
            workflow = _with_timestamps(workflow, created_existing=path.is_file())
            atomic_write_text(path, render_workflow(workflow))
            return workflow

    def _snapshot(self, workflow: Workflow) -> None:
        try:
            target_dir = self.history_dir / safe_filename(workflow.name)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"v{workflow.version}.yaml"
            if not target.exists():
                atomic_write_text(target, render_workflow(workflow))
        except Exception as exc:  # noqa: BLE001 - history must never block a save
            logger.warning("workflow snapshot failed for %s: %s", workflow.name, exc)

    def next_version(self, name: str) -> int:
        current = self.get(name)
        if current is None:
            return 1
        return int(current.version) + 1

    def delete(self, name: str) -> bool:
        with self._lock:
            found = self._find_path(name)
            if found is None:
                return False
            path = found
            try:
                path.unlink()
            except OSError:
                return False
            return True

    # ── versions (list / read / delete) ─────────────────────────────────

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        """All versions incl. the current one (newest first)."""
        items: list[dict[str, Any]] = []
        current = self.get(name)
        if current is not None:
            items.append(
                {"version": current.version, "is_current": True, "updated_at": current.updated_at or ""}
            )
        for archived in self.history(name):
            items.append(
                {
                    "version": archived["version"],
                    "is_current": False,
                    "updated_at": archived.get("updated_at", ""),
                }
            )
        # De-duplicate by version (current wins) and sort desc.
        by_version: dict[int, dict[str, Any]] = {}
        for item in items:
            existing = by_version.get(item["version"])
            if existing is None or item["is_current"]:
                by_version[item["version"]] = item
        return sorted(by_version.values(), key=lambda v: v["version"], reverse=True)

    def read_version(self, name: str, version: int) -> tuple[Workflow | None, bool]:
        current = self.get(name)
        if current is not None and current.version == version:
            return current, True
        path = self.history_dir / safe_filename(name) / f"v{version}.yaml"
        if not path.is_file():
            return None, False
        workflow, _ = load_workflow_file(path)
        return workflow, False

    def delete_version(self, name: str, version: int) -> bool:
        """Delete an archived version. The active (current) version is protected."""
        current = self.get(name)
        if current is not None and current.version == version:
            return False
        path = self.history_dir / safe_filename(name) / f"v{version}.yaml"
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def history(self, name: str) -> list[dict[str, Any]]:
        target_dir = self.history_dir / safe_filename(name)
        if not target_dir.is_dir():
            return []
        versions: list[dict[str, Any]] = []
        for path in sorted(target_dir.glob("v*.yaml")):
            version_label = path.stem.lstrip("v")
            versions.append(
                {
                    "version": int(version_label) if version_label.isdigit() else 0,
                    "file_path": str(path),
                    "updated_at": _mtime(path),
                }
            )
        versions.sort(key=lambda v: v["version"])
        return versions

    # ── drafts ──────────────────────────────────────────────────────────

    def draft_path(self, name: str) -> Path:
        return self.drafts_dir / f"{safe_filename(name)}.yaml"

    def write_draft(self, name: str, content: str) -> None:
        with self._lock:
            atomic_write_text(self.draft_path(name), content)

    def read_draft(self, name: str) -> str | None:
        path = self.draft_path(name)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def list_drafts(self) -> list[dict[str, Any]]:
        drafts: list[dict[str, Any]] = []
        for path in sorted(self.drafts_dir.glob("*.yaml")):
            content = self.read_draft(path.stem)
            if content is None:
                continue
            workflow, _ = parse_workflow(content, name_hint=path.stem, source="agent")
            entry: dict[str, Any] = {
                # Prefer the real (possibly non-ASCII) name from the draft body.
                "name": workflow.name if workflow is not None and workflow.name else path.stem,
                "created_at": _mtime(path),
                "content": content,
            }
            if workflow is not None:
                entry["description"] = workflow.description
                entry["provenance"] = workflow.provenance
                entry["step_count"] = len(workflow.steps)
                entry["sources"] = workflow.provenance.get("sources", [])
                entry["action"] = workflow.provenance.get("action", "create")
            else:
                entry["diagnostics"] = parse_workflow(content, name_hint=path.stem)[1]
            drafts.append(entry)
        return drafts

    def remove_draft(self, name: str) -> bool:
        with self._lock:
            path = self.draft_path(name)
            if not path.is_file():
                return False
            try:
                path.unlink()
            except OSError:
                return False
            return True

    def replace_draft_content(self, name: str, content: str) -> bool:
        if not self.draft_path(name).is_file():
            return False
        self.write_draft(name, content)
        return True

    # ── runs ────────────────────────────────────────────────────────────

    def run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def save_run(self, run: Run) -> None:
        with self._lock:
            atomic_write_text(
                self.run_path(run.run_id),
                json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
            )

    def load_run(self, run_id: str) -> Run | None:
        path = self.run_path(run_id)
        if not path.is_file():
            return None
        try:
            return Run.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def list_runs(self, workflow: str = "", limit: int = 50) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.glob("*.json"), key=_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if workflow and data.get("workflow") != workflow:
                continue
            runs.append(data)
            if len(runs) >= limit:
                break
        return runs

    def append_event(self, event: RunEvent) -> None:
        path = self.runs_dir / f"{event.run_id}.events.jsonl"
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.runs_dir / f"{run_id}.events.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return events


def _with_timestamps(workflow: Workflow, *, created_existing: bool) -> Workflow:
    from dataclasses import replace

    now = _now()
    created = workflow.created_at or ("" if created_existing else now)
    if created_existing and workflow.created_at:
        created = workflow.created_at
    return replace(workflow, created_at=created or now, updated_at=now)


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def parse_workflow_or_raise(content: str, *, name_hint: str = "", source: str = "user") -> Workflow:
    workflow, diagnostics = parse_workflow(content, name_hint=name_hint, source=source)
    if workflow is None:
        raise WorkflowParseError("; ".join(diagnostics) or "invalid workflow")
    return workflow
