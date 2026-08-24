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
    # 插話 (interject) 持久化标记：该 user 消息是一条插进运行中任务的引导消息，
    # 前端不把它渲染为独立用户泡泡（内容由 assistant 的「收到插話」card 展示）。
    interject: bool = False


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
            todos=[dict(item) for item in payload.get("todos", []) if isinstance(item, dict)],
            title_auto=bool(payload.get("title_auto", False)),
            context_used_tokens=int(payload.get("context_used_tokens", 0) or 0),
            context_used_tokens_calibrated=int(payload.get("context_used_tokens_calibrated", 0) or 0),
            context_used_chars=int(payload.get("context_used_chars", 0) or 0),
            context_calibration_factor=float(payload.get("context_calibration_factor", 0.0) or 0.0),
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
            "todos": self.todos,
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
            "todos": self.todos,
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
