"""Self-contained sanity checks for the memory subsystem (v2 library tree).

Runs with the venv python directly (no pytest needed)::

    cd backend && ./venv/bin/python coworker/memory/selftest.py

Covers layout path safety, registry skeletons, Markdown-block CRUD, discovery
injection order, and prompt budget warning. Exits non-zero on the first
failure.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coworker.memory.layout import memory_dir_from_created_at, resolve_rel_path, sanitize_name
from coworker.memory.memory_file import render_blocks, split_blocks
from coworker.memory.memory_prompt import format_memory_prompt
from coworker.memory.memory_store import MemoryError, MemoryStore
from coworker.memory.memory_discovery import MemoryScanner
from coworker.memory.registry import MemoryRegistry
from coworker.memory.transfer import apply_import, export_memory, preview_import
from coworker.org import ORG_MODE_MULTI, ORG_MODE_SINGLE, OrgError, OrgStore, OrgTeam, OrgAgent, default_org
from coworker.sessions import SessionStore

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        CHECKS.append(f"PASS {name}")
    else:
        CHECKS.append(f"FAIL {name}: {detail}")


def main() -> int:
    # --- layout: timestamps, sanitize, path safety -------------------------
    check(
        "timestamp from iso",
        memory_dir_from_created_at("2026-08-12T10:00:00+00:00") == "20260812100000",
        memory_dir_from_created_at("2026-08-12T10:00:00+00:00"),
    )
    check("timestamp fallback", len(memory_dir_from_created_at("garbage")) == 14)
    check("timestamp + mode suffix distinct", memory_dir_from_created_at("2026-08-12T10:00:00+00:00") + "_single" != memory_dir_from_created_at("2026-08-12T10:00:00+00:00") + "_multi")
    check("timestamp + mode suffix format", memory_dir_from_created_at("2026-08-12T10:00:00+00:00") + "_multi" == "20260812100000_multi")
    check("sanitize spaces", sanitize_name("  my project!  ") == "my_project_")
    check("sanitize empty", sanitize_name("") == "untitled")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "memory"
        root.mkdir()
        try:
            resolve_rel_path(root, "../evil.md")
            check("escape rejected", False, "no exception on .. escape")
        except ValueError:
            check("escape rejected", True)
        try:
            resolve_rel_path(root, "a/../../evil.md")
            check("nested escape rejected", False)
        except ValueError:
            check("nested escape rejected", True)
        ok = resolve_rel_path(root, "proj/BASE/note.md")
        check("valid rel resolves", str(ok) == str((root / "proj" / "BASE" / "note.md").resolve()))

    # --- blocks: split/render round trip -----------------------------------
    blocks = ["first block", "second block"]
    rendered = render_blocks(blocks)
    check("render joins with blank line", rendered == "first block\n\nsecond block\n")
    check("split round-trip", split_blocks(rendered) == blocks, str(split_blocks(rendered)))
    check("split skips blanks", split_blocks("a\n\n\n\nb\n") == ["a", "b"])

    # --- registry skeletons -------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        registry = MemoryRegistry(data_dir)
        registry.ensure_root()
        check("root created", (data_dir / "memory").is_dir())
        for name in ("MEMORY.md", "USER.md", "AGENT.md"):
            check(f"system file {name}", (data_dir / "memory" / name).is_file())

        project_path = registry.ensure_project("20260812100000")
        check("BASE exists", (project_path / "BASE").is_dir())
        check(
            "BASE template EXAMPLE.md",
            (project_path / "BASE" / "EXAMPLE.md").is_file(),
        )
        check(
            "PROJECT subdir exists",
            (project_path / "BASE" / "PROJECT" / "GOALS.md").is_file(),
        )
        agent_path = registry.ensure_agent(project_path, "default_agent")
        for name in ("SOUL.md", "AGENT.md", "MEMORY.md"):
            check(f"agent file {name}", (agent_path / "BASE" / name).is_file())
        check("SESSIONS dir", (agent_path / "SESSIONS").is_dir())
        check("registry idempotent", registry.ensure_root() == registry.ensure_root())

        with tempfile.TemporaryDirectory() as legacy_tmp:
            legacy_registry = MemoryRegistry(Path(legacy_tmp))
            legacy_registry.ensure_root()
            legacy_project = legacy_registry.project_dir("20990101000000")
            legacy_project.mkdir(parents=True, exist_ok=True)
            legacy_base = legacy_project / "BASE"
            legacy_base.mkdir(parents=True, exist_ok=True)
            (legacy_base / "project.md").write_text("# project.md\n", encoding="utf-8")
            (legacy_base / "game_rule.md").write_text("# game_rule.md\n", encoding="utf-8")
            (legacy_base / "custom_note.md").write_text("real user content\n", encoding="utf-8")
            legacy_proj = legacy_base / "PROJECT"
            legacy_proj.mkdir(parents=True, exist_ok=True)
            (legacy_proj / "goals.md").write_text(
                "# 项目高层级目标\n\n（由系统生成与维护 — 记录项目的高层级目标）\n", encoding="utf-8"
            )
            (legacy_proj / "context.md").write_text("user edited context\n", encoding="utf-8")
            legacy_registry.ensure_project("20990101000000")
            check("legacy skeleton base pruned", not (legacy_base / "project.md").exists())
            check("legacy game_rule pruned", not (legacy_base / "game_rule.md").exists())
            check("legacy user content kept", (legacy_base / "custom_note.md").is_file())
            check("GOALS recreated", (legacy_proj / "GOALS.md").is_file())
            goals_content = (legacy_proj / "GOALS.md").read_text(encoding="utf-8")
            check("GOALS skeleton content", "# 项目高层级目标" in goals_content, goals_content[:40])
            check("legacy edited context kept", (legacy_proj / "context.md").is_file())

        store = MemoryStore(data_dir / "memory")

        # --- store block CRUD ------------------------------------------------
        rel = "20260812100000/default_agent/BASE/MEMORY.md"
        check("write_file creates parent", store.write_file(rel, "# M\n").path.is_file())
        store.write_file(rel, "# M\n")
        added = store.add_block(rel, "port 9527 is the backend")
        check("add_block appended", "port 9527 is the backend" in added, str(added))
        store.add_block(rel, "agent binds 0.0.0.0")
        try:
            store.add_block(rel, "port 9527 is the backend")
            check("duplicate rejected", False, "no exception on duplicate")
        except MemoryError:
            check("duplicate rejected", True)
        replaced = store.replace_block(rel, "port 9527", "port 9527 (FastAPI) is the backend")
        check("replace_block swapped", any("FastAPI" in b for b in replaced), str(replaced))
        try:
            store.replace_block(rel, "no-such-target", "x")
            check("replace missing raises", False)
        except MemoryError:
            check("replace missing raises", True)
        removed = store.remove_block(rel, "0.0.0.0")
        check("remove_block works", "port 9527 (FastAPI) is the backend" in removed, str(removed))
        check("clear empties", store.clear_file(rel) == [])

        # --- whole-file write + read ------------------------------------------
        store.write_file("USER.md", "语言偏好：中文\n\n项目类型：全栈\n")
        mf = store.read_file("USER.md")
        check("read_file round-trip", mf.content == "语言偏好：中文\n\n项目类型：全栈\n")
        check("remove_file deletes", store.remove_file("USER.md") is True)
        check("remove_file missing", store.remove_file("USER.md") is False)

        # --- discovery + injection precedence --------------------------------
        store.write_file("MEMORY.md", "system fact\n")
        store.add_block(rel, "agent fact")
        scanner = MemoryScanner(data_dir / "memory")
        library = scanner.scan(include_missing=True)
        check("system nodes found", len(library.system) == 3, str(len(library.system)))
        store.write_file("team_playbook.md", "user root file\n")
        library2 = scanner.scan(include_missing=False)
        check("user root file discovered", any(n.name == "team_playbook.md" for n in library2.system))
        check("root file injected", any(n.name == "team_playbook.md" for n in library2.injected()))
        check("project found", len(library.projects) == 1, str(len(library.projects)))
        project = library.projects[0]
        check("project BASE nodes", len(project.base) >= 1, str(len(project.base)))
        check("project context nodes", len(project.project) == 2, str(len(project.project)))
        check("project agent found", len(project.agents) == 1, str(len(project.agents)))
        agent = project.agents[0]
        check("agent memory node", agent.memory is not None)
        check("agent sessions empty", agent.sessions == [])

        injected = library.injected(project_dir="20260812100000", agent="default_agent")
        kinds = [n.kind for n in injected]
        check("system injected", kinds[0] == "system", str(kinds))
        check("base before project_context", kinds.index("base_file") < kinds.index("project_file"), str(kinds))
        check("agent memory injected", "agent_file" in kinds, str(kinds))
        check("agent memory content", any("agent fact" in n.content for n in injected))

        # --- on-demand sessions: SESSIONS not resident, readable via store -----
        sess_rel = "20260812100000/default_agent/SESSIONS/2026-01-01.md"
        store.write_file(sess_rel, "# Session\n\n回顾了记忆注入的优先级。\n")
        lib_s = scanner.scan()
        injected_s = lib_s.injected(project_dir="20260812100000", agent="default_agent")
        check("sessions NOT injected resident", all(n.kind != "session_file" for n in injected_s), str([n.kind for n in injected_s]))
        check("sessions readable on demand", "回顾" in store.read_file(sess_rel).content)

        # --- prompt budget + ordering ----------------------------------------
        prompt = format_memory_prompt(injected, char_limit=5)
        check("budget warning present", "<budget_warning>" in prompt)
        check("files rendered", "<file kind=" in prompt)
        check("empty prompt", format_memory_prompt([], char_limit=2000) == "")

        # --- budget is HARD: resident block truncated, data intact on disk ----
        store.clear_file(rel)
        store.add_block(rel, "z" * 5000)
        over = format_memory_prompt(library.injected(project_dir="20260812100000", agent="default_agent"), char_limit=100)
        check("budget warning present when truncated", "<budget_warning>" in over)
        check("resident block truncated", len(over) < 5000, f"len={len(over)}")
        check("on-demand pointer present", "memory_read" in over)
        check("file intact on disk", "z" * 5000 in store.read_file(rel).content)

        # --- non-agent folder classification ---------------------------------
        mem_root = store.root
        folder = mem_root / "20260812100000" / "notes"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "ideas.md").write_text("folded idea about blue widgets\n", encoding="utf-8")
        lib3 = scanner.scan(include_missing=False)
        p3 = lib3.projects[0]
        check("user folder not an agent", len(p3.agents) == 1, str([a.name for a in p3.agents]))
        check("user folder surfaced", any(f.name == "notes" for f in p3.folders), str([f.name for f in p3.folders]))
        notes = next(f for f in p3.folders if f.name == "notes")
        check("folder files collected", any(n.name == "ideas.md" for n in notes.files))
        injected_kinds = [n.kind for n in lib3.injected(project_dir="20260812100000", agent="default_agent")]
        check("folder files not injected", "folder_file" not in injected_kinds, str(injected_kinds))

        # --- full-text search -------------------------------------------------
        store.write_file(rel, "agent fact about blue widgets\n")
        results = scanner.search("blue")
        rels = [r["rel"] for r in results]
        check("search finds agent memory", rel in rels, rels)
        check("search finds folder file", any("notes/ideas.md" in r for r in rels), rels)
        check("search snippet present", all(r["snippet"] for r in results))
        check("search no match", scanner.search("no-such-term-xyz") == [])

        # --- move -------------------------------------------------------------
        moved_rel = store.move_file("20260812100000/notes/ideas.md", "20260812100000/BASE/folded_ideas.md")
        check("move returns new rel", moved_rel == "20260812100000/BASE/folded_ideas.md")
        check("move source gone", not (folder / "ideas.md").exists())
        check("move dest exists", (mem_root / moved_rel).is_file())
        try:
            store.move_file(moved_rel, moved_rel)
            check("move same target rejected", False)
        except MemoryError:
            check("move same target rejected", True)

        # --- trash (injected dir) --------------------------------------------
        trash_dir = Path(tmp) / "trash"
        store.write_file("USER.md", "trash me\n")
        check("trash file", store.remove_file("USER.md", trash_dir=trash_dir) is True)
        check("trash landed", (trash_dir / "USER.md").is_file())
        check("trash source gone", not (mem_root / "USER.md").exists())
        store.write_file("USER.md", "again\n")
        store.remove_file("USER.md", trash_dir=trash_dir)
        check("trash collision suffixed", (trash_dir / "USER 2.md").is_file())

        # --- export / import round-trip ---------------------------------------
        work_dir = Path(tmp) / "work"
        export = export_memory(mem_root, work_dir, scope="all", project_dirs=[])
        check("export zip created", Path(export["path"]).is_file())
        preview = preview_import(mem_root, work_dir, export["path"])
        check("preview token", bool(preview["token"]))
        check("preview lists files", len(preview["files"]) > 0, str(len(preview["files"])))
        check("preview conflict flags", all("exists" in f for f in preview["files"]))
        decisions = {f["rel"]: "skip" for f in preview["files"] if f["exists"]}
        applied = apply_import(mem_root, work_dir, preview["token"], decisions)
        check("apply no-op on skip", applied["imported"] == 0 and applied["skipped"] >= 0, str(applied))
        preview2 = preview_import(mem_root, work_dir, export["path"])
        decisions2 = {f["rel"]: "overwrite" for f in preview2["files"]}
        applied2 = apply_import(mem_root, work_dir, preview2["token"], decisions2)
        check("apply overwrite", applied2["overwritten"] >= 1, str(applied2))

    # --- org registry + team memory injection ------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "memory"
        root.mkdir(parents=True)
        org_store = OrgStore(root)

        # default org is single-mode (new projects default to a single agent)
        check("default org mode is single", default_org().mode == ORG_MODE_SINGLE, default_org().mode)

        # multi-mode org + default_agent
        org = default_org()
        org.mode = ORG_MODE_MULTI
        org.agents.append(OrgAgent(id="default_agent", name="default_agent", role="team lead"))
        org_store.save("proj1", org)
        check("org save/load round trip", org_store.load("proj1").agents[0].name == "default_agent")
        check("org keeps multi mode", org_store.load("proj1").mode == ORG_MODE_MULTI)

        # validation: duplicate agent rejected
        try:
            org2 = org_store.load("proj1")
            org2.agents.append(OrgAgent(id="default_agent", name="x"))
            org_store.save("proj1", org2)
            check("org duplicate agent rejected", False)
        except OrgError:
            check("org duplicate agent rejected", True)

        # add members + team; lead auto-assigned
        org_store.upsert_agent("proj1", OrgAgent(id="coder", name="coder", role="developer"))
        org_store.upsert_team("proj1", OrgTeam(id="backend", name="后端", lead="coder"))
        org3 = org_store.load("proj1")
        coder = next(a for a in org3.agents if a.id == "coder")
        check("team lead auto-assigned", coder.team_id == "backend", coder.team_id)
        check("team ancestors", org_store.team_ancestors(org3, "backend") == ["backend"])
        check("agents depth root", org_store.agents_depth(org3, "coder") == 1)
        check("roster lists active", any(m["id"] == "coder" and m["team"] == "后端" for m in org_store.roster(org3)))

        # depth limit: chain deeper than max_depth rejected
        org_store.upsert_agent("proj1", OrgAgent(id="a1", name="a1", parent="coder"))
        try:
            org_store.upsert_agent("proj1", OrgAgent(id="a2", name="a2", parent="a1"))
            org_store.upsert_agent("proj1", OrgAgent(id="a3", name="a3", parent="a2"))
            check("org depth limit rejected", False)
        except OrgError:
            check("org depth limit rejected", True)

        # deletion constraints: cannot delete team lead / referenced parent
        try:
            org_store.remove_agent("proj1", "coder")
            check("org delete lead rejected", False)
        except OrgError:
            check("org delete lead rejected", True)
        try:
            org_store.remove_team("proj1", "backend")
            check("org delete non-empty team rejected", False)
        except OrgError:
            check("org delete non-empty team rejected", True)

        # cycle detection via parent chain
        try:
            org4 = org_store.load("proj1")
            org4.agents.append(OrgAgent(id="b1", name="b1", parent="b2"))
            org4.agents.append(OrgAgent(id="b2", name="b2", parent="b1"))
            org_store.save("proj1", org4)
            check("org cycle rejected", False)
        except OrgError:
            check("org cycle rejected", True)

        # team memory + injected() includes team + ancestor team files
        registry = MemoryRegistry(tmp)
        project_path = registry.ensure_project("proj1")
        registry.ensure_agent(project_path, "coder")
        team_dir = project_path / "teams" / "backend"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "GOALS.md").write_text("# GOALS\n\n加速交付\n", encoding="utf-8")
        library = MemoryScanner(root).scan()
        nodes = library.injected(project_dir="proj1", agent="coder", team_ids=["backend"])
        rels = [n.rel for n in nodes]
        check("team goals injected", "proj1/teams/backend/GOALS.md" in rels, str(rels))
        nodes_no_team = library.injected(project_dir="proj1", agent="coder")
        check("no team injection without team_ids", all("teams/" not in n.rel for n in nodes_no_team))

        # discover exposes teams in project view
        view = next(p for p in library.projects if p.name == "proj1")
        check("discover exposes teams", len(view.teams) == 1 and view.teams[0].id == "backend", str(view.teams))
        check("team goals scanned", view.teams[0].goals is not None and "加速交付" in (view.teams[0].goals.content or ""))

        # single-mode scan strips teams + extra agents (only default_agent surfaces)
        single_lib = MemoryScanner(root).scan(mode=ORG_MODE_SINGLE)
        single_view = next(p for p in single_lib.projects if p.name == "proj1")
        check("single scan hides teams", len(single_view.teams) == 0, str(single_view.teams))
        single_ids = {a.id for a in single_view.agents}
        check("single scan strips extra agents", not ({"coder", "qa", "worker"} & single_ids), str(single_ids))
        check("single scan never surfaces default_agent in proj1", "default_agent" not in single_ids, str(single_ids))

        # discover agent view: id = dir name (stable), name = display name (renamed later)
        av = next(a for a in view.agents if a.id == "coder")
        check("discover agent has id", av.id == "coder", av.id)
        check("discover agent name display", av.name == "coder" or av.name == "首席编码", av.name)
        injected_after = library.injected(project_dir="proj1", agent="coder")
        check("injected matches by id", any(n.kind == "agent_file" for n in injected_after))

        # renamed agent: discover id unchanged, display name reflects rename, injected still hits by id
        org_store.upsert_agent("proj1", OrgAgent(id="coder", name="首席编码", role="developer", team_id="backend"))
        library_rn = MemoryScanner(
            root,
            agent_name_resolver=lambda project_dir, agent_id: (
                next((m["name"] for m in org_store.members_for(org_store.load(project_dir)) if m["id"] == agent_id), agent_id)
            ),
        ).scan()
        view_rn = next(p for p in library_rn.projects if p.name == "proj1")
        av_rn = next(a for a in view_rn.agents if a.id == "coder")
        check("discover id stable after rename", av_rn.id == "coder", av_rn.id)
        check("discover name reflects rename", av_rn.name == "首席编码", av_rn.name)
        injected_rn = library_rn.injected(project_dir="proj1", agent="coder")
        check("injected by id after rename", any(n.kind == "agent_file" for n in injected_rn))
        # fallback: no resolver -> name falls back to id
        av_nr = next(a for a in next(p for p in MemoryScanner(root).scan().projects if p.name == "proj1").agents if a.id == "coder")
        check("discover name falls back to id", av_nr.name == "coder", av_nr.name)

        # migration: org missing but agent dirs exist -> backfilled via discover path
        os2 = OrgStore(root)
        check("org missing initially", not os2.exists("proj2"))
        # simulate: create agent dir without org, then ensure migration helper
        p2 = registry.ensure_project("proj2")
        registry.ensure_agent(p2, "default_agent")
        registry.ensure_agent(p2, "worker")
        from coworker.memory.memory_manager import MemoryManager

        mgr = MemoryManager(tmp, memory_dir=root, config=type("C", (), {"enabled": True, "char_limit": 100000, "auto_extract": False, "nudge_interval": 0, "extract_model": ""})())
        mgr.org_store = os2
        library2 = MemoryScanner(root).scan()
        view2 = next(p for p in library2.projects if p.name == "proj2")
        org5 = os2.load("proj2")
        for aview in view2.agents:
            if not any(a.id == aview.id for a in org5.agents):
                org5.agents.append(OrgAgent(id=aview.id, name=aview.name, role="", parent="", team_id="", status="active"))
        os2.save("proj2", org5)
        migrated = os2.load("proj2")
        ids = {a.id for a in migrated.agents}
        check("migration backfills agent dirs", {"default_agent", "worker"} <= ids, str(ids))

        # single-mode scan on a project with default_agent + worker keeps only default_agent
        single_lib2 = MemoryScanner(root).scan(mode=ORG_MODE_SINGLE)
        single_proj2 = next(p for p in single_lib2.projects if p.name == "proj2")
        check("single scan keeps only default_agent", [a.id for a in single_proj2.agents] == ["default_agent"], str([a.id for a in single_proj2.agents]))

        # members_for includes disabled; roster filters to active
        org6 = org_store.load("proj1")
        org_store.upsert_agent("proj1", OrgAgent(id="qa", name="qa", role="reviewer", status="disabled"))
        org6 = org_store.load("proj1")
        all_ids = {m["id"] for m in org_store.members_for(org6)}
        active_ids = {m["id"] for m in org_store.roster(org6)}
        check("members_for includes disabled", "qa" in all_ids and "coder" in all_ids, str(all_ids))
        check("roster filters disabled", "qa" not in active_ids and "coder" in active_ids, str(active_ids))
        qa_card = next(m for m in org_store.members_for(org6) if m["id"] == "qa")
        check("member card has status", qa_card["status"] == "disabled", str(qa_card))

        # rename: only display name changes, id stays (memory dir / session key intact)
        org_store.upsert_agent("proj1", OrgAgent(id="coder", name="首席编码", role="developer", team_id="backend"))
        org7 = org_store.load("proj1")
        renamed = next(a for a in org7.agents if a.id == "coder")
        check("rename updates display name", renamed.name == "首席编码", renamed.name)
        check("rename keeps id", renamed.id == "coder", renamed.id)
        check("rename keeps team", renamed.team_id == "backend", renamed.team_id)
        roster_names = {m["id"]: m["name"] for m in org_store.roster(org7)}
        check("roster shows new name", roster_names.get("coder") == "首席编码", str(roster_names))

        # empty-name rename rejected
        try:
            org8 = org_store.load("proj1")
            for a in org8.agents:
                if a.id == "coder":
                    a.name = "   "
            org_store.save("proj1", org8)
            check("org empty name rejected", False)
        except OrgError:
            check("org empty name rejected", True)

        # delete_by_agent only removes sessions bound to the given agent
        sstore = SessionStore(Path(tmp) / "sessions")
        sstore.create("s1", project_id="proj1", agent_id="coder")
        sstore.create("s2", project_id="proj1", agent_id="default_agent")
        sstore.create("s3", project_id="proj1", agent_id="coder")
        sstore.create("s4", project_id="other", agent_id="coder")
        removed = sstore.delete_by_agent("proj1", "coder")
        check("delete_by_agent removes bound sessions", removed == 2, str(removed))
        remaining_titles = {s["title"] for s in sstore.list_sessions("proj1")}
        check("delete_by_agent keeps other agent sessions", remaining_titles == {"s2"}, str(remaining_titles))
        other_titles = {s["title"] for s in sstore.list_sessions("other")}
        check("delete_by_agent scoped to project", other_titles == {"s4"}, str(other_titles))

        # --- write-side discipline: raw-paste guard ---------------------------
        from coworker.agent.core import _looks_like_raw_paste

        check("paste guard rejects long quoted paste", _looks_like_raw_paste("> 引用\n\n> 更多\n\n" + "长文本" * 200))
        check("paste guard rejects very long block", _looks_like_raw_paste("x" * 1300))
        check("paste guard allows concise fact", not _looks_like_raw_paste("用户偏好中文回复，前端用 npm run build 构建。"))

        # --- dream merge guardrails (rule-based, no LLM verify) ---------------
        import asyncio

        from coworker.memory.auto_extract import run_extract_and_merge

        class _FakeLLM:
            def __init__(self, payload: str):
                self._payload = payload

            async def ainvoke(self, messages):
                return type("R", (), {"content": self._payload})()

        async def _run(llm, existing, new_fact):
            return await run_extract_and_merge(
                llm=llm,
                messages=[{"role": "user", "content": f"新信息：{new_fact}"}],
                existing_blocks=split_blocks(existing),
                session_id="s",
                max_prior_loss=0.25,
                max_total_chars=4000,
            )

        # Guardrail: too many prior entries dropped -> rejected (blocks None).
        keep_most = '{"blocks": ["用户偏好中文"]}'
        r_keep = asyncio.run(_run(_FakeLLM(keep_most), "用户偏好中文\n\n端口 9527\n\n用 pnpm", "偏好中文"))
        check("consolidation rejects heavy loss", r_keep["blocks"] is None, r_keep["note"])

        # Guardrail: unparseable -> rejected.
        r_bad = asyncio.run(_run(_FakeLLM("not json at all"), "a\n\nb", "c"))
        check("consolidation rejects unparseable", r_bad["blocks"] is None, r_bad["note"])

        # Success path: preserves prior entries, integrates candidate.
        ok_payload = '{"blocks": ["用户偏好中文", "端口 9527", "用户使用 pnpm"], "new": ["用户使用 pnpm"]}'
        r_ok = asyncio.run(_run(_FakeLLM(ok_payload), "用户偏好中文\n\n端口 9527", "用户使用 pnpm"))
        check("consolidation succeeds", r_ok["blocks"] is not None, r_ok["note"])
        check("consolidation integrates candidate", any("pnpm" in b for b in r_ok["blocks"] or []), str(r_ok["blocks"]))

        # Guardrail leniency: pure headings are not facts — dropping them must
        # not count as losing memory.
        heading_payload = '{"blocks": ["用户偏好中文", "端口 9527", "用 pnpm"]}'
        r_h = asyncio.run(_run(_FakeLLM(heading_payload), "# MEMORY\n\n用户偏好中文\n\n端口 9527\n\n用 pnpm", "偏好中文"))
        check("consolidation tolerates dropped headings", r_h["blocks"] is not None, r_h["note"])

        # Guardrail leniency: merged duplicates (substring containment) count as kept.
        dup_prior = "项目背景：Hub 平台\n\n项目：Hub 平台，目录 /Users/x/\n\n用户偏好：中文"
        merged_payload = '{"blocks": ["项目背景：Hub 平台，目录 /Users/x/", "用户偏好：中文"]}'
        r_d = asyncio.run(_run(_FakeLLM(merged_payload), dup_prior, "用户偏好：中文"))
        check("consolidation tolerates merge", r_d["blocks"] is not None, r_d["note"])

        # Guardrail leniency: a reworded entry (fuzzy match) still counts as kept.
        reword_payload = '{"blocks": ["用户偏好中文回复，前端用 npm 构建", "端口 9527"]}'
        r_r = asyncio.run(_run(_FakeLLM(reword_payload), "用户偏好中文回复，前端用 npm run build 构建。\n\n端口 9527", "用户偏好中文回复"))
        check("consolidation tolerates rewording", r_r["blocks"] is not None, r_r["note"])

        # --- _recent_transcript: budget-aware tail building --------------------
        from coworker.memory.auto_extract import _parse_blocks_and_new, _recent_transcript

        # Local models often return single-quoted (Python-style) JSON; parsing
        # must tolerate both quote styles and both shapes (dict / bare array).
        check("parser dict double-quoted", _parse_blocks_and_new('{"blocks": ["a", "b"], "new": ["a"]}') == (["a", "b"], ["a"]), str(_parse_blocks_and_new('{"blocks": ["a", "b"], "new": ["a"]}')))
        check("parser dict single-quoted", _parse_blocks_and_new("{'blocks': ['a', 'b']}") == (["a", "b"], []), str(_parse_blocks_and_new("{'blocks': ['a', 'b']}")))
        check("parser bare array", _parse_blocks_and_new('["a", "b"]') == (["a", "b"], []), str(_parse_blocks_and_new('["a", "b"]')))
        check("parser garbage", _parse_blocks_and_new("garbage") is None, str(_parse_blocks_and_new("garbage")))

        small = [
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复一"},
            {"role": "user", "content": "第二条"},
        ]
        t_small = _recent_transcript(small)
        check("transcript keeps all small messages", "第一条" in t_small and "第二条" in t_small and "回复一" in t_small, t_small)
        check("transcript newest last", t_small.strip().endswith("第二条"), t_small)
        check("transcript empty input", _recent_transcript([]) == "")

        # A huge newest message must be kept, clipped to the budget.
        huge_newest = [{"role": "assistant", "content": "A" * 30_000}]
        t_huge = _recent_transcript(huge_newest, max_chars=1000)
        check("huge newest clipped to budget", len(t_huge) == 1000, str(len(t_huge)))
        check("huge newest keeps tail", t_huge.endswith("A" * 200), t_huge[-50:])

        # A huge older message must NOT truncate the transcript to nothing: the
        # newest messages stay and the overflowing older one fills the remainder.
        mixed = [
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "B" * 30_000},   # huge older reply
            {"role": "user", "content": "最新消息"},
        ]
        t_mixed = _recent_transcript(mixed, max_chars=2000)
        check("mixed keeps newest message", "最新消息" in t_mixed, t_mixed[-80:])
        check("mixed keeps newer older reply tail", "ASSISTANT: BBB" in t_mixed, t_mixed[-80:])
        check("mixed fills the whole budget", len(t_mixed) >= 1900, str(len(t_mixed)))

        # Regression: a real workload whose replies are each ~18k chars must NOT
        # collapse to just the newest user message (the original bug).
        realistic = [
            {"role": "user", "content": "早期用户消息"},
            {"role": "assistant", "content": "C" * 18_000},
            {"role": "user", "content": "现在的需求"},
        ]
        t_real = _recent_transcript(realistic)
        check("realistic transcript uses budget", len(t_real) >= 11_000, str(len(t_real)))
        check("realistic keeps latest user", "现在的需求" in t_real, t_real[-60:])

        # --- end-to-end dream: ONE merged LLM call -> MEMORY.md write ---------
        from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager

        class _DispatchLLM:
            """Returns the merged-blocks object for the dream merge prompt and a
            JSON array for the session notetaker, selected by the prompt text."""

            def __init__(self):
                self.model_name = "fake"
                self.calls = []

            async def ainvoke(self, messages):
                text = str(messages[0].content)
                self.calls.append(text[:60])
                if "long-term memory keeper" in text:
                    return type("R", (), {"content": '{"blocks": ["自动提取的事实一", "自动提取的事实二"], "new": ["自动提取的事实一", "自动提取的事实二"]}'})()
                if "session notetaker" in text:
                    return type("R", (), {"content": '["本周完成了记忆功能排查", "修复了转写截断问题"]'})()
                return type("R", (), {"content": "[]"})()

        with tempfile.TemporaryDirectory() as dream_tmp:
            d_root = Path(dream_tmp) / "memory"
            d_mgr = MemoryManager(
                Path(dream_tmp),
                memory_dir=d_root,
                config=MemoryConfig(enabled=True, auto_extract=True),
            )
            d_view = d_mgr.for_project("20260812100000", DEFAULT_AGENT)
            d_llm = _DispatchLLM()
            d_view.configure_extractor(
                llm_factory=lambda: d_llm,
                transcript_provider=lambda sid: [{"role": "user", "content": "第一条"}, {"role": "assistant", "content": "第二条"}],
            )

            asyncio.run(d_view._dream_async("dream-s1"))
            # ONE merged extract+merge call (no extract→stage→consolidate→verify chain).
            check("dream ran a single merge call", sum("long-term memory keeper" in c for c in d_llm.calls) == 1, str(d_llm.calls))
            mem_raw = d_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/BASE/MEMORY.md")
            check("dream wrote MEMORY.md", "自动提取的事实一" in mem_raw and "自动提取的事实二" in mem_raw, mem_raw)

        # --- session summary -> SESSIONS/<date>.md, once per session/day -------
        with tempfile.TemporaryDirectory() as sess_tmp:
            s_root = Path(sess_tmp) / "memory"
            s_mgr = MemoryManager(
                Path(sess_tmp),
                memory_dir=s_root,
                config=MemoryConfig(enabled=True, auto_extract=True),
            )
            s_view = s_mgr.for_project("20260812100000", DEFAULT_AGENT)
            s_llm = _DispatchLLM()
            note1 = asyncio.run(
                s_view._write_session_summary(s_llm, "sess-1", "用户要求排查记忆功能，并修复了截断问题")
            )
            check("session summary wrote bullets", note1 == "wrote 2 bullets", note1)
            import datetime as _dt

            sess_rel = f"20260812100000/{DEFAULT_AGENT}/SESSIONS/{_dt.datetime.now().strftime('%Y-%m-%d')}.md"
            sess_raw = s_view.store.read_raw(sess_rel)
            check("SESSIONS file created", "记忆功能排查" in sess_raw, sess_raw)
            note2 = asyncio.run(
                s_view._write_session_summary(s_llm, "sess-1", "更多内容")
            )
            check("session summary deduped", note2 == "skip (already summarized today)", note2)

        # --- memory tool SESSIONS target resolution -----------------------------
        from coworker.agent.core import _resolve_memory_target

        base_rel = f"20260812100000/{DEFAULT_AGENT}/BASE/MEMORY.md"
        ok_sess, rel_sess = _resolve_memory_target(base_rel, "agent", "SESSIONS/2026-08-19.md")
        check("SESSIONS target resolves", ok_sess and rel_sess == f"20260812100000/{DEFAULT_AGENT}/SESSIONS/2026-08-19.md", rel_sess)
        ok_bad, msg_bad = _resolve_memory_target(base_rel, "agent", "SESSIONS/a/b.md")
        check("SESSIONS nested rejected", not ok_bad, msg_bad)
        ok_plain, rel_plain = _resolve_memory_target(base_rel, "agent", "RULES.md")
        check("BASE sibling still resolves", ok_plain and rel_plain.endswith("/BASE/RULES.md"), rel_plain)

        # --- DREAMS.md diary carries the actual extracted facts ------------------
        with tempfile.TemporaryDirectory() as diary_tmp:
            dr_root = Path(diary_tmp) / "memory"
            dr_mgr = MemoryManager(Path(diary_tmp), memory_dir=dr_root)
            dr_view = dr_mgr.for_project("20260812100000", DEFAULT_AGENT)
            dr_view._write_dream_diary(
                "sess-9",
                added=2,
                consolidated=True,
                note="consolidated 10 -> 7 blocks",
                candidates=["project is an event platform named hpcpgo", "方向是游戏化"],
                summary_note="wrote 3 bullets",
            )
            diary_raw = dr_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/BASE/DREAMS.md")
            check("diary has heading", "## " in diary_raw, diary_raw)
            check("diary has facts", "project is an event platform named hpcpgo" in diary_raw and "方向是游戏化" in diary_raw, diary_raw)
            check("diary has outcome", "consolidated" in diary_raw and "new 2" in diary_raw, diary_raw)
            check("diary references session note", "SESSIONS/" in diary_raw, diary_raw)

        # --- DREAMS.md excluded from injection ----------------------------------
        with tempfile.TemporaryDirectory() as inj_tmp:
            i_root = Path(inj_tmp) / "memory"
            i_mgr = MemoryManager(Path(inj_tmp), memory_dir=i_root)
            i_mgr.registry.ensure_project("20260812100000")
            i_agent = i_mgr.registry.ensure_agent(i_root / "20260812100000", DEFAULT_AGENT)
            i_mgr.store.write_file(f"20260812100000/{DEFAULT_AGENT}/BASE/MEMORY.md", "真实记忆\n")
            i_mgr.store.write_file(f"20260812100000/{DEFAULT_AGENT}/BASE/DREAMS.md", "- x · appended\n")
            library = i_mgr.scanner.scan()
            injected = library.injected(project_dir="20260812100000", agent=DEFAULT_AGENT)
            names = [n.name for n in injected]
            check("MEMORY.md injected", "MEMORY.md" in names, str(names))
            check("DREAMS.md not injected", "DREAMS.md" not in names, str(names))

        # --- governance: write_auto_facts is FIFO-capped ------------------------
        with tempfile.TemporaryDirectory() as cap_tmp:
            cap_root = Path(cap_tmp) / "memory"
            cap_mgr = MemoryManager(
                Path(cap_tmp),
                memory_dir=cap_root,
                config=MemoryConfig(enabled=True, auto_extract=True, inject_char_limit=100),
            )
            cap_view = cap_mgr.for_project("20260812100000", DEFAULT_AGENT)
            cap_view.write_auto_facts(["旧条目A" + "x" * 40, "旧条目B" + "y" * 40, "新条目C" + "z" * 40])
            cap_raw = cap_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/BASE/MEMORY.md")
            check("write_auto_facts keeps newest", "新条目C" in cap_raw, cap_raw)
            check("write_auto_facts drops oldest", "旧条目A" not in cap_raw, cap_raw)
            check("write_auto_facts bounded", len(cap_raw) <= 200, f"len={len(cap_raw)}")
            cap_view.write_auto_facts(["新条目C" + "z" * 40])  # exact duplicate skipped
            cap_raw2 = cap_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/BASE/MEMORY.md")
            check("write_auto_facts dedupes", cap_raw2.count("新条目C") == 1, cap_raw2)

        # --- governance: DREAMS.md monthly archive ------------------------------
        with tempfile.TemporaryDirectory() as ar_tmp:
            ar_root = Path(ar_tmp) / "memory"
            ar_mgr = MemoryManager(Path(ar_tmp), memory_dir=ar_root)
            ar_view = ar_mgr.for_project("20260812100000", DEFAULT_AGENT)
            import datetime as _dt

            _today = _dt.date.today()
            cur_day = _today.strftime("%Y-%m-%d")
            prev_day = _today.replace(day=1) - _dt.timedelta(days=1)
            prev_month = prev_day.strftime("%Y-%m")
            ar_view.store.write_file(
                f"20260812100000/{DEFAULT_AGENT}/BASE/DREAMS.md",
                f"# Dream Diary\n\n## {cur_day} 22:00 · consolidated · new 1\n- 当月事实\n\n"
                f"## {prev_day.strftime('%Y-%m-%d')} 21:00 · consolidated · new 2\n- 旧月事实一\n- 旧月事实二\n",
            )
            ar_view._rollup_archives()
            diary_after = ar_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/BASE/DREAMS.md")
            check("diary keeps current month", "当月事实" in diary_after and "旧月事实一" not in diary_after, diary_after)
            check("diary keeps single header", diary_after.count("# Dream Diary") == 1, diary_after)
            arch_d = ar_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/ARCHIVE/DREAMS-{prev_month}.md")
            check("diary archived old month", "旧月事实一" in arch_d and "旧月事实二" in arch_d, arch_d)

        # --- governance: SESSIONS monthly archive -------------------------------
        with tempfile.TemporaryDirectory() as ss_tmp:
            ss_root = Path(ss_tmp) / "memory"
            ss_mgr = MemoryManager(Path(ss_tmp), memory_dir=ss_root)
            ss_view = ss_mgr.for_project("20260812100000", DEFAULT_AGENT)
            _today2 = _dt.date.today()
            cur_day2 = _today2.strftime("%Y-%m-%d")
            prev_day2 = _today2.replace(day=1) - _dt.timedelta(days=1)
            prev_month2 = prev_day2.strftime("%Y-%m")
            ss_view.store.write_file(f"20260812100000/{DEFAULT_AGENT}/SESSIONS/{prev_day2.strftime('%Y-%m-%d')}.md", "- 旧月会话\n")
            ss_view.store.write_file(f"20260812100000/{DEFAULT_AGENT}/SESSIONS/{cur_day2}.md", "- 当月会话\n")
            ss_view._rollup_archives()
            arch_s = ss_view.store.read_raw(f"20260812100000/{DEFAULT_AGENT}/ARCHIVE/SESSIONS-{prev_month2}.md")
            check("sessions archived old month", "旧月会话" in arch_s, arch_s)
            sess_dir = ss_root / "20260812100000" / DEFAULT_AGENT / "SESSIONS"
            check("sessions old file removed", not (sess_dir / f"{prev_day2.strftime('%Y-%m-%d')}.md").exists(), str(sorted(p.name for p in sess_dir.iterdir())))
            check("sessions current kept", (sess_dir / f"{cur_day2}.md").exists(), str(sorted(p.name for p in sess_dir.iterdir())))

        # --- context budget: table resolution + conversion ----------------------
        from coworker.agent.core import _estimate_tokens, _message_text, _msg_chars, context_budget_chars, is_context_overflow_error, CONTEXT_SAFETY_FACTOR
        from coworker.context import LATIN_CHARS_PER_TOKEN
        from coworker.providers import DEFAULT_CONTEXT_WINDOW, MODEL_CONTEXT_TABLE, ProviderEntry, ProviderManager

        gpt = ProviderEntry(id="p1", name="gpt", provider_type="custom", base_url="http://localhost:9000", model="gpt-4o")
        win, src = ProviderManager.resolve_context_window(gpt)
        check("table resolves gpt-4o", win == 128_000, f"{win}/{src}")
        haiku = ProviderEntry(id="p2", name="h", provider_type="custom", base_url="http://localhost:9000", model="claude-haiku-4-5")
        win_h, src_h = ProviderManager.resolve_context_window(haiku)
        check("table prefers haiku prefix", win_h == 200_000, f"{win_h}/{src_h}")
        scout = ProviderEntry(id="p3", name="s", provider_type="custom", base_url="http://localhost:9000", model="llama4:scout")
        win_s, src_s = ProviderManager.resolve_context_window(scout)
        check("table prefers scout prefix", win_s == 10_000_000, f"{win_s}/{src_s}")
        qwen4 = ProviderEntry(id="p4", name="q", provider_type="custom", base_url="http://localhost:9000", model="qwen3:4b")
        win_q, src_q = ProviderManager.resolve_context_window(qwen4)
        check("table prefers qwen3:4b variant", win_q == 262_144, f"{win_q}/{src_q}")
        unknown = ProviderEntry(id="p5", name="u", provider_type="custom", base_url="http://localhost:9000", model="weird-model-x")
        win_u, src_u = ProviderManager.resolve_context_window(unknown)
        # Window must always fall back to 128k. The source label is either
        # "default" (probe returned nothing) or "unreachable" (nothing is
        # listening on the local base_url) — both mean a safe 128k fallback.
        check("unknown model falls back to default", win_u == DEFAULT_CONTEXT_WINDOW and src_u in ("default", "unreachable"), f"{win_u}/{src_u}")
        overridden = ProviderEntry(id="p6", name="o", provider_type="custom", base_url="http://localhost:9000", model="gpt-4o", context_window=9999)
        win_o, src_o = ProviderManager.resolve_context_window(overridden)
        check("user override wins", win_o == 9999 and src_o == "user", f"{win_o}/{src_o}")

        # T1: the char budget derives from the SAME Latin ratio as the estimator.
        expected_chars = int(128_000 * CONTEXT_SAFETY_FACTOR * LATIN_CHARS_PER_TOKEN)
        check("budget 128k derives from estimator ratio", context_budget_chars(128_000) == expected_chars, str(context_budget_chars(128_000)))
        check("budget floors at 20k", context_budget_chars(1) == 20_000, str(context_budget_chars(1)))
        check("budget default when 0", context_budget_chars(0) == expected_chars, str(context_budget_chars(0)))

        # B3: message size counts tool results & tool calls (not just text)
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        m_tool = ToolMessage(content="cat /etc/os-release", tool_call_id="tc1")
        m_ai = AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "a.py"}}])
        check("msg chars counts tool results", _msg_chars(m_tool) == len("cat /etc/os-release"), str(_msg_chars(m_tool)))
        check("msg chars counts tool calls", _msg_chars(m_ai) == len("read_file{'path': 'a.py'}"), str(_msg_chars(m_ai)))

        # B4: CJK-aware token estimate (denser than the flat 3.5 chars/token)
        cjk = "这是一个用于验证的非常长的中文句子" * 20
        check(
            "token estimate CJK denser than 3.5",
            _estimate_tokens(cjk) > 0 and len(cjk) / _estimate_tokens(cjk) < 3.5,
            f"{len(cjk)} chars -> {_estimate_tokens(cjk)} tok",
        )

        # B7: per-turn model override recomputes the window from THAT model
        p_ovr = ProviderEntry(id="p7", name="ovr", provider_type="custom", base_url="http://localhost:9000", model="gpt-4o")
        win_ovr, src_ovr = ProviderManager.resolve_context_window(p_ovr, model="claude-sonnet-4")
        check("model override recomputes window", win_ovr == 1_000_000 and src_ovr == "table", f"{win_ovr}/{src_ovr}")

        # expanded context table (50+ mainstream models)
        def _win(model: str) -> int:
            return ProviderManager.table_context_window(model)

        check("qwen3.8 resolves 256k", _win("qwen3.8:27b") == 262_144, str(_win("qwen3.8:27b")))
        check("qwen3.6 resolves 256k", _win("qwen3.6:35b") == 262_144, str(_win("qwen3.6:35b")))
        check("glm-5.2 resolves 1M", _win("glm-5.2") == 1_000_000, str(_win("glm-5.2")))
        check("glm-5.1 resolves 198k", _win("glm-5.1") == 198_000, str(_win("glm-5.1")))
        check("glm-4.7 resolves 198k", _win("glm-4.7-flash") == 198_000, str(_win("glm-4.7-flash")))
        check("kimi-k3 resolves 1M", _win("kimi-k3") == 1_000_000, str(_win("kimi-k3")))
        check("kimi-k2.7 resolves 256k", _win("kimi-k2.7-code") == 262_144, str(_win("kimi-k2.7-code")))
        check("minimax-m3 resolves 1M", _win("minimax-m3") == 1_000_000, str(_win("minimax-m3")))
        check("gemma4 resolves 128k", _win("gemma4") == 131_072, str(_win("gemma4")))
        check("gemma4:12b resolves 256k", _win("gemma4:12b") == 262_144, str(_win("gemma4:12b")))
        check("mistral-medium-3.5 resolves 256k", _win("mistral-medium-3.5") == 262_144, str(_win("mistral-medium-3.5")))
        check("deepseek-v4 resolves 1M", _win("deepseek-v4-flash") == 1_000_000, str(_win("deepseek-v4-flash")))
        check("phi4 resolves 128k", _win("phi4") == 128_000, str(_win("phi4")))
        check("grok-4.5 resolves 256k", _win("grok-4.5") == 262_144, str(_win("grok-4.5")))
        check("doubao resolves 256k", _win("doubao-1.5-pro") == 262_144, str(_win("doubao-1.5-pro")))
        check("ernie resolves 128k", _win("ernie-4.5") == 128_000, str(_win("ernie-4.5")))
        check("internlm resolves 1M", _win("internlm3") == 1_000_000, str(_win("internlm3")))
        check("yi- resolves 200k", _win("yi-large") == 200_000, str(_win("yi-large")))
        check("granite4.1 resolves 128k", _win("granite4.1") == 131_072, str(_win("granite4.1")))
        check("table covers 50+ models", len(MODEL_CONTEXT_TABLE) >= 90, str(len(MODEL_CONTEXT_TABLE)))

        check("overflow detection", is_context_overflow_error(ValueError("This model's maximum context length is 4096 tokens.")))
        check("overflow detection ignores normal", not is_context_overflow_error(ValueError("rate limit")))

        # fetch_context_window: ollama /api/show model_info parsing (mocked).
        from unittest.mock import patch

        ollama = ProviderEntry(id="p7", name="ollama", provider_type="ollama", base_url="http://localhost:11434", model="qwen3:8b")
        fake_show = json.dumps({"model_info": {"qwen3.context_length": 40960}}).encode()
        with patch("urllib.request.urlopen") as mock_open:
            class _Resp:
                status = 200

                def __enter__(self): return self

                def __exit__(self, *a): return False

                def read(self): return fake_show

            mock_open.return_value = _Resp()
            discovered = ProviderManager.fetch_context_window(ollama)
        check("ollama fetch parses model_info", discovered == 40960, str(discovered))

        # --- context compaction: framework-backed middleware regression checks ----
        # Guards the observed failure where _summarize_segment fed the model a
        # transcript of character counts, producing a garbage "numerical exchanges"
        # summary that was injected into the conversation and echoed by the model.
        from coworker.agent.middleware import (
            COMPACTION_PROMPTS,
            KEEP_RECENT_TOKENS,
            CoworkerSummarizationMiddleware,
            _anchored_summary_prompt,
            _cap_summary,
            _compaction_summary_prefix,
            _strip_compaction_echo,
            _summary_ok,
        )
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        from langgraph.graph.message import RemoveMessage

        check("summary prompt has messages slot", "{messages}" in COMPACTION_PROMPTS["zh"] and "{messages}" in COMPACTION_PROMPTS["en"], "")
        check("summary prefix localized", _compaction_summary_prefix("zh").startswith("先前对话摘要"), _compaction_summary_prefix("zh"))
        check("summary ok accepts real text", _summary_ok("用户想加自动化测试，涉及 /src/main.py 与 x.yaml，决策是改用 pnpm"), "")
        check("summary ok rejects numeric garbage", not _summary_ok("4 3 12 55 24 66 236 183 38 2453 375 120 234 102 107"), "numeric transcript must be rejected")
        check("summary ok rejects empty", not _summary_ok(""), "")
        check("echo strip removes summary", _strip_compaction_echo("好的。\n\n用户想加自动化测试，涉及 /src/main.py 与 x.yaml。\n\n继续吗？", "用户想加自动化测试，涉及 /src/main.py 与 x.yaml") == "好的。\n\n。\n\n继续吗？", "")
        check("echo strip keeps normal text", _strip_compaction_echo("正常回复文本", "另一个不相关的长摘要文本内容用于测试") == "正常回复文本", "")

        class _FakeCompModel:
            model_name = "fake"
            base_url = "http://fake"

            def __init__(self, payload):
                self._payload = payload

            def invoke(self, messages, **kwargs):
                return type("R", (), {"content": self._payload})()

            async def ainvoke(self, messages, **kwargs):
                return self.invoke(messages, **kwargs)

        class _FakeCompRuntime:
            def __init__(self):
                self.events = []

            def stream_writer(self, event):
                self.events.append(event)

        def _over_budget_state():
            msgs = [SystemMessage(content="sys", id="s0")]
            for i in range(10):
                msgs.append(HumanMessage(content="用户消息" * 5 + str(i), id=f"u{i}"))
                msgs.append(AIMessage(content="助手回复" * 5, id=f"a{i}"))
                msgs.append(ToolMessage(content="x" * 5000, tool_call_id=f"tc{i}", id=f"t{i}"))
            return {"messages": msgs, "context_compact_count": 0}

        async def _compact_checks():
            good_summary = "会话意图：完成自动化测试改造，涉及 /src/main.py 与配置 x.yaml，决策是改用 pnpm"
            mw = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=_FakeCompModel(good_summary), language="zh", context_window_tokens=1000,
            )
            rt = _FakeCompRuntime()
            result = await mw.abefore_model(_over_budget_state(), rt)
            check("compaction returns update", result is not None, str(result)[:120] if result else "")
            if result:
                kept = [m for m in result["messages"] if not isinstance(m, RemoveMessage)]
                check("summary injected as HumanMessage", bool(kept) and kept[0].type == "human", str([getattr(m, "type", "?") for m in kept][:3]))
                check("summary localized prefix", bool(kept) and str(kept[0].content).startswith("先前对话摘要："), str(kept[0].content)[:40] if kept else "")
                check("flush reminder is HumanMessage", bool(kept) and kept[-1].type == "human" and getattr(kept[-1], "id", "") == "__compaction_flush__", "")
                check("compact count incremented", result.get("context_compact_count") == 1, str(result.get("context_compact_count")))
                check("context_summary persisted", result.get("context_summary") == good_summary, str(result.get("context_summary")))
                check("context_usage telemetry emitted", any(e.get("type") == "context_usage" for e in rt.events), "")
            # Degenerate (numeric) summary must fall back to trim — never injected.
            mw_bad = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=_FakeCompModel("4 3 12 55 24 66 236 183 38 2453 375"), language="zh", context_window_tokens=1000,
            )
            result_bad = await mw_bad.abefore_model(_over_budget_state(), _FakeCompRuntime())
            check("degenerate summary falls back to trim", result_bad is not None and not result_bad.get("context_summary"), str(result_bad))
            # Fallback chain: first candidate fails, second succeeds.
            class _FailingModel:
                model_name = "failing"
                base_url = "http://f"

                def invoke(self, messages, **kwargs):
                    raise RuntimeError("boom")

                async def ainvoke(self, messages, **kwargs):
                    raise RuntimeError("boom")

            mw_chain = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=_FailingModel(), summarizer_candidates=[_FailingModel(), _FakeCompModel(good_summary)],
                language="zh", context_window_tokens=1000,
            )
            result_chain = await mw_chain.abefore_model(_over_budget_state(), _FakeCompRuntime())
            check("summarizer fallback chain succeeds", bool(result_chain and result_chain.get("context_summary")), str(result_chain))
            # Cheap layer first: clearing stale tool results alone fits the budget
            # -> micro-compact, no model summary invoked, counter still increments.
            from langchain.agents.middleware.context_editing import ClearToolUsesEdit

            edit = ClearToolUsesEdit(trigger=100, keep=2, placeholder="[cleared]")
            mw_prune = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=_FakeCompModel(good_summary), language="zh", context_window_tokens=1000, tool_edit=edit,
            )
            msgs_prune = [SystemMessage(content="sys", id="s0")]
            for i in range(10):
                msgs_prune.append(HumanMessage(content="用户消息" * 5 + str(i), id=f"u{i}"))
                msgs_prune.append(AIMessage(content="", tool_calls=[{"id": f"tc{i}", "name": "read_file", "args": {"p": "x"}}], id=f"a{i}"))
                msgs_prune.append(ToolMessage(content="x" * 3000, tool_call_id=f"tc{i}", id=f"t{i}"))
            result_prune = await mw_prune.abefore_model({"messages": msgs_prune, "context_compact_count": 0}, _FakeCompRuntime())
            if result_prune:
                tool_kept = [m for m in result_prune["messages"] if isinstance(m, ToolMessage)]
                check(
                    "prune layer clears stale tool results (micro-compact)",
                    bool(tool_kept) and all(m.content == "[cleared]" for m in tool_kept[:-2]),
                    str([str(m.content)[:12] for m in tool_kept]),
                )
                check("prune layer does not inject summary", not result_prune.get("context_summary"), "")
                check("prune layer increments compact count", result_prune.get("context_compact_count") == 1, str(result_prune.get("context_compact_count")))

            # --- opencode-aligned compaction behavior ---
            # Fixed keep window (8k), independent of the budget (old behavior was
            # budget×0.6 which left the resident set near the ceiling).
            check("keep window fixed (opencode-aligned)", KEEP_RECENT_TOKENS == 8_000, str(KEEP_RECENT_TOKENS))
            msgs_fixed = [SystemMessage(content="sys", id="s0")]
            for i in range(4):
                msgs_fixed.append(HumanMessage(content="用户消息" * 5 + str(i), id=f"k{i}"))
                msgs_fixed.append(AIMessage(content="助手回复" * 5, id=f"ka{i}"))
            mw_fixed = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=_FakeCompModel(good_summary), language="zh", context_window_tokens=10_000_000,
            )
            mw_fixed._determine_cutoff_index(msgs_fixed)
            check("fixed keep window not budget-scaled", mw_fixed.keep == ("tokens", KEEP_RECENT_TOKENS), str(mw_fixed.keep))

            # Anchored preamble: present with a previous summary, absent without.
            anchored_prompt = _anchored_summary_prompt(COMPACTION_PROMPTS["zh"], "上一版摘要")
            check("anchored preamble includes previous summary", "上一版摘要" in anchored_prompt and "<previous-summary>" in anchored_prompt, "")
            check("anchored preamble skipped when empty", _anchored_summary_prompt(COMPACTION_PROMPTS["zh"], "") == COMPACTION_PROMPTS["zh"], "")

            # Summary cap: oversized output trimmed to <= SUMMARY_OUTPUT_TOKENS.
            long_summary = "长摘要" * 50_000
            capped = _cap_summary(long_summary)
            from coworker.agent.core import _estimate_tokens
            check("summary capped to budget", _estimate_tokens(capped) <= 4_096, f"{_estimate_tokens(capped)} tokens")
            check("cap keeps short summaries intact", _cap_summary(good_summary) == good_summary, "")
            check("capped summary has truncation marker", capped.endswith("to fit context]"), capped[-40:])

            # Recording fake model so we can assert the anchored summary was
            # threaded through to the summarizer on a follow-up compaction.
            class _RecordingCompModel(_FakeCompModel):
                def __init__(self, payload):
                    super().__init__(payload)
                    self.last_prompt = ""

                def invoke(self, messages, **kwargs):
                    self.last_prompt = str(messages)
                    return super().invoke(messages, **kwargs)

                async def ainvoke(self, messages, **kwargs):
                    self.last_prompt = str(messages)
                    return super().invoke(messages, **kwargs)

            recording = _RecordingCompModel("会话意图：更新后的摘要，涉及 src/main.py 与 x.yaml 的新决策")
            mw_rec = CoworkerSummarizationMiddleware(
                budget_chars=1000, llm=recording, language="zh", context_window_tokens=1000,
            )
            rec_state = _over_budget_state()
            rec_state["context_summary"] = "上一版摘要内容"
            rec_result = await mw_rec.abefore_model(rec_state, _FakeCompRuntime())
            check("anchored summary threaded to model", rec_result and rec_result.get("context_summary"), str(rec_result))
            check("summarizer received anchored preamble", bool(recording.last_prompt) and "上一版摘要内容" in recording.last_prompt, recording.last_prompt[:80])
            if rec_result:
                check("fingerprints persisted in state", "context_summarized_fingerprints" in rec_result, str(list(rec_result.keys())))
                fps = rec_result.get("context_summarized_fingerprints") or []
                check("fingerprint non-empty after compact", bool(fps), str(fps)[:80])
                # Cross-turn dedup: rerun over the SAME history with the persisted
                # fingerprints -> the same segment is not summarized again.
                rec_state2 = dict(_over_budget_state())
                rec_state2["context_summary"] = rec_result.get("context_summary")
                rec_state2["context_summarized_fingerprints"] = fps
                mw_rec2 = CoworkerSummarizationMiddleware(
                    budget_chars=1000, llm=_RecordingCompModel(good_summary), language="zh", context_window_tokens=1000,
                )
                rec_result2 = await mw_rec2.abefore_model(rec_state2, _FakeCompRuntime())
                check("dedup: same segment not re-summarized", not (rec_result2 or {}).get("context_summary"), str(rec_result2))

            # Tool-output truncation in summary serialization.
            msgs_ser = [SystemMessage(content="sys", id="s0"), HumanMessage(content="hi", id="u0")]
            msgs_ser.append(AIMessage(content="", tool_calls=[{"id": "tc0", "name": "read_file", "args": {}}], id="a0"))
            msgs_ser.append(ToolMessage(content="z" * 20_000, tool_call_id="tc0", id="t0"))
            ser = mw_fixed._serialize_for_summary(msgs_ser)
            check("tool output truncated for summary", "z" * 2_000 + "\n[truncated]" in ser, f"len={len(ser)}")
            check("truncated tool text not full-length", "z" * 19_000 not in ser, "")

        asyncio.run(_compact_checks())

    # --- SSE infrastructure: ApprovalEventBus non-blocking publish + unsubscribe drain ---
    async def _sse_async_checks() -> None:
        import main as _m
        bus = _m.approval_event_bus

        # 1) publish never blocks on full queue
        q = bus.subscribe("seftest_sse_pub")
        for i in range(256):
            q.put_nowait({"i": i})
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                bus.publish("seftest_sse_pub", {"type": "nack-test"}),
                timeout=1.0,
            )
            check(
                "ApprovalEventBus publish() never blocks on full queue",
                time.monotonic() - t0 < 0.5,
                f"elapsed={time.monotonic()-t0:.4f}s",
            )
        except asyncio.TimeoutError:
            check("ApprovalEventBus publish() never blocks on full queue", False, "timeout 1s")
        while True:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break
        bus.close("seftest_sse_pub")

        # 1b) publish drops the OLDEST queued event when full (latest-wins)
        q3 = bus.subscribe("seftest_sse_dropold")
        for i in range(256):
            q3.put_nowait({"i": i})
        await asyncio.wait_for(
            bus.publish("seftest_sse_dropold", {"type": "newest"}),
            timeout=1.0,
        )
        drained = []
        while True:
            try:
                drained.append(q3.get_nowait())
            except asyncio.QueueEmpty:
                break
        check(
            "publish drops oldest on full queue (latest-wins)",
            len(drained) == 256 and drained[-1].get("type") == "newest" and drained[0].get("i") == 1,
            f"len={len(drained)} first={drained[0] if drained else None} last={drained[-1] if drained else None}",
        )
        bus.close("seftest_sse_dropold")

        # 2) unsubscribe drains the queue to prevent memory leak
        q2 = bus.subscribe("seftest_sse_unsub")
        for i in range(100):
            q2.put_nowait({"i": i})
        before = q2.qsize()
        bus.unsubscribe("seftest_sse_unsub", q2)
        after = q2.qsize()
        check("ApprovalEventBus unsubscribe drains queue", before > 0 and after == 0, f"before={before} after={after}")
        bus.close("seftest_sse_unsub")

        # 3) _sse_events idle_warning: fires after the 240s threshold and
        #    seconds_idle counts from stream start (mock monotonic clock).
        fake_time = [1000.0]
        original = _m._get_monotonic
        _m._get_monotonic = lambda: fake_time[0]

        async def _never_iter():
            await asyncio.sleep(3600)
            yield {}  # pragma: no cover - stream never produces an event

        try:
            gen = _m._sse_events(_never_iter())
            kind1, payload1 = await anext(gen)  # 1s wait_for timeout -> heartbeat
            check("idle heartbeat emitted before threshold", kind1 == "heartbeat", f"kind={kind1}")
            fake_time[0] = 1300.0  # +300s from stream start, > 240s threshold
            kind2, payload2 = await anext(gen)
            check(
                "idle_warning fired after 240s idle (mock clock)",
                kind2 == "event" and isinstance(payload2, dict) and payload2.get("type") == "idle_warning",
                f"kind={kind2} payload={payload2}",
            )
            check(
                "seconds_idle counted from stream start",
                isinstance(payload2, dict) and payload2.get("seconds_idle") == 300,
                f"seconds_idle={payload2.get('seconds_idle') if isinstance(payload2, dict) else payload2}",
            )
            await gen.aclose()
        finally:
            _m._get_monotonic = original

    asyncio.run(_sse_async_checks())

    print("\n".join(CHECKS))
    failures = [c for c in CHECKS if c.startswith("FAIL")]
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
