# Computer Use — Capability Manifest & Platform Parity

> Audience: engineers continuing the macOS ⇄ Windows computer-use alignment.
> This is the **contract reference**. The roadmap/phases live in
> [`PLATFORM-ALIGNMENT-HANDOFF.md`](./PLATFORM-ALIGNMENT-HANDOFF.md).

The shared orchestration layer (`electron/automation/driver.js`,
`electron/desktop-controller.js`, backend `computer/bridge_client.py`) must
**gate on what a helper advertises**, never on `process.platform` / `sys.platform`.
Each helper reports its manifest in the `ping` response:

```json
{
  "platform": "darwin" | "win32",
  "version": 4,
  "methods": [ ... ],
  "features": { ... }
}
```

`driver.js` records this as `driver.capabilities` and exposes:
`supports(method)`, `feature(name)`, `featureValue(name, fallback)`,
`features()`, and `await ensureReady()`. `desktop-controller.state()` includes
`features` + `methods`, and the backend turns manifest gaps into concrete
model-facing guidance via `_capability_notes()` in `bridge_client.py`.

---

## 1. Feature manifest keys

| Key | Type | Meaning |
|---|---|---|
| `overlay` | bool | Native virtual-cursor / HUD overlay is drawn by the helper. |
| `physical_displays` | bool | `displays` reports **physical pixels** (true) vs logical DIP/points (false); drives `win-driver.mapPoint`. |
| `ax` | bool | Accessibility tree is macOS AX. |
| `uia` | bool | Accessibility tree is Windows UI Automation. |
| `input_model` | `"per_pid"` \| `"global"` | `per_pid` posts events to a target process without moving the real pointer; `global` uses OS-wide injection that moves the real pointer. |
| `background_input` | bool | Can drive a non-frontmost app. |
| `clipboard_restore` | bool | Clipboard-based text entry restores the user's previous clipboard afterwards. |
| `target_confirmation` | bool | Text entry confirms the change landed (paste receipt / target-change readback). |
| `unicode_graphemes` | bool | Unicode typing is grapheme/surrogate-pair safe. |
| `middle_click` | bool | Middle-click is supported. |
| `keypad_keys` | bool | Keypad/`fn`/`num_lock`/`help` key tokens are supported. |
| `permission_model` | `"tcc"` \| `"uipi"` \| `"none"` | macOS TCC prompts vs Windows UIPI/elevation gate. |

`scripts/automation-conformance.js` (a) fails if any required method/feature key
is missing, and (b) with `--parity <other-helper>` compares two helpers'
**method set** and **feature key set** (values may legitimately differ —
divergence is printed as an expected warning).

---

## 2. Current per-platform values

| Key | macOS (`cw-automa`) | Windows (`cwautoma-win`) |
|---|---|---|
| `overlay` | true | true |
| `physical_displays` | false (logical points) | true (physical px) |
| `ax` / `uia` | true / false | false / true |
| `input_model` | `per_pid` | `global` (P3 research) |
| `background_input` | true | **false** (P3 research) |
| `clipboard_restore` | true | true (P1 done) |
| `target_confirmation` | true | **false** (P1 todo) |
| `unicode_graphemes` | true | true (P1 done) |
| `middle_click` | **false** (P1 todo) | true |
| `keypad_keys` | true | **false** (P1 todo) |
| `permission_model` | `tcc` | `uipi` |

Bold = the lagging side. When a flag flips, the corresponding
`bridge_client._capability_notes()` guidance disappears automatically (it is
derived from these values, not hardcoded).

---

## 3. Known gaps (bilateral)

### Windows behind macOS
1. Coordinate injection moves the real pointer; no background input (`Input.cs` vs `Injection.swift` `postToPid`). — **P3 research** (documented via `input_model:global`).
2. `permissions` / `permissions_request` are placeholders (hardcoded granted); real gate is UIPI (`Program.cs`, `Elevation.cs`). — *Windows has no TCC-equivalent; P3 makes UIPI a first-class permission state.*
3. ~~Clipboard text entry does not restore the user clipboard~~ — **fixed P1** (`TextInput.PasteText` restores prior text). Paste *receipt* (`target_confirmation`) still todo.
4. ~~Unicode typed per UTF-16 code unit~~ — **fixed P1** (`Input.TypeUnicode` groups surrogate pairs in one SendInput).
5. ~~`list_apps(scope='installed')` returns running apps (stub)~~ — **fixed P1** (`AppInventory.Installed` enumerates Start Menu shortcuts + App Paths).
6. ~~`launch` cannot resolve localized display names~~ — **fixed P1** (`launch` falls back to a Start Menu shortcut by display name; confirms via new windowed pid).
7. ~~`app_state` silently retargets to the frontmost app~~ — **fixed P1** (`HandleSnapshot` strict `no_target`).
8. `scroll`/`scroll_to` anchor at the real cursor / ignore pid; `scroll_to` defaults to (0,0) when x/y omitted (`Program.cs`). — **P1 todo**.
9. ~~`cursor_demo` ignores `seconds`~~ — **fixed P1** (animates for the requested duration, bounded ≤15s).

### macOS behind Windows
10. No middle-click (`main.swift` `click_point`); no keypad/`fn` tokens; `delete` semantics differ (`KeyMapping.swift` vs `Input.cs`); dead `realTypeInto`.

### Shared (both)
11. ~~`disable_diff` dropped before the helper~~ — **fixed in P0** (kernel → `_replCall` → `desktop-controller.axAppState` → `driver.getAppState` → helper).

---

## 4. How to gate on capabilities (examples)

```js
// Electron (shared layer) — prefer capability checks over platform.
const driver = this._driverInstance();
if (driver.featureValue('input_model') === 'global') { this._ensureFrontmost(); }
if (!driver.feature('clipboard_restore')) { /* warn / snapshot clipboard */ }
```

```python
# Backend — mirror of the same idea.
features = ComputerClient(data_dir).state().get("features") or {}
if features.get("input_model") == "global":
    ...  # ask the user to bring the app forward
```

The P3 refactor converts the remaining
`process.platform` / `sys.platform` branches in the shared computer-use layer
into these capability checks (keep platform checks only in the per-OS
`*-driver.js` and native helpers).
