import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import atomic_write_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_from_message(message: str) -> str:
    cleaned = " ".join(message.split())
    if not cleaned:
        return "新会话"
    return cleaned[:20] + ("…" if len(cleaned) > 20 else "")


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
    goal_text: str = ""
    goal_done: bool = False
    goal_paused: bool = False
    goal_todos: list[dict[str, Any]] = field(default_factory=list)
    goal_max_rounds: int = 50
    goal_force_count: int = 0
    goal_stopped: bool = False
    goal_just_edited: bool = False
    goal_stream_id: str = ""
    goal_interrupted: bool = False
    # Plan → Execute → Verify. Owned by goal_stream; persisted so /goal/status and
    # the frontend can show the current stage. Defaults to "plan" for old sessions.
    goal_phase: str = "plan"
    title_auto: bool = False
    messages: list[SessionMessage] = field(default_factory=list)

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
            goal_text=str(payload.get("goal_text", "")),
            goal_done=bool(payload.get("goal_done", False)),
            goal_paused=bool(payload.get("goal_paused", False)),
            goal_todos=[dict(item) for item in payload.get("goal_todos", []) if isinstance(item, dict)],
            goal_max_rounds=int(payload.get("goal_max_rounds", 50)),
            goal_force_count=int(payload.get("goal_force_count", 0)),
            goal_stopped=bool(payload.get("goal_stopped", False)),
            goal_just_edited=bool(payload.get("goal_just_edited", False)),
            goal_stream_id=str(payload.get("goal_stream_id", "")),
            goal_interrupted=bool(payload.get("goal_interrupted", False)),
            goal_phase=str(payload.get("goal_phase", "plan")),
            title_auto=bool(payload.get("title_auto", False)),
            messages=[SessionMessage(**item) for item in payload.get("messages", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "work_mode": self.work_mode,
            "autonomy": self.autonomy,
            "goal_text": self.goal_text,
            "goal_done": self.goal_done,
            "goal_paused": self.goal_paused,
            "goal_todos": self.goal_todos,
            "goal_max_rounds": self.goal_max_rounds,
            "goal_force_count": self.goal_force_count,
            "goal_stopped": self.goal_stopped,
            "goal_just_edited": self.goal_just_edited,
            "goal_stream_id": self.goal_stream_id,
            "goal_interrupted": self.goal_interrupted,
            "goal_phase": self.goal_phase,
            "message_count": len(self.messages),
        }

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
            "goal_text": self.goal_text,
            "goal_done": self.goal_done,
            "goal_paused": self.goal_paused,
            "goal_todos": self.goal_todos,
            "goal_max_rounds": self.goal_max_rounds,
            "goal_force_count": self.goal_force_count,
            "goal_stopped": self.goal_stopped,
            "goal_just_edited": self.goal_just_edited,
            "goal_stream_id": self.goal_stream_id,
            "goal_interrupted": self.goal_interrupted,
            "goal_phase": self.goal_phase,
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

    def update_goal(
        self,
        session_id: str,
        *,
        goal_text: str | None = None,
        goal_done: bool | None = None,
        goal_paused: bool | None = None,
        goal_todos: list[dict[str, Any]] | None = None,
        goal_max_rounds: int | None = None,
        goal_force_count: int | None = None,
        goal_stopped: bool | None = None,
        goal_just_edited: bool | None = None,
        goal_stream_id: str | None = None,
        goal_interrupted: bool | None = None,
        goal_phase: str | None = None,
    ) -> Session:
        session = self.require(session_id)
        if goal_text is not None:
            session.goal_text = goal_text
        if goal_done is not None:
            session.goal_done = goal_done
        if goal_paused is not None:
            session.goal_paused = goal_paused
        if goal_todos is not None:
            session.goal_todos = goal_todos
        if goal_max_rounds is not None:
            session.goal_max_rounds = goal_max_rounds
        if goal_force_count is not None:
            session.goal_force_count = goal_force_count
        if goal_stopped is not None:
            session.goal_stopped = goal_stopped
        if goal_just_edited is not None:
            session.goal_just_edited = goal_just_edited
        if goal_stream_id is not None:
            session.goal_stream_id = goal_stream_id
        if goal_interrupted is not None:
            session.goal_interrupted = goal_interrupted
        if goal_phase is not None:
            session.goal_phase = goal_phase
        self.save(session)
        return session

    def commit_goal_end(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        content: str = "",
        parts: list[dict[str, Any]] | None = None,
        mode: str = "",
        provider: str = "",
        model: str = "",
        work_mode: str = "",
        autonomy: str = "",
        done: bool | None = None,
        paused: bool | None = None,
        stopped: bool | None = None,
        interrupted: bool | None = None,
        todos: list[dict[str, Any]] | None = None,
        agent_id: str = "",
    ) -> Session:
        """Atomically commit a goal's terminal state AND its final assistant
        message in a single load→mutate→save cycle.

        The goal loop owns the goal state machine; every terminal decision
        (achieved / paused / stopped / timed out / stalled / max rounds) must
        land in the session together with the round's message so that a crash
        between the two can never leave the goal looking "active". The message
        is only appended when ``content`` is non-empty — a blank bubble is never
        persisted.
        """
        session = self.require(session_id)
        if done is not None:
            session.goal_done = done
        if paused is not None:
            session.goal_paused = paused
        if stopped is not None:
            session.goal_stopped = stopped
        if interrupted is not None:
            session.goal_interrupted = interrupted
        if todos is not None:
            session.goal_todos = todos
        if content:
            resolved_id = message_id or str(uuid.uuid4())
            resolved_agent = agent_id or session.agent_id
            session.messages.append(
                SessionMessage(
                    id=resolved_id,
                    role="assistant",
                    content=content,
                    created_at=_now(),
                    mode=mode,
                    provider=provider,
                    model=model,
                    work_mode=work_mode,
                    autonomy=autonomy,
                    attachments=[],
                    parts=parts or [],
                    references=[],
                    agent_id=resolved_agent,
                )
            )
        self.save(session)
        return session

    def update_force_count(self, session_id: str, count: int) -> Session:
        return self.update_goal(session_id, goal_force_count=count)

    def update_max_rounds(self, session_id: str, max_rounds: int) -> Session:
        return self.update_goal(session_id, goal_max_rounds=max_rounds)

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
    ) -> Session:
        session = self.require(session_id)
        # 优先使用调用方传入的 id（通常是前端乐观渲染时生成的 id），
        # 保证前端展示的 message id 与后端持久化 id 一致，避免按 id 检索（回退/重生成）时 404。
        resolved_id = message_id or str(uuid.uuid4())
        resolved_agent = agent_id or session.agent_id
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
            )
        )
        if role == "user" and session.title == "新会话":
            session.title = _title_from_message(content)
            session.title_auto = True
        self.save(session)
        return session

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
