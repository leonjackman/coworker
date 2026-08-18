"""Memory library export/import as zip archives.

Export writes the tree relative to the memory root into a zip: system files at
the zip root, each project under its ``<memory_dir>/`` subtree. Transient
artifacts are skipped (hidden paths, ``.lock`` siblings, ``.DS_Store``, the
in-repo ``.trash`` fallback, migration backups).

Import is two-phase so the user can decide per-file on conflicts:

1. ``preview_import`` unpacks the zip into a staging directory and reports
   every entry with a ``exists`` flag (a conflict when the destination file
   already exists). Nothing is written to the memory root.
2. ``apply_import`` merges the staging tree per a ``{rel: "skip"|"overwrite"}``
   decision map. Entries with no decision default to ``skip`` when the
   destination exists; brand-new files are always imported.

All entry paths are validated against the memory root (no ``..`` escape, no
absolute paths, Markdown files only).
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from coworker.logger import get_logger
logger = get_logger(__name__)

_MEMORY_SUFFIXES = (".md", ".markdown")
_EXPORT_SUBDIR = "memory_exports"
_IMPORT_SUBDIR = "memory_imports"

_ESCAPE_RE = re.compile(r"(^/)|(\.\./)|(\.\.$)")


def _is_md(name: str) -> bool:
    return name.lower().endswith(_MEMORY_SUFFIXES)


def _safe_memory_rel(name: str) -> bool:
    """Reject paths that escape the memory root or are not Markdown files."""
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or _ESCAPE_RE.search(name):
        return False
    return _is_md(name)


def _excluded_parts(parts: tuple[str, ...]) -> bool:
    return any(p.startswith(".") for p in parts)


def export_memory(
    root: Path,
    work_dir: Path,
    *,
    scope: str,
    project_dirs: list[str],
) -> dict:
    """Zip a subset of the memory library. Returns ``{path, filename, size, file_count}``."""
    root = Path(root)
    out_dir = Path(work_dir) / _EXPORT_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"coworker-memory-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    zip_path = out_dir / filename

    scope = scope or "all"
    project_set = set(project_dirs or [])

    def _include_project(memory_dir: str) -> bool:
        if scope == "system":
            return False
        if scope == "projects":
            return memory_dir in project_set
        return True

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if entry.is_dir():
                    if entry.name.startswith(".") or not _include_project(entry.name):
                        continue
                    for path in sorted(entry.rglob("*")):
                        if not path.is_file() or not _is_md(path.name):
                            continue
                        rel_parts = path.relative_to(root).parts
                        if _excluded_parts(rel_parts):
                            continue
                        zf.write(path, path.relative_to(root).as_posix())
                        count += 1
                elif _is_md(entry.name) and scope in ("all", "system"):
                    zf.write(entry, entry.name)
                    count += 1

    size = zip_path.stat().st_size if zip_path.exists() else 0
    return {"path": str(zip_path), "filename": filename, "size": size, "file_count": count}


def preview_import(root: Path, work_dir: Path, zip_path: str) -> dict:
    """Unpack a zip into a staging dir; report entries without writing memory."""
    src = Path(zip_path)
    if not src.is_file():
        raise ValueError("zip file not found")
    token = uuid.uuid4().hex[:12]
    staging = Path(work_dir) / _IMPORT_SUBDIR / token
    files: list[dict] = []
    try:
        with zipfile.ZipFile(src, "r") as zf:
            for name in zf.namelist():
                if name.endswith("/") or not _safe_memory_rel(name):
                    continue
                target = (staging / name).resolve()
                try:
                    target.relative_to(staging.resolve())
                except ValueError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
                files.append({"rel": name, "exists": (root / name).is_file()})
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"cannot read archive: {exc}") from exc
    if not files:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("no valid memory files found in archive")
    return {"token": token, "files": files}


def apply_import(root: Path, work_dir: Path, token: str, decisions: dict[str, str]) -> dict:
    """Merge a previously previewed import into the memory root."""
    root = Path(root)
    staging = Path(work_dir) / _IMPORT_SUBDIR / token
    if not staging.is_dir():
        raise ValueError("import session expired or invalid")
    imported = 0
    overwritten = 0
    skipped = 0
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(staging).as_posix()
        if not _safe_memory_rel(rel):
            continue
        decision = (decisions or {}).get(rel, "skip")
        dest = root.resolve() / rel
        if dest.is_file():
            if decision == "overwrite":
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                overwritten += 1
            else:
                skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        imported += 1
    shutil.rmtree(staging, ignore_errors=True)
    return {"imported": imported, "overwritten": overwritten, "skipped": skipped}
