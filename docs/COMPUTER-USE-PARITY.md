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
| `clipboard_restore` | true | true |
| `target_confirmation` | true | true |
| `unicode_graphemes` | true | true |
| `middle_click` | true | true |
| `keypad_keys` | true | true |
| `permission_model` | `tcc` | `uipi` |

Only `input_model`/`background_input` still differ (macOS can post events to a
pid; Windows injection is global) — the deliberate P3 research item, advertised
and surfaced via `_capability_notes()`. Everything else is aligned; `delete`
now means forward-delete on both (with `backspace` for Backspace).

---

## 3. Known gaps (bilateral)

### Windows behind macOS
1. Coordinate injection moves the real pointer; no background input (`Input.cs` vs `Injection.swift` `postToPid`). — **P3 research** (advertised via `input_model:global`; deliberate, not a bug).
2. ~~`permissions` / `permissions_request` were placeholders~~ — **fixed**: report `model:uipi` + real state (`self_elevated`, `can_input_frontmost`, `blocked_reason`); Electron gates on `permission_model`.
3. ~~Clipboard text entry does not restore the user clipboard~~ — **fixed** (`TextInput.PasteText` restores prior text).
4. ~~No paste receipt / `target_confirmation`~~ — **fixed**: el-based entry falls back to clipboard paste **with readback**.
5. ~~Unicode typed per UTF-16 code unit~~ — **fixed** (`Input.TypeUnicode` groups surrogate pairs in one SendInput).
6. ~~`list_apps(scope='installed')` returned running apps (stub)~~ — **fixed** (Start Menu shortcuts + App Paths).
7. ~~`launch` could not resolve localized display names~~ — **fixed** (Start Menu shortcut by display name; confirms via new windowed pid).
8. ~~`app_state` silently retargeted to the frontmost app~~ — **fixed** (strict `no_target`).
9. ~~`scroll_to` defaulted to (0,0) when x/y omitted~~ — **fixed** (brings target forward, scrolls at the current cursor).
10. ~~`cursor_demo` ignored `seconds`~~ — **fixed** (animates for the requested duration, ≤15s).

### macOS behind Windows
11. ~~No middle-click~~ — **fixed** (`click_point` uses `MouseButton.parse`); ~~keypad/`fn` tokens~~ — **keypad added to Windows** so both support them; ~~`delete` semantics differ~~ — **unified** (`delete`=forward, `backspace`=Backspace); ~~dead `realTypeInto`~~ — **removed**; `act` `right`/`double` no longer collapse into a single AXPress.

### Shared (both)
12. ~~`disable_diff` dropped before the helper~~ — **fixed in P0**.

---

## 3b. Action contract & semantic shortcuts (root-cause single source)

`backend/coworker/computer/actions.py` is the **single source** for every
computer action. Each `Action` now also declares its wiring:

- `rpc` — the host/helper JSON-RPC method (drives the host's table-driven
  `_replCall` dispatch; the host may not have a hand-written route the table
  lacks).
- `script` — the JS sandbox binding name (must exist in
  `electron/computer-repl-kernel.js`).
- `op` — the sub-op when `rpc == "act"` (click/double/right/show).
- `shortcut` — an implicit semantic shortcut (e.g. `go_back` → `back`).
- `param_map` — catalog param → RPC/script arg (removes the `scroll_to`
  `scroll_app`/`scroll_x`/`scroll_y` mismatch).
- `requires_any` — at least one of these params must be present.

`contract_dump()` exports it as JSON; `backend/tests/test_action_contract.py`
fails if the kernel/host drift from it (a real drift guard, not a doc).

The complete script sandbox surface (`SCRIPT_APP_METHODS` / `SCRIPT_CUA_METHODS`)
also lives in `actions.py` and is the source for the author-time script validator
(`validation._SCRIPT_APP_METHODS`) — the old hardcoded copy drifted and falsely
rejected real methods (`getAXStateText`, `shortcut`, `doubleClick`, …).

**Semantic shortcuts** (`SEMANTIC_SHORTCUTS`) are platform-NEUTRAL names
(`copy`, `save`, `find`, `back`, …). The concrete key+modifiers live in the
**per-platform driver** (`driver.shortcutMap()`; `mod` → Cmd on macOS / Ctrl on
Windows), selected at startup by `createDriver()`. The shared layer and the
model use only the neutral name or `mod`, so the same script is correct on both
OSes (no `cmd+f` that breaks on Windows).



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
