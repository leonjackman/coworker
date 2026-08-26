"""Tests for the logging governance work:

- json_log=0 must still write file logs (plain text) — regression guard for the
  dictConfig failure that silently fell back to console-only basicConfig.
- worker_events disk retention (prune_disk) keeps the newest runs within caps and
  never races active writers.
- JsonFormatter redacts sensitive-looking extra fields / contexts.
- Runtime log config (rotation caps) applies live and is readable back.
- HTTP request logging middleware emits one INFO record per request.
"""

import asyncio
import json
import logging
import logging.config
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.events import WorkerEventBus
from coworker.logger import JsonFormatter, FilePlainFormatter, apply_log_config, current_session_id, get_log_settings, is_sensitive_key, redact

import main  # noqa: E402


@pytest.mark.parametrize("json_log", [True, False])
def test_init_logger_writes_file_in_both_modes(json_log: bool) -> None:
    """Regression: COWORKER_JSON_LOG=0 previously broke dictConfig and file
    logging silently stopped (basicConfig console-only fallback). Both modes must
    still land records on disk."""
    env_val = "1" if json_log else "0"
    script = f"""
import logging, tempfile, os
from pathlib import Path
os.environ["COWORKER_JSON_LOG"] = "{env_val}"
import sys; sys.path.insert(0, r"{Path(__file__).resolve().parents[1]}")
from coworker.logger import init_logger, get_logger
d = Path(tempfile.mkdtemp())
path = init_logger(d, "INFO")
get_logger("governance").info("hello json=%s", {json_log!r})
with path.open(encoding="utf-8") as fh:
    content = fh.read()
print("FILE=" + str(path.exists()))
print("HAS_MSG=" + str("hello json=" in content))
print("IS_JSON=" + str(content.strip().startswith("{{")))
"""
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True,
    )
    assert "FILE=True" in out.stdout
    assert "HAS_MSG=True" in out.stdout
    assert f"IS_JSON={json_log}" in out.stdout


def test_worker_events_prune_disk_keeps_newest_within_cap() -> None:
    with tempfile.TemporaryDirectory() as td:
        bus = WorkerEventBus()
        bus.configure(td)
        dirpath = Path(td) / "worker_events"
        for i in range(5):
            bus.publish(f"run_{i}", {"type": "delta", "i": i})
            bus.close(f"run_{i}")
            # Backdate so pruning sees them as old (older files first).
            f = dirpath / f"run_{i}.jsonl"
            t = time.time() - (10 - i)
            os.utime(f, (t, t))
        stats = bus.prune_disk(max_runs=2, grace_seconds=0)
        remaining = sorted(p.stem for p in dirpath.glob("*.jsonl"))
        assert stats["removed"] == 3
        assert remaining == ["run_3", "run_4"]


def test_worker_events_prune_disk_skips_active_and_recent() -> None:
    with tempfile.TemporaryDirectory() as td:
        bus = WorkerEventBus()
        bus.configure(td)
        dirpath = Path(td) / "worker_events"
        # An old run (must be prunable), an active run and a recent file (skipped).
        bus.publish("run_old", {"type": "delta"})
        bus.close("run_old")
        f = dirpath / "run_old.jsonl"
        t = time.time() - 3600
        os.utime(f, (t, t))

        bus.expect("run_active")
        (dirpath / "run_active.jsonl").write_text('{"type":"x"}\n')
        (dirpath / "run_recent.jsonl").write_text('{"type":"x"}\n')

        stats = bus.prune_disk(max_runs=0, max_bytes=1, grace_seconds=300)
        assert stats["removed"] == 1
        assert (dirpath / "run_old.jsonl").exists() is False
        assert (dirpath / "run_active.jsonl").exists() is True
        assert (dirpath / "run_recent.jsonl").exists() is True


def test_worker_events_prune_disk_respects_total_bytes() -> None:
    with tempfile.TemporaryDirectory() as td:
        bus = WorkerEventBus()
        bus.configure(td)
        dirpath = Path(td) / "worker_events"
        for i in range(4):
            bus.publish(f"run_{i}", {"type": "delta", "payload": "x" * 100})
            bus.close(f"run_{i}")
            t = time.time() - (10 - i)
            os.utime(dirpath / f"run_{i}.jsonl", (t, t))
        # Each file is ~210 bytes; a 500-byte budget must keep the newest 2 runs.
        stats = bus.prune_disk(max_runs=100, max_bytes=500, grace_seconds=0)
        remaining = list(dirpath.glob("*.jsonl"))
        assert stats["removed"] == 2
        assert sorted(p.stem for p in remaining) == ["run_2", "run_3"]
        total = sum(p.stat().st_size for p in remaining)
        assert total <= 500


def test_redact_sensitive_keys() -> None:
    assert is_sensitive_key("api_key") is True
    assert is_sensitive_key("Authorization") is True
    assert is_sensitive_key("PAT") is True
    assert is_sensitive_key("content") is False

    payload = {"token": "sekrit", "context": {"password": "hunter2", "notes": "ok"}}
    out = redact(payload)
    assert out["token"] == "***"
    assert out["context"]["password"] == "***"
    assert out["context"]["notes"] == "ok"


def test_json_formatter_redacts_extra_and_context() -> None:
    record = logging.LogRecord(
        name="coworker.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    record.session_id = "sess-1"
    record.context = {"api_key": "shh", "model": "qwen"}
    record.secret_token = "plain-leak"  # sneaky extra
    line = JsonFormatter().format(record)
    entry = json.loads(line)
    assert entry["context"]["api_key"] == "***"
    assert entry["context"]["model"] == "qwen"
    assert entry["secret_token"] == "***"
    assert entry["session_id"] == "sess-1"


def test_file_plain_formatter_is_timestamped() -> None:
    record = logging.LogRecord(
        name="coworker.test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="boom %s", args=("x",), exc_info=None,
    )
    line = FilePlainFormatter().format(record)
    assert "boom x" in line
    assert "coworker.test" in line
    assert "WARNING" in line


def test_apply_and_get_log_settings_rotation() -> None:
    """Rotation caps must change live and be readable back via get_log_settings."""
    before = get_log_settings()
    try:
        apply_log_config(max_bytes=5 * 1024 * 1024, backup_count=3)
        effective = get_log_settings()
        assert effective["log_max_bytes"] == 5 * 1024 * 1024
        assert effective["log_backup_count"] == 3
    finally:
        apply_log_config(
            max_bytes=before["log_max_bytes"],
            backup_count=before["log_backup_count"],
            json_log=before["json_log"],
        )


def test_http_middleware_logs_requests() -> None:
    """One INFO record per request with method/path/status/request_id; health and
    log-file endpoints are skipped."""
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    http_logger = main.http_logger
    old_level = http_logger.level
    old_propagate = http_logger.propagate
    handler = Capture()
    try:
        http_logger.setLevel(logging.INFO)
        http_logger.propagate = False
        http_logger.addHandler(handler)

        client = TestClient(main.app)
        r = client.get("/this-does-not-exist")
        assert r.status_code == 404
        client.get("/health")
    finally:
        http_logger.removeHandler(handler)
        http_logger.setLevel(old_level)
        http_logger.propagate = old_propagate

    assert len(records) == 1, f"expected only the 404 request to be logged, got {len(records)}"
    text = records[0].getMessage()
    assert "/this-does-not-exist" in text
    assert "404" in text
    assert "request_id=" in text
    assert "/health" not in text


def test_session_correlation_contextvar_feeds_json_formatter() -> None:
    """JsonFormatter falls back to the request-scoped session id contextvar."""
    token = current_session_id.set("sess-ctx-42")
    try:
        record = logging.LogRecord(
            name="coworker.test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="turn event", args=(), exc_info=None,
        )
        entry = json.loads(JsonFormatter().format(record))
        assert entry["session_id"] == "sess-ctx-42"

        # An explicit record extra wins over the contextvar.
        record2 = logging.LogRecord(
            name="coworker.test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="explicit", args=(), exc_info=None,
        )
        record2.session_id = "explicit-id"
        entry2 = json.loads(JsonFormatter().format(record2))
        assert entry2["session_id"] == "explicit-id"
    finally:
        current_session_id.reset(token)


def test_session_correlation_cleared_without_context() -> None:
    """Without any bound session id, JsonFormatter emits no session_id field."""
    record = logging.LogRecord(
        name="coworker.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="no session", args=(), exc_info=None,
    )
    entry = json.loads(JsonFormatter().format(record))
    assert "session_id" not in entry


def test_http_middleware_correlates_session_path_and_query() -> None:
    """Middleware binds session_id from /sessions/{id}/... paths and the
    session_id query param, surfacing it in the coworker.http line."""
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    http_logger = main.http_logger
    old_level = http_logger.level
    old_propagate = http_logger.propagate
    handler = Capture()
    try:
        http_logger.setLevel(logging.INFO)
        http_logger.propagate = False
        http_logger.addHandler(handler)

        client = TestClient(main.app)
        client.get("/sessions/sess-path-1/changes")
        client.get("/goal?session_id=sess-query-2")
        client.get("/health")  # skipped
    finally:
        http_logger.removeHandler(handler)
        http_logger.setLevel(old_level)
        http_logger.propagate = old_propagate

    texts = [r.getMessage() for r in records]
    assert len(texts) == 2, texts
    assert any("/sessions/sess-path-1/changes" in t and "session_id=sess-path-1" in t for t in texts)
    assert any("/goal" in t and "session_id=sess-query-2" in t for t in texts)
