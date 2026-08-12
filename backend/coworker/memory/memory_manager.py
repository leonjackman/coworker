"""MemoryManager: global entry point for the memory subsystem.

Owns the configuration, the default scanner/store, and the per-workspace
middleware factory. Mirrors ``SkillManager``'s role in ``main.py``: it is
constructed once at startup and handed to the agent registry, which passes it
into each runtime; the runtime then builds a workspace-scoped middleware via
:meth:`build_middleware`.

Phase 2 (auto-extract) is orchestrated here as well: a per-session turn counter
feeds the nudge trigger, and extraction itself lives in ``auto_extract``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_discovery import MemoryScanner
from .memory_prompt import format_memory_prompt
from .memory_store import MemoryStore

logger = logging.getLogger(__name__)


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
        workspace_root: Path | None = None,
        *,
        config: MemoryConfig | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else Path.cwd()
        self.config = config or MemoryConfig()
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._lock = threading.Lock()
        # Default scanner/store (used by the API surface and render_prompt).
        self.scanner = MemoryScanner(workspace_root=self._workspace_root)
        self.store = MemoryStore(self.scanner)
        # Phase 2: per-session turn counters (in-memory; reset on restart).
        self._turn_counters: dict[str, int] = {}
        # Injected extractor dependencies (set via configure_extractor).
        self._proposal_store: Any | None = None
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

    # -- read path ----------------------------------------------------------

    def render_prompt(self) -> str:
        """Render the injected memory block for the default workspace."""
        if not self.config.enabled:
            return ""
        return self._render(self._workspace_root)

    def _render(self, workspace_root: Path | None) -> str:
        scanner = MemoryScanner(workspace_root=workspace_root)
        scan = scanner.scan()
        sections: list[tuple[str, str, list[str]]] = []
        for memory in scan.files():
            label = memory.scope
            source = f"{memory.path} (updated {_format_mtime(memory.mtime)})"
            sections.append((label, source, memory.entries))
        return format_memory_prompt(sections, self.config.char_limit)

    # -- middleware factory -------------------------------------------------

    def build_middleware(self, workspace_root: Path | None) -> Any:
        """Build a workspace-scoped ``MemoryMiddleware``.

        The injected read is bound to the *session's* workspace, so project
        memory follows the session even though the manager itself is global.
        """
        from .memory_middleware import MemoryMiddleware

        return MemoryMiddleware(self.for_workspace(workspace_root))

    def for_workspace(self, workspace_root: Path | None) -> "MemoryManager":
        """Return a lightweight view of this manager for one workspace.

        Shares config + data_dir + extractor deps, but scans the given root.
        """
        if workspace_root is not None and Path(workspace_root).resolve() == self._workspace_root:
            return self
        view = MemoryManager(self.data_dir, workspace_root, config=self.config)
        view._proposal_store = self._proposal_store
        view._llm_factory = self._llm_factory
        view._transcript_provider = self._transcript_provider
        return view

    def configure_extractor(
        self,
        *,
        proposal_store: Any,
        llm_factory: Any,
        transcript_provider: Any,
    ) -> None:
        """Inject Phase 2 dependencies (called once from ``main.py``)."""
        self._proposal_store = proposal_store
        self._llm_factory = llm_factory
        self._transcript_provider = transcript_provider

    # -- Phase 2 nudge ------------------------------------------------------

    def record_turn(self, session_id: str) -> None:
        """Count a settled turn for a session (Phase 2 trigger bookkeeping)."""
        if not self.config.auto_extract:
            return
        with self._lock:
            self._turn_counters[session_id] = self._turn_counters.get(session_id, 0) + 1

    def should_extract(self, session_id: str) -> bool:
        """Whether the nudge threshold has been crossed for this session."""
        with self._lock:
            count = self._turn_counters.get(session_id, 0)
            return self.config.auto_extract and count >= max(1, self.config.nudge_interval)

    def reset_turns(self, session_id: str) -> None:
        with self._lock:
            self._turn_counters.pop(session_id, None)

    def after_turn(self, session_id: str, workspace_root: Path | None = None) -> None:
        """Called once per settled turn by the agent runtimes.

        Records the turn; when the nudge threshold is crossed, dispatches an
        async extraction task. Never blocks and never raises.
        """
        if not self.config.enabled or not self.config.auto_extract:
            return
        self.record_turn(session_id)
        if not self.should_extract(session_id):
            return
        self.reset_turns(session_id)
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("auto-extract skipped for %s: no running loop", session_id)
            return
        root = Path(workspace_root).resolve() if workspace_root else self._workspace_root
        loop.create_task(self._extract_async(session_id, root))
        logger.info("auto-extract scheduled for session %s", session_id)

    async def _extract_async(self, session_id: str, workspace_root: Path | None) -> None:
        """Extract candidate memories and propose them (best-effort)."""
        if (
            self._proposal_store is None
            or self._llm_factory is None
            or self._transcript_provider is None
        ):
            logger.debug("auto-extract skipped for %s: extractor not configured", session_id)
            return
        try:
            from .auto_extract import run_auto_extract

            llm = self._llm_factory()
            if llm is None:
                logger.info("auto-extract skipped for %s: no provider configured", session_id)
                return
            messages = self._transcript_provider(session_id)
            result = await run_auto_extract(
                llm=llm,
                messages=messages,
                proposal_store=self._proposal_store,
                session_id=session_id,
                provider_name=self.config.extract_model or "memory-extract",
                model_name=self.config.extract_model,
                workspace_path=str(workspace_root) if workspace_root else "",
            )
            logger.info("auto-extract done for %s: %s", session_id, result)
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("auto-extract failed for %s: %s", session_id, exc)


def _format_mtime(mtime: float) -> str:
    """Format an mtime as a readable local timestamp (or 'never')."""
    if not mtime:
        return "never"
    import datetime

    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")