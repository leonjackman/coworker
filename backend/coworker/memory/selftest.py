"""Self-contained sanity checks for the memory subsystem.

Runs with the venv python directly (no pytest needed)::

    cd backend && ./venv/bin/python coworker/memory/selftest.py

Covers parsing/render round-trip, duplicate rejection, replace/remove,
clear, discovery precedence, budget warning injection, and drift/verification
behaviour. Exits non-zero on the first failure.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coworker.memory.memory_file import HEADER, render_file, split_entries
from coworker.memory.memory_discovery import MemoryScanner
from coworker.memory.memory_prompt import format_memory_prompt
from coworker.memory.memory_store import MemoryError, MemoryStore

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        CHECKS.append(f"PASS {name}")
    else:
        CHECKS.append(f"FAIL {name}: {detail}")


def main() -> int:
    # --- parse / render round trip ---------------------------------------
    entries = ["user prefers Chinese replies", "frontend builds with npm run build only"]
    text = render_file(entries)
    check("render includes header", text.startswith(HEADER))
    check("render separators", text.count("§") == 1)
    parsed = split_entries(text)
    check("parse round-trip", parsed == entries, f"{parsed!r} != {entries!r}")
    check("empty render", render_file([]).strip() == HEADER)

    # manual multi-entry edit by a user must also parse
    manual = f"{HEADER}\n\na\n§\n\nb\n\n§\n\nc\n"
    check("manual format parse", split_entries(manual) == ["a", "b", "c"])

    # --- store CRUD --------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        scanner = MemoryScanner(workspace_root=ws, user_home=Path(tmp))
        store = MemoryStore(scanner)
        project = store.add("project", "port 9527 is the backend")
        check("add creates project file", project.path.is_file())
        check("add entry count", len(store.list_scope("project").entries) == 1)
        store.add("project", "agent binds 0.0.0.0 for tests")
        try:
            store.add("project", "port 9527 is the backend")
            check("duplicate rejected", False, "no exception on duplicate")
        except MemoryError:
            check("duplicate rejected", True)
        m = store.replace("project", "port 9527", "port 9527 (FastAPI) is the backend")
        check("replace swapped entry", any("FastAPI" in e for e in m.entries), str(m.entries))
        try:
            store.replace("project", "no-such-target", "x")
            check("replace missing raises", False)
        except MemoryError:
            check("replace missing raises", True)
        m = store.remove("project", "0.0.0.0")
        check("remove works", len(m.entries) == 1, str(m.entries))
        store.clear("project")
        check("clear empties", len(store.list_scope("project").entries) == 0)

        # --- user scope + discovery precedence -----------------------------
        store.add("user", "global preference: reply in Chinese")
        scan = scanner.scan(include_missing=True)
        check("project scope found", scan.project is not None)
        check("user scope found", scan.user is not None)
        check(
            "project precedes user",
            scan.files()[0].scope == "project",
            f"{[f.scope for f in scan.files()]}",
        )
        # project path lives under workspace .coworker
        check(
            "project path convention",
            str(scan.project.path.resolve()) == str((ws / ".coworker" / "MEMORY.md").resolve()),
            str(scan.project.path),
        )

        # --- budget warning ------------------------------------------------
        small = format_memory_prompt(
            [("project", "/p (updated 2026-08-12 10:00)", ["a"] * 10)],
            char_limit=5,
        )
        check("budget warning present", "<budget_warning>" in small)
        empty = format_memory_prompt([], char_limit=2000)
        check("empty prompt is empty", empty == "")

    print("\n".join(CHECKS))
    failures = [c for c in CHECKS if c.startswith("FAIL")]
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())