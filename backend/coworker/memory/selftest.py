"""Self-contained sanity checks for the memory subsystem (v2 library tree).

Runs with the venv python directly (no pytest needed)::

    cd backend && ./venv/bin/python coworker/memory/selftest.py

Covers layout path safety, registry skeletons, Markdown-block CRUD, discovery
injection order, and prompt budget warning. Exits non-zero on the first
failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coworker.memory.layout import memory_dir_from_created_at, resolve_rel_path, sanitize_name
from coworker.memory.memory_file import render_blocks, split_blocks
from coworker.memory.memory_prompt import format_memory_prompt
from coworker.memory.memory_store import MemoryError, MemoryStore
from coworker.memory.memory_discovery import MemoryScanner
from coworker.memory.registry import MemoryRegistry
from coworker.memory.transfer import apply_import, export_memory, preview_import
from coworker.org import OrgError, OrgStore, OrgTeam, OrgAgent, default_org
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

        # --- prompt budget + ordering ----------------------------------------
        prompt = format_memory_prompt(injected, char_limit=5)
        check("budget warning present", "<budget_warning>" in prompt)
        check("files rendered", "<file kind=" in prompt)
        check("empty prompt", format_memory_prompt([], char_limit=2000) == "")

        # --- budget is SOFT: writes never blocked -----------------------------
        store.clear_file(rel)
        store.add_block(rel, "z" * 5000)
        over = format_memory_prompt(library.injected(project_dir="20260812100000", agent="default_agent"), char_limit=100)
        check("budget warns but does not truncate", "<budget_warning>" in over)

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

        # default org + default_agent
        org = default_org()
        org.agents.append(OrgAgent(id="default_agent", name="default_agent", role="team lead"))
        org_store.save("proj1", org)
        check("org save/load round trip", org_store.load("proj1").agents[0].name == "default_agent")

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

    print("\n".join(CHECKS))
    failures = [c for c in CHECKS if c.startswith("FAIL")]
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
