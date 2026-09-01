"""Tests for skill self-authoring & self-calibration (Milestone 1).

Covers: the draft queue (never injected until approved), the ``skill_manage``
orchestrator (create/patch/edit/delete), the pending API
(list/get/put/approve/reject), and the post-turn review loop (gating + parsing
+ staging).
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_self_author_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coworker.agent import skill_review  # noqa: E402
from coworker.agent.skill_review import _build_system_prompt, _parse_review, run_skill_review  # noqa: E402

SKILL_CONTENT = """---
name: {name}
description: {desc}
---
# {name}

## When to Use
Trigger conditions.

## Procedure
1. Do the thing.

## Pitfalls
- Common failure.

## Verification
How to confirm it worked.
"""


def _reset_review_settings():
    """Clear the skill_review key so each test starts from product defaults."""
    path = main.settings.data_dir / ".coworker_settings.json"
    if path.exists():
        import json as _json

        data = _json.loads(path.read_text() or "{}")
        data.pop("skill_review", None)
        path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def manager(tmp_path):
    """A SkillManager whose user skills root is redirected to tmp_path."""
    _reset_review_settings()
    main.skill_manager.scanner.user_skills_dir = tmp_path
    main.skill_manager.refresh()
    return main.skill_manager


@pytest.fixture()
def client(tmp_path):
    _reset_review_settings()
    main.skill_manager.scanner.user_skills_dir = tmp_path
    main.skill_manager.refresh()
    with TestClient(main.app) as test_client:
        yield test_client


def _stage(manager, name, **kwargs):
    result = manager.stage_skill_draft(
        name, SKILL_CONTENT.format(name=name, desc=f"desc {name}"), **kwargs
    )
    assert result["status"] == "ok", result
    return result


# ── draft queue ────────────────────────────────────────────────────

def test_draft_never_injected_until_approved(manager):
    _stage(manager, "mail-triage", sources=["session:s1", "correction:xyz"])
    assert manager.injection_list() == [], "drafts must never enter the injected catalog"

    pending = manager.pending()
    assert len(pending) == 1
    assert pending[0]["name"] == "mail-triage"
    assert pending[0]["provenance"] == "agent"
    assert pending[0]["status"] == "draft"
    assert pending[0]["sources"] == ["session:s1", "correction:xyz"]
    assert pending[0]["created_at"], "drafts carry a created_at timestamp"

    result = manager.approve_pending("mail-triage")
    assert result["status"] == "ok", result
    assert manager.pending() == []
    names = {s.name for s in manager.injection_list()}
    assert "mail-triage" in names
    entry = manager.get("mail-triage")
    assert entry.provenance == "agent" and entry.status == "active"


def test_skill_manage_create_collision_and_delete(manager):
    _stage(manager, "report-gen")
    # duplicate create while pending is rejected
    dup = manager.skill_manage("create", "report-gen", content=SKILL_CONTENT.format(name="report-gen", desc="dup"))
    assert dup["status"] == "error"
    assert "already exists" in dup["message"]
    # delete works only on active skills
    res = manager.skill_manage("delete", "report-gen")
    assert res["status"] == "error"  # not active yet
    manager.approve_pending("report-gen")
    res = manager.skill_manage("delete", "report-gen")
    assert res["status"] == "ok", res
    assert manager.get("report-gen") is None


def test_skill_manage_patch_stages_replacement(manager):
    _stage(manager, "deploy-proc")
    manager.approve_pending("deploy-proc")

    res = manager.skill_manage("patch", "deploy-proc", old_string="# deploy-proc", new_string="# deploy-proc v2")
    assert res["status"] == "ok", res
    assert len(manager.pending()) == 1
    # active skill is NOT modified by the staged patch
    assert "# deploy-proc v2" not in manager.get("deploy-proc").file_path.read_text()

    # patch with a missing old_string fails
    res = manager.skill_manage("patch", "deploy-proc", old_string="NOPE", new_string="x")
    assert res["status"] == "error"

    # approving applies the replacement over the active skill
    manager.approve_pending("deploy-proc")
    assert "# deploy-proc v2" in manager.get("deploy-proc").file_path.read_text()


def test_skill_manage_edit_and_reject(manager):
    _stage(manager, "doc-proc")
    manager.approve_pending("doc-proc")
    res = manager.skill_manage("edit", "doc-proc", content=SKILL_CONTENT.format(name="doc-proc", desc="rewritten"))
    assert res["status"] == "ok", res
    res = manager.reject_pending("doc-proc")
    assert res["status"] == "ok"
    assert manager.pending() == []
    assert "rewritten" not in manager.get("doc-proc").file_path.read_text()


# ── pending API ────────────────────────────────────────────────────

def test_pending_api_flow(client, manager):
    _stage(manager, "mail-triage")

    r = client.get("/skills/pending")
    assert r.status_code == 200
    assert len(r.json()["pending"]) == 1
    assert r.json()["pending"][0]["name"] == "mail-triage"

    r = client.get("/skills/pending/mail-triage")
    assert r.status_code == 200
    assert "## Procedure" in r.json()["content"]

    r = client.get("/skills/pending/nope")
    assert r.status_code == 404

    # edit before approve
    edited = SKILL_CONTENT.format(name="mail-triage", desc="edited description")
    r = client.put("/skills/pending/mail-triage", json={"content": edited})
    assert r.status_code == 200
    assert "edited description" in manager.read_pending("mail-triage")

    r = client.post("/skills/pending/mail-triage/approve")
    assert r.status_code == 200
    assert manager.pending() == []
    assert manager.get("mail-triage") is not None


def test_pending_api_reject(client, manager):
    _stage(manager, "temp-skill")
    r = client.post("/skills/pending/temp-skill/reject")
    assert r.status_code == 200
    assert manager.pending() == []
    assert manager.read_pending("temp-skill") is None


# ── review loop ────────────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, output):
        self._output = output

    async def ainvoke(self, messages):
        return type("_R", (), {"content": self._output})()


def _run(coro):
    return asyncio.run(coro)


def test_parse_review_lenient():
    assert _parse_review('{"action": "create", "name": "x", "content": "y"}') == {
        "action": "create",
        "name": "x",
        "content": "y",
    }
    fenced = '```json\n{"action": "none"}\n```'
    assert _parse_review(fenced) == {"action": "none"}
    embedded = 'Here is the verdict: {"action": "create", "name": "a", "content": "b"}'
    assert _parse_review(embedded)["action"] == "create"
    assert _parse_review("no json here") is None


def test_review_stages_create(manager):
    llm = _FakeLLM(json.dumps({"action": "create", "name": "weekly-report", "content": SKILL_CONTENT.format(name="weekly-report", desc="weekly report")}))
    messages = [{"type": "human", "content": "make the weekly report"}, {"type": "ai", "content": "done"}]
    parts = [{"type": "tool", "name": "run_command", "status": "done"}]

    result = _run(run_skill_review(llm, manager, session_id="sess-1", messages=messages, parts=parts))
    assert result["action"] == "create"
    assert result["staged"] is True
    assert len(manager.pending()) == 1
    assert manager.pending()[0]["name"] == "weekly-report"
    assert "session:sess-1" in manager.pending()[0]["sources"]


def test_review_none_stages_nothing(manager):
    llm = _FakeLLM('{"action": "none"}')
    parts = [{"type": "tool", "name": "run_command", "status": "done"}]
    result = _run(run_skill_review(llm, manager, session_id="s2", messages=[], parts=parts))
    assert result["action"] == "none"
    assert manager.pending() == []


def test_review_unparseable_skips(manager):
    llm = _FakeLLM("I don't think a skill is needed here.")
    parts = [{"type": "tool", "name": "run_command", "status": "done"}]
    result = _run(run_skill_review(llm, manager, session_id="s3", messages=[], parts=parts))
    assert result["action"] == "none"
    assert manager.pending() == []


def test_review_update_targets_existing(manager):
    _stage(manager, "deploy-proc")
    manager.approve_pending("deploy-proc")
    llm = _FakeLLM(json.dumps({"action": "update", "name": "deploy-proc", "content": SKILL_CONTENT.format(name="deploy-proc", desc="updated deploy")}))
    parts = [{"type": "tool", "name": "run_command", "status": "done"}]
    result = _run(run_skill_review(llm, manager, session_id="s4", messages=[], parts=parts))
    assert result["action"] == "update"
    assert result["staged"] is True
    assert len(manager.pending()) == 1
    # active skill unchanged until approval
    assert "updated deploy" not in manager.get("deploy-proc").file_path.read_text()


def test_concurrent_stage_keeps_single_draft(manager):
    """Two racing reviews for the same name must not produce two drafts."""
    import threading

    results: list[dict] = []
    barrier = threading.Barrier(2)

    def stage():
        barrier.wait()
        results.append(
            manager.stage_skill_draft(
                "race-skill", SKILL_CONTENT.format(name="race-skill", desc="race"), sources=["session:r1"]
            )
        )

    threads = [threading.Thread(target=stage) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error" and "already exists" in r["message"])
    assert ok_count == 1, results
    assert err_count == 1, results
    assert len(manager.pending()) == 1


def test_recent_session_messages_loading():
    """Resumed turns load the conversation tail from the session store."""
    import datetime as dt

    from coworker.agent.runtime import OpenAICompatibleStreamRuntime
    from coworker.sessions import Session, SessionMessage, SessionStore

    with tempfile.TemporaryDirectory() as d:
        store = SessionStore(Path(d))
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        session = Session(id="sess-resume", title="t", project_id="p1", created_at=now, updated_at=now)
        session.messages = [
            SessionMessage(id="m1", role="user", content="deploy it", created_at=now),
            SessionMessage(id="m2", role="assistant", content="running deploy now", created_at=now),
        ]
        store.save(session)

        runtime = object.__new__(OpenAICompatibleStreamRuntime)
        runtime.session_store = store
        loaded = runtime._recent_session_messages("sess-resume")
        assert loaded == [
            {"type": "user", "content": "deploy it"},
            {"type": "assistant", "content": "running deploy now"},
        ]
        assert runtime._recent_session_messages("nope") == []


def test_tool_summary_handles_raw_parts():
    """Tool summary dedupes raw tool_start/tool_end parts and keeps the status."""
    parts = [
        {"type": "tool_start", "id": "t1", "name": "read_file", "input": "a"},
        {"type": "tool_end", "id": "t1", "name": "read_file", "status": "success"},
        {"type": "tool_start", "id": "t2", "name": "run_command", "input": "b"},
        {"type": "tool_end", "id": "t2", "name": "run_command", "status": "error"},
        {"type": "tool", "name": "search_files", "status": "success"},
    ]
    summary = skill_review._tool_summary(parts)
    assert "read_file (success)" in summary
    assert "run_command (error)" in summary
    assert "search_files" in summary
    assert summary.count("read_file") == 1, summary
    assert summary.count("run_command") == 1, summary


def test_maybe_self_review_gate_accepts_raw_tool_parts(manager):
    """The scheduling gate must treat raw tool_start/tool_end parts as tool use
    (regression: the gate previously looked for merged ``tool`` events that only
    exist after the review is already scheduled)."""
    from coworker.agent.runtime import OpenAICompatibleStreamRuntime

    runtime = object.__new__(OpenAICompatibleStreamRuntime)
    runtime.skill_manager = main.skill_manager
    runtime.llm = _FakeLLM(json.dumps({"action": "create", "name": "gate-skill", "content": SKILL_CONTENT.format(name="gate-skill", desc="gate check")}))
    runtime.session_store = None
    runtime.data_dir = main.settings.data_dir

    async def run():
        runtime._maybe_self_review(
            "sess-gate",
            [],
            [
                {"type": "tool_start", "id": "t1", "name": "read_file", "input": "x"},
                {"type": "tool_end", "id": "t1", "name": "read_file", "status": "success"},
            ],
        )
        await asyncio.sleep(0.3)

    asyncio.run(run())
    names = [p["name"] for p in manager.pending()]
    assert "gate-skill" in names, names


# ── auto-skills review settings ────────────────────────────────────

def _write_review_settings(**patch):
    import json as _json

    existing = {}
    path = main.settings.data_dir / ".coworker_settings.json"
    if path.exists():
        existing = _json.loads(path.read_text() or "{}")
    existing["skill_review"] = {**existing.get("skill_review", {}), **patch}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(existing, ensure_ascii=False), encoding="utf-8")


def test_settings_api_defaults_and_save(client):
    r = client.get("/api/skill-review/settings")
    assert r.status_code == 200
    assert r.json() == {"aggressiveness": "cautious", "approval_required": True}

    r = client.post("/api/skill-review/settings", json={"aggressiveness": "active", "approval_required": False})
    assert r.status_code == 200
    assert r.json() == {"aggressiveness": "active", "approval_required": False}

    r = client.get("/api/skill-review/settings")
    assert r.json() == {"aggressiveness": "active", "approval_required": False}

    # invalid aggressiveness is ignored
    r = client.post("/api/skill-review/settings", json={"aggressiveness": "bogus"})
    assert r.json()["aggressiveness"] == "active"


def test_system_prompt_per_aggressiveness():
    assert "ACTIVE" in _build_system_prompt("active")
    assert "CAUTIOUS" in _build_system_prompt("cautious")
    assert "PASSIVE" in _build_system_prompt("passive")
    assert "CAUTIOUS" in _build_system_prompt("unknown")


def test_apply_agent_skill_direct_no_draft(manager):
    """approval_required=false writes directly as an active skill (no queue)."""
    r = manager.apply_agent_skill("create", "direct-skill", SKILL_CONTENT.format(name="direct-skill", desc="direct"), sources=["session:x"])
    assert r["status"] == "ok", r
    assert manager.pending() == [], "no draft staged"
    entry = manager.get("direct-skill")
    assert entry is not None and entry.status == "active" and entry.provenance == "agent"
    assert "direct-skill" in {s.name for s in manager.injection_list()}

    # update overwrites in place
    r = manager.apply_agent_skill("update", "direct-skill", SKILL_CONTENT.format(name="direct-skill", desc="direct v2"))
    assert r["status"] == "ok", r
    assert "direct v2" in manager.get("direct-skill").file_path.read_text()

    # create collision rejected
    r = manager.apply_agent_skill("create", "direct-skill", SKILL_CONTENT.format(name="direct-skill", desc="dup"))
    assert r["status"] == "error"


def test_review_approval_off_applies_directly(manager):
    """run_skill_review with approval_required=False applies, not stages."""
    llm = _FakeLLM(json.dumps({"action": "create", "name": "auto-skill", "content": SKILL_CONTENT.format(name="auto-skill", desc="auto")}))
    parts = [{"type": "tool_start", "id": "t1", "name": "run_command", "input": "x"}, {"type": "tool_end", "id": "t1", "name": "run_command", "status": "success"}]
    result = _run(run_skill_review(llm, manager, session_id="s5", messages=[], parts=parts, approval_required=False))
    assert result["applied"] is True and result["staged"] is False
    assert manager.pending() == []
    assert manager.get("auto-skill") is not None


def test_passive_aggressiveness_skips_review(manager):
    """aggressiveness=passive disables the review loop entirely."""
    from coworker.agent.runtime import OpenAICompatibleStreamRuntime

    _write_review_settings(aggressiveness="passive")
    runtime = object.__new__(OpenAICompatibleStreamRuntime)
    runtime.skill_manager = main.skill_manager
    runtime.llm = _FakeLLM(json.dumps({"action": "create", "name": "should-not-exist", "content": SKILL_CONTENT.format(name="should-not-exist", desc="nope")}))
    runtime.session_store = None
    runtime.data_dir = main.settings.data_dir

    async def run():
        runtime._maybe_self_review(
            "sess-passive",
            [],
            [{"type": "tool_start", "id": "t1", "name": "run_command", "input": "x"}],
        )
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert manager.pending() == []
    assert manager.get("should-not-exist") is None
    # restore defaults so this mutation doesn't leak into other tests
    _write_review_settings(aggressiveness="cautious", approval_required=True)


def test_agent_description_with_colon_space_parses(manager):
    """Regression: an agent-written description containing ': ' (e.g.
    'project: coworker') is invalid YAML plain-scalar and used to make the
    whole frontmatter unparseable → misleading 'description is required'."""
    content = (
        "---\n"
        "name: colon-desc\n"
        "description: 加 front matter（project: coworker, author: leon）。掃描 .md 檔。\n"
        "version: \"1.0.0\"\n"
        "---\n\n"
        "## When to Use\n## Procedure\n1. x\n## Pitfalls\n## Verification\n"
    )
    result = manager.stage_skill_draft("colon-desc", content, sources=["session:s1"])
    assert result["status"] == "ok", result
    assert len(manager.pending()) == 1
    # and the value is preserved correctly
    from coworker.skills.skills import parse_frontmatter
    fm, _ = parse_frontmatter(content)
    assert "project: coworker" in fm["description"]
