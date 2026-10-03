# Windows Computer Use — Development & Handoff

> Audience: the agent (or engineer) continuing Windows computer-use development on a Windows machine.
> Status: **Windows helper builds and passes contract conformance.** macOS is unchanged and verified
> green. On Windows: the helper compiles (one FlaUI `Focus()` return-type fix), `ping` +
> `automation-conformance.js` pass, the DIP→physical mapping matches Electron
> `screen.dipToScreenPoint` at 150% scale, and `uipi_blocked` detection is implemented. Remaining:
> multi-monitor validation, full-app end-to-end (`computer_observe`/`computer_script`), packaging.

---

## 1. What this is

CoWorker's OS-level computer use was macOS-only. It is now a **single shared orchestration layer
with a frozen JSON-RPC contract and one thin native driver per platform**:

```
                ┌──────────────────────────────────────────────────────────┐
   Python       │ backend/coworker/computer/bridge_client.py (+actions.py)  │   platform-neutral
   (tools)      │ tool schema, HITL, loop guard, verification               │
                └───────────────────────────┬──────────────────────────────┘
                                            │ loopback HTTP bridge (main.js)
                ┌───────────────────────────▼──────────────────────────────┐
   Electron     │ electron/desktop-controller.js  (screen capture, pause,   │   platform-neutral
   (main)       │ coordinate mapping, JS-worker surface)                    │
                └───────────────────────────┬──────────────────────────────┘
                                            │ createDriver()  (electron/automation/index.js)
                ┌───────────────────────────▼──────────────────────────────┐
   Driver       │ electron/automation/driver.js  (HelperDriver base)        │   shared
                │   mac-driver.js ──► cw-automa   (Swift, Accessibility)    │   per-OS
                │   win-driver.js ──► cwautoma-win (C#, UI Automation)      │   per-OS
                └──────────────────────────────────────────────────────────┘
```

**Design rule:** shared code (`desktop-controller.js`, `computer-repl*.js`, backend) never branches
on `process.platform`. It asks the driver `supports(method)` / `supported` and delegates. Adding a
platform is additive: implement `HelperDriver`, register it in `index.js`, ship a helper binary.

---

## 2. Files

### Added
| Path | Purpose |
|---|---|
| `electron/automation/driver.js` | `HelperDriver` base: spawn, JSON-lines JSON-RPC, boot/ping, `capabilities`, `supports()`, all high-level methods |
| `electron/automation/mac-driver.js` | macOS driver: `cw-automa` binary resolution + dev `swiftc` auto-compile |
| `electron/automation/win-driver.js` | Windows driver: `cwautoma-win.exe` resolution + DIP→physical coordinate conversion |
| `electron/automation/index.js` | `createDriver()` factory + back-compat re-exports |
| `electron/cw-automa-win/cwautoma-win.csproj` | .NET 8 (FlaUI) project, `win-x64`, self-contained, `PublishSingleFile` |
| `electron/cw-automa-win/app.manifest` | Per-Monitor-DPI-v2 awareness (critical for coordinates) |
| `electron/cw-automa-win/Program.cs` | stdin loop + method dispatch + snapshot diff |
| `electron/cw-automa-win/Protocol.cs` | request parsing, `Responder`, `Params` helpers |
| `electron/cw-automa-win/Input.cs` | `SendInput` P/Invoke, key table, unicode typing, drag/scroll |
| `electron/cw-automa-win/Monitors.cs` | physical monitor enumeration (`EnumDisplayMonitors` + `GetDpiForMonitor`) |
| `electron/cw-automa-win/NativeMethods.cs` | window/process P/Invoke (foreground/activate) |
| `electron/cw-automa-win/UiaTree.cs` | UIA tree → `sig#n` refs, re-find, settle signature |
| `electron/cw-automa-win/UiaActions.cs` | ref actions: Invoke/Value/Toggle/ScrollItem + coord fallback |
| `electron/cw-automa-win/TextInput.cs` | text ladder: ValuePattern → Unicode keys → clipboard |
| `electron/cw-automa-win/AppInventory.cs` | list/resolve/launch/focus apps |
| `electron/cw-automa-win/Overlay.cs` | always-on-top click-through virtual cursor + HUD |
| `scripts/automation-conformance.js` | cross-platform contract test for any helper |

### Changed
| Path | Change |
|---|---|
| `electron/desktop-controller.js` | Removed all `if (process.platform !== 'darwin')` gates; single `_driverInstance()` via factory; paste modifier is platform-aware; `scriptRun` gates on `driver.supported` |
| `electron/automation-adapter.js` | Now a back-compat shim re-exporting `MacDriver` as `AutomationAdapter` |
| `electron/cw-automa/src/main.swift` | Additive only: `ping` now returns `version:3`, `methods[]`, `features{}`; `helperMethods` constant added |
| `backend/coworker/computer/bridge_client.py` | Docstrings generalized (AX/UIA); `go_back` is platform-aware (`Alt+Left` on Windows) |
| `backend/coworker/computer/actions.py` | Action descriptions generalized (macOS bundle id / Windows process name) |
| `package.json` | Added `build:automa-win`; `build:windows` builds it |
| `electron-builder.config.json` | `win.extraResources` copies `cwautoma-win.exe`; excludes `cw-automa-win/**` from asar |
| `.github/workflows/release.yml` | Windows job: `setup-dotnet` + `build:automa-win` + conformance; macOS job: conformance |

---

## 3. Frozen wire contract

Newline-delimited JSON over the helper's **stdio**:

```
in : {"id":N,"method":"snapshot","params":{...}}
out: {"id":N,"ok":true,"result":{...}}
   | {"id":N,"ok":false,"error":"...","error_code":"..."}
```

`ping` (capability handshake):

```json
{"platform":"darwin|win32","version":3,
 "methods":["ping","frontmost","displays","snapshot","get_app_state","act",
   "list_apps","resolve_app","focus_app","launch","input_text","type_text",
   "press_hotkey","press_key","click_coords","click_point","drag_point","scroll",
   "scroll_to","drag_to","click_point_to","ui_settle","cursor_move","cursor_show",
   "cursor_hide","cursor_park","cursor_debug","cursor_demo","cursor_position",
   "hud_show","hud_pause","hud_hide","set_stop_label","permissions","permissions_request"],
 "features":{"overlay":true,"ax":<bool>,"uia":<bool>,"physical_displays":<bool>}}
```

Result shapes the shared layer relies on:

| Method | Result shape |
|---|---|
| `snapshot` / `get_app_state` | `{frontmost, app, pid, refs:number, text:string, root:object, window:{title,frame?}, changed:bool, removed?:string[], diff:bool}` |
| `frontmost` | `{pid:number, app:string}` |
| `displays` | `{displays:[{index,id,bounds:{x,y,width,height},scale_factor,primary?,internal?}]}` — **Windows `bounds` are PHYSICAL pixels**; macOS logical points |
| `list_apps` | `{scope, apps:[{appId,bundleId,displayName,pid,path?}]}` |
| `resolve_app` | `{selector, pid?, appId?/bundleId?, path?}` |
| `act` | `{performed, via?/strategy?, ...}` |
| `input_text` | `{performed:"input_text", submit, strategy, verified, value}` |
| `permissions` | `{accessibility:bool, screen:bool, ...}` |
| cursor/hud | `{shown/paused/performed, ...}` |

`ref` format (both platforms): `sig#n` where `sig = normalized("<role>:<name>")` lowercased and `n`
is that sig's occurrence ordinal — an **identity**, stable across UI churn. Root ref is `"window"`.

### Capability negotiation
`HelperDriver` records `ping.methods`/`ping.features`. `supports(m)` returns `true` when `methods`
is absent (older helper). `desktop-controller` uses `driver.supported` (false for unsupported OS)
and the driver throws `unsupported_method` for methods not advertised — never a platform branch.

---

## 4. macOS (do not break)

- `electron/cw-automa/**` is **unchanged** except the additive `ping` fields + `helperMethods` constant.
- `MacDriver` is a behavior-preserving extraction of the old `AutomationAdapter`.
- Verified green after the refactor:
  - `node scripts/automation-conformance.js electron/cw-automa/build/cw-automa` → PASS
  - `node scripts/computer-repl-smoke.js` → PASS
  - `backend/venv/bin/python -m pytest backend/tests/test_computer_use.py -q` → 40 passed

**Rule for all future Windows work:** keep it additive. Re-run the three commands above after any
shared-layer change.

---

## 5. Windows helper design (`cw-automa-win`)

Mirrors `cw-automa` method-for-method; macOS AX concepts map to Windows UIA:

| macOS (AX) | Windows (UIA) |
|---|---|
| `AXUIElementCreateApplication(pid)` | `AutomationElement` by foreground handle / `FindAllChildren(ByProcessId)` |
| `AX.focusedWindow` + child walk | `TreeWalker.ControlViewWalker` + depth limit |
| ref `sig#n` | ref `sig#n` (same generation rule, `ControlType:Name`) |
| `AXUIElementPerformAction(kAXPressAction)` | `InvokePattern.Invoke()` |
| `AXUIElementSetAttributeValue(kAXValue)` | `ValuePattern.SetValue()` |
| center + `Injection.click` fallback | `BoundingRectangle` center + `SendInput` |
| `CGEvent.postToPid` (never moves cursor) | `SendInput` (**coordinate actions DO move the real cursor**; ref actions don't) |
| text ladder (value → unicode → paste) | `ValuePattern` → Unicode `SendInput` → `Ctrl+V` |
| `list_apps`/`launch` | `Process.GetProcesses()` / `Process.Start(UseShellExecute)` |
| TCC permissions | no-op (`{accessibility:true, screen:true}`); elevation is the real gate |
| `VirtualCursor`/`StatusHUD` | `Overlay.cs` WinForms topmost click-through windows |

### Coordinate mapping (DIP → physical) — the #1 thing to validate
Electron reports display bounds in **DIP**; `SendInput` needs **physical pixels**. `win-driver.js`
pairs `electron.screen.getAllDisplays()` with the helper's `displays` **by index** and maps a point
by its **fraction within the monitor**:

```
physicalX = monitor.physical.x + (x - electronDisplay.bounds.x) * (monitor.physical.width / electronDisplay.bounds.width)
```

This avoids needing the absolute DIP origin to match. **Validate on mixed-DPI / multi-monitor.** If
pairing-by-index is wrong, use the monitor's device name / relative position to match instead.

> Validated on a single 3840×2160 @150% monitor: `WinDriver._toPhysical` matched Electron's
> `screen.dipToScreenPoint` exactly for all sampled points. A cleaner alternative to index-pairing,
> if a multi-monitor discrepancy appears, is to call `screen.dipToScreenPoint()` directly (it is
> Windows-only and already reflects the correct monitor).

### Cursor behaviour difference
macOS never moves the real cursor (per-PID events). Windows has no equivalent, so **coordinate
fallback actions move the real mouse**. Ref/pattern actions (Invoke/SetValue) do not. If "hands-off"
parity is required, investigate `PostMessage`/UIA-only paths (unreliable for many apps) — document
the tradeoff rather than silently changing behaviour.

---

## 6. Build & run on Windows

### Prerequisites
- .NET SDK 8.0 (`dotnet --version`)
- Node 22, Python 3.11 (as for the rest of the app)

### Build the helper
```powershell
npm run build:automa-win
# -> electron/cw-automa-win/bin/cwautoma-win.exe
```
Under the hood: `dotnet publish electron/cw-automa-win/cwautoma-win.csproj -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o electron/cw-automa-win/bin`

### Manual protocol smoke (no app needed)
```powershell
'{ "id":1,"method":"ping","params":{} }' | .\electron\cw-automa-win\bin\cwautoma-win.exe
```
Expect one JSON line with `"platform":"win32"` and the full `methods` array.

### Contract conformance
```powershell
node scripts/automation-conformance.js electron/cw-automa-win/bin/cwautoma-win.exe
```
Hard-fails on: missing helper, bad `ping`, missing contract methods, bad basic shapes.
Warns (not fatal) on headless-session UI calls (`snapshot`).

### Full app (dev)
```powershell
.\coworker_desktop.ps1
```
Enable Settings → Computer Use, then exercise `computer_observe` (snapshot/screenshot) and
`computer`/`computer_script` on Notepad / Calculator / File Explorer.

---

## 7. Known risks / TODO on Windows (validate in this order)

1. ~~**Compile the C# helper and fix compile errors.**~~ **DONE.** One error fixed: FlaUI
   `AutomationElement.Focus()` returns `void`, not `bool` (`UiaActions.FocusElement`). Also switched
   DPI awareness from `app.manifest` to `Application.SetHighDpiMode(PerMonitorV2)` (clears the
   WinForms `WFAC010` warning) and added `bin/`/`obj/` to `.gitignore`.
2. **DPI / multi-monitor mapping** (see §5). 150% single-monitor **validated** (matches Electron's
   `dipToScreenPoint`). Still to test: 100% scaling and a **secondary monitor** — index-pairing is
   the only untested assumption.
3. **UIPI / elevation** — **IMPLEMENTED.** `Elevation.cs` compares the helper's token elevation with
   the target pid; `snapshot` / `act` / `input_text` / `type_text` throw `uipi_blocked` (+ `hint`)
   before touching the UI, and `Input.Send` maps `SendInput` `ERROR_ACCESS_DENIED` (5) to the same
   error. Verified: SYSTEM processes → `uipi_blocked`; medium-integrity apps unaffected.
4. **UIA tree quality**: Electron/Chromium apps may expose a thin UIA tree; canvas/games expose
   almost nothing. Coordinate fallback (`click_coords`) is the escape hatch. Consider enabling
   Chromium's UIA (`--force-renderer-accessibility`) for `webview` content if needed.
5. **`snapshot` window selection**: `FindWindow` uses the foreground window if pid matches, else the
   largest top-level `Window`. Verify with apps that have multiple top-level windows (dialogs).
6. **Overlay**: verify it is click-through (`WS_EX_TRANSPARENT`) and never steals focus; verify it
   does not appear in screenshots taken by `desktopCapturer` in a way that confuses the model.
7. **`type_text` without ref** uses clipboard + `Ctrl+V`; ensure `Clipboard.SetText` runs on the STA
   main thread (it does — `Main` is `[STAThread]`).
8. **Secure desktop** (UAC prompt, Ctrl+Alt+Del) is intentionally not automatable.

---

## 8. Adding a platform or method later

- **New OS**: add `electron/automation/<os>-driver.js` extending `HelperDriver` (implement
  `candidateBinaryPaths`, `_resolve`, optionally `_ensureExecutable`), register in `index.js`.
  Implement a helper that speaks §3. Add packaging/CI. No shared-code edits.
- **New method**: add it to BOTH helpers' dispatch and the `methods` list, add high-level wrapper in
  `driver.js`, expose a route in `main.js` if the backend needs it, and add it to
  `REQUIRED_METHODS` in `scripts/automation-conformance.js`.

---

## 9. Verification checklist (run before claiming done)

macOS:
- [ ] `node scripts/automation-conformance.js electron/cw-automa/build/cw-automa`
- [ ] `node scripts/computer-repl-smoke.js`
- [ ] `backend/venv/bin/python -m pytest backend/tests/test_computer_use.py -q`

Windows:
- [x] `npm run build:automa-win` (compiles clean; requires the .NET 8 **SDK**, not just the runtime)
- [x] manual `ping` smoke
- [x] `node scripts/automation-conformance.js electron/cw-automa-win/bin/cwautoma-win.exe`
- [~] end-to-end: helper-level `snapshot` + `input_text`/`set_value` verified on Notepad; full-app
      `computer_observe` screenshot + `computer_script` still to run
- [~] multi-monitor + mixed DPI click accuracy: 150% single-monitor mapping verified; secondary
      monitor TBD
- [ ] `state` reports `platform: win32`; pause hotkey `Ctrl+Shift+Esc` works (code path present)

---

## 10. Root-cause fixes (post-first-implementation)

Driven by a real Windows session (agent failed to drive Paint / Calculator). Fixed at the source,
not patched at the call site:

- **Ref vocabulary was macOS-only in shared strings.** The model-facing notes and the capability
  line hardcoded `[axbutton:搜索#1]`, so the model invented an `ax…` prefix on Windows (whose helper
  emits `button:…`), producing a cascade of `no UIA element` failures. `_element_note()` now names
  both platforms AND emits the **real** refs read from the current snapshot (`_sample_refs`); it also
  states the tree covers one window and points at `list_apps`/`app_state(app=…)`.
- **Stale refs were undetectable on Windows.** Both helpers now return a dedicated
  **`stale_ref`** error code (not generic `computer_error`); the backend self-heals by returning a
  `fresh_snapshot` so the model re-picks a ref. Legacy string matching is retained for old helpers.
- **`type_into` contradicted its own schema.** The catalog marks `ref` optional, but the runtime
  hard-failed (`act requires a ref`) when neither `ref` nor `app` was given. With no ref it now types
  into the focused field of `app` (or the frontmost app), i.e. it no longer needs a ref to fill a
  search box you just focused.
- **`computer_script(reset=true, code=…)` silently dropped the code.** It now resets and then runs
  the code in the fresh session (a reset with no code stays a pure reset).
- **`launch_app` could report success without launching.** Verification used "some window became
  frontmost", so on Windows a missing program's shell error dialog counted as success. The Windows
  helper now resolves the app (PATH / App Paths / shell alias, refusing unknown names instead of
  handing them to the shell), waits for the real process, activates it, and returns a truthful
  `{ok, pid}`; the backend verifies via that pid (falling back to `resolve_app`), never a bare
  frontmost change. `ResolvePid` also strips a `.exe` suffix so `notepad.exe` resolves.
- **No reliable clock.** `computer_observe state` now returns local `now`/`now_iso`/`timezone`/
  `utc_offset_minutes`, and the capability line tells the model to use it for "current time" tasks
  instead of guessing from web search.

Contract additions (additive, §3): `launch` → `{launched, ok, pid}`; error code `stale_ref`;
`state` → `{…, now, now_iso, timezone, utc_offset_minutes}`.
