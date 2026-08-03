import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_from_message(message: str) -> str:
    cleaned = " ".join(message.split())
    if not cleaned:
        return "新会话"
    return cleaned[:40] + ("…" if len(cleaned) > 40 else "")


@dataclass
class SessionMessage:
    id: str
    role: str
    content: str
    created_at: str
    mode: str = ""
    provider: str = ""
    model: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    project_id: str = ""
    work_mode: str = "build"
    access_mode: str = "default"
    messages: list[SessionMessage] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Session":
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "新会话")),
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
            project_id=str(payload.get("project_id", "")),
            work_mode=str(payload.get("work_mode", "build")),
            access_mode=str(payload.get("access_mode", "default")),
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
            "work_mode": self.work_mode,
            "access_mode": self.access_mode,
            "message_count": len(self.messages),
        }

    def full(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "work_mode": self.work_mode,
            "access_mode": self.access_mode,
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
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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

    def new_session(self, title: str = "", project_id: str = "", work_mode: str = "build", access_mode: str = "default") -> Session:
        now = _now()
        return Session(
            id=str(uuid.uuid4()),
            title=title or "新会话",
            created_at=now,
            updated_at=now,
            project_id=project_id,
            work_mode=work_mode,
            access_mode=access_mode,
        )

    def create(self, title: str = "", project_id: str = "", work_mode: str = "build", access_mode: str = "default") -> Session:
        session = self.new_session(title, project_id, work_mode, access_mode)
        self.save(session)
        return session

    def update_modes(self, session_id: str, work_mode: str, access_mode: str) -> Session:
        session = self.require(session_id)
        session.work_mode = work_mode
        session.access_mode = access_mode
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

    def rename(self, session_id: str, title: str) -> Session | None:
        session = self.require(session_id)
        cleaned = title.strip()
        if cleaned:
            session.title = cleaned[:40]
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
        attachments: list[dict[str, Any]] | None = None,
        parts: list[dict[str, Any]] | None = None,
    ) -> Session:
        session = self.require(session_id)
        session.messages.append(
            SessionMessage(
                id=str(uuid.uuid4()),
                role=role,
                content=content,
                created_at=_now(),
                mode=mode,
                provider=provider,
                model=model,
                attachments=attachments or [],
                parts=parts or [],
            )
        )
        if role == "user" and session.title == "新会话":
            session.title = _title_from_message(content)
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
