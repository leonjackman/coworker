# Acceptance — Platform Alignment & Robustness (Computer Use × Workflow)

> Repeatable acceptance for the bilateral-alignment work. Run on each OS.
> Scope: P0–P3 in [`PLATFORM-ALIGNMENT-HANDOFF.md`](./PLATFORM-ALIGNMENT-HANDOFF.md).

## Automated gates

| # | Gate | Command | Expected |
|---|---|---|---|
| A | Backend suite | `$env:PYTHONPATH="backend"; backend/venv/Scripts/python.exe -m pytest backend/tests -q` | all pass except the documented environmental failures (below) |
| B | JS syntax | `node --check electron/automation/driver.js; node --check electron/desktop-controller.js; node --check scripts/automation-conformance.js` | clean |
| C1 | Contract conformance | `node scripts/automation-conformance.js <helper>` | `PASSED` |
| C2 | Manifest + behavior probe | `node scripts/computer-use-probe.js <helper>` | `13 passed, 0 failed` |
| C3 | Parity (two helpers) | `node scripts/automation-conformance.js <mac> --parity <win>` | method set + feature key set match (value divergence only a warning) |
| D | New behavior tests | `pytest backend/tests -q -k "js_escaped or capabilities_view or repeated_failure or unchanged or bridge_kinds or platform_suggestion or unzip or notify_body or capability_notes or native_script_tools or action_label or reject_assert"` | all pass |
| E | Action-contract drift guard | `pytest backend/tests/test_action_contract.py -q` | all pass (table ↔ kernel/host) |

Helper paths: Windows `electron/cw-automa-win/bin/cwautoma-win.exe`; macOS `electron/cw-automa/build/cw-automa`.

## Manual / environment gates

- **Windows helper build** — the exe is locked while the app runs. Quit the app, or publish to a temp dir:
  `dotnet publish electron/cw-automa-win/cwautoma-win.csproj -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o <temp>`
  (or `npm run build:automa-win`). Then run gates C1/C2 against the produced exe.
- **macOS helper build** — `mac-driver` auto-compiles via `swiftc` in dev; run C1/C2/C3 after the first run. (The Swift changes are not compiled on Windows.)

## Recorded results

### 2026-10-05 — Windows (developer machine) — root-cause contract batch
- A: **809 passed**, 2 skipped, **10 failed** (environmental, see below).
- B: clean.
- C1: `Conformance PASSED (0 warnings)` on `cwautoma-win.exe` (version 4, 12 manifest keys, 35 methods).
- C2: **13 passed, 0 failed**.
- D: all pass.
- E: **5 passed** (contract drift guard: every `script` binding exists in the kernel; every emitted RPC is routed by the host).

### macOS
- Pending the switch (run the same gates; the Swift/`click_point`/`act`/`delete` changes need a macOS compile).

## Documented environmental failures (not regressions)
Confirmed identical on `HEAD` (pre-change) via `git stash`:

| Test | Root cause |
|---|---|
| `test_node_execution_contracts::test_command_set_wait_assert_execute` | a command binary not on this Windows PATH |
| `test_schedules::*` (4) + `test_schedules_api::*` (2) | `zoneinfo`/`tzdata` missing (`timezone_ok("Asia/Shanghai")` False) |
| `test_search_ripgrep::test_rg_fast_path_used_when_available` | `rg` (ripgrep) not installed |
| `test_shell_allowlist_bypass::test_allowlisted_programs_are_all_permitted[argv2]` | platform allowlist expectation on Windows |

Install `tzdata`/`ripgrep` and put the missing binary on PATH to clear them; they are unrelated to this work.
