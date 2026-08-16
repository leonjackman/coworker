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

    print("\n".join(CHECKS))
    failures = [c for c in CHECKS if c.startswith("FAIL")]
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
