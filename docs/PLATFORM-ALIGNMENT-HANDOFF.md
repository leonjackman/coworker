# Platform Alignment & Robustness — Roadmap & Handoff (Computer Use × Workflow)

> Audience: the engineer/agent continuing this work, **starting on Windows and
> later switching to macOS**.
> Contract reference: [`COMPUTER-USE-PARITY.md`](./COMPUTER-USE-PARITY.md).
> Prior Windows build details: [`WINDOWS-COMPUTER-USE-HANDOFF.md`](./WINDOWS-COMPUTER-USE-HANDOFF.md).

## Goal
Make COMPUTER USE and WORKFLOW **bilateral** (macOS ⇄ Windows behavior aligned)
and **root-cause robust**: behavior is driven by an advertised capability
manifest, failures are loud and early, and drift is caught by contract tests.

## Decisions (locked with the user)
1. **Bilateral alignment**: where a platform cannot match a capability, it is
   **explicitly marked `unsupported`**, gated, documented and surfaced — not
   silently degraded. Deep Windows input re-architecture (per-app targeting /
   no cursor movement) is a *separate later research item* (§P3), not a blocker.
2. **Capability-driven, not platform-driven**: refactor `process.platform` /
   `sys.platform` branches in the *shared* computer-use layer into capability
   checks (WS-A → P3).
3. **Workflow platform tags are auto-inferred** (`infer_platforms`) in addition
   to explicit declarations.
4. Adopt phases **P0 → P3**; fold in the previously-deferred **capabilities
   payload compaction** and **iteration circuit breaker** into P2.
5. CI can run both OSes; develop on Windows first, then switch to macOS using
   this document.

---

## Status board

| Phase | Scope | Status |
|---|---|---|
| **P0** | capability manifest + conformance + low-risk correctness fixes | **done** |
| **P1** | Windows Computer Use B1 behavioral parity | **partial** (5/9 done — see below) |
| **P2** | Workflow robustness (inference, gating, ActionSpec, compaction, circuit breaker) | pending |
| **P3** | Capability-gate refactor; Windows input research; mac small gaps | pending |

### P0 — delivered
- **WS-C**: `unzip` removed from `MACOS_ONLY_TOKENS`
  (`workflows/platform_support.py`); JS-safe template resolution now covers
  `app` as well as `computer` script steps (`workflows/executor.py`
  `_resolve_payload` + `_SCRIPT_KINDS`).
- **`disable_diff` end-to-end**:
  `electron/computer-repl-kernel.js` (already sent it) →
  `desktop-controller.js` `_replCall`/`axAppState` →
  `automation/driver.js` `getAppState(..., disableDiff)` → helper.
- **WS-A manifest**: both helpers advertise `version:4` + the 12-key feature
  manifest (`cw-automa/src/main.swift`, `cw-automa-win/Program.cs`);
  `driver.js` adds `featureValue/features/ensureReady`; `desktop-controller.state()`
  includes `features`+`methods`; backend `_capability_notes()`
  (`computer/bridge_client.py`) turns gaps into model guidance.
- **WS-D conformance**: `scripts/automation-conformance.js` validates the 12
  manifest keys and supports `--parity <other-helper>` (method-set + feature-key-set
  comparison); `--json` for CI.

---

## P1 — Windows Computer Use B1 parity
Files: `electron/cw-automa-win/{Program.cs,Input.cs,TextInput.cs,AppInventory.cs,UiaActions.cs,Overlay.cs}`.

**Done (verified: builds clean; conformance passes; `installed`=148, `no_target`
and `cursor_demo` confirmed via a helper probe):**
- **Clipboard restore** — `TextInput.PasteText` saves/restores prior text (item 3).
- **Unicode grapheme/surrogate-safe typing** — `Input.TypeUnicode` groups
  surrogate pairs in one `SendInput` (item 4). Flip `unicode_graphemes` ✅.
- **`list_apps(installed)`** real inventory — Start Menu shortcuts + App Paths
  (`AppInventory.Installed`) (item 5).
- **`launch` localized display name** — falls back to a Start Menu shortcut,
  confirmed via a newly appeared windowed pid (`AppInventory.Launch`) (item 6).
- **`app_state`/`snapshot` strict `no_target`** — no silent frontmost retarget
  (`Program.HandleSnapshot` + `ResolveRequestedPid`) (item 7).
- **`cursor_demo(seconds)`** honors duration, bounded ≤15s (item 9 of old list).

**Todo:**
1. **Paste receipt / `target_confirmation`** — `TextInput` should confirm the
   clipboard paste landed (readback/target-change), not optimistically `true`.
2. **Permissions semantics** — surface UIPI/elevation as a first-class
   permission state (keep `permission_model: uipi`).
3. **`scroll`/`scroll_to`** anchor + coordinate defaults (mac falls back to the
   last pointer; Windows defaults to (0,0) when x/y omitted).
4. **mac small gaps** (do on macOS after the switch): middle-click, keypad/`fn`
   tokens, unify `delete` semantics, remove dead `realTypeInto`.

> Build note: the built exe is locked while the desktop app runs. Publish to a
> temp dir (`-o <temp>`) or quit the app; then
> `node scripts/automation-conformance.js <helper>` and the probe pattern in
> `PLATFORM-ALIGNMENT-HANDOFF` verification.

Acceptance: `node scripts/automation-conformance.js <helper>` green on both; the
flipped flags documented in `COMPUTER-USE-PARITY.md`; real-machine E2E on Windows
(clipboard unchanged after typing; permission failure surfaces clearly).

> Build note: the Windows exe is locked while the desktop app runs. Either quit
> the app, or publish to a temp dir:
> `dotnet publish electron/cw-automa-win/cwautoma-win.csproj -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o <temp>`.
> Normally `npm run build:automa-win`.

---

## P2 — Workflow robustness
Files: `workflows/platform_support.py`, `validation.py`, `capabilities.py`,
`executor.py`, `manager.py`, `agent/graph.py`, `agent/core.py`.

1. **Auto-infer platform**: wire `infer_platforms` into validation so an
   undeclared but clearly macOS-only (or Windows-only) workflow is *suggested*
   the tag (keep explicit platform required; add a `platform` suggestion to the
   `missing_platform` diagnostic).
2. **Linux/unsupported gating**: GUI `computer`/`app`/`browser` kinds are
   effectively mac+Windows only; mark them unavailable (or explicitly degraded)
   where no bridge exists instead of failing mid-run.
3. **Native-tool ActionSpec**: add declarative entries for `run_applescript`
   (darwin) / `run_powershell` (win32) so offline validation catches unavailable
   tools.
4. **Capabilities payload compaction**: `workflow action=capabilities` currently
   returns ~50KB (`capabilities.to_schema()`); add `kinds:[...]` + compact view at
   the **agent tool layer** (`agent/core.py` `WorkflowArgs`, `agent/graph.py`) —
   keep the HTTP shape (`api/workflows.py` → Studio) unchanged.
5. **Iteration circuit breaker**: `workflow action=run` reads
   `manager.list_runs(name, K)`; on ≥N same-signature consecutive failures, append
   a `guidance` field ("same step failed N times — fix step X only, do not rewrite
   the whole workflow"); plus reject a byte-identical `update` when the last run
   failed (`no_change`).
6. **JS-safe resolution generality**: any future script-bearing kind should use
   `_resolve_payload` (document in `capabilities.py`).

---

## P3 — Capability-gate refactor + research
1. Convert shared-layer `process.platform` / `sys.platform` branches (search:
   `desktop-controller.js:57,119,207,577,750`; `bridge_client.py:719`) to
   capability checks from the manifest. Keep platform checks only in
   `mac-driver.js` / `win-driver.js` / native helpers.
2. Research: Windows per-app input targeting / avoid moving the real cursor
   (UIA-first everywhere; assess scope). If infeasible, keep `background_input:false`
   + `input_model:"global"` and rely on the capability note.
3. Elevation/UIPI as a first-class permission state end-to-end.

---

## Win → Mac switch checklist
1. `git pull`; `git log --oneline -20` to see the P0–P2 commits.
2. macOS helper builds via `mac-driver` dev auto-compile (`swiftc`) — run
   `node scripts/automation-conformance.js electron/cw-automa/build/cw-automa`.
3. Run parity: `node scripts/automation-conformance.js <mac> --parity <win-exe>`
   (or run on each OS and compare `--json`).
4. `docs/COMPUTER-USE-PARITY.md` §2 table is the source of truth for which flags
   should have flipped; update it as P1 lands.
5. Backend: `$env:PYTHONPATH="backend"; backend/venv/Scripts/python.exe -m pytest backend/tests -q`
   (macOS: `venv/bin/python`). Known pre-existing failures on Windows (no `rg`,
   no `tzdata`, PATH) are environmental.

## Verification commands
```powershell
# backend tests
$env:PYTHONPATH = "backend"; & "backend\venv\Scripts\python.exe" -m pytest backend/tests -q

# contract conformance (+ parity)
node scripts/automation-conformance.js <helper> [--parity <other-helper>] [--json]

# windows helper build (quit the app first, or publish to a temp -o dir)
npm run build:automa-win
```

## Definition of done (bilateral)
- Contract conformance green on both OSes; parity method/feature key sets match.
- Every `required` capability behaves per `COMPUTER-USE-PARITY.md` on both OSes,
  or is explicitly `unsupported` + gated + documented + surfaced to the model.
- Same GUI workflow behaves equivalently on both OSes; workflow create→run no
  longer blind-retries (circuit breaker) and authoring is auto-tagged.
