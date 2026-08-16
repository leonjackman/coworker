"""MemoryManager: global entry point for the memory subsystem.

Owns the configuration, the memory library root, and the per-project/agent
middleware factory. Mirrors ``SkillManager``'s role in ``main.py``: it is
constructed once at startup and handed to the agent registry, which passes it
into each runtime; the runtime then builds a project-scoped middleware via
:meth:`for_project`.

Phase 2 (auto-extract) is orchestrated here as well: a per-session turn counter
feeds the nudge trigger, and extraction itself lives in ``auto_extract``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import MEMORY_ROOT_NAME
from .memory_discovery import MemoryScanner
from .memory_prompt import format_memory_prompt
from .memory_store import MemoryStore
from .registry import MemoryRegistry

logger = logging.getLogger(__name__)

DEFAULT_AGENT = "default_agent"


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = True
    char_limit: int = 2000
    auto_extract: bool = False
    nudge_interval: int = 10
    extract_model: str = ""


class MemoryManager:
    """Global memory configuration + store + middleware factory."""

    def __init__(
        self,
        data_dir: Path | None = None,
        memory_dir: Path | None = None,
        *,
        config: MemoryConfig | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else Path.cwd()
        self.config = config or MemoryConfig()
        self._root = Path(memory_dir).resolve() if memory_dir else (self.data_dir / MEMORY_ROOT_NAME).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.registry = MemoryRegistry(self.data_dir)
        self.scanner = MemoryScanner(self._root)
        self.store = MemoryStore(self._root)
        # Org registry (injected by main.py) powers roster + team memory injection.
        self.org_store: Any | None = None
        # Phase 2: per-session turn counters (in-memory; reset on restart).
        self._turn_counters: dict[str, int] = {}
        # Injected extractor dependencies (set via configure_extractor).
        self._llm_factory: Any | None = None
        self._transcript_provider: Any | None = None

    # -- config helpers -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def char_limit(self) -> int:
        return self.config.char_limit

    @property
    def auto_extract(self) -> bool:
        return self.config.auto_extract

    @property
    def root(self) -> Path:
        return self._root

    # -- read path ----------------------------------------------------------

    def render_prompt(self) -> str:
        """Render the injected memory block for the default (system-only) scope."""
        if not self.config.enabled:
            return ""
        library = self.scanner.scan()
        return format_memory_prompt(library.injected(), self.config.char_limit)

    def render_for(self, project_dir: str | None = None, agent: str | None = None) -> str:
        """Render the injected memory block for one project/agent scope.

        Includes team-level memory (GOALS/CONTEXT/MEMORY of the agent's team and
        its ancestor teams) and a lightweight team roster when the org registry
        is available and the agent belongs to a team.
        """
        if not self.config.enabled:
            return ""
        library = self.scanner.scan()
        team_ids: list[str] = []
        roster_lines: list[str] = []
        if project_dir and agent and self.org_store is not None:
            try:
                org = self.org_store.load(project_dir)
                target = next((a for a in org.agents if a.id == agent), None)
                if target and target.team_id:
                    team_ids = self.org_store.team_ancestors(org, target.team_id)
                for member in self.org_store.roster(org):
                    role = f" · {member['role']}" if member["role"] else ""
                    team = f" · {member['team']}" if member["team"] else ""
                    roster_lines.append(f"- {member['name']} ({member['id']}){role}{team}")
            except Exception:  # noqa: BLE001 - roster/team injection must never break chat
                team_ids = []
                roster_lines = []
        nodes = library.injected(project_dir=project_dir, agent=agent or DEFAULT_AGENT, team_ids=team_ids)
        rendered = format_memory_prompt(nodes, self.config.char_limit)
        if roster_lines:
            block = "\n".join(roster_lines)
            if rendered:
                rendered = f"{rendered}\n\n## 团队成员\n{block}"
            else:
                rendered = f"## 团队成员\n{block}"
        return rendered

    # -- middleware factory -------------------------------------------------

    def for_project(self, project_dir: str | None = None, agent: str | None = None) -> "MemoryManager":
        """Return a lightweight view of this manager bound to one project/agent.

        Shares config + root + store + extractor deps, but renders/injects only
        the given project/agent's memory.
        """
        view = MemoryManager.__new__(MemoryManager)
        view.data_dir = self.data_dir
        view.config = self.config
        view._root = self._root
        view._lock = self._lock
        view.registry = self.registry
        view.scanner = self.scanner
        view.store = self.store
        view.org_store = self.org_store
        view._turn_counters = self._turn_counters
        view._llm_factory = self._llm_factory
        view._transcript_provider = self._transcript_provider
        view._project_dir = project_dir
        view._agent = agent or DEFAULT_AGENT
        return view

    @property
    def bound_project(self) -> str | None:
        return getattr(self, "_project_dir", None)

    @property
    def bound_agent(self) -> str:
        return getattr(self, "_agent", DEFAULT_AGENT)

    def configure_extractor(
        self,
        *,
        llm_factory: Any,
        transcript_provider: Any,
    ) -> None:
        """Inject Phase 2 dependencies (called once from ``main.py``)."""
        self._llm_factory = llm_factory
        self._transcript_provider = transcript_provider

    # -- Phase 2 nudge ------------------------------------------------------

    # Hard cap on tracked sessions so abandoned counters cannot grow unbounded.
    _MAX_TRACKED_SESSIONS = 500

    def after_turn(self, session_id: str, workspace_root: Path | None = None) -> None:
        """Called once per settled turn by the agent runtimes.

        Records the turn and, when the nudge threshold is crossed, dispatches an
        async extraction task. Never blocks and never raises.
        """
        if not self.config.enabled or not self.config.auto_extract:
            return
        threshold = max(1, self.config.nudge_interval)
        with self._lock:
            count = self._turn_counters.get(session_id, 0) + 1
            if len(self._turn_counters) >= self._MAX_TRACKED_SESSIONS and session_id not in self._turn_counters:
                self._turn_counters.pop(next(iter(self._turn_counters)), None)
            self._turn_counters[session_id] = count
            if count < threshold:
                return
            self._turn_counters.pop(session_id, None)
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("auto-extract skipped for %s: no running loop", session_id)
            return
        loop.create_task(self._extract_async(session_id))
        logger.info("auto-extract scheduled for session %s", session_id)

    async def _extract_async(self, session_id: str) -> None:
        """Extract candidate memories and write them directly (best-effort)."""
        if self._llm_factory is None or self._transcript_provider is None:
            logger.debug("auto-extract skipped for %s: extractor not configured", session_id)
            return
        try:
            from .auto_extract import run_auto_extract

            llm = self._llm_factory()
            if llm is None:
                logger.info("auto-extract skipped for %s: no provider configured", session_id)
                return
            model_label = getattr(llm, "model_name", "") or self.config.extract_model
            messages = self._transcript_provider(session_id)
            result = await run_auto_extract(
                llm=llm,
                messages=messages,
                session_id=session_id,
                provider_name=model_label or "memory-extract",
                model_name=model_label,
                write_facts=self.write_auto_facts,
                project_dir=self.bound_project or "",
                agent=self.bound_agent,
            )
            logger.info("auto-extract done for %s: %s", session_id, result)
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("auto-extract failed for %s: %s", session_id, exc)

    def write_auto_facts(self, candidates: list[str]) -> int:
        """Persist extracted facts directly into long-term memory.

        Project-scoped extraction targets the current agent's ``MEMORY.md``;
        global extraction (no bound project) targets the system ``USER.md``.
        Exact-duplicate entries are skipped. Never raises — each write is
        guarded so a single bad candidate cannot abort the batch.
        """
        store = self.store
        if self.bound_project:
            target = f"{self.bound_project}/{self.bound_agent}/BASE/MEMORY.md"
        else:
            target = "USER.md"
        added = 0
        for text in candidates:
            text = (text or "").strip()
            if not text:
                continue
            try:
                store.add_block(target, text)
                added += 1
            except Exception:  # noqa: BLE001 - skip duplicates/edge failures
                continue
        return added
