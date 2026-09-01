import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import atomic_write_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _default_title_from_message(message: str) -> str:
    """SINGLE SOURCE of truth for rule-based session titles.

    Canonical implementation lives in ``coworker.agent.prompts`` (the N4 rule:
    strips framing, cuts at a sentence boundary / ~20 chars). Imported here so
    the auto-title path and the ``/sessions`` title endpoint produce identical
    titles — never redefine the rule in two places.
    """
    from .agent.prompts import _default_title_from_message as _rule

    return _rule(message)


@dataclass
class SessionMessage:
    id: str
    role: str
    content: str
    created_at: str
    mode: str = ""
    provider: str = ""
    model: str = ""
    work_mode: str = ""
    autonomy: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    parts: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    agent_id: str = ""
    # 插話 (interject) 持久化标记：该 user 消息是一条插进运行中任务的引导消息，
    # 前端不把它渲染为独立用户泡泡（内容由 assistant 的「收到插話」card 展示）。
    interject: bool = False


# 默认 goal token 预算（对齐 codex 的 max_goal_token_budget）：未显式指定时作为
# 硬终止天花板——模型不自觉调 update_goal(complete) 时，撞预算即 budget_limited 硬停，
# 防止无限续跑。用户仍可在 /goal 时显式传 token_budget 覆盖。
DEFAULT_GOAL_TOKEN_BUDGET = 5_000_000


@dataclass
class GoalState:
    """Persistent goal bound to a session (对齐 codex `ThreadGoal`).

    Status machine: active → paused | blocked | complete | budget_limited | usage_limited.
    Only ``active`` drives the auto-continuation loop; pause/resume/clear are
    user-controlled; ``update_goal`` (model tool) may only set complete/blocked.
    """

    objective: str
    status: str = "active"
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    # 已完成的回合数（round 0 = 首轮用户驱动轮）。引擎在每轮结束后写回，
    # 供 `update_goal(blocked)` 做引擎侧 blocked audit（须连续 ≥3 轮同一阻塞）。
    round: int = 0
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "GoalState | None":
        if not payload:
            return None
        token_budget = payload.get("token_budget")
        return cls(
            objective=str(payload.get("objective", "")),
            status=str(payload.get("status", "active")),
            token_budget=int(token_budget) if token_budget is not None else None,
            tokens_used=int(payload.get("tokens_used", 0) or 0),
            time_used_seconds=int(payload.get("time_used_seconds", 0) or 0),
            round=int(payload.get("round", 0) or 0),
            created_at=int(payload.get("created_at", 0) or 0),
            updated_at=int(payload.get("updated_at", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    project_id: str = ""
    agent_id: str = ""
    work_mode: str = "build"
    autonomy: str = "guarded"
    todos: list[dict[str, Any]] = field(default_factory=list)
    title_auto: bool = False
    # Last FULL context-usage measurement (system prompt + tool schemas + messages
    # + overhead, calibrated) persisted from the most recent run, so the session
    # preview shows the true request size instead of a message-only undercount.
    context_used_tokens: int = 0
    context_used_tokens_calibrated: int = 0
    context_used_chars: int = 0
    context_calibration_factor: float = 0.0
    # Persistent compaction state (C1): the compaction summary + fingerprint set
    # survive across turns/goal rounds even though the per-turn LangGraph
    # checkpoint is discarded. Re-injected at turn start so anchored-update
    # compaction accumulates instead of re-summarizing the full history every
    # turn. Mirrors codex (summary lives in the rollout) / opencode (compaction
    # marker persisted in the DB) / LangChain's persistent-thread design.
    context_summary: str = ""
    context_compact_count: int = 0
    context_summarized_fingerprints: list[str] = field(default_factory=list)
    messages: list[SessionMessage] = field(default_factory=list)
    # Persistent session-scoped goal (唯一 goal 真源). None = no goal.
    goal: GoalState | None = None
    # 未讀水位線：assistant 訊息的 created_at 大於此值的算未讀。預設空值＝全部已讀。
    last_read_at: str = ""
    # 最近一輪以錯誤終止時的摘要；空值 = 無錯誤。
    last_error: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Session":
        autonomy = payload.get("autonomy")
        if autonomy is None:
            # Legacy sessions stored access_mode ("default"/"full").
            legacy = str(payload.get("access_mode", "default") or "default")
            autonomy = "autonomous" if legacy == "full" else "guarded"
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "新会话")),
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
            project_id=str(payload.get("project_id", "")),
            agent_id=str(payload.get("agent_id", "")),
            work_mode=str(payload.get("work_mode", "build")),
            autonomy=str(autonomy),
            todos=[dict(item) for item in payload.get("todos", []) if isinstance(item, dict)],
            title_auto=bool(payload.get("title_auto", False)),
            context_used_tokens=int(payload.get("context_used_tokens", 0) or 0),
            context_used_tokens_calibrated=int(payload.get("context_used_tokens_calibrated", 0) or 0),
            context_used_chars=int(payload.get("context_used_chars", 0) or 0),
            context_calibration_factor=float(payload.get("context_calibration_factor", 0.0) or 0.0),
            context_summary=str(payload.get("context_summary", "") or ""),
            context_compact_count=int(payload.get("context_compact_count", 0) or 0),
            context_summarized_fingerprints=[str(x) for x in payload.get("context_summarized_fingerprints", []) if isinstance(x, str)],
            messages=[SessionMessage(**item) for item in payload.get("messages", [])],
            goal=GoalState.from_dict(payload.get("goal")),
            last_read_at=str(payload.get("last_read_at", "") or ""),
            last_error=str(payload.get("last_error", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public(self) -> dict[str, Any]:
        unread_count = self.unread_count()
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "work_mode": self.work_mode,
            "autonomy": self.autonomy,
            "todos": self.todos,
            "goal": self.goal.to_dict() if self.goal is not None else None,
            "message_count": len(self.messages),
            "unread_count": unread_count,
            "last_error": self.last_error if self.last_error else None,
        }

    def unread_count(self) -> int:
        """Count of unread assistant messages.

        Messages with ``created_at > last_read_at`` are unread.  An empty
        ``last_read_at`` means "all read" (legacy sessions).  String comparison
        works because ``_now()`` produces fixed ``+00:00`` ISO 8601.
        """
        if not self.last_read_at:
            return 0
        return sum(
            1 for m in self.messages
            if m.role == "assistant" and m.created_at > self.last_read_at
        )

    def full(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "work_mode": self.work_mode,
            "autonomy": self.autonomy,
            "todos": self.todos,
            "goal": self.goal.to_dict() if self.goal is not None else None,
            "messages": [asdict(message) for message in self.messages],
        }


class SessionStore:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def load(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return Session.from_dict(payload)

    def save(self, session: Session) -> None:
        session.updated_at = _now()
        atomic_write_text(
            self._path(session.id),
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def update_context_usage(
        self,
        session_id: str,
        *,
        used_tokens: int,
        used_tokens_calibrated: int,
        used_chars: int = 0,
        calibration_factor: float = 0.0,
    ) -> None:
        """Persist the last full context-usage measurement for the session-open
        preview, so it reflects the real request size (system + tools + messages
        + overhead) rather than a message-only undercount."""
        try:
            session = self.require(session_id)
        except KeyError:
            return
        session.context_used_tokens = int(used_tokens)
        session.context_used_tokens_calibrated = int(used_tokens_calibrated)
        session.context_used_chars = int(used_chars)
        session.context_calibration_factor = float(calibration_factor)
        # Persist without bumping updated_at (this is telemetry, not activity).
        atomic_write_text(
            self._path(session.id),
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def update_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        fingerprints: list[str] | None = None,
        count: int | None = None,
    ) -> None:
        """Persist the compaction state (C1) so it survives across turns / goal
        rounds (the per-turn LangGraph checkpoint is discarded)."""
        try:
            session = self.require(session_id)
        except KeyError:
            return
        if summary:
            session.context_summary = summary
        if fingerprints is not None:
            session.context_summarized_fingerprints = [str(x) for x in fingerprints]
        if count is not None:
            session.context_compact_count = int(count)
        atomic_write_text(
            self._path(session.id),
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def list_sessions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sessions = []
        for path in sorted(self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            session = self.load(path.stem)
            if not session:
                continue
            if project_id is not None and session.project_id != project_id:
                continue
            sessions.append(session.public())
        return sessions

    def new_session(self, title: str = "", project_id: str = "", work_mode: str = "build", autonomy: str = "guarded", agent_id: str = "") -> Session:
        now = _now()
        return Session(
            id=str(uuid.uuid4()),
            title=title or "新会话",
            created_at=now,
            updated_at=now,
            project_id=project_id,
            agent_id=agent_id,
            work_mode=work_mode,
            autonomy=autonomy,
        )

    def create(self, title: str = "", project_id: str = "", work_mode: str = "build", autonomy: str = "guarded", agent_id: str = "") -> Session:
        session = self.new_session(title, project_id, work_mode, autonomy, agent_id)
        self.save(session)
        return session

    def update_modes(self, session_id: str, work_mode: str, autonomy: str) -> Session:
        session = self.require(session_id)
        session.work_mode = work_mode
        session.autonomy = autonomy
        self.save(session)
        return session

    def update_todos(self, session_id: str, todos: list[dict[str, Any]]) -> Session:
        """Persist the task-list (``write_todos``) so it survives a refresh."""
        session = self.require(session_id)
        session.todos = [dict(item) for item in todos if isinstance(item, dict)]
        self.save(session)
        return session

    # ------------------------------------------------------------------
    # Goal CRUD — 唯一 goal 真源在 `Session.goal`；写失败不影响主链。
    # ------------------------------------------------------------------

    def get_goal(self, session_id: str) -> GoalState | None:
        return self.require(session_id).goal

    def set_goal(self, session_id: str, objective: str, token_budget: int | None = None) -> GoalState:
        """新建或覆盖重建目标（状态置 active，计数清零，刷新时间戳）。

        未显式传 ``token_budget`` 时套用 ``DEFAULT_GOAL_TOKEN_BUDGET`` 作为硬终止
        天花板（对齐 codex 的 max_goal_token_budget），避免模型不自觉调
        update_goal(complete) 时无限续跑。
        """
        session = self.require(session_id)
        now = _now_epoch_ms()
        effective_budget = int(token_budget) if token_budget is not None else DEFAULT_GOAL_TOKEN_BUDGET
        goal = GoalState(
            objective=objective,
            status="active",
            token_budget=effective_budget,
            tokens_used=0,
            time_used_seconds=0,
            created_at=now,
            updated_at=now,
        )
        session.goal = goal
        self.save(session)
        return goal

    def clear_goal(self, session_id: str) -> bool:
        session = self.require(session_id)
        if session.goal is None:
            return False
        session.goal = None
        self.save(session)
        return True

    def update_goal_status(self, session_id: str, status: str) -> GoalState | None:
        session = self.require(session_id)
        if session.goal is None:
            return None
        session.goal.status = status
        session.goal.updated_at = _now_epoch_ms()
        self.save(session)
        return session.goal

    def update_goal_objective(self, session_id: str, objective: str) -> GoalState | None:
        session = self.require(session_id)
        if session.goal is None:
            return None
        session.goal.objective = objective
        session.goal.updated_at = _now_epoch_ms()
        self.save(session)
        return session.goal

    def update_goal_round(self, session_id: str, round_: int) -> GoalState | None:
        session = self.require(session_id)
        if session.goal is None:
            return None
        session.goal.round = max(0, int(round_))
        session.goal.updated_at = _now_epoch_ms()
        self.save(session)
        return session.goal

    def account_goal_usage(
        self,
        session_id: str,
        token_delta: int,
        time_delta_seconds: float,
    ) -> GoalState | None:
        """累加 token/time；若超过 token_budget 自动置 `budget_limited`。"""
        session = self.require(session_id)
        goal = session.goal
        if goal is None:
            return None
        goal.tokens_used = max(0, goal.tokens_used + int(token_delta))
        goal.time_used_seconds = max(0, goal.time_used_seconds + int(time_delta_seconds))
        # 仅在仍 active 时超预算才置 budget_limited；不覆盖 complete/blocked/paused。
        if goal.status == "active" and goal.token_budget is not None and goal.tokens_used >= goal.token_budget:
            goal.status = "budget_limited"
        goal.updated_at = _now_epoch_ms()
        self.save(session)
        return goal

    def get(self, session_id: str) -> Session | None:
        return self.load(session_id)

    def require(self, session_id: str) -> Session:
        session = self.load(session_id)
        if not session:
            raise KeyError(f"session {session_id} not found")
        return session

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def delete_by_project(self, project_id: str) -> int:
        deleted = 0
        for session in self.list_sessions(project_id):
            if self.delete(session["id"]):
                deleted += 1
        return deleted

    def delete_by_agent(self, project_id: str, agent_id: str) -> int:
        """Delete every session of one project bound to ``agent_id``."""
        deleted = 0
        for session in self.list_sessions(project_id):
            if session.get("agent_id") != agent_id:
                continue
            if self.delete(session["id"]):
                deleted += 1
        return deleted

    def rename(self, session_id: str, title: str) -> Session | None:
        session = self.require(session_id)
        cleaned = title.strip()
        if cleaned:
            session.title = cleaned[:40]
            session.title_auto = False
            self.save(session)
        return session

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        mode: str = "",
        provider: str = "",
        model: str = "",
        work_mode: str = "",
        autonomy: str = "",
        attachments: list[dict[str, Any]] | None = None,
        parts: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
        message_id: str | None = None,
        agent_id: str = "",
        interject: bool = False,
    ) -> Session:
        session = self.require(session_id)
        # 优先使用调用方传入的 id（通常是前端乐观渲染时生成的 id），
        # 保证前端展示的 message id 与后端持久化 id 一致，避免按 id 检索（回退/重生成）时 404。
        resolved_id = message_id or str(uuid.uuid4())
        resolved_agent = agent_id or session.agent_id
        # Idempotent by id: W6/N2-P1 incremental persistence may have already
        # created this assistant message at a tool boundary — update it instead of
        # appending a duplicate with the same id.
        existing_idx = next((i for i, m in enumerate(session.messages) if m.id == resolved_id), None)
        if existing_idx is not None:
            existing = session.messages[existing_idx]
            existing.role = role
            existing.content = content or existing.content
            existing.mode = mode or existing.mode
            existing.provider = provider or existing.provider
            existing.model = model or existing.model
            existing.work_mode = work_mode or existing.work_mode
            existing.autonomy = autonomy or existing.autonomy
            if parts is not None:
                existing.parts = list(parts)
            if references is not None:
                existing.references = list(references)
            if attachments is not None:
                existing.attachments = list(attachments)
            if agent_id:
                existing.agent_id = agent_id
            self.save(session)
            return session
        session.messages.append(
            SessionMessage(
                id=resolved_id,
                role=role,
                content=content,
                created_at=_now(),
                mode=mode,
                provider=provider,
                model=model,
                work_mode=work_mode,
                autonomy=autonomy,
                attachments=attachments or [],
                parts=parts or [],
                references=references or [],
                agent_id=resolved_agent,
                interject=interject,
            )
        )
        if role == "user" and session.title == "新会话":
            session.title = _default_title_from_message(content)
            session.title_auto = True
        self.save(session)
        return session

    def replace_assistant_parts(self, session_id: str, message_id: str, parts: list[dict[str, Any]]) -> None:
        """W6/N2-P1: persist the CURRENT merged parts of an in-flight assistant
        message at tool boundaries (crash-resilience).

        Creates the assistant message if it does not exist; otherwise REPLACES
        its parts with the cumulative merged view (idempotent across boundaries).
        Best-effort — a failure must never break the live stream; ``done``
        reconciliation finalizes the message.
        """
        if not parts:
            return
        try:
            session = self.require(session_id)
        except KeyError:
            return
        idx = next((i for i, m in enumerate(session.messages) if m.id == message_id), None)
        if idx is None:
            session.messages.append(
                SessionMessage(
                    id=message_id,
                    role="assistant",
                    content="",
                    created_at=_now(),
                    parts=list(parts),
                )
            )
        else:
            session.messages[idx].parts = list(parts)
        self.save(session)

    def find_message_index(self, session_id: str, message_id: str) -> int:
        session = self.require(session_id)
        for index, message in enumerate(session.messages):
            if message.id == message_id:
                return index
        raise KeyError(f"message {message_id} not found in session {session_id}")

    def truncate_from(self, session_id: str, message_id: str) -> list[SessionMessage]:
        """Truncate the session so it keeps only messages up to and including
        the given message id. Returns the remaining messages."""
        session = self.require(session_id)
        index = self.find_message_index(session_id, message_id)
        session.messages = session.messages[: index + 1]
        self.save(session)
        return session.messages

    def truncate_before(self, session_id: str, message_id: str) -> list[SessionMessage]:
        """Truncate the session so it keeps only messages strictly before the
        given message id (the target message is dropped too). Returns the
        remaining messages."""
        session = self.require(session_id)
        index = self.find_message_index(session_id, message_id)
        session.messages = session.messages[:index]
        self.save(session)
        return session.messages

    def update_message_content(self, session_id: str, message_id: str, content: str) -> SessionMessage:
        session = self.require(session_id)
        index = self.find_message_index(session_id, message_id)
        message = session.messages[index]
        message.content = content
        self.save(session)
        return message
