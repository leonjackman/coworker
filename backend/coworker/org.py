"""Org registry: project = organization container of agents and teams.

The multi-agent team model treats each project's memory directory as an
*organization*: a tree of ``agents`` (members, each with their own memory
skeleton) optionally grouped into ``teams`` (departments) that carry team-level
memory. The org manifest lives at ``{memory_dir}/.org.json`` — a dotfile so the
memory scanner, injection and export/import all ignore it.

The manifest is the single source of truth for team structure; the ``Project``
dataclass intentionally gains no fields here.

Validation invariants enforced on every write (``_validate``):

- agent ids are unique within the org;
- an agent's ``team_id`` and ``parent`` must reference existing entities (or be empty);
- a team's ``lead`` must be an agent of that team, and ``parent_team_id`` must
  reference an existing team (or be empty);
- agent ``parent`` chains and team ``parent_team_id`` chains must be acyclic;
- every agent's depth along its parent chain must not exceed ``max_depth``;
- ``status`` ∈ {active, disabled} and ``mode`` ∈ {single, multi}.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from coworker.logger import get_logger
logger = get_logger(__name__)

ORG_FILENAME = ".org.json"
ORG_VERSION = 1

AGENT_STATUS_ACTIVE = "active"
AGENT_STATUS_DISABLED = "disabled"
AGENT_STATUSES = (AGENT_STATUS_ACTIVE, AGENT_STATUS_DISABLED)

ORG_MODE_SINGLE = "single"
ORG_MODE_MULTI = "multi"
ORG_MODES = (ORG_MODE_SINGLE, ORG_MODE_MULTI)

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_ALLOW_AGENT_CREATION = True


class OrgError(ValueError):
    """Raised for invalid org structure / operations."""


@dataclass
class OrgAgent:
    id: str
    name: str
    role: str = ""
    description: str = ""
    parent: str = ""          # superior agent id ("" = reports to the user)
    team_id: str = ""         # "" = not grouped
    status: str = AGENT_STATUS_ACTIVE
    created_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "OrgAgent":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            role=str(payload.get("role", "")),
            description=str(payload.get("description", "")),
            parent=str(payload.get("parent", "")),
            team_id=str(payload.get("team_id", "")),
            status=str(payload.get("status", AGENT_STATUS_ACTIVE)),
            created_at=str(payload.get("created_at", "")),
        )


@dataclass
class OrgTeam:
    id: str
    name: str
    lead: str = ""            # agent id; must belong to this team
    parent_team_id: str = ""
    status: str = AGENT_STATUS_ACTIVE

    @classmethod
    def from_dict(cls, payload: dict) -> "OrgTeam":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            lead=str(payload.get("lead", "")),
            parent_team_id=str(payload.get("parent_team_id", "")),
            status=str(payload.get("status", AGENT_STATUS_ACTIVE)),
        )


@dataclass
class Org:
    version: int = ORG_VERSION
    mode: str = ORG_MODE_SINGLE
    max_depth: int = DEFAULT_MAX_DEPTH
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    allow_agent_creation: bool = DEFAULT_ALLOW_AGENT_CREATION
    agents: list[OrgAgent] = field(default_factory=list)
    teams: list[OrgTeam] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> "Org":
        agents = [OrgAgent.from_dict(a) for a in payload.get("agents", []) if isinstance(a, dict)]
        teams = [OrgTeam.from_dict(t) for t in payload.get("teams", []) if isinstance(t, dict)]
        return cls(
            version=int(payload.get("version", ORG_VERSION)),
            mode=str(payload.get("mode", ORG_MODE_SINGLE)),
            max_depth=int(payload.get("max_depth", DEFAULT_MAX_DEPTH)),
            max_concurrent=int(payload.get("max_concurrent", DEFAULT_MAX_CONCURRENT)),
            allow_agent_creation=bool(payload.get("allow_agent_creation", DEFAULT_ALLOW_AGENT_CREATION)),
            agents=agents,
            teams=teams,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def default_org() -> Org:
    return Org()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OrgStore:
    """Read/write the ``.org.json`` manifest under one memory root."""

    def __init__(self, memory_root: Path):
        self.memory_root = Path(memory_root).resolve()
        self._lock = threading.RLock()

    # -- paths -------------------------------------------------------------

    def org_path(self, memory_dir: str) -> Path:
        return self.memory_root / memory_dir / ORG_FILENAME

    # -- load / save -------------------------------------------------------

    def load(self, memory_dir: str) -> Org:
        """Load the org manifest, falling back to a default org on absence/corruption."""
        path = self.org_path(memory_dir)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            org = Org.from_dict(payload)
            org.version = ORG_VERSION
            if org.mode not in ORG_MODES:
                raise OrgError(f"mode must be one of {ORG_MODES}, got {org.mode!r}")
            return org
        except (OSError, ValueError, TypeError):
            logger.warning("org manifest %s unreadable/corrupt; falling back to default", path, exc_info=True)
            return default_org()

    def save(self, memory_dir: str, org: Org) -> None:
        """Validate and atomically persist the manifest."""
        self._validate(org)
        path = self.org_path(memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd, tmp = tempfile.mkstemp(prefix=".org-", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(org.to_dict(), fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def exists(self, memory_dir: str) -> bool:
        return self.org_path(memory_dir).is_file()

    # -- mutations (all validate on save) -----------------------------------

    def upsert_agent(self, memory_dir: str, agent: OrgAgent) -> Org:
        org = self.load(memory_dir)
        replaced = False
        for i, existing in enumerate(org.agents):
            if existing.id == agent.id:
                org.agents[i] = agent
                replaced = True
                break
        if not replaced:
            if not agent.created_at:
                agent.created_at = _now()
            org.agents.append(agent)
        self.save(memory_dir, org)
        return org

    def remove_agent(self, memory_dir: str, agent_id: str) -> Org:
        org = self.load(memory_dir)
        if not any(a.id == agent_id for a in org.agents):
            raise OrgError(f"agent {agent_id!r} does not exist")
        if any(a.parent == agent_id for a in org.agents):
            raise OrgError(f"agent {agent_id!r} is a superior of another agent; reassign them first")
        for team in org.teams:
            if team.lead == agent_id:
                raise OrgError(f"agent {agent_id!r} leads team {team.id!r}; reassign the lead first")
        org.agents = [a for a in org.agents if a.id != agent_id]
        self.save(memory_dir, org)
        return org

    def upsert_team(self, memory_dir: str, team: OrgTeam) -> Org:
        org = self.load(memory_dir)
        replaced = False
        for i, existing in enumerate(org.teams):
            if existing.id == team.id:
                org.teams[i] = team
                replaced = True
                break
        if not replaced:
            org.teams.append(team)
        # The team lead is auto-assigned to the team (resolves the
        # create-team-with-lead chicken-and-egg).
        if team.lead:
            lead = next((a for a in org.agents if a.id == team.lead), None)
            if lead is not None:
                lead.team_id = team.id
        self.save(memory_dir, org)
        return org

    def remove_team(self, memory_dir: str, team_id: str) -> Org:
        org = self.load(memory_dir)
        if not any(t.id == team_id for t in org.teams):
            raise OrgError(f"team {team_id!r} does not exist")
        if any(a.team_id == team_id for a in org.agents):
            raise OrgError(f"team {team_id!r} still has members; reassign them first")
        if any(t.parent_team_id == team_id for t in org.teams):
            raise OrgError(f"team {team_id!r} is a parent of another team; move children first")
        org.teams = [t for t in org.teams if t.id != team_id]
        self.save(memory_dir, org)
        return org

    def update_config(
        self,
        memory_dir: str,
        *,
        mode: str | None = None,
        max_depth: int | None = None,
        max_concurrent: int | None = None,
        allow_agent_creation: bool | None = None,
    ) -> Org:
        org = self.load(memory_dir)
        if mode is not None:
            org.mode = mode
        if max_depth is not None:
            org.max_depth = max(1, int(max_depth))
        if max_concurrent is not None:
            org.max_concurrent = max(1, int(max_concurrent))
        if allow_agent_creation is not None:
            org.allow_agent_creation = bool(allow_agent_creation)
        self.save(memory_dir, org)
        return org

    # -- helpers ------------------------------------------------------------

    def roster(self, org: Org) -> list[dict]:
        """Lightweight roster for context injection: every active member's name/role/team."""
        return [
            entry
            for entry in self.members_for(org)
            if entry["status"] == AGENT_STATUS_ACTIVE
        ]

    def members_for(self, org: Org) -> list[dict]:
        """Every member's identity card (id/name/role/team/status), disabled included.

        The sidebar / full session page rely on this to render agent group
        headings (name · role · sessions · team) and to grey out disabled
        members, so unlike :meth:`roster` it intentionally keeps disabled
        members. ``roster()`` reuses it and filters to active members.
        """
        team_names = {t.id: t.name for t in org.teams}
        return [
            {
                "id": a.id,
                "name": a.name,
                "role": a.role,
                "team": team_names.get(a.team_id, ""),
                "status": a.status,
            }
            for a in org.agents
        ]

    def team_ancestors(self, org: Org, team_id: str) -> list[str]:
        """Return the team ids from ``team_id`` up the parent chain (including self)."""
        by_id = {t.id: t for t in org.teams}
        chain: list[str] = []
        seen: set[str] = set()
        current = team_id
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            team = by_id.get(current)
            current = team.parent_team_id if team else ""
        return chain

    def agents_depth(self, org: Org, agent_id: str) -> int:
        """Depth of an agent along its parent chain (root agents = 1)."""
        by_id = {a.id: a for a in org.agents}
        depth = 0
        seen: set[str] = set()
        current = agent_id
        while current and current not in seen:
            seen.add(current)
            depth += 1
            current = by_id[current].parent if current in by_id else ""
        return depth

    def is_active(self, org: Org, agent_id: str) -> bool:
        agent = next((a for a in org.agents if a.id == agent_id), None)
        return agent is not None and agent.status == AGENT_STATUS_ACTIVE

    def get_agent(self, org: Org, agent_id: str) -> OrgAgent | None:
        return next((a for a in org.agents if a.id == agent_id), None)

    def get_team(self, org: Org, team_id: str) -> OrgTeam | None:
        return next((t for t in org.teams if t.id == team_id), None)

    # -- validation ---------------------------------------------------------

    def _validate(self, org: Org) -> None:
        if org.mode not in ORG_MODES:
            raise OrgError(f"mode must be one of {ORG_MODES}, got {org.mode!r}")
        if org.max_depth < 1:
            raise OrgError("max_depth must be >= 1")
        if org.max_concurrent < 1:
            raise OrgError("max_concurrent must be >= 1")

        agent_ids = {a.id for a in org.agents}
        if len(agent_ids) != len(org.agents):
            raise OrgError("duplicate agent ids")
        for a in org.agents:
            if not a.id or not a.name.strip():
                raise OrgError("agent id and name are required")
            if a.status not in AGENT_STATUSES:
                raise OrgError(f"agent {a.id!r} has invalid status {a.status!r}")
            if a.parent and a.parent not in agent_ids:
                raise OrgError(f"agent {a.id!r} references unknown superior {a.parent!r}")
            if a.team_id and a.team_id not in {t.id for t in org.teams}:
                raise OrgError(f"agent {a.id!r} references unknown team {a.team_id!r}")

        team_ids = {t.id for t in org.teams}
        if len(team_ids) != len(org.teams):
            raise OrgError("duplicate team ids")
        for t in org.teams:
            if not t.id or not t.name:
                raise OrgError("team id and name are required")
            if t.status not in AGENT_STATUSES:
                raise OrgError(f"team {t.id!r} has invalid status {t.status!r}")
            if t.lead and t.lead not in agent_ids:
                raise OrgError(f"team {t.id!r} lead {t.lead!r} must be an existing agent")
            if t.parent_team_id and t.parent_team_id not in team_ids:
                raise OrgError(f"team {t.id!r} references unknown parent team {t.parent_team_id!r}")

        for a in org.agents:
            if self.agents_depth(org, a.id) > org.max_depth:
                raise OrgError(f"agent {a.id!r} exceeds max_depth {org.max_depth}")
            # Explicit parent-chain cycle detection (a depth walker that stops at
            # seen nodes would otherwise hide a cycle behind a finite depth).
            seen: set[str] = set()
            current = a.id
            while current and current in agent_ids:
                if current in seen:
                    raise OrgError(f"agent parent chain contains a cycle at {current!r}")
                seen.add(current)
                current = next((x.parent for x in org.agents if x.id == current), "")
        for t in org.teams:
            seen_t: set[str] = set()
            current_t = t.id
            while current_t and current_t in team_ids:
                if current_t in seen_t:
                    raise OrgError(f"team parent chain contains a cycle at {current_t!r}")
                seen_t.add(current_t)
                current_t = next((x.parent_team_id for x in org.teams if x.id == current_t), "")
