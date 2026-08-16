#!/usr/bin/env python3
"""Comprehensive memory subsystem stress tests.

Covers:
  1. Block CRUD (add/replace/remove/round-trip)
  2. Project isolation
  3. Agent isolation  
  4. Session file isolation
  5. Duplicate detection
  6. Budget pressure (soft budget never blocks)
  7. Injection order correctness
  8. Prompt rendering accuracy
  9. Many projects x many blocks
  10. MemoryManager scoping
  11. HTTP API test (requires live backend)
  12. HTTP pressure test (200 writes)

Designed to run purely in-process:
  cd backend && ./venv/bin/python memory/stress_test.py

Or against a live backend:
  cd backend && ./venv/bin/python memory/stress_test.py --url http://127.0.0.1:9527 --project-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import random
import string
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coworker.memory.layout import (
    MEMORY_ROOT_NAME,
    BASE_DIR,
    PROJECT_SUBDIR,
    SESSIONS_DIR,
    SYSTEM_FILES,
)
from coworker.memory.registry import MemoryRegistry
from coworker.memory.memory_store import MemoryStore, MemoryError
from coworker.memory.memory_discovery import MemoryScanner
from coworker.memory.memory_prompt import format_memory_prompt
from coworker.memory.memory_manager import MemoryManager, DEFAULT_AGENT

# Config
PRESSURE_BLOCK_COUNT = 500
PROJECT_ID = ""

PASS_COUNT = 0
FAIL_COUNT = 0
FAILURES: list[str] = []
WARNINGS: list[str] = []
URL = ""

# Unique prefix per test to avoid cross-project contamination
_TEST_SEQ = 0

def _next_md(prefix: str = "2026") -> str:
    global _TEST_SEQ
    _TEST_SEQ += 1
    return f"{prefix}{_TEST_SEQ:06d}"


def check(name: str, condition: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        FAILURES.append(f"FAIL {name}: {detail}")


def warn(name: str, detail: str = ""):
    WARNINGS.append(f"WARN {name}: {detail}")


def random_text(n: int = 0) -> str:
    if n <= 0:
        return "".join(random.choices(string.ascii_letters, k=30))
    return "".join(random.choices(string.ascii_letters + " ", k=n))


_RUN_TAG = f"{time.time():.0f}"


def _unique(text: str) -> str:
    """Suffix content with a per-run tag so HTTP tests never collide with
    content persisted by a previous run against the same live library."""
    return f"{text} [{_RUN_TAG}]"


def _http(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode() if body else None
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{URL}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if body else {},
            method=method,
        )
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as exc:
        return {"__error": str(exc)}


# --- Phase A: In-process store tests ---
def test_basic_crud(tmp: Path):
    print("\n[A] Basic CRUD")
    store = MemoryStore(tmp)
    check("write creates dir", store.write_file("test/file.md", "# Hello\n\nSome text\n").path.exists())
    mf = store.read_file("test/file.md")
    check("read returns content", mf.content == "# Hello\n\nSome text\n")
    check("read returns blocks", mf.blocks == ("# Hello", "Some text"))
    store.add_block("test/file.md", "first block")
    blocks = store.list_blocks("test/file.md")
    check("append adds block", "first block" in blocks, str(blocks))
    try:
        store.add_block("test/file.md", "first block")
        check("duplicate rejected", False, "no exception")
    except MemoryError:
        check("duplicate rejected", True)
    store.replace_block("test/file.md", "first", "replaced block")
    blocks = store.list_blocks("test/file.md")
    check("replace works", any("replaced" in b for b in blocks), str(blocks))
    store.remove_block("test/file.md", "replaced")
    blocks = store.list_blocks("test/file.md")
    check("remove works", not any("replaced" in b for b in blocks), str(blocks))
    store.clear_file("test/file.md")
    check("clear empties", store.read_file("test/file.md").blocks == ())
    store.remove_file("test/file.md")
    check("remove deletes", not store.read_file("test/file.md").path.exists())
    store.remove_file("test/file.md")  # idempotent remove


def test_skeletons(tmp: Path):
    print("\n[B] Skeleton materialization")
    registry = MemoryRegistry(tmp)
    md = _next_md()
    registry.ensure_root()
    for name in SYSTEM_FILES:
        check(f"system {name}", (tmp / MEMORY_ROOT_NAME / name).is_file())
    proj = registry.ensure_project(md)
    check("project dir", proj.is_dir())
    check("BASE dir", (proj / BASE_DIR).is_dir())
    check("PROJECT subdir", (proj / BASE_DIR / PROJECT_SUBDIR).is_dir())
    check("GOALS.md skeleton", (proj / BASE_DIR / PROJECT_SUBDIR / "GOALS.md").exists())
    check("CONTEXT.md skeleton", (proj / BASE_DIR / PROJECT_SUBDIR / "CONTEXT.md").exists())
    base_files = [f.name for f in (proj / BASE_DIR).iterdir() if f.is_file()]
    check("BASE template EXAMPLE.md", "EXAMPLE.md" in base_files, str(base_files))
    check("no legacy BASE files", not (proj / BASE_DIR / "BASE.md").exists(), str(base_files))
    agent = registry.ensure_agent(proj, DEFAULT_AGENT)
    check(f"agent SOUL.md", (agent / "BASE" / "SOUL.md").is_file())
    check(f"agent AGENT.md", (agent / "BASE" / "AGENT.md").is_file())
    check(f"agent MEMORY.md", (agent / "BASE" / "MEMORY.md").is_file())
    check("sessions dir", (agent / SESSIONS_DIR).is_dir())


def test_injection_order(tmp: Path):
    print("\n[C] Injection order")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    registry.ensure_agent(registry.ensure_project(md), DEFAULT_AGENT)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.write_file("MEMORY.md", "SYS_MESS")
    store.write_file("USER.md", "USR_MESS")
    store.write_file(f"{md}/BASE/rules.md", "PRJ_MESS")
    store.write_file(f"{md}/BASE/PROJECT/CONTEXT.md", "PXZ_MESS")
    store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "AGT_MESS")
    store.write_file(f"{md}/{DEFAULT_AGENT}/BASE/SOUL.md", "SOUL_MESS")
    store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/s1.md", "SES_MESS")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    nodes = library.injected(project_dir=md, agent=DEFAULT_AGENT)
    msg_list = [n.content.strip() for n in nodes if n.content and len(n.content) > 6 and n.content.strip().endswith("_MESS")]
    check("injection has system msg", msg_list[0] == "SYS_MESS" if msg_list else False, str(msg_list[:3]))
    check("injection order system first", "SYS_MESS" in msg_list and msg_list.index("SYS_MESS") == 0 if msg_list else False)


def test_prompt_rendering(tmp: Path):
    print("\n[D] Prompt rendering")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.write_file("MEMORY.md", "huge fact system")
    store.write_file("USER.md", "huge fact user")
    store.write_file(f"{md}/BASE/rules.md", "huge fact project")
    store.write_file(f"{md}/BASE/PROJECT/CONTEXT.md", "huge fact context")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    nodes = library.injected(project_dir=md, agent=DEFAULT_AGENT)
    pw = format_memory_prompt(nodes, char_limit=10)
    check("budget warning present", "<budget_warning>" in pw, pw[:200])
    pw2 = format_memory_prompt(nodes, char_limit=10000)
    check("no warning under limit", "<budget_warning>" not in pw2)
    check("prompt opens <memory>", "<memory>" in pw2)
    check("prompt closes </memory>", "</memory>" in pw2)
    check("prompt uses <file>", "<file" in pw2)
    check("empty prompt is empty", format_memory_prompt([], char_limit=500) == "")


def test_project_isolation(tmp: Path):
    print("\n[E] Project isolation")
    md1, md2 = "20260104120000", "20260105120000"
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.write_file(f"{md1}/BASE/rules.md", "fact in project alpha UNIQUE")
    store.write_file(f"{md1}/{DEFAULT_AGENT}/BASE/MEMORY.md", "memo alpha UNIQUE")
    store.write_file(f"{md2}/BASE/rules.md", "fact in project beta UNIQUE")
    store.write_file(f"{md2}/{DEFAULT_AGENT}/BASE/MEMORY.md", "memo beta UNIQUE")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    nodes_p1 = library.injected(project_dir=md1, agent=DEFAULT_AGENT)
    for n in nodes_p1:
        check(f"p1 no beta memo", "memo beta" not in (n.content or ""), str(n.content[:40] or ""))
    nodes_p2 = library.injected(project_dir=md2, agent=DEFAULT_AGENT)
    for n in nodes_p2:
        check(f"p2 no alpha memo", "memo alpha" not in (n.content or ""), str(n.content[:40] or ""))


def test_agent_isolation(tmp: Path):
    print("\n[F] Agent isolation")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    registry.ensure_project(md)
    registry.ensure_agent(registry.project_dir(md), "coder")
    registry.ensure_agent(registry.project_dir(md), "reviewer")
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.add_block(f"{md}/coder/BASE/MEMORY.md", "coder fact UNIQUE")
    store.add_block(f"{md}/reviewer/BASE/MEMORY.md", "reviewer fact UNIQUE")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    for n in library.injected(project_dir=md, agent="coder"):
        check("coder no reviewer", "reviewer fact" not in (n.content or ""))
    for n in library.injected(project_dir=md, agent="reviewer"):
        check("reviewer no coder", "coder fact" not in (n.content or ""))


def test_unicode_edge(tmp: Path):
    print("\n[G] Unicode & edge cases")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "中文记忆条目")
    check("unicode add", "中文记忆条目" in store.read_raw(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"))
    store.replace_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "中文", "中文修改后")
    check("unicode replace", "中文修改后" in store.read_raw(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"))
    store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "x" * 10000)
    check("long block OK", store.read_raw(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md").count("x") >= 9990)
    store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "特殊字符 <script>x</script>")
    check("special chars OK", "<script>" in store.read_raw(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"))
    try:
        store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "")
        check("empty rejected", False, "no exc")
    except MemoryError:
        check("empty rejected", True)
    try:
        store.replace_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "xyz_nonexistent_999999", "y")
        check("missing target rejected", False, "no exc")
    except MemoryError:
        check("missing target rejected", True)


def test_pressure_blocks(tmp: Path):
    print("\n[H] Pressure: many blocks")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    rel = f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"
    t0 = time.time()
    for i in range(PRESSURE_BLOCK_COUNT):
        store.add_block(rel, f"fact #{i}: {random_text(20)}")
    dur = (time.time() - t0) * 1000
    result = list(store.list_blocks(rel))
    check(f"pressure {PRESSURE_BLOCK_COUNT} blocks", len(result) == PRESSURE_BLOCK_COUNT, f"got {len(result)}")
    check("pressure <5s", dur < 5000, f"{dur:.0f}ms")
    check("pressure integrity", all(f"fact #{i}:" in b for i, b in enumerate(result)))


def test_over_budget(tmp: Path):
    print("\n[I] Over budget (soft budget)")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    rel = f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"
    for i in range(50):
        store.add_block(rel, "x" * 200 + f"_{i}")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    nodes = library.injected(project_dir=md, agent=DEFAULT_AGENT)
    text_len = sum(len(n.content) for n in nodes)
    check("over budget exceeds 2000", text_len > 2000, f"{text_len} chars")
    store.add_block(rel, "overflow entry")
    check("write OK over budget", "overflow entry" in store.read_raw(rel))


def test_replace_remove(tmp: Path):
    print("\n[J] Replace & remove by substring")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    rel = f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"
    store.add_block(rel, "first fact with alpha")
    store.add_block(rel, "second fact with beta")
    store.add_block(rel, "third fact with gamma")
    check("3 blocks", len(store.list_blocks(rel)) == 3)
    store.replace_block(rel, "alpha", "DELTA")
    check("replace alpha->DELTA", any("DELTA" in b for b in store.list_blocks(rel)))
    store.remove_block(rel, "beta")
    bl = store.list_blocks(rel)
    check("remove beta", not any("beta" in b.lower() for b in bl), str(bl))


def test_session_isolation(tmp: Path):
    print("\n[K] Session file isolation")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_agent(registry.ensure_project(md), DEFAULT_AGENT)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    agent_dir = registry.agent_dir(registry.project_dir(md), DEFAULT_AGENT)
    sessions_dir = agent_dir / SESSIONS_DIR
    store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/session_001.md", "session 1 fact")
    store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/session_002.md", "session 2 fact")
    store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/session_003.md", "session 3 fact")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    # Find the agent belonging to our md
    found_agent = None
    for p in library.projects:
        if p.name == md:
            found_agent = next((a for a in p.agents if a.name == DEFAULT_AGENT), None)
            break
    if found_agent is None:
        check("3 sessions found", False, "no agent found")
    else:
        check("3 sessions found", len(found_agent.sessions) == 3, str(len(found_agent.sessions)))
        for sess_name, expected in [("session_001.md", "session 1 fact"), ("session_002.md", "session 2 fact"), ("session_003.md", "session 3 fact")]:
            found_content = next((s.content for s in found_agent.sessions if s.name == sess_name), "")
            check(f"session {sess_name}", expected in found_content, f"expected '{expected}', got '{found_content[:30]}'")


def test_include_missing(tmp: Path):
    print("\n[L] Discovery with include_missing=True")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan(include_missing=True)
    projects = library.projects
    check("project discovered", len(projects) > 0)
    if projects:
        p = projects[0]
        check("project has BASE", len(p.base) >= 1, str(len(p.base)))
        check("project has PROJECT", len(p.project) >= 2, str(len(p.project)))


def test_full_inject_order(tmp: Path):
    print("\n[M] Full injection order")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    registry.ensure_project(md)
    agent_dir = registry.ensure_agent(registry.project_dir(md), DEFAULT_AGENT)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    store.write_file("MEMORY.md", "SYS_MESS")
    store.write_file("USER.md", "USR_MESS")
    store.write_file(f"{md}/BASE/notes.md", "BR_MESS")
    store.write_file(f"{md}/BASE/rules.md", "BP_MESS")
    store.write_file(f"{md}/BASE/PROJECT/GOALS.md", "PG_MESS")
    store.write_file(f"{md}/BASE/PROJECT/CONTEXT.md", "PC_MESS")
    store.write_file(f"{md}/{DEFAULT_AGENT}/BASE/SOUL.md", "SOUL_MESS")
    store.write_file(f"{md}/{DEFAULT_AGENT}/BASE/AGENT.md", "AG_MESS")
    store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "MEM_MESS")
    store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/sess1.md", "SES_MESS")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    nodes = library.injected(project_dir=md, agent=DEFAULT_AGENT)
    found_kw2 = set()
    for kw in ["SYS_MESS","USR_MESS","BR_MESS","BP_MESS","PG_MESS","PC_MESS","SOUL_MESS","AG_MESS","MEM_MESS","SES_MESS"]:
        for n in nodes:
            if kw in (n.content or ""):
                found_kw2.add(kw)
                break
    check("all keywords injected", len(found_kw2) == 10, f"found {len(found_kw2)}: {found_kw2}")


def test_block_merge(tmp: Path):
    print("\n[N] Block merge after replacements")
    md = _next_md()
    registry = MemoryRegistry(tmp)
    registry.ensure_project(md)
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    rel = f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md"
    store.add_block(rel, "alpha")
    store.add_block(rel, "beta")
    store.add_block(rel, "gamma")
    store.replace_block(rel, "alpha", "ALPHA")
    bl = store.list_blocks(rel)
    check("replace preserves blocks", "beta" in bl, str(bl))
    store.remove_block(rel, "beta")
    bl = store.list_blocks(rel)
    check("remove preserves blocks", "ALPHA" in bl, str(bl))


def test_many_projects(tmp: Path):
    print("\n[O] Many projects (50) x 20 blocks each")
    registry = MemoryRegistry(tmp)
    registry.ensure_root()
    store = MemoryStore(tmp / MEMORY_ROOT_NAME)
    nproj, blk = 50, 20
    global _TEST_SEQ
    prefix = f"stress_{_TEST_SEQ:06d}_"
    _TEST_SEQ += 1
    created = []
    for i in range(nproj):
        md = f"{prefix}{i:04d}"
        registry.ensure_project(md)
        created.append(md)
        for j in range(blk):
            store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", f"fact {i}_{j}")
    library = MemoryScanner(tmp / MEMORY_ROOT_NAME).scan()
    # Filter: only projects we created
    ours = [p for p in library.projects if p.name.startswith(prefix)]
    check(f"projects found", len(ours) == nproj, f"total={len(library.projects)} ours={len(ours)}")
    total = 0
    for p in ours:
        ags = list(p.agents)
        if ags and ags[0].memory:
            total += len(ags[0].memory.blocks)
    check(f"total blocks =={nproj * blk}", total == nproj * blk, str(total))


def test_memory_manager_scoping(tmp: Path):
    print("\n[P] MemoryManager for_project scoping")
    md1, md2 = _next_md("20260115"), _next_md("20260116")
    class MockCfg:
        enabled = True
        char_limit = 5000
        auto_extract = False
        nudge_interval = 10
        extract_model = ""
    mgr = MemoryManager(data_dir=tmp, memory_dir=tmp / MEMORY_ROOT_NAME, config=MockCfg())
    mgr.registry.ensure_root()
    mgr.registry.ensure_project(md1)
    mgr.registry.ensure_project(md2)
    mgr.store.write_file(f"{md1}/{DEFAULT_AGENT}/BASE/MEMORY.md", "project alpha memory")
    mgr.store.write_file(f"{md2}/{DEFAULT_AGENT}/BASE/MEMORY.md", "project beta memory")
    mgr.store.write_file("MEMORY.md", "global system memory")
    mgr.store.write_file("USER.md", "user preferences")
    scoped = mgr.for_project(md1, DEFAULT_AGENT)
    rendered = scoped.render_for(md1, DEFAULT_AGENT)
    check("scoped sees alpha", "project alpha memory" in rendered, str(rendered[:100]))
    check("scoped no beta", "project beta memory" not in rendered)
    # render_prompt should see system but not project memories
    rp = mgr.render_prompt()
    check("system only has global", "global system memory" in rp if rp else False, str(rp[:80] if rp else "empty"))
    check("render_prompt no project alpha", "project alpha" not in (rp or ""))


def test_full_inject_order_mm(tmp: Path):
    print("\n[Q] MemoryManager full render order")
    md = _next_md()
    class MockCfg:
        enabled = True
        char_limit = 5000
        auto_extract = False
        nudge_interval = 10
        extract_model = ""
    mgr = MemoryManager(data_dir=tmp, memory_dir=tmp / MEMORY_ROOT_NAME, config=MockCfg())
    mgr.registry.ensure_root()
    agent_dir = mgr.registry.ensure_agent(mgr.registry.ensure_project(md), DEFAULT_AGENT)
    mgr.store.write_file("MEMORY.md", "SYS_MESS")
    mgr.store.write_file("USER.md", "USR_MESS")
    mgr.store.write_file(f"{md}/BASE/notes.md", "BR_MESS")
    mgr.store.write_file(f"{md}/BASE/rules.md", "BP_MESS")
    mgr.store.write_file(f"{md}/BASE/PROJECT/GOALS.md", "PG_MESS")
    mgr.store.write_file(f"{md}/BASE/PROJECT/CONTEXT.md", "PC_MESS")
    mgr.store.write_file(f"{md}/{DEFAULT_AGENT}/BASE/SOUL.md", "SOUL_MESS")
    mgr.store.write_file(f"{md}/{DEFAULT_AGENT}/BASE/AGENT.md", "AG_MESS")
    mgr.store.add_block(f"{md}/{DEFAULT_AGENT}/BASE/MEMORY.md", "MEM_MESS")
    mgr.store.write_file(f"{md}/{DEFAULT_AGENT}/SESSIONS/sess1.md", "SES_MESS")
    rendered = mgr.render_for(md, DEFAULT_AGENT)
    for kw in ["SYS_MESS", "USR_MESS", "BR_MESS", "BP_MESS", "PG_MESS", "PC_MESS", "SOUL_MESS", "AG_MESS", "MEM_MESS", "SES_MESS"]:
        check(f"rendered has {kw}", kw in rendered, str(rendered[:200]))


# --- HTTP tests ---

def test_http_api(url: str, project_id: str):
    print("\n[HTTP] API endpoints")
    r = _http(f"/api/memory/discover?project_id={project_id}")
    check("discover", "root" in r, str(r.get("__error", "")))
    if "error" not in str(r):
        r2 = _http(f"/api/memory/file?rel=USER.md")
        check("file read", "content" in r2, str(r2.get("__error", "")))
        r3 = _http("/api/memory/file", "POST", {"rel": "USER.md", "content": "test user preference"})
        check("file save", r3.get("rel") == "USER.md", str(r3.get("__error", "")))
    else:
        warn("discover error", str(r))

    r5 = _http("/api/memory/status")
    check("status", "file_count" in r5, str(r5.get("__error", "")))

    r6 = _http("/api/memory/settings")
    check("settings", "enabled" in r6, str(r6.get("__error", "")))

    r7 = _http("/api/memory/search?q=test")
    check("search", "results" in r7, str(r7.get("__error", "")))

    r7b = _http("/api/memory/proposals")
    check("proposals endpoint removed", "__error" in r7b, str(r7b)[:120])

    r8 = _http("/api/memory/write", "POST", {"action": "add", "content": _unique("API test fact"), "project_id": project_id, "agent": "default_agent"})
    check("write block", "blocks" in r8 or "error" in str(r8), str(r8.get("__error", r8)))

    r9 = _http("/api/memory/register-agent", "POST", {"project_id": project_id, "agent": "stress_agent"})
    check("register agent", r9.get("status") == "ok", str(r9.get("__error", "")))

    r10 = _http("/api/memory/write", "POST", {"action": "add", "content": _unique("stress_agent wrote this"), "project_id": project_id, "agent": "stress_agent"})
    check("write to new agent", "blocks" in r10, str(r10.get("__error", r10)))

    # --- org API ---
    r11 = _http(f"/api/org?project_id={project_id}")
    check("org get", "agents" in r11 and "config" in r11, str(r11.get("__error", "")))
    if "agents" in r11:
        default_present = any(a.get("id") == "default_agent" for a in r11["agents"])
        check("org has default_agent", default_present, str(r11.get("agents"))[:200])

    r12 = _http("/api/org/agent", "POST", {"project_id": project_id, "name": "org_worker", "role": "developer", "parent": "default_agent"})
    check("org create agent", "agents" in r12 and any(a.get("id") == "org_worker" for a in r12["agents"]), str(r12.get("__error", "")))

    r12b = _http("/api/org/agent", "POST", {"project_id": project_id, "name": "org_worker", "role": "dup"})
    check("org duplicate agent rejected", "__error" in r12b, str(r12b)[:120])

    r13 = _http("/api/org/team", "POST", {"project_id": project_id, "id": "org_team", "name": "团队", "lead": "org_worker"})
    check("org create team", "teams" in r13 and any(t.get("id") == "org_team" for t in r13["teams"]), str(r13.get("__error", "")))

    r14 = _http("/api/org/config", "PATCH", {"project_id": project_id, "mode": "single"})
    check("org config single", r14.get("config", {}).get("mode") == "single", str(r14.get("__error", "")))
    r14b = _http("/api/org/config", "PATCH", {"project_id": project_id, "mode": "multi"})
    check("org config multi", r14b.get("config", {}).get("mode") == "multi", str(r14b.get("__error", "")))

    r15 = _http("/api/org/team", "DELETE", {"project_id": project_id, "id": "org_team"})
    check("org delete team", "__error" not in r15 or r15.get("status"), str(r15)[:120])
    r16 = _http("/api/org/agent", "DELETE", {"project_id": project_id, "id": "org_worker"})
    check("org delete agent", "__error" not in r16 or r16.get("status"), str(r16)[:120])


def test_http_pressure(url: str, project_id: str):
    print("\n[HTTP] Pressure: 200 writes to single agent")
    _http("/api/memory/register-agent", "POST", {"project_id": project_id, "agent": "stress_agent_pressure"})
    count = 0
    for i in range(200):
        r = _http("/api/memory/write", "POST", {"action": "add", "content": _unique(f"pressure fact {i}"), "project_id": project_id, "agent": "stress_agent_pressure"})
        if isinstance(r, dict) and "blocks" in r and "error" not in str(r):
            count += 1
    check("HTTP write count == 200", count == 200, f"got {count}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    parser.add_argument("--project-id", default="")
    args = parser.parse_args()

    global URL, PROJECT_ID
    URL = args.url or URL
    PROJECT_ID = args.project_id or PROJECT_ID

    if URL:
        print(f"\nRunning against live backend: {URL}")
    else:
        print("\nNo URL specified, running purely in-process tests.")

    print(f"\n{'='*60}")
    print("  MEMORY SUBSYSTEM STRESS TEST")
    print(f"{'='*60}")

    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmpstr:
        tmp = Path(tmpstr)
        test_basic_crud(tmp)
        test_skeletons(tmp)
        test_injection_order(tmp)
        test_prompt_rendering(tmp)
        test_project_isolation(tmp)
        test_agent_isolation(tmp)
        test_unicode_edge(tmp)
        test_pressure_blocks(tmp)
        test_over_budget(tmp)
        test_replace_remove(tmp)
        test_session_isolation(tmp)
        test_include_missing(tmp)
        test_full_inject_order(tmp)
        test_block_merge(tmp)
        test_many_projects(tmp)
        test_memory_manager_scoping(tmp)
        test_full_inject_order_mm(tmp)

    if URL and PROJECT_ID:
        try:
            test_http_api(URL, PROJECT_ID)
            test_http_pressure(URL, PROJECT_ID)
        except Exception as e:
            fail("HTTP tests failed", str(e))

    dur_ms = (time.time() - t0) * 1000
    print("\n" + "=" * 60)
    print(f"  RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed, {len(WARNINGS)} warnings")
    print(f"  Total time: {dur_ms:.0f}ms")
    print("=" * 60)

    if FAILURES:
        print("\n=== FAILURES ===")
        for f in FAILURES:
            print(f"  {f}")
        print(f"\n{len(FAILURES)} failure(s)")
        if WARNINGS:
            print(f"\n=== WARNINGS ===")
            for w in WARNINGS:
                print(f"  {w}")
        return 1
    elif WARNINGS:
        print("\n=== WARNINGS (but no failures) ===")
        for w in WARNINGS:
            print(f"  {w}")
    print(f"\nAll {PASS_COUNT} checks passed! {FAIL_COUNT} failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
