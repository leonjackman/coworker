"""WS5 regression: an interrupted turn must persist as ONE assistant message.

Incremental persistence creates a parts-only placeholder keyed by the round's
client id; the error path persists the terminal partial with a generated id
(so the frontend never adopts a half reply as success). Without folding, the
session ends up with an empty bubble + a text bubble for the same reply.
"""

import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

from coworker.sessions import SessionStore  # noqa: E402


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path)


def test_merge_placeholder_folds_parts_and_drops_placeholder(tmp_path):
    store = _store(tmp_path)
    session = store.create(title="t")
    sid = session.id
    client_id = "assistant-123-abc"

    store.replace_assistant_parts(sid, client_id, [{"type": "tool", "name": "computer"}])
    # Terminal partial uses a generated id (message_id=None).
    session = store.append_message(sid, role="assistant", content="half reply", message_id=None)
    generated_id = session.messages[-1].id
    assert generated_id != client_id
    assert len([m for m in session.messages if m.role == "assistant"]) == 2

    session = store.merge_placeholder(sid, client_id, generated_id)
    assistants = [m for m in session.messages if m.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].id == generated_id
    assert assistants[0].content == "half reply"
    assert assistants[0].parts == [{"type": "tool", "name": "computer"}]


def test_merge_placeholder_noop_when_placeholder_absent(tmp_path):
    store = _store(tmp_path)
    session = store.create(title="t")
    sid = session.id
    session = store.append_message(sid, role="assistant", content="x", message_id=None)
    target = session.messages[-1].id
    session = store.merge_placeholder(sid, "does-not-exist", target)
    assert len([m for m in session.messages if m.role == "assistant"]) == 1


def test_done_reuses_client_id_in_place(tmp_path):
    """The normal done path updates the placeholder, never duplicates it."""
    store = _store(tmp_path)
    session = store.create(title="t")
    sid = session.id
    client_id = "assistant-999-xyz"
    store.replace_assistant_parts(sid, client_id, [{"type": "text", "text": "hi"}])
    session = store.append_message(
        sid, role="assistant", content="hi there", message_id=client_id,
        parts=[{"type": "text", "text": "hi there"}],
    )
    assistants = [m for m in session.messages if m.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].id == client_id
    assert assistants[0].content == "hi there"
