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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import DEFAULT_AGENT_NAME, MEMORY_ROOT_NAME
from .memory_discovery import MemoryScanner
from .memory_store import MemoryStore
from .registry import MemoryRegistry

from coworker.logger import get_logger
logger = get_logger(__name__)

# Alias for the default single-agent id — canonical value lives in layout.py.
DEFAULT_AGENT = DEFAULT_AGENT_NAME


def _transcript_fingerprint(messages: list[dict[str, Any]] | None) -> str:
    """Cheap content fingerprint of a transcript for dream de-duplication.

    Two turns with identical message content must not re-trigger extraction; a
    length + tail hash is enough (content only ever grows between turns).
    """
    if not messages:
        return ""
    total = 0
    tail = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        total += len(content)
        if len(tail) < 2000:
            tail += content
    return f"{len(messages)}:{total}:{tail[-2000:]}"


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = True
    # Internal read-side hard cap for the resident injection block. Not user
    # facing: the settings page only exposes ``enabled`` / ``auto_extract``.
    inject_char_limit: int = 4000
    # Resident memory is a token-budgeted INDEX (codex-aligned; codex caps its
    # resident summary at 2500 tokens). This caps the CONTENT tokens of the
    # injected block; structural markup is excluded. ``inject_char_limit`` stays
    # as the write-side file-size cap (and a legacy alias).
    inject_token_limit: int = 2500
    auto_extract: bool = False
    # Internal consolidation knobs (not exposed in the settings UI).
    nudge_interval: int = 3
    extract_model: str = ""
    max_prior_loss: float = 0.25  # dream rewrite must preserve >= 75% of prior entries
    dream_idle_seconds: int = 30  # session-end idle window before dreaming
    # Minimum gap between dreams for the SAME session. A session that settles
    # many turns in a row (e.g. rapid edits / approvals) would otherwise fire a
    # dream after every turn, hammering the model with extract+summarize calls
    # that compete with the user's actual requests.
    dream_min_interval_seconds: int = 60
    # Global cap on concurrently-running dreams. Dreams run off the main turn
    # path but still consume the SAME provider; bounding concurrency guarantees
    # they never starve an active conversation.
    max_concurrent_dreams: int = 1

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
        # Phase 2: per-session dream state (idle timers).
        self._pending_dreams: dict[str, Any] = {}
        # One session note per (session, date): tracks which day a session's
        # SESSIONS/<date>.md has already been summarized to avoid duplicates.
        self._session_summarized: dict[str, str] = {}
        # Read-side render cache: (project, agent, team_ids) -> (fingerprint,
        # rendered). Recomputed when any scoped file's mtime/size changes, so a
        # long turn's many model calls do not re-scan / re-render the same
        # unchanged memory (M2: no whole-library scan, no per-call re-render).
        self._render_cache: dict[tuple[Any, ...], tuple[str, str]] = {}
        # Injected extractor dependencies (set via configure_extractor).
        self._llm_factory: Any | None = None
        self._transcript_provider: Any | None = None
        # Dream throttling: a global concurrency cap plus per-session cooldown
        # and transcript fingerprint, so background extraction never spams the
        # provider or competes with the user's active turns.
        self._dream_semaphore = threading.Semaphore(max(1, int(getattr(config, "max_concurrent_dreams", 1) or 1)))
        self._last_dream_at: dict[str, float] = {}
        self._last_dream_fingerprint: dict[str, str] = {}

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
        """Render the injected memory block for the default (system-only) scope.

        Uses the scoped scan (system files only) + token budget + render cache.
        """
        if not self.config.enabled:
            return ""
        key: tuple[Any, ...] = ("", "", tuple())
        fp = self._scope_fingerprint(None, None, [])
        cached = self._render_cache.get(key)
        if cached is not None and cached[0] == fp:
            return cached[1]
        nodes = self.scanner.scan_scoped(project_dir=None)
        rendered = self._render_index(nodes)
        self._render_cache[key] = (fp, rendered)
        return rendered

    def render_for(self, project_dir: str | None = None, agent: str | None = None) -> str:
        """Render the injected memory block for one project/agent scope.

        Includes team-level memory (GOALS/CONTEXT/MEMORY of the agent's team and
        its ancestor teams) and a lightweight team roster when the org registry
        is available and the agent belongs to a team. Reads only the scoped
        files (M2) and reuses the cached render while they are unchanged.
        """
        if not self.config.enabled:
            return ""
        team_ids, roster_lines, identity_lines = self._org_context(project_dir, agent)
        key: tuple[Any, ...] = (project_dir, agent or DEFAULT_AGENT, tuple(team_ids))
        fp = self._scope_fingerprint(project_dir, agent, team_ids)
        cached = self._render_cache.get(key)
        if cached is not None and cached[0] == fp:
            return cached[1]
        nodes = self.scanner.scan_scoped(
            project_dir=project_dir,
            agent=agent or DEFAULT_AGENT,
            team_ids=team_ids,
        )
        rendered = self._render_index(nodes)
        if identity_lines:
            block = "\n".join(identity_lines)
            rendered = f"{rendered}\n\n{block}" if rendered else block
        if roster_lines:
            block = "\n".join(roster_lines)
            section = f"## 团队成员\n{block}"
            rendered = f"{rendered}\n\n{section}" if rendered else section
        self._render_cache[key] = (fp, rendered)
        return rendered

    def _org_context(
        self,
        project_dir: str | None,
        agent: str | None,
    ) -> tuple[list[str], list[str], list[str]]:
        """Resolve the org-driven identity / roster / team_ids for a scope."""
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
        return team_ids, roster_lines, identity_lines

    def _scope_fingerprint(
        self,
        project_dir: str | None,
        agent: str | None,
        team_ids: list[str],
    ) -> str:
        """Cheap mtime/size fingerprint of the scoped files (no content reads)."""
        try:
            paths = self.scanner.scoped_paths(
                project_dir=project_dir,
                agent=agent or DEFAULT_AGENT,
                team_ids=team_ids,
            )
        except Exception:  # noqa: BLE001 - a fingerprint failure must never break chat
            return ""
        parts: list[str] = []
        for p in paths:
            try:
                st = p.stat()
                parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append(f"{p.name}:missing")
        return "|".join(parts)

    def _render_index(self, nodes: list[Any]) -> str:
        """Render the scoped nodes as a compact token-budgeted index (codex-aligned)."""
        from .memory_prompt import format_memory_index

        budget = int(getattr(self.config, "inject_token_limit", 0) or 0) or 2500
        return format_memory_index(nodes, token_budget=budget)

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
        view._session_summarized = self._session_summarized
        view._render_cache = self._render_cache
        view._llm_factory = self._llm_factory
        view._transcript_provider = self._transcript_provider
        view._dream_semaphore = self._dream_semaphore
        view._last_dream_at = self._last_dream_at
        view._last_dream_fingerprint = self._last_dream_fingerprint
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
            entry = self._pending_dreams.pop(session_id, None)
        if entry is not None:
            entry.get("cancel", lambda: None)()

    def after_turn(self, session_id: str, workspace_root: Path | None = None) -> None:
        """Called once per settled turn by the agent runtimes.

        Schedules a background dream (extract + consolidate) to run once the
        session has been idle for ``dream_idle_seconds`` (Codex-style). It never
        blocks and never raises; an active turn cancels the pending dream via
        :meth:`note_turn_active`.

        Works when called from both async (e.g. streaming path) and sync/worker
        (e.g. ``asyncio.to_thread``) contexts.  In the async path the dream is
        scheduled as an ``asyncio.Task`` on the current loop so it can be
        cancelled by ``note_turn_active``.  In the sync path (no running loop)
        we use ``threading.Timer`` + ``asyncio.run()`` so the dream still fires
        but the worker thread is not blocked.
        """
        if not self.config.enabled or not self.config.auto_extract:
            return
        import threading

        idle = self.config.dream_idle_seconds or 5
        cancelled = threading.Event()

        def _fire_sync_dream():
            if cancelled.is_set():
                return
            asyncio.run(self._dream_async(session_id))

        def _cancel_flag():
            cancelled.set()

        with self._lock:
            previous = self._pending_dreams.pop(session_id, None)
            try:
                import asyncio

                loop = asyncio.get_running_loop()
            except RuntimeError:
                # Sync path: no event loop — schedule via thread Timer so the
                # dream fires asynchronously without blocking the worker.
                t = threading.Timer(idle, _fire_sync_dream)
                t.daemon = True
                t.start()
                # Store cancel handle so note_turn_active can abort it.
                self._pending_dreams[session_id] = {"cancel": _cancel_flag}
                if previous is not None:
                    try:
                        if hasattr(previous, "cancel"):
                            previous.cancel()
                    except Exception:
                        pass
                logger.debug("dream scheduled (sync) for session %s in %ss", session_id, idle)
                return

            self._pending_dreams[session_id] = {"cancel": _cancel_flag}
        if previous is not None:
            try:
                if hasattr(previous, "cancel"):
                    previous.cancel()
            except Exception:
                pass
        with self._lock:
            task = loop.create_task(self._dream_later(session_id, idle, cancelled))
            self._pending_dreams[session_id] = {"task": task, "cancel": _cancel_flag}
        logger.debug("dream scheduled for session %s in %ss", session_id, idle)

    async def _dream_later(self, session_id: str, idle: int, cancelled: Any) -> None:
        """Wait for the idle window, then run extraction + consolidation.

        ``cancelled`` is a ``threading.Event``; when set the function returns
        immediately (mirroring what a ``CancelledError`` does).
        """
        try:
            import asyncio

            # Periodically check the cancellation flag.  ``asyncio.sleep`` can
            # be interrupted by a ``CancelledError``, but the flag also lets
            # ``note_turn_active`` abort it even if cancellation is missed.
            remaining = idle
            while remaining > 0 and not cancelled.is_set():
                slice = min(remaining, 1.0)
                try:
                    await asyncio.sleep(slice)
                except asyncio.CancelledError:
                    cancelled.set()
                    return
                remaining -= slice
            if not cancelled.is_set():
                await self._dream_async(session_id)
        except asyncio.CancelledError:
            cancelled.set()

    async def _dream_async(self, session_id: str) -> None:
        """Run the background memory pass: ONE merged extract+merge LLM call.

        ``run_extract_and_merge`` extracts new durable facts and merges them
        into the current MEMORY.md in a single prompt (rule-based guardrails, no
        LLM verify). A separate once-per-day session note is appended after.

        Throttled three ways so background extraction never starves the user's
        turns: a global concurrency cap, a per-session cooldown, and skipping
        when the transcript is unchanged since the last dream.
        """
        if self._llm_factory is None or self._transcript_provider is None:
            logger.debug("dream skipped for %s: extractor not configured", session_id)
            return
        if not self._dream_semaphore.acquire(blocking=False):
            logger.debug("dream skipped for %s: at global dream cap", session_id)
            return
        ran = False
        fingerprint = ""
        try:
            messages = list(self._transcript_provider(session_id) or [])
            fingerprint = _transcript_fingerprint(messages)
            with self._lock:
                last_at = self._last_dream_at.get(session_id, 0.0)
                last_fp = self._last_dream_fingerprint.get(session_id)
            if fingerprint and fingerprint == last_fp:
                logger.debug("dream skipped for %s: transcript unchanged", session_id)
                return
            min_interval = int(getattr(self.config, "dream_min_interval_seconds", 60) or 60)
            if time.monotonic() - last_at < min_interval:
                logger.debug("dream skipped for %s: inside per-session cooldown", session_id)
                return

            llm = self._llm_factory()
            if llm is None:
                logger.info("dream skipped for %s: no provider configured", session_id)
                return
            model_label = getattr(llm, "model_name", "") or self.config.extract_model

            # E1/E2: ONE merged LLM call — extract new facts AND merge them into
            # the current MEMORY.md in-band. Guardrails are rule-based (coverage
            # + size budget); the old extract → stage → consolidate → verify
            # chain (up to 4 main-model calls) is gone.
            from .auto_extract import run_extract_and_merge
            from .memory_file import render_blocks, split_blocks

            target = self._memory_target_rel()
            try:
                existing_blocks = split_blocks(self.store.read_file(target).content or "")
            except Exception:  # noqa: BLE001 - a missing/invalid MEMORY.md starts empty
                existing_blocks = []
            result = await run_extract_and_merge(
                llm=llm,
                messages=messages,
                existing_blocks=existing_blocks,
                session_id=session_id,
                provider_name=model_label or "memory-extract",
                model_name=model_label,
                max_total_chars=self.config.inject_char_limit,
                max_prior_loss=self.config.max_prior_loss,
            )
            blocks = result.get("blocks")
            new = list(result.get("new") or [])
            note = str(result.get("note") or "")
            transcript = str(result.get("transcript") or "")
            if blocks:
                self.store.write_file(target, render_blocks(blocks))
                consolidated = True
                added = len(new)
            else:
                # Guardrail rejected the merge: append the new facts (bounded,
                # deduped). Nothing is lost.
                consolidated = False
                added = self.write_auto_facts(new)
            summary_note = await self._write_session_summary(llm, session_id, transcript)
            self._write_dream_diary(
                session_id,
                added=added,
                consolidated=consolidated,
                note=note,
                candidates=new or None,
                summary_note=summary_note,
            )
            logger.info(
                "dream done for %s: added=%d transcript=%d chars %s summary=%s",
                session_id, added, len(transcript), note, summary_note,
            )
            ran = True
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("dream failed for %s: %s", session_id, exc)
        finally:
            with self._lock:
                if ran:
                    self._last_dream_at[session_id] = time.monotonic()
                if fingerprint:
                    self._last_dream_fingerprint[session_id] = fingerprint
            self._dream_semaphore.release()

    def _memory_target_rel(self) -> str:
        """The MEMORY.md rel path for the current scope (or system USER.md)."""
        if self.bound_project:
            return f"{self.bound_project}/{self.bound_agent}/BASE/MEMORY.md"
        return "USER.md"

    async def _write_session_summary(
        self, llm: Any, session_id: str, transcript: str
    ) -> str:
        """Append a session note to ``SESSIONS/<date>.md`` (once per session/day).

        Uses the same transcript as extraction. Returns a short outcome label for
        the dream log. Never raises — a summary hiccup must not break the dream.
        """
        from .auto_extract import run_session_summary

        try:
            import datetime

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            if self._session_summarized.get(session_id) == today:
                return "skip (already summarized today)"
            if self.bound_project:
                target = f"{self.bound_project}/{self.bound_agent}/SESSIONS/{today}.md"
            else:
                target = f"SESSIONS/{today}.md"
            try:
                existing = self.store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
            bullets, note = await run_session_summary(
                llm=llm,
                transcript=transcript,
                session_id=session_id,
                existing=existing,
            )
            if not bullets:
                return note or "no summary"
            existing_blocks = {b.strip().lower() for b in (existing or "").split("\n") if b.strip()}
            new_blocks = [b for b in bullets if b.lower() not in existing_blocks]
            if not new_blocks:
                self._session_summarized[session_id] = today
                return "skip (summary already present)"
            content = (existing.rstrip() + "\n\n" + "\n".join(f"- {b}" for b in new_blocks) + "\n") if existing.strip() else "\n".join(f"- {b}" for b in new_blocks) + "\n"
            self.store.write_file(target, content)
            self._session_summarized[session_id] = today
            return f"wrote {len(new_blocks)} bullets"
        except Exception as exc:  # noqa: BLE001 - summary must never break the dream
            logger.warning("session summary write failed for %s: %s", session_id, exc)
            return "write failed"

    def _write_dream_diary(
        self,
        session_id: str,
        added: int,
        consolidated: bool,
        note: str,
        candidates: list[str] | None = None,
        summary_note: str = "",
    ) -> None:
        """Append a human-readable entry to the agent's DREAMS.md diary.

        Each entry records the dream outcome AND the actual facts that were
        extracted (so the diary doubles as a readable "what did I learn" log).
        Best-effort: a write hiccup must never break the dream.
        """
        try:
            import datetime

            if self.bound_project:
                target = f"{self.bound_project}/{self.bound_agent}/BASE/DREAMS.md"
            else:
                target = "DREAMS.md"
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            outcome = "consolidated" if consolidated else "appended"
            parts = [f"## {stamp} · {outcome} · new {added} · {note}"]
            for cand in candidates or []:
                cand = (cand or "").strip()
                if cand:
                    parts.append(f"- {cand}")
            if summary_note:
                if self.bound_project:
                    day = datetime.datetime.now().strftime("%Y-%m-%d")
                    parts.append(f"- 会话笔记 → SESSIONS/{day}.md（{summary_note}）")
                else:
                    parts.append(f"- 会话笔记（{summary_note}）")
            entry = "\n".join(parts)
            existing = ""
            try:
                existing = self.store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
            content = (existing.rstrip() + "\n\n" + entry + "\n") if existing.strip() else f"# Dream Diary\n\n{entry}\n"
            self.store.write_file(target, content)
            self._rollup_archives()
        except Exception:  # noqa: BLE001 - diary must never break chat
            logger.warning("dream diary write failed for %s", session_id, exc_info=True)

    def _rollup_archives(self) -> None:
        """Move older-month records out of the live files into ``ARCHIVE/``.

        Governs two growth points (project-scoped only):

        - ``BASE/DREAMS.md``: entries whose ``## YYYY-MM-DD`` stamp is older
          than the current month move to ``ARCHIVE/DREAMS-YYYY-MM.md``; the
          live diary keeps only the current month.
        - ``SESSIONS/YYYY-MM-DD.md``: day files older than the current month
          merge into ``ARCHIVE/SESSIONS-YYYY-MM.md`` and are deleted.

        Lazy + best-effort: runs at write time (no scheduler), never raises.
        Archive files live outside ``BASE/`` / ``SESSIONS/`` so the scanner
        neither injects them nor surfaces them in the memory tree; they stay
        readable on demand via ``memory_read``.
        """
        if not self.bound_project:
            return
        import datetime
        import re

        from .memory_file import render_blocks, split_blocks

        date_re = re.compile(r"^##\s+(\d{4}-\d{2})-\d{2}")
        today = datetime.date.today()
        current_month = today.strftime("%Y-%m")
        archive_dir = f"{self.bound_project}/{self.bound_agent}/ARCHIVE"

        def _append_archive(name: str, blocks: list[str]) -> None:
            rel = f"{archive_dir}/{name}"
            try:
                existing = self.store.read_file(rel).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
            known = {b for b in split_blocks(existing)}
            merged = list(known)
            for block in blocks:
                if block not in known:
                    merged.append(block)
                    known.add(block)
            try:
                self.store.write_file(rel, render_blocks(merged))
            except Exception:  # noqa: BLE001 - best-effort
                logger.warning("memory archive write failed for %s", rel, exc_info=True)

        try:
            # -- DREAMS.md rollup --------------------------------------------
            diary_rel = f"{self.bound_project}/{self.bound_agent}/BASE/DREAMS.md"
            try:
                diary_raw = self.store.read_file(diary_rel).content or ""
            except Exception:  # noqa: BLE001
                diary_raw = ""
            current: list[str] = []
            stale: dict[str, list[str]] = {}
            for block in split_blocks(diary_raw):
                # Only single-hash document headings are structural (rewritten
                # fresh below); ## dated entries are content, not headings.
                if re.match(r"^#(?:[^#]|$)", block.strip()):
                    continue
                match = date_re.match(block)
                month = match.group(1) if match else current_month
                if month == current_month:
                    current.append(block)
                else:
                    stale.setdefault(month, []).append(block)
            for month, blocks in stale.items():
                _append_archive(f"DREAMS-{month}.md", blocks)
            if stale:
                body = current if current else ["（当月无 dream 记录）"]
                self.store.write_file(diary_rel, render_blocks(["# Dream Diary", *body]))

            # -- SESSIONS/<date>.md rollup -----------------------------------
            sessions_dir = f"{self.bound_project}/{self.bound_agent}/SESSIONS"
            sessions_path = self.store._resolve(sessions_dir)
            if sessions_path.is_dir():
                day_re = re.compile(r"^(\d{4}-\d{2})-\d{2}\.md$")
                for entry in sorted(sessions_path.iterdir()):
                    if not entry.is_file():
                        continue
                    m = day_re.match(entry.name)
                    if not m:
                        continue
                    month = m.group(1)
                    if month >= current_month:
                        continue
                    try:
                        content = entry.read_text(encoding="utf-8", errors="replace") or ""
                    except OSError:
                        continue
                    _append_archive(f"SESSIONS-{month}.md", split_blocks(content))
                    try:
                        entry.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001 - rollup must never break the dream
            logger.warning("memory archive rollup failed for %s: %s", self.bound_project, exc)

    def write_auto_facts(self, candidates: list[str]) -> int:
        """Persist extracted facts directly into long-term memory, bounded.

        Project-scoped extraction targets the current agent's ``MEMORY.md``;
        global extraction (no bound project) targets the system ``USER.md``.
        Exact-duplicate entries are skipped. The file is FIFO-capped at
        ``inject_char_limit`` characters: if the new facts would push it over
        the budget, the OLDEST entries are dropped first (newest facts always
        win, the newest entry is never evicted). This keeps the append-only
        fallback bounded even when a consolidation rewrite is rejected. Never
        raises — each write is guarded so a single bad candidate cannot abort.
        """
        from .memory_file import render_blocks, split_blocks

        store = self.store
        if self.bound_project:
            target = f"{self.bound_project}/{self.bound_agent}/BASE/MEMORY.md"
        else:
            target = "USER.md"
        limit = self.config.inject_char_limit
        added = 0
        for text in candidates:
            text = (text or "").strip()
            if not text:
                continue
            try:
                existing = store.read_file(target).content or ""
            except Exception:  # noqa: BLE001
                existing = ""
            blocks = split_blocks(existing)
            if any(b == text for b in blocks):
                continue
            blocks.append(text)
            total = sum(len(b) for b in blocks)
            # Drop oldest entries (front) until the file fits the budget; the
            # newest entry is never evicted.
            while len(blocks) > 1 and total > limit:
                total -= len(blocks.pop(0))
            try:
                store.write_file(target, render_blocks(blocks))
                added += 1
            except Exception:  # noqa: BLE001 - skip edge failures
                continue
        return added
