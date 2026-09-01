"""Message normalization and user interjection (steer) injection middleware."""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import AIMessage, HumanMessage

from ...logger import get_logger
from ...steer import steer_inbox
from ..core import CoworkerAgentState, format_user_message

logger = get_logger(__name__)


class NormalizeMessagesMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Ensures no ``system`` message ends up in a non‑first position of the
    message list passed to the model.

    Some providers (e.g. Qwen3.6 / vLLM) reject any request where a system
    message is not the very first message. Historical checkpoints created
    before the plan marker fix can contain a residual ``SystemMessage``
    (``[CW-PLAN]``) in the middle of the conversation, which would trigger a
    400 on resume. This middleware downgrades such misplaced system messages
    to ``human`` (content preserved) right before each model call.
    """

    def _normalize(self, state: CoworkerAgentState) -> list[Any] | None:
        from langchain_core.messages import HumanMessage as HM

        messages = state.get("messages", [])
        if not messages:
            return None

        changed = False
        normalized: list[Any] = []
        for index, msg in enumerate(messages):
            msg_type = getattr(msg, "type", None)
            if msg_type == "system" and index > 0:
                normalized.append(HM(content=msg.content, id=getattr(msg, "id", None), additional_kwargs=msg.additional_kwargs or {}))
                changed = True
            else:
                normalized.append(msg)

        return normalized if changed else None

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        normalized = self._normalize(state)
        if normalized is None:
            return None
        return {"messages": normalized}


class EnsureUserMessageMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Guard the model boundary against a DEGENERATE empty conversation.

    A sequential HITL resume can restore an EMPTY ``messages`` channel (the
    resumed graph's checkpoint lacks the conversation). Sending ``messages=[]``
    to a strict provider (vLLM/Qwen) yields ``400 No user query found in
    messages``. When the message list is empty, this middleware re-seeds the
    recent conversation from the session store so the model always has context.

    Healthy calls (messages present) are a no-op; the guard only acts on the
    genuinely-empty degenerate case.
    """

    def __init__(self, session_store: Any | None = None, max_messages: int = 40) -> None:
        super().__init__()
        self.session_store = session_store
        self.max_messages = max_messages

    def _ensure(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        from langchain_core.messages import HumanMessage as HM

        messages = state.get("messages", [])
        if messages:
            return None
        session_id = str(state.get("session_id") or "")
        if not session_id or self.session_store is None:
            return None
        try:
            session = self.session_store.load(session_id)
        except Exception:  # noqa: BLE001 - a session read must never break a model call
            return None
        if session is None:
            return None
        seeded: list[Any] = []
        for message in getattr(session, "messages", [])[-self.max_messages :]:
            content = getattr(message, "content", "")
            if not content:
                continue
            role = str(getattr(message, "role", "") or "human")
            if role == "assistant":
                seeded.append(AIMessage(content=content))
            elif role == "user":
                seeded.append(HM(content=content))
        if not seeded:
            return None
        logger.warning(
            "ensure-user: degenerate empty conversation re-seeded %d messages for %s",
            len(seeded), session_id,
        )
        return {"messages": seeded}

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._ensure(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._ensure(state)


class SteerInjectionMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Inject user interjections (插話) into the running turn at model boundaries.

    The interjection feature lets the user pick a queued message and steer the
    agent WHILE it is still streaming — without pausing or aborting the current
    stream (opencode/codex "steer" semantics). The frontend pushes the message
    via ``/chat/interject`` into :data:`coworker.steer.steer_inbox`; this
    middleware drains that inbox at every model-call boundary and folds the
    pending messages into the next model request as ``HumanMessage`` inputs, so
    the agent's next reasoning step incorporates the guidance.

    The current in-flight ``llm.stream`` is never interrupted: steers only take
    effect on the NEXT model call (i.e. after the current response settles and
    any tool round completes). Steers that arrive after the graph already
    finished stay pending for the frontend's auto-continue fallback.

    ``before_model`` state overrides are request-local, so the middleware keeps
    an instance-level ``_injected`` list (the middleware instance is rebuilt
    every turn) to make already-injected steers visible to EVERY subsequent
    model call in the turn, exactly as if they were part of the conversation.
    """

    def __init__(self, steer_emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__()
        # Runtime callback that buffers a ``steer_injected`` frame for ``parts``
        # persistence and publishes it to the session event bus (mirrors the
        # delegation emit wiring in the runtime). Set per turn via
        # ``reset_per_turn`` when the middleware is compiled once (W1).
        self._emit = steer_emit
        # Steers already folded into this turn's conversation (persist across
        # every model call of the turn).
        self._injected: list[HumanMessage] = []
        self._injected_ids: set[str] = set()

    def reset_per_turn(self, steer_emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        """W1 (compile-cache prerequisite): start a turn with no injected steers
        and the per-turn emit callback (the build-time callback is session/turn
        specific and must not be pinned at compile)."""
        self._injected = []
        self._injected_ids = set()
        if steer_emit is not None:
            self._emit = steer_emit

    def _steer_message(self, entry: Any) -> HumanMessage:
        content = format_user_message(
            entry.content,
            entry.attachments or [],
            entry.references or [],
            max_attachment_bytes=getattr(entry, "max_attachment_bytes", None),
        )
        return HumanMessage(content=content, id=f"steer-{entry.id}")

    def _inject(self, state: CoworkerAgentState) -> dict[str, Any] | None:
        session_id = str(state.get("session_id") or "")
        if not session_id:
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        try:
            fresh = steer_inbox.take_all(session_id)
        except Exception:  # noqa: BLE001 - an inbox hiccup must never break a model call
            fresh = []
        if not fresh and not self._injected:
            return None
        new_messages: list[HumanMessage] = []
        for entry in fresh:
            steer_id = str(getattr(entry, "id", "") or "")
            if steer_id and steer_id in self._injected_ids:
                continue
            new_messages.append(self._steer_message(entry))
            self._injected_ids.add(steer_id)
            if self._emit is not None:
                try:
                    self._emit(
                        {
                            "type": "steer_injected",
                            "session_id": session_id,
                            "steer_id": steer_id,
                            "content": str(getattr(entry, "content", "") or ""),
                        }
                    )
                except Exception:  # noqa: BLE001 - emission is best-effort
                    logger.debug("steer_injected emit failed for %s", steer_id, exc_info=True)
        if not new_messages and not self._injected:
            return None
        injected = list(self._injected)
        injected.extend(new_messages)
        self._injected = injected
        return {"messages": list(messages) + injected}

    def before_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._inject(state)

    async def abefore_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self._inject(state)
