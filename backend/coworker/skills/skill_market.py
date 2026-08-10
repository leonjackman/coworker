"""Skill market — browse and install from external sources.

Supports two external market sources:
- Tencent SkillHub (https://skillhub.tencent.com) — Chinese-focused, domestic CDN
- ClawHub (https://clawhub.ai) — Global largest skill marketplace

Skills are installed into the user-level directory so they apply to all projects.

Pagination
----------
The two upstreams use *different* pagination protocols, so this module
normalises them behind a single :class:`MarketPage` envelope:

- SkillHub  → real offset paging via ``page`` / ``pageSize`` and reports ``total``.
- ClawHub   → opaque cursor paging (``nextCursor``); ``offset`` is emulated by
  walking cursors when the caller cannot supply one.

Never slice a single upstream page to fake pagination — that silently caps the
result set and makes "load more" return duplicates or nothing.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from coworker.skills.skills import (
    MAX_DESCRIPTION_LENGTH,
    load_skill_from_file,
    parse_frontmatter,
    validate_description,
    validate_name,
)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

SKILLHUB_API = "https://api.skillhub.cn/api/skills"
SKILLHUB_V1 = "https://api.skillhub.cn/api/v1"
CLAWHUB_SEARCH = "https://clawhub.ai/api/v1/search"
CLAWHUB_SKILLS = "https://clawhub.ai/api/v1/skills"

# Timeout for HTTP requests (seconds)
_REQUEST_TIMEOUT = 15

# Upstream hard cap on page size
_MAX_PAGE_SIZE = 50

# Safety guard when emulating offset paging on a cursor-only upstream
_MAX_CURSOR_HOPS = 12

# ---------------------------------------------------------------------------
# Facets — what the left-hand filter bar offers for each source
#
# A "facet" is whatever dimension a source can actually slice on:
#   * SkillHub publishes a curated ``category`` vocabulary (13 entries).
#   * ClawHub has no categories at all (``/api/v1/topics`` is 404, ``?topic=``
#     is silently ignored, and per-skill ``topics`` is a sparse free-form tag
#     cloud). It does expose a real, server-side ``sort`` dimension, so that is
#     surfaced instead of leaving the bar empty.
# ---------------------------------------------------------------------------

FACET_CATEGORY = "category"
FACET_SORT = "sort"

# ClawHub advertises ten ``sort`` values but only five produce distinct
# orderings; the rest are aliases. Exposing all ten would show the user tabs
# that silently return the same list, so only the distinct behaviours are kept.
#   recommended == default == stars == rating
#   downloads   == installs
#   newest      == createdAt
#   updated     == (no sort param)  ← the pre-facet default
#   trending    — distinct, and the only one without cursor support
CLAWHUB_SORTS: list[dict[str, Any]] = [
    {"key": "recommended", "name": "推荐", "name_en": "Recommended", "sort_order": 0},
    {"key": "downloads", "name": "下载最多", "name_en": "Most downloaded", "sort_order": 10},
    {"key": "trending", "name": "上升最快", "name_en": "Trending", "sort_order": 20},
    {"key": "newest", "name": "最新发布", "name_en": "Newest", "sort_order": 30},
    {"key": "updated", "name": "最近更新", "name_en": "Recently updated", "sort_order": 40},
]
CLAWHUB_SORT_KEYS = frozenset(entry["key"] for entry in CLAWHUB_SORTS)
CLAWHUB_DEFAULT_SORT = "recommended"

# Each ClawHub cursor names the index it walks. Pairing a cursor with a
# different ``sort`` makes the upstream seek into the wrong index and quietly
# return rows the caller already has — the same silent-duplication failure mode
# as the original pagination bug, so the pairing is validated rather than
# trusted.
CLAWHUB_CURSOR_INDEX = {
    "recommended": "by_active_recommended_rank",
    "downloads": "by_active_stats_downloads",
    "newest": "by_active_created",
    "updated": "by_active_updated",
}
CLAWHUB_INDEX_SORT = {v: k for k, v in CLAWHUB_CURSOR_INDEX.items()}

# ``sort=trending`` is a bounded leaderboard (~164 rows) served without a
# cursor. Because the set is small and finite, windowing it locally is correct
# here — unlike the 111k-row main catalogue, where slicing was the original
# fake-pagination bug.
CLAWHUB_TRENDING_FETCH = 200


@dataclass
class MarketPage:
    """A normalised page of market results."""

    skills: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": self.skills,
            "count": len(self.skills),
            "total": self.total,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
        }


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
        offset: int = 0,
        category: str | None = None,
    ) -> MarketPage:
        """Search a market source for matching skills."""
        limit = _clamp_limit(limit)
        offset = max(0, offset)
        if source == "skillhub":
            return await self._skillhub_window(
                limit=limit, offset=offset, keyword=query, category=category
            )
        elif source == "clawhub":
            return await self._search_clawhub(query, limit, offset)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def list_hot(
        self,
        source: str,
        limit: int = 20,
        offset: int = 0,
        cursor: str | None = None,
        category: str | None = None,
        sort: str | None = None,
    ) -> MarketPage:
        """List popular/hot skills from a market source."""
        limit = _clamp_limit(limit)
        offset = max(0, offset)
        if source == "skillhub":
            return await self._skillhub_window(
                limit=limit, offset=offset, keyword=None, category=category
            )
        elif source == "clawhub":
            return await self._list_clawhub_hot(limit, offset, cursor, sort)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def list_facets(self, source: str) -> dict[str, Any]:
        """Describe the filter dimension a source can actually slice on.

        Returns ``{"kind": ..., "items": [...], "default": ...}``. ``kind`` tells
        the UI which query parameter the tabs drive: ``category`` (SkillHub) or
        ``sort`` (ClawHub). An empty ``items`` list means "render no bar".
        """
        if source == "skillhub":
            return {
                "kind": FACET_CATEGORY,
                "items": await self._list_skillhub_categories(),
                "default": "all",
            }
        elif source == "clawhub":
            return {
                "kind": FACET_SORT,
                "items": [dict(entry) for entry in CLAWHUB_SORTS],
                "default": CLAWHUB_DEFAULT_SORT,
            }
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def list_categories(self, source: str) -> list[dict[str, Any]]:
        """Backwards-compatible accessor returning only the facet items."""
        facet = await self.list_facets(source)
        items = facet.get("items")
        return items if isinstance(items, list) else []

    async def install(
        self,
        source: str,
        slug: str,
        owner: str | None = None,
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
            content, error = await self._fetch_skill_content(source, slug, owner)
            if content is None:
                return {
                    "status": "error",
                    "message": error or "Failed to fetch skill file (downloaded content is empty)",
                }

            # Step 2: validate
            install_dir = self.install_dir / slug
            install_dir.mkdir(parents=True, exist_ok=True)
            skill_file = install_dir / "SKILL.md"

            # Before writing, ensure the name matches the slug
            frontmatter, _body = parse_frontmatter(content)
            fallback_name = slug.strip()
            name: str = frontmatter.get("name") or fallback_name
            if isinstance(name, str):
                name = name.strip() or fallback_name
            else:
                name = fallback_name

            if name != slug:
                # The frontmatter name wins; drop the placeholder directory we
                # just created (only when it is still empty).
                try:
                    if not any(install_dir.iterdir()):
                        install_dir.rmdir()
                except OSError:
                    pass
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
    # SkillHub (Tencent) — real ``page``/``pageSize`` pagination
    # ------------------------------------------------------------------

    async def _skillhub_window(
        self,
        *,
        limit: int,
        offset: int,
        keyword: str | None,
        category: str | None,
    ) -> MarketPage:
        """Return exactly ``limit`` items starting at ``offset``.

        Upstream pages are aligned to ``pageSize``; when ``offset`` is not a
        multiple of ``limit`` we stitch two adjacent pages together instead of
        returning a short window.
        """
        page = offset // limit + 1
        skip = offset % limit

        items, total = await self._skillhub_fetch_page(
            page=page, page_size=limit, keyword=keyword, category=category
        )
        if skip and len(items) >= limit:
            extra, _ = await self._skillhub_fetch_page(
                page=page + 1, page_size=limit, keyword=keyword, category=category
            )
            items = items + extra

        window = items[skip : skip + limit]

        if isinstance(total, int) and total >= 0:
            has_more = (offset + len(window)) < total
        else:
            has_more = len(window) >= limit

        return MarketPage(skills=window, total=total, has_more=has_more)

    async def _skillhub_fetch_page(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str | None,
        category: str | None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {
            "sortBy": "score",
            "page": max(1, page),
            "pageSize": _clamp_limit(page_size),
        }
        if keyword:
            params["keyword"] = keyword
        if category:
            params["category"] = category

        data = await _get_json(SKILLHUB_API, params)
        if not isinstance(data, dict):
            return [], None

        inner = data.get("data")
        if isinstance(inner, dict):
            raw = inner.get("skills") or []
            total = inner.get("total")
        elif isinstance(inner, list):
            raw, total = inner, None
        else:
            raw, total = [], None

        if not isinstance(raw, list):
            return [], None
        if not isinstance(total, int):
            total = None

        items = [
            normalised
            for entry in raw
            if (normalised := self._normalise_skillhub(entry)) is not None
        ]
        return items, total

    async def _list_skillhub_categories(self) -> list[dict[str, Any]]:
        data = await _get_json(f"{SKILLHUB_V1}/categories")
        if not isinstance(data, dict):
            return []
        raw = data.get("items")
        if not isinstance(raw, list):
            return []

        cats: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("active") is False:
                continue
            key = _str_or_none(entry.get("key"))
            if not key:
                continue
            cats.append({
                "key": key,
                "name": _str_or_none(entry.get("name")) or key,
                "name_en": _str_or_none(entry.get("nameEn")) or key,
                "sort_order": entry.get("sortOrder") if isinstance(entry.get("sortOrder"), int) else 0,
            })
        cats.sort(key=lambda c: c["sort_order"])
        return cats

    @staticmethod
    def _normalise_skillhub(item: Any) -> dict[str, Any] | None:
        """Normalise one SkillHub entry.

        Only ``slug`` is mandatory — dropping entries with an empty description
        would desynchronise page sizes from the upstream offsets.
        """
        if not isinstance(item, dict):
            return None

        slug = (_str_or_none(item.get("slug")) or _str_or_none(item.get("name")) or "").strip()
        if not slug:
            return None

        name = (_str_or_none(item.get("name")) or slug).strip() or slug
        desc = (
            _str_or_none(item.get("description"))
            or _str_or_none(item.get("description_zh"))
            or ""
        ).strip()

        namespace = item.get("namespace")
        owner = None
        if isinstance(namespace, dict):
            owner = _str_or_none(namespace.get("handle"))
        owner = owner or _str_or_none(item.get("ownerName"))

        publisher = item.get("publisher")
        verified = bool(publisher.get("verified")) if isinstance(publisher, dict) else False

        score = item.get("installs")
        if not isinstance(score, int):
            score = item.get("downloads") if isinstance(item.get("downloads"), int) else 0

        return {
            "uid": f"skillhub:{owner}/{slug}" if owner else f"skillhub:{slug}",
            "slug": slug,
            "name": name,
            "description": desc[:MAX_DESCRIPTION_LENGTH],
            "score": score,
            "source": "skillhub",
            "category": _str_or_none(item.get("category")),
            "owner": owner,
            "icon_url": _str_or_none(item.get("iconUrl")),
            "version": _str_or_none(item.get("version")),
            "verified": verified,
        }

    # ------------------------------------------------------------------
    # ClawHub — cursor pagination
    # ------------------------------------------------------------------

    async def _search_clawhub(self, query: str, limit: int, offset: int) -> MarketPage:
        """ClawHub search has no upstream paging, so we window a single fetch."""
        fetch_n = _clamp_limit(offset + limit)
        data = await _get_json(
            CLAWHUB_SEARCH, {"q": query, "limit": fetch_n}, retries=1
        )
        if not isinstance(data, dict):
            return MarketPage()

        raw = data.get("results")
        if not isinstance(raw, list):
            return MarketPage()

        items = [
            normalised
            for entry in raw
            if (normalised := self._normalise_clawhub(entry)) is not None
        ]
        window = items[offset : offset + limit]
        exhausted = len(raw) < fetch_n
        return MarketPage(
            skills=window,
            total=len(items) if exhausted else None,
            has_more=len(items) > offset + limit,
        )

    async def _list_clawhub_hot(
        self, limit: int, offset: int, cursor: str | None, sort: str | None = None
    ) -> MarketPage:
        sort = sort if sort in CLAWHUB_SORT_KEYS else CLAWHUB_DEFAULT_SORT

        # A cursor is only ever minted by a previous page of one specific sort,
        # and "load more" always means "continue this list", so when the two
        # disagree the cursor is authoritative. Realigning here is what stops a
        # stale/mismatched token from silently re-emitting rows.
        if cursor:
            cursor_sort = CLAWHUB_INDEX_SORT.get(_cursor_index(cursor) or "")
            if cursor_sort and cursor_sort != sort:
                sort = cursor_sort

        # ``trending`` is the one sort ClawHub serves without a cursor.
        if sort == "trending":
            return await self._list_clawhub_trending(limit, offset)

        if not cursor and offset > 0:
            cursor = await self._clawhub_seek(offset, limit, sort)
            if cursor is None:
                return MarketPage()

        data = await _get_json(
            CLAWHUB_SKILLS, {"limit": limit, "cursor": cursor, "sort": sort}, retries=1
        )
        if not isinstance(data, dict):
            return MarketPage()

        raw = data.get("items")
        if not isinstance(raw, list):
            return MarketPage()

        items = [
            normalised
            for entry in raw
            if (normalised := self._normalise_clawhub(entry)) is not None
        ]
        next_cursor = _normalise_cursor(data.get("nextCursor"))
        return MarketPage(
            skills=items,
            has_more=bool(next_cursor),
            next_cursor=next_cursor,
        )

    async def _list_clawhub_trending(self, limit: int, offset: int) -> MarketPage:
        """Window the cursor-less trending leaderboard.

        The board is finite (~164 rows) and returned whole in one call, so the
        exact size is known and ``total`` / ``has_more`` are both exact.
        """
        data = await _get_json(
            CLAWHUB_SKILLS,
            {"limit": CLAWHUB_TRENDING_FETCH, "sort": "trending"},
            retries=1,
        )
        if not isinstance(data, dict):
            return MarketPage()

        raw = data.get("items")
        if not isinstance(raw, list):
            return MarketPage()

        items = [
            normalised
            for entry in raw
            if (normalised := self._normalise_clawhub(entry)) is not None
        ]
        return MarketPage(
            skills=items[offset : offset + limit],
            total=len(items),
            has_more=len(items) > offset + limit,
        )

    async def _clawhub_seek(self, offset: int, step: int, sort: str | None = None) -> str | None:
        """Walk cursors until ``offset`` items have been skipped.

        The walk **must** advance in the caller's page size: ClawHub's listing
        is not stable across different ``limit`` values (the first 20 items of
        a ``limit=40`` request differ from a ``limit=20`` request), so seeking
        with one big page would land on a different boundary than the
        cursor-driven path and re-emit rows the client already has.

        Returns the cursor pointing at item #``offset``, or ``None`` when the
        listing is exhausted before reaching it.
        """
        cursor: str | None = None
        skipped = 0
        hops = 0
        step = max(1, min(step, _MAX_PAGE_SIZE))
        while skipped < offset and hops < _MAX_CURSOR_HOPS:
            take = min(step, offset - skipped)
            data = await _get_json(
                CLAWHUB_SKILLS, {"limit": take, "cursor": cursor, "sort": sort}
            )
            if not isinstance(data, dict):
                return None
            batch = data.get("items")
            batch_len = len(batch) if isinstance(batch, list) else 0
            cursor = _normalise_cursor(data.get("nextCursor"))
            skipped += batch_len
            hops += 1
            if not cursor or batch_len == 0:
                # Ran out of upstream results before reaching the offset.
                return None if skipped < offset else cursor
        return cursor

    @staticmethod
    def _normalise_clawhub(item: Any) -> dict[str, Any] | None:
        """Normalise one ClawHub entry (search or listing shape).

        ClawHub slugs are **not unique** — the same ``pdf`` slug exists under
        several owners — so the owner handle is captured for disambiguation and
        folded into the ``uid``.
        """
        if not isinstance(item, dict):
            return None

        canonical = _str_or_none(item.get("canonicalUrl")) or ""
        slug = (_str_or_none(item.get("slug")) or "").strip()
        owner = None

        owner_obj = item.get("owner")
        if isinstance(owner_obj, dict):
            owner = _str_or_none(owner_obj.get("handle"))
        elif isinstance(owner_obj, str):
            owner = owner_obj

        if canonical:
            parts = [p for p in canonical.strip("/").split("/") if p]
            if not owner and parts:
                owner = parts[0]
            if not slug and parts:
                slug = parts[-1]

        name = (
            _str_or_none(item.get("displayName")) or _str_or_none(item.get("name")) or slug or ""
        ).strip()
        if not slug:
            slug = name
        if not slug:
            return None

        desc = (
            _str_or_none(item.get("summary")) or _str_or_none(item.get("description")) or ""
        ).strip()

        score = 0
        metrics = item.get("metrics")
        if isinstance(metrics, dict) and isinstance(metrics.get("rolling60DayInstalls"), int):
            score = metrics["rolling60DayInstalls"]
        elif isinstance(item.get("installCount"), int):
            score = item["installCount"]

        uid = _str_or_none(item.get("id"))
        if not uid:
            uid = f"clawhub:{owner}/{slug}" if owner else f"clawhub:{slug}"

        return {
            "uid": uid,
            "slug": slug,
            "name": name or slug,
            "description": desc[:MAX_DESCRIPTION_LENGTH],
            "score": score,
            "source": "clawhub",
            "category": None,
            "owner": owner,
            "icon_url": _str_or_none(item.get("iconUrl")) or _str_or_none(item.get("imageUrl")),
            "version": _str_or_none(item.get("version")),
            "verified": bool(item.get("verified")),
        }

    # ------------------------------------------------------------------
    # File fetching
    # ------------------------------------------------------------------

    async def _fetch_skill_content(
        self, source: str, slug: str, owner: str | None = None
    ) -> tuple[str | None, str | None]:
        """Fetch the SKILL.md content. Returns ``(content, error_message)``."""
        if source == "skillhub":
            return await self._fetch_skillhub_file(slug)
        elif source == "clawhub":
            return await self._fetch_clawhub_file(slug, owner)
        else:
            raise ValueError(f"Unknown market source: {source}")

    async def _fetch_skillhub_file(self, slug: str) -> tuple[str | None, str | None]:
        """Fetch SKILL.md from SkillHub.

        ``/api/v1/skills/{slug}/file?path=SKILL.md`` answers with a 302 to the
        COS object; aiohttp follows it transparently.
        """
        file_url = f"{SKILLHUB_V1}/skills/{_urlencode(slug)}/file"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    file_url,
                    params={"path": "SKILL.md"},
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 200:
                        text = await resp.text(encoding="utf-8")
                        if text.strip():
                            return text, None
                    elif resp.status == 404:
                        return None, f"SkillHub has no SKILL.md for '{slug}'"
        except Exception:
            pass

        # Fallback: some entries embed the body in the detail endpoint.
        detail = await _get_json(f"{SKILLHUB_V1}/skills/{_urlencode(slug)}")
        if isinstance(detail, dict):
            skill = detail.get("skill")
            if isinstance(skill, dict):
                content = skill.get("content") or skill.get("rawContent")
                if isinstance(content, str) and content.strip():
                    return content, None
        return None, f"Failed to download SKILL.md for '{slug}' from SkillHub"

    async def _fetch_clawhub_file(
        self, slug: str, owner: str | None = None
    ) -> tuple[str | None, str | None]:
        """Fetch SKILL.md from ClawHub.

        Slugs collide across owners; ``?owner=`` disambiguates. Without it the
        API answers 409 ``AMBIGUOUS_SKILL_SLUG``.
        """
        file_url = f"https://clawhub.ai/api/v1/skills/{_urlencode(slug)}/file"
        params: dict[str, Any] = {"path": "SKILL.md"}
        if owner:
            params["owner"] = owner
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    file_url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        text = await resp.text(encoding="utf-8")
                        if text.strip():
                            return text, None
                    if resp.status == 409:
                        return None, await _describe_clawhub_conflict(resp, slug)
                    return None, f"ClawHub returned HTTP {resp.status} for '{slug}'"
        except Exception as exc:
            return None, f"Failed to reach ClawHub: {exc}"
        return None, f"Downloaded SKILL.md for '{slug}' is empty"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _describe_clawhub_conflict(resp: aiohttp.ClientResponse, slug: str) -> str:
    """Turn ClawHub's AMBIGUOUS_SKILL_SLUG payload into actionable guidance."""
    try:
        payload = await resp.json(content_type=None)
    except Exception:
        return f"Skill slug '{slug}' is ambiguous on ClawHub"

    matches = payload.get("matches") if isinstance(payload, dict) else None
    refs: list[str] = []
    if isinstance(matches, list):
        for m in matches:
            if isinstance(m, dict):
                ref = _str_or_none(m.get("ref")) or _str_or_none(m.get("ownerHandle"))
                if ref:
                    refs.append(ref)
    if refs:
        return f"Skill slug '{slug}' is ambiguous on ClawHub; candidates: {', '.join(refs)}"
    return f"Skill slug '{slug}' is ambiguous on ClawHub"


async def _get_json(
    url: str, params: dict[str, Any] | None = None, retries: int = 0
) -> Any | None:
    """GET a JSON document, returning ``None`` on any failure.

    ``retries`` shelters the client from ClawHub's aggressive rate limiting
    (it intermittently answers a burst of requests with ``503``/empty). A
    single short-delayed retry recovers the default view without faking any
    data — this is resilience, not pagination.
    """
    attempt = 0
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=_clean_params(params),
                    timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
                ) as resp:
                    data = await resp.json(content_type=None) if resp.status == 200 else None
        except Exception:
            data = None
        if data is not None:
            return data
        if attempt >= retries:
            return None
        attempt += 1
        await asyncio.sleep(0.5)


def _clean_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    """aiohttp rejects ``None`` values — drop them and stringify the rest."""
    if not params:
        return None
    return {k: str(v) for k, v in params.items() if v is not None and v != ""}


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, _MAX_PAGE_SIZE))


def _cursor_index(cursor: str | None) -> str | None:
    """Read the index name a ClawHub cursor walks, or ``None`` if unreadable."""
    if not cursor:
        return None
    try:
        payload = json.loads(cursor)
    except (TypeError, ValueError):
        return None
    return payload.get("index") if isinstance(payload, dict) else None


def _normalise_cursor(value: Any) -> str | None:
    """ClawHub cursors are opaque; accept both string and object encodings."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    try:
        return json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
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
