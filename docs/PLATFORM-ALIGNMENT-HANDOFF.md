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
| **P1** | Windows Computer Use B1 behavioral parity | **done** |
| **P2** | Workflow robustness (inference, gating, ActionSpec, compaction, circuit breaker) | **done** |
| **P3** | Capability-gate refactor; mac small gaps; Windows input research (documented) | **done except the input-model research** |

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

**All delivered (verified: `dotnet build` clean; conformance passes; helper probe
shows the flipped manifest, rich `permissions`, and `scroll_to` without a point):**
- **Clipboard restore** — `TextInput.PasteText` saves/restores prior text.
- **Paste receipt / `target_confirmation`** — el-based entry falls back to a
  clipboard paste **then reads the value back**; flag → true.
- **Unicode grapheme/surrogate-safe typing** — surrogate pairs in one SendInput.
- **`list_apps(installed)`** real inventory — Start Menu shortcuts + App Paths.
- **`launch` localized display name** — Start Menu shortcut, confirmed via a new
  windowed pid.
- **`app_state`/`snapshot` strict `no_target`** — no silent frontmost retarget.
- **UIPI as a first-class permission state** — `permissions` reports
  `model:uipi` + `self_elevated`/`can_input_frontmost`/`blocked_reason`;
  `permissions_request` explains the elevation gate; the Electron layer gates on
  `features.permission_model` (not `process.platform`).
- **`scroll_to`** no longer jumps to (0,0): brings the target forward and scrolls
  at the current cursor when no point is given.
- **`cursor_demo(seconds)`** honors duration, ≤15s.
- **mac small gaps** — middle-click (`click_point` via `MouseButton.parse`),
  keypad tokens (added on Windows too), `delete`=forward / `backspace`=Backspace
  on both, dead `realTypeInto` removed, `act` `right`/`double` no longer collapse
  into a single AXPress.

> Build note: the built exe is locked while the desktop app runs. Publish to a
> temp dir (`-o <temp>`) or quit the app; then
> `node scripts/automation-conformance.js <helper>`.

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

1. **Auto-infer platform** — **done (advisory)**: `platform_suggestion` warning
   when steps imply exactly one OS and none is declared (`validation.py`;
   `infer_platforms` was previously only used by tests).
2. **Linux/unsupported gating** — **done**: `computer`/`app` (native bridge
   kinds; `browser` is the embedded Electron browser, so it is NOT gated) are
   rejected at authoring when `platform` is disjoint from `{darwin, win32}`.
3. **Native-tool ActionSpec** — **done**: `run_applescript`/`run_powershell`
   declared in `TOOL_ACTIONS` with outputs + per-OS platform tags
   (`capabilities.py`), so the catalog/offline validation knows them.
4. **Capabilities payload compaction** — **done**: `capabilities_view(kinds,
   verbose)` (manager) + `overview()`/`compact_schema()` (registry); agent tool
   returns a 3.3KB overview by default or a per-kind compact view, instead of the
   full 50KB (`graph.py`, `agent/core.py`). HTTP/Studio shape unchanged.
5. **Iteration circuit breaker** — **done**: `manager.repeated_failure_guidance()`
   reads run history and, on ≥N same-signature consecutive failures, the `run`
   tool result carries `guidance` ("STOP rewriting; fix step X only"). Plus the
   byte-identical `update` → `unchanged` guard (no version bump).


---

## P3 — Capability-gate refactor + research
1. **Capability-gate the permission functions** — **done**: `desktop-controller`
   `inputPermission`/`screenPermission` now read `features.permission_model`
   (set from the helper manifest in `state()`), not `process.platform`. Remaining
   `process.platform` uses are legitimate OS UX (TCC settings pane, stop hotkey
   label) and stay.
2. **mac small gaps** — **done** (see P1 note above): middle-click, keypad on
   both, `delete` unified, dead code removed, `act` right/double real events.
3. **Research (open)**: Windows per-app input targeting / avoiding real-cursor
   movement. Windows has no `postToPid`; UIA Invoke/ValuePattern already avoids
   the cursor for structure-native controls, but coordinate actions must move it.
   If a robust per-app path is not feasible, keep `background_input:false` +
   `input_model:"global"` and rely on the capability note.

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
