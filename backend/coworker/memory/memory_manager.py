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

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import MEMORY_ROOT_NAME
from .memory_discovery import MemoryScanner
from .memory_prompt import format_memory_prompt
from .memory_store import MemoryStore
from .registry import MemoryRegistry

from coworker.logger import get_logger
logger = get_logger(__name__)

DEFAULT_AGENT = "default_agent"


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = True
    # Internal read-side hard cap for the resident injection block. Not user
    # facing: the settings page only exposes ``enabled`` / ``auto_extract``.
    inject_char_limit: int = 4000
    auto_extract: bool = False
    # Internal consolidation knobs (not exposed in the settings UI).
    nudge_interval: int = 3
    extract_model: str = ""
    max_prior_loss: float = 0.25  # dream rewrite must preserve >= 75% of prior entries
    dream_idle_seconds: int = 300  # session-end idle window before dreaming

    # Backwards-compatible alias for ``inject_char_limit`` (older call sites /
    # selftests may still read ``config.char_limit``).
    @property
    def char_limit(self) -> int:
        return self.inject_char_limit


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
        # Phase 2: per-session dream state (idle timers + staged candidates).
        self._pending_dreams: dict[str, Any] = {}
        self._pending_candidates: dict[str, list[str]] = {}
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
        identity_lines: list[str] = []
        if project_dir and agent and self.org_store is not None:
            try:
                org = self.org_store.load(project_dir)
                multi_mode = getattr(org, "mode", "single") == "multi"
                members = {a.id: a for a in org.agents}
                target = members.get(agent)
                if target and target.team_id:
                    team_ids = self.org_store.team_ancestors(org, target.team_id)
                if target is not None:
                    name = target.name or agent
                    role = f"（{target.role}）" if target.role else ""
                    identity_lines.append(f"你是 {name} ({agent}){role}。")
                    if target.parent:
                        parent = members.get(target.parent)
                        parent_label = f"{parent.name} ({parent.id})" if parent else target.parent
                        identity_lines.append(f"你的上级是 {parent_label}。")
                    else:
                        identity_lines.append("你是本项目的负责人，直接向用户汇报。")
                if multi_mode:
                    for member in self.org_store.roster(org):
                        if member["id"] == agent:
                            continue
                        role = f" · {member['role']}" if member["role"] else ""
                        team = f" · {member['team']}" if member["team"] else ""
                        hierarchy = ""
                        m = members.get(member["id"])
                        if m is not None and m.parent:
                            parent = members.get(m.parent)
                            parent_label = f"{parent.name} ({parent.id})" if parent else m.parent
                            hierarchy = f" · 上级:{parent_label}"
                        roster_lines.append(f"- {member['name']} ({member['id']}){role}{team}{hierarchy}")
            except Exception:  # noqa: BLE001 - roster/team injection must never break chat
                team_ids = []
                roster_lines = []
                identity_lines = []
        nodes = library.injected(project_dir=project_dir, agent=agent or DEFAULT_AGENT, team_ids=team_ids)
        rendered = format_memory_prompt(nodes, self.config.char_limit)
        if identity_lines:
            block = "\n".join(identity_lines)
            rendered = f"{rendered}\n\n{block}" if rendered else block
        if roster_lines:
            block = "\n".join(roster_lines)
            section = f"## 团队成员\n{block}"
            rendered = f"{rendered}\n\n{section}" if rendered else section
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
        view._pending_dreams = self._pending_dreams
        view._pending_candidates = self._pending_candidates
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

    # -- Phase 2 dream (extract + consolidate) ------------------------------

    def note_turn_active(self, session_id: str) -> None:
        """Signal that a turn is running for ``session_id``.

        Cancels any pending dream (consolidation) so it does not fire mid-turn.
        The runtime calls this at the start of a stream.
        """
        if not self.config.enabled or not self.config.auto_extract:
            return
        with self._lock:
            task = self._pending_dreams.pop(session_id, None)
        if task is not None:
            task.cancel()

    def after_turn(self, session_id: str, workspace_root: Path | None = None) -> None:
        """Called once per settled turn by the agent runtimes.

        Schedules a background dream (extract + consolidate) to run once the
        session has been idle for ``dream_idle_seconds`` (Codex-style). It never
        blocks and never raises; an active turn cancels the pending dream via
        :meth:`note_turn_active`.
        """
        if not self.config.enabled or not self.config.auto_extract:
            return
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("dream skipped for %s: no running loop", session_id)
            return
        idle = max(1, self.config.dream_idle_seconds)
        with self._lock:
            previous = self._pending_dreams.pop(session_id, None)
            task = loop.create_task(self._dream_later(session_id, idle))
            self._pending_dreams[session_id] = task
        if previous is not None:
            previous.cancel()
        logger.debug("dream scheduled for session %s in %ss", session_id, idle)

    async def _dream_later(self, session_id: str, idle: int) -> None:
        """Wait for the idle window, then run extraction + consolidation."""
        try:
            await asyncio.sleep(idle)
        except asyncio.CancelledError:
            return
        await self._dream_async(session_id)

    async def _dream_async(self, session_id: str) -> None:
        """Run the full background memory pass: extract, then consolidate."""
        if self._llm_factory is None or self._transcript_provider is None:
            logger.debug("dream skipped for %s: extractor not configured", session_id)
            return
        try:
            from .auto_extract import run_auto_extract, run_consolidation

            llm = self._llm_factory()
            if llm is None:
                logger.info("dream skipped for %s: no provider configured", session_id)
                return
            model_label = getattr(llm, "model_name", "") or self.config.extract_model
            messages = self._transcript_provider(session_id)
            result = await run_auto_extract(
                llm=llm,
                messages=messages,
                session_id=session_id,
                provider_name=model_label or "memory-extract",
                model_name=model_label,
                write_facts=lambda candidates: self._stage_candidates(session_id, candidates),
                project_dir=self.bound_project or "",
                agent=self.bound_agent,
            )
            added = int(result.get("added") or 0)
            consolidated, note = await self._consolidate_now(llm, session_id)
            self._write_dream_diary(session_id, added=added, consolidated=consolidated, note=note)
            logger.info("dream done for %s: added=%d %s", session_id, added, note)
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("dream failed for %s: %s", session_id, exc)

    def _stage_candidates(self, session_id: str, candidates: list[str]) -> int:
        """Extract step: stage candidates for the consolidation pass (and fall
        back to direct writes when no consolidation will run)."""
        stored = self._pending_candidates.setdefault(session_id, [])
        for text in candidates:
            text = (text or "").strip()
            if text and text not in stored:
                stored.append(text)
        # Keep a bounded in-memory staging list per session.
        self._pending_candidates[session_id] = stored[-50:]
        return len(stored)

    async def _consolidate_now(self, llm: Any, session_id: str) -> tuple[bool, str]:
        """Consolidate staged candidates into the agent's MEMORY.md.

        Returns ``(applied, note)``. Guarded: on any rejection the candidates
        fall back to direct appends so nothing is lost.
        """
        from .auto_extract import run_consolidation

        staged = self._pending_candidates.pop(session_id, [])
        if not staged:
            return False, "no staged candidates to consolidate"
        if self.bound_project:
            target = f"{self.bound_project}/{self.bound_agent}/BASE/MEMORY.md"
            try:
                existing = self.store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
        else:
            target = "USER.md"
            try:
                existing = self.store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
        new_blocks, note = await run_consolidation(
            llm=llm,
            existing=existing,
            candidates=staged,
            session_id=session_id,
            max_prior_loss=self.config.max_prior_loss,
            max_total_chars=self.config.inject_char_limit,
        )
        if new_blocks is None:
            # Guardrail rejected the rewrite: append candidates directly (no loss).
            added = self.write_auto_facts(staged)
            return False, f"append-only fallback ({added} added); {note}"
        try:
            from .memory_file import render_blocks

            self.store.write_file(target, render_blocks(new_blocks))
            return True, note
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream consolidate write failed for %s: %s", session_id, exc)
            added = self.write_auto_facts(staged)
            return False, f"write failed; append-only fallback ({added} added)"

    def _write_dream_diary(self, session_id: str, added: int, consolidated: bool, note: str) -> None:
        """Append a line to the agent's DREAMS.md review diary (best-effort)."""
        try:
            import datetime

            if self.bound_project:
                target = f"{self.bound_project}/{self.bound_agent}/BASE/DREAMS.md"
            else:
                target = "DREAMS.md"
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            outcome = "consolidated" if consolidated else "appended"
            line = f"- {stamp} · {outcome} · new {added} · {note}"
            existing = ""
            try:
                existing = self.store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
            content = (existing.rstrip() + "\n" + line + "\n") if existing.strip() else f"# Dream Diary\n\n{line}\n"
            self.store.write_file(target, content)
        except Exception:  # noqa: BLE001 - diary must never break chat
            logger.warning("dream diary write failed for %s", session_id, exc_info=True)

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
