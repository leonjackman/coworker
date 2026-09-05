# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.skills.skill_market import SkillMarketManager
from coworker.api.state import (
    app,
    logger,
    skill_manager
)

from fastapi import APIRouter

router = APIRouter()


def _norm_market_key(value: str | None) -> str:
    return "".join(c for c in (value or "").lower() if c.isalnum())
async def _backfill_market_provenance_once() -> None:
    installed = list_skills()["skills"]
    provenance = skill_market_manager._load_provenance()
    for s in installed:
        name = s.get("name")
        if not name or name in provenance:
            continue
        target = _norm_market_key(name)
        if not target:
            continue
        hit: tuple[str, str | None, str | None] | None = None
        for source in ("skillhub", "clawhub"):
            try:
                page = await skill_market_manager.search(source, name, limit=5)
            except Exception:
                continue
            for sk in page.skills:
                if _norm_market_key(sk.get("slug")) == target or _norm_market_key(sk.get("name")) == target:
                    hit = (source, sk.get("slug"), sk.get("owner"))
                    break
            if hit:
                break
        if hit:
            skill_market_manager.record_install(hit[0], hit[1], hit[2], name)
async def _backfill_market_provenance_loop() -> None:
    while True:
        try:
            await _backfill_market_provenance_once()
            return
        except Exception as exc:  # network / upstream transient failures
            logger.warning("market provenance backfill deferred: %s", exc)
            await asyncio.sleep(120)
class SkillUpdatePayload(BaseModel):
    enabled: bool | None = None
    permission: str | None = None
class SkillValidatePayload(BaseModel):
    path: str = ""
    name: str = ""
@router.get("/skills")
def list_skills(enabled_only: bool = False):
    """List discovered skills (catalog) with scan diagnostics."""
    result = skill_manager.refresh()
    skills = [s.to_dict() for s in result.skills if not enabled_only or s.enabled]
    return {
        "status": "ok",
        "skills": skills,
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "count": len(skills),
    }
skill_market_manager = SkillMarketManager(Path.home())
class MarketInstallRequest(BaseModel):
    """Request body for skill installation from market."""
    source: str  # "skillhub" | "clawhub"
    slug: str    # skill identifier
    owner: str | None = None  # disambiguates colliding slugs (ClawHub)
class SkillInstallRequest(BaseModel):
    """Request body for installing a skill from raw SKILL.md content.

    Used by chat-driven installs (agent ``install_skill`` tool) and any external
    caller that already has the skill's SKILL.md text. ``commands`` optionally
    declares sub-commands whose instruction bodies are written to
    ``commands/<name>.md`` and listed in the root SKILL.md frontmatter.
    """
    name: str  # skill slug/identifier
    content: str  # full SKILL.md content including YAML frontmatter
    commands: list[dict[str, str]] | None = None  # [{name, description, body}]
@router.get("/skills/market")
def list_market_sources():
    """List available skill market sources."""
    return {
        "status": "ok",
        "sources": [
            {"id": "skillhub", "name": "腾讯 SkillHub", "description": "中文技能市场，国内 CDN 加速"},
            {"id": "clawhub", "name": "ClawHub", "description": "全球最大技能市场"},
        ],
    }
@router.get("/skills/market/categories")
async def list_market_categories(source: str):
    """Describe the filter dimension a market source can slice on.

    ``kind`` says which query parameter the tabs drive — ``category`` for
    SkillHub, ``sort`` for ClawHub (which has no category vocabulary upstream).
    The legacy ``categories`` key is kept so older clients keep working.
    """
    try:
        facet = await skill_market_manager.list_facets(source)
        items = facet.get("items", [])
        return {
            "status": "ok",
            "kind": facet.get("kind"),
            "default": facet.get("default"),
            "categories": items,
            "count": len(items),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
def _mark_market_installed(
    result: dict, installed_names: set, installed_ids: set
) -> dict:
    """Annotate each market skill dict with ``installed``.

    Matching is exact-first: a skill is marked installed when its ``(source,
    slug, owner)`` matches a record persisted at install time (see
    ``SkillMarketManager.record_install``). Owner is part of the identity because
    ClawHub slugs are not unique across owners.

    A name/slug fallback is kept so skills installed *before* this feature also
    get flagged when their frontmatter ``name`` happens to match.
    """
    skills = result.get("skills") if isinstance(result, dict) else None
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            slug = skill.get("slug") or ""
            name = skill.get("name") or ""
            src = skill.get("source") or ""
            owner = skill.get("owner") or ""
            skill["installed"] = (
                (src, slug, owner) in installed_ids
                or slug in installed_names
                or name in installed_names
            )
    return result
@router.get("/skills/market/search")
async def search_market_skills(
    source: str,
    q: str,
    limit: int = 20,
    offset: int = 0,
    category: str | None = None,
):
    """Search skills in a market source."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="Search query 'q' is required")
    try:
        page = await skill_market_manager.search(
            source, q.strip(), limit, offset, category
        )
        result = page.to_dict()
        installed_names = {s["name"] for s in list_skills()["skills"]}
        installed_ids = skill_market_manager.installed_identifiers()
        _mark_market_installed(result, installed_names, installed_ids)
        return {"status": "error" if page.error else "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/skills/market/hot")
async def list_hot_market_skills(
    source: str,
    limit: int = 20,
    offset: int = 0,
    cursor: str | None = None,
    category: str | None = None,
    sort: str | None = None,
):
    """List hot/popular skills in a market source."""
    try:
        page = await skill_market_manager.list_hot(
            source, limit, offset, cursor, category, sort
        )
        result = page.to_dict()
        installed_names = {s["name"] for s in list_skills()["skills"]}
        installed_ids = skill_market_manager.installed_identifiers()
        _mark_market_installed(result, installed_names, installed_ids)
        return {"status": "error" if page.error else "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/skills/market/detail")
async def get_market_skill_detail(
    source: str,
    slug: str,
    owner: str | None = None,
):
    """Fetch full SKILL.md content for a market skill (by source + slug)."""
    try:
        result = await skill_market_manager.get_detail(source, slug, owner)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.post("/skills/market/install")
async def install_market_skill(request: MarketInstallRequest):
    """Install a skill from a market source."""
    try:
        result = await skill_market_manager.install(
            request.source, request.slug, request.owner
        )
        if result.get("status") == "ok":
            # Auto-trigger scan to pick up the newly installed skill
            skill_manager.refresh()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.post("/skills/install")
def install_skill_from_content(request: SkillInstallRequest):
    """Install a skill from raw SKILL.md content (chat-driven / agent installs)."""
    try:
        result = skill_market_manager.install_from_content(
            request.name, request.content, commands=request.commands
        )
        if result.get("status") == "ok":
            # Auto-trigger scan to pick up the newly installed skill
            skill_manager.refresh()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get("/skills/pending")
def list_pending_skills():
    """List draft skills awaiting approval (self-calibration review queue)."""
    return {"status": "ok", "pending": skill_manager.pending()}
class SkillPendingUpdateRequest(BaseModel):
    """Request body for editing a pending draft before approval."""
    content: str  # full proposed SKILL.md content
def _pending_skill_404(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no pending draft: {name}")
@router.get("/skills/pending/{skill_name}")
def get_pending_skill(skill_name: str):
    """Full proposed SKILL.md of one pending draft (for review/edit)."""
    body = skill_manager.read_pending(skill_name)
    if body is None:
        raise _pending_skill_404(skill_name)
    return {"status": "ok", "name": skill_name, "content": body}
@router.put("/skills/pending/{skill_name}")
def update_pending_skill(skill_name: str, request: SkillPendingUpdateRequest):
    """Edit a pending draft in place (edit-before-approve)."""
    result = skill_manager.update_pending(skill_name, request.content)
    if result.get("status") != "ok":
        status_code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=status_code, detail=result.get("message", "update failed"))
    return {"status": "ok", "name": skill_name}
@router.post("/skills/pending/{skill_name}/approve")
def approve_pending_skill(skill_name: str):
    """Approve a draft: activate a new skill or apply a replacement."""
    result = skill_manager.approve_pending(skill_name)
    if result.get("status") != "ok":
        status_code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=status_code, detail=result.get("message", "approval failed"))
    return {"status": "ok", "name": skill_name, "approved": True}
@router.post("/skills/pending/{skill_name}/reject")
def reject_pending_skill(skill_name: str):
    """Reject a draft (delete it from the queue)."""
    result = skill_manager.reject_pending(skill_name)
    if result.get("status") != "ok":
        status_code = 404 if "no pending draft" in (result.get("message") or "") else 400
        raise HTTPException(status_code=status_code, detail=result.get("message", "rejection failed"))
    return {"status": "ok", "name": skill_name, "rejected": True}
@router.get("/skills/{skill_name}")
def get_skill(skill_name: str, command: str | None = None):
    """Return one skill's catalog entry plus its body (progressive disclosure).

    When ``command`` is provided, returns that sub-command's instructions
    (read from the package's ``commands/<name>.md``) instead of the whole
    skill body — this powers the ``/<command>`` chat menu entries.
    """
    skill = skill_manager.get(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    payload = skill.to_dict()
    if command:
        cmd_body = skill_manager.read_command_body(skill_name, command)
        if cmd_body is None:
            raise HTTPException(
                status_code=404, detail=f"Command '{command}' not found in skill '{skill_name}'"
            )
        payload["body"] = cmd_body[0]
        payload["base_dir"] = cmd_body[1]
        payload["command"] = command
    else:
        body = skill_manager.read_body(skill_name)
        if body is not None:
            payload["body"] = body[0]
            payload["base_dir"] = body[1]
    return {"status": "ok", "skill": payload}
@router.patch("/skills/{skill_name}")
def update_skill(skill_name: str, request: SkillUpdatePayload):
    """Toggle a skill's enabled state or permission override."""
    skill = skill_manager.get(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    try:
        if request.enabled is not None:
            skill = skill_manager.set_enabled(skill_name, request.enabled)
        if request.permission is not None:
            skill = skill_manager.set_permission(skill_name, request.permission)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "skill": skill.to_dict() if skill else None}
@router.delete("/skills/{skill_name}")
def delete_skill_route(skill_name: str):
    """Uninstall a skill: remove its directory from disk and refresh the catalog."""
    try:
        removed = skill_manager.delete_skill(skill_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    # Drop the market provenance record so the card becomes installable again.
    skill_market_manager.forget_install(skill_name)
    return {"status": "ok", "name": skill_name, "removed": True}
@router.post("/skills/scan")
def scan_skills():
    """Force a re-scan of all skill roots."""
    result = skill_manager.refresh()
    return {
        "status": "ok",
        "skills": [s.to_dict() for s in result.skills],
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "count": len(result.skills),
    }
@router.post("/skills/validate")
def validate_skill(request: SkillValidatePayload):
    """Validate a single skill directory/file without loading it into the catalog."""
    from coworker.skills.skill_discovery import SKILL_FILE, SkillScanner
    from coworker.skills.skills import load_skill_from_file

    target = request.path.strip()
    if not target:
        raise HTTPException(status_code=400, detail="path is required")
    candidate = Path(target).expanduser().resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {target}")
    # Restrict validation to the skill roots this app actually scans, so the
    # endpoint cannot be used to probe arbitrary files on the machine.
    allowed_roots: list[Path] = []
    try:
        allowed_roots = [Path(root).resolve() for root, _label in skill_manager.scanner.roots()]
    except Exception:  # noqa: BLE001 - never let scanner errors disable the guard
        pass
    if not any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside the skill directories")
    if candidate.is_dir():
        candidate = candidate / SKILL_FILE
        if not candidate.exists():
            raise HTTPException(status_code=404, detail=f"No {SKILL_FILE} in {target}")
    entry, diagnostics = load_skill_from_file(candidate, "validate")
    return {
        "status": "ok",
        "valid": entry is not None,
        "skill": entry.to_dict() if entry else None,
        "diagnostics": [d.to_dict() for d in diagnostics],
    }
