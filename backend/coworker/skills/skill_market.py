"""Skill market — browse and install from external sources.

Supports two external market sources:
- Tencent SkillHub (https://skillhub.tencent.com) — Chinese-focused, domestic CDN
- ClawHub (https://clawhub.ai) — Global largest skill marketplace

Skills are installed into the user-level directory so they apply to all projects.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import aiohttp

from coworker.skills.skills import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    load_skill_from_file,
    parse_frontmatter,
    validate_description,
    validate_name,
)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

SKILLHUB_API = "https://api.skillhub.cn/api/skills"
CLAWHUB_SEARCH = "https://clawhub.ai/api/v1/search"
CLAWHUB_SKILLS = "https://clawhub.ai/api/v1/skills"

# Timeout for HTTP requests (seconds)
_REQUEST_TIMEOUT = 15

# Number of results to fetch from the external market (may be larger than what we return)
_FETCH_LIMIT = 50


class SkillMarketManager:
    """Manage external skill market sources."""

    def __init__(self, user_home: Path):
        """
        Args:
            user_home: User home directory (~). Skills are installed into
                       ``~/.agents/skills``.
        """
        self.user_home = user_home
        self.install_dir = user_home / ".agents" / "skills"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        source: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search a market source for matching skills.

        Returns a list of skill dict entries suitable for display.
        """
        if source == "skillhub":
            return await self._search_skillhub(query, limit)
        elif source == "clawhub":
            return await self._search_clawhub(query, limit)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def list_hot(
        self,
        source: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List popular/hot skills from a market source."""
        if source == "skillhub":
            return await self._list_skillhub_hot(limit, offset)
        elif source == "clawhub":
            return await self._list_clawhub_hot(limit, offset)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def install(
        self,
        source: str,
        slug: str,
    ) -> dict[str, Any]:
        """Install a skill from a market source.

        Flow:
            1. Fetch SKILL.md content from the market source.
            2. Validate the SKILL.md content.
            3. Write to ``~/.agents/skills/{slug}/SKILL.md``.
            4. Return success or error dict.
        """
        try:
            # Step 1: fetch SKILL.md content
            content = await self._fetch_skill_content(source, slug)
            if content is None:
                return {"status": "error", "message": "Failed to fetch skill file (downloaded content is empty)"}

            # Step 2: validate
            install_dir = self.install_dir / slug
            install_dir.mkdir(parents=True, exist_ok=True)
            skill_file = install_dir / "SKILL.md"

            # Before writing, ensure the name matches the slug
            frontmatter, _body = parse_frontmatter(content)
            fallback_name = slug.strip()
            name: str = (frontmatter.get("name") or fallback_name)
            if isinstance(name, str):
                name = name.strip() or fallback_name
            else:
                name = fallback_name

            if name != slug:
                # Create directory with the actual skill name
                self.install_dir.rmdir() if not any(self.install_dir.iterdir()) else None
                install_dir = self.install_dir / name
                install_dir.mkdir(parents=True, exist_ok=True)
                skill_file = install_dir / "SKILL.md"

            problems = validate_name(name) + validate_description(
                _str_or_none(frontmatter.get("description", "")) or ""
            )
            if problems:
                return {"status": "error", "message": "; ".join(problems)}

            # Step 3: write SKILL.md
            skill_file.write_text(content, encoding="utf-8")

            # Step 4: validate the installed file end-to-end
            entry, diagnostics = load_skill_from_file(skill_file, "coworker-user")
            if diagnostics:
                # Remove the just-created file
                try:
                    skill_file.unlink()
                except OSError:
                    pass
                return {
                    "status": "error",
                    "message": "Validation failed after install: " + "; ".join(d.message for d in diagnostics),
                }

            return {
                "status": "ok",
                "message": f"Skill '{name}' installed to {skill_file}",
                "skill": entry.to_dict() if entry else None,
            }

        except ValueError:
            raise
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    # ------------------------------------------------------------------
    # SkillHub (Tencent)
    # ------------------------------------------------------------------

    async def _search_skillhub(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        url = f"{SKILLHUB_API}?keyword={_urlencode(query)}&sortBy=score&pageSize={min(limit * 2, 50)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, Exception):
            return []

        return self._extract_skillhub_items(data, limit)

    async def _list_skillhub_hot(
        self, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        url = f"{SKILLHUB_API}?sortBy=score&pageSize={min(limit * 2, 50)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, Exception):
            return []

        skills = self._extract_skillhub_items(data, limit + offset)
        return skills[offset:]

    @staticmethod
    def _extract_skillhub_items(data: dict, limit: int) -> list[dict[str, Any]]:
        """Extract skill items from SkillHub API response.

        SkillHub returns: { "code": 0, "data": { "skills": [...], ... } }
        """
        items: list[dict[str, Any]] = []
        inner = data.get("data", {})
        if isinstance(inner, dict):
            results = inner.get("skills", []) or []
        elif isinstance(inner, list):
            results = inner
        else:
            results = []

        if not isinstance(results, list):
            return items

        for item in results[:limit]:
            name = _str_or_none(item.get("name")) or _str_or_none(item.get("slug")) or ""
            desc = _str_or_none(item.get("description")) or _str_or_none(item.get("description_zh")) or ""
            slug = _str_or_none(item.get("slug")) or name
            if slug and desc:
                items.append({
                    "slug": slug,
                    "name": name,
                    "description": desc[:MAX_DESCRIPTION_LENGTH],
                    "score": item.get("installs", 0),
                    "source": "skillhub",
                })
        return items

    # ------------------------------------------------------------------
    # ClawHub
    # ------------------------------------------------------------------

    async def _search_clawhub(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        url = f"{CLAWHUB_SEARCH}?q={_urlencode(query)}&limit={min(limit * 2, 50)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, Exception):
            return []

        return SkillMarketManager._extract_clawhub_search(data, limit)

    async def _list_clawhub_hot(
        self, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        url = f"{CLAWHUB_SKILLS}?limit={min(limit * 2, 50)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, Exception):
            return []

        skills = SkillMarketManager._extract_clawhub_list(data, limit + offset)
        return skills[offset:]

    @staticmethod
    def _extract_clawhub_search(data: dict, limit: int) -> list[dict[str, Any]]:
        """Extract skill items from ClawHub search response.

        ClawHub returns: { "results": [ {...}, {...} ] }
        Each item: { "displayName": "...", "slug": "...", "summary": "...", "canonicalUrl": "...", "install": {...}, ... }
        """
        items: list[dict[str, Any]] = []
        results = data.get("results", [])
        if not isinstance(results, list):
            return items

        for item in results[:limit]:
            name = _str_or_none(item.get("displayName")) or _str_or_none(item.get("name")) or ""
            desc = _str_or_none(item.get("summary")) or _str_or_none(item.get("description")) or ""
            slug = _str_or_none(item.get("slug")) or ""
            if not slug:
                canonical = _str_or_none(item.get("canonicalUrl", ""))
                if canonical:
                    slug = canonical.strip("/")

            # Score from metrics
            score = 0
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                score = metrics.get("rolling60DayInstalls", 0)

            if name and desc:
                items.append({
                    "slug": slug or name,
                    "name": name,
                    "description": desc[:MAX_DESCRIPTION_LENGTH],
                    "score": score,
                    "source": "clawhub",
                })
        return items

    @staticmethod
    def _extract_clawhub_list(data: dict, limit: int) -> list[dict[str, Any]]:
        """Extract skill items from ClawHub /skills list response.

        ClawHub returns: { "items": [ {...} ], "nextCursor": "..." }
        Each item: { "slug": "...", "displayName": "...", "summary": "..." }
        """
        items: list[dict[str, Any]] = []
        results = data.get("items", [])
        if not isinstance(results, list):
            return items

        for item in results[:limit]:
            name = _str_or_none(item.get("displayName")) or _str_or_none(item.get("name")) or ""
            desc = _str_or_none(item.get("summary")) or _str_or_none(item.get("description")) or ""
            slug = _str_or_none(item.get("slug")) or ""
            score = item.get("installCount", 0)

            if name and desc:
                items.append({
                    "slug": slug,
                    "name": name,
                    "description": desc[:MAX_DESCRIPTION_LENGTH],
                    "score": score,
                    "source": "clawhub",
                })
        return items

    # ------------------------------------------------------------------
    # File fetching — ClawHub
    # ------------------------------------------------------------------

    async def _fetch_skill_content(self, source: str, slug: str) -> str | None:
        """Fetch the SKILL.md content from a market source."""
        if source == "skillhub":
            return await self._fetch_skillhub_file(slug)
        elif source == "clawhub":
            return await self._fetch_clawhub_file(slug)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def _fetch_skillhub_file(self, slug: str) -> str | None:
        """Fetch SKILL.md from SkillHub.

        SkillHub doesn't have a public file download API yet.
        We attempt to fetch via their v1 API's skill detail endpoint,
        which may contain content in a future update. For now, this
        returns None — users should use ClawHub or install via CLI.

        TODO: Implement when SkillHub provides a public file API.
        """
        # Try the v1 skill detail endpoint as a fallback
        url = f"https://api.skillhub.cn/api/v1/skills/{_urlencode(slug)}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Check if content is available in the response
                        skill = data.get("skill", {})
                        content = skill.get("content") or skill.get("rawContent")
                        if content:
                            return content
        except (aiohttp.ClientError, Exception):
            pass
        return None

    async def _fetch_clawhub_file(self, slug: str) -> str | None:
        """Fetch SKILL.md from ClawHub via their file API.

        ClawHub file API: GET /api/v1/skills/{slug}/file?path=SKILL.md
        """
        file_url = f"https://clawhub.ai/api/v1/skills/{_urlencode(slug)}/file?path=SKILL.md"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as resp:
                    if resp.status == 200:
                        return await resp.text(encoding="utf-8")
        except (aiohttp.ClientError, Exception):
            pass
        return None


def _str_or_none(value: Any) -> str | None:
    """Convert a value to string or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _urlencode(s: str) -> str:
    """URL-encode a string (no external dependency needed)."""
    from urllib.parse import quote
    return quote(s, safe="")
