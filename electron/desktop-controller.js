// DesktopController — the agent's OS-level computer-use surface.
//
// Mirrors the BrowserController pattern: Electron main owns the hardware and
// the Python backend drives it over a separate loopback HTTP bridge (random
// port + bearer token), so the agent can see and click the real desktop —
// Finder, system dialogs, other IDEs, native apps the embedded <webview> can
// never reach.
//
//   capture    electron.desktopCapturer  (macOS Screen Recording TCC)
//   input      native cw-automa helper    (CGEvent.postToPid only — the real
//                                          OS cursor is NEVER moved; the agent
//                                          drives a blue virtual cursor overlay
//                                          inside the helper. macOS Accessibility
//                                          TCC)
//   coords     the model acts in "shot space" (the pixel grid of the JPEG it
//              was shown); Electron maps shot fraction -> display point space:
//                pointX = bounds.x + (x / shot.width)  * bounds.width
//                pointY = bounds.y + (y / shot.height) * bounds.height
//              Because every screenshot is downscaled *preserving aspect ratio*
//              from a full-display capture, this mapping is exact regardless of
//              Retina scaleFactor or the absolute capture resolution.
//
// Safety: the controller can be paused (human preemption). `powerMonitor`
// lock/suspend pauses automatically; unlock resumes an auto pause. A user
// hotkey (Cmd/Ctrl+Shift+Escape) and the tray item toggle a manual pause.
// While paused every injection is rejected with computer_paused so the agent
// tells the user instead of fighting for the pointer.

'use strict';

const { app, screen, desktopCapturer, clipboard, globalShortcut, powerMonitor, shell, systemPreferences } = require('electron');

// ── Coordinate contract (pure helpers, unit-tested) ────────────────────────

// shot-space (x,y in the JPEG the model saw) -> display-local point space.
function shotToPoint(x, y, shot, display) {
  const shotW = Math.max(1, Number(shot && shot.width) || 1);
  const shotH = Math.max(1, Number(shot && shot.height) || 1);
  const b = display && display.bounds ? display.bounds : { x: 0, y: 0, width: shotW, height: shotH };
  const w = Math.max(1, Number(b.width) || shotW);
  const h = Math.max(1, Number(b.height) || shotH);
  const gx = Math.round((Number(b.x) || 0) + (Number(x) / shotW) * w);
  const gy = Math.round((Number(b.y) || 0) + (Number(y) / shotH) * h);
  // Clamp into the display's own bounds (never click the menu bar / another
  // monitor because the model guessed slightly outside the shot).
  const cx = Math.min(Math.max(gx, b.x), b.x + w - 1);
  const cy = Math.min(Math.max(gy, b.y), b.y + h - 1);
  return { x: cx, y: cy };
}

// ── Platform helpers ────────────────────────────────────────────────────────

function isDarwin() {
  return process.platform === 'darwin';
}

// Permission status via Electron's own native APIs — no nut-js / native-perms
// module required any more (the input path itself now lives in cw-automa).
function inputPermission() {
  try {
    if (isDarwin()) {
      const trusted = systemPreferences.isTrustedAccessibilityClient(false);
      return { status: trusted ? 'authorized' : 'denied' };
    }
    return { status: 'granted' };
  } catch (e) {
    return { status: 'unknown', detail: String(e && e.message || e).slice(0, 120) };
  }
}

function screenPermission() {
  try {
    if (isDarwin()) {
      const raw = systemPreferences.getMediaAccessStatus('screen');
      const status = raw === 'granted'
        ? 'authorized'
        : (raw === 'not-determined' ? 'not determined' : raw);
      return { status };
    }
    return { status: 'granted' };
  } catch (e) {
    return { status: 'unknown', detail: String(e && e.message || e).slice(0, 120) };
  }
}

// Open the exact System Settings pane for a TCC permission. macOS never lets an
// app reset its own denial, so after a user chooses "don't allow" the only
// compliant path back is the user toggling the app's switch off and on here —
// that removes & re-adds the TCC entry (status -> "not determined"), after
// which the next real use re-triggers the OS alert automatically.
function openPermissionSettings(kind) {
  if (!isDarwin()) return { ok: true, opened_settings: false };
  const k = String(kind || '').toLowerCase();
  const pane = k === 'accessibility' ? 'Privacy_Accessibility' : 'Privacy_ScreenCapture';
  const url = `x-apple.systempreferences:com.apple.preference.security?${pane}`;
  shell
    .openExternal(url)
    .then(() => {})
    .catch(() => {
      // Deep link may be unsupported on some macOS versions — fall back to the
      // Security & Privacy pane root.
      shell.openExternal('x-apple.systempreferences:com.apple.preference.security').catch(() => {});
    });
  return { ok: true, opened_settings: true, pane };
}

// ── Main controller ─────────────────────────────────────────────────────────

class DesktopController {
  constructor() {
    this.paused = false;
    this.pauseReason = '';
    this._stopPauseListener = null;
    this._stopAccel = null;
    this._stopLabel = process.platform === 'darwin' ? '⌘ + ⇧ Esc' : 'Ctrl + Shift + Esc';
    this._adapter = null;
    this._script = null;
  }

  // Lazy AutomationAdapter (native cw-automa AX helper). Structure-first source
  // of truth for computer use; native macOS only.
  _adapterInstance() {
    if (this._adapter === null) {
      const { AutomationAdapter } = require('./automation-adapter');
      this._adapter = new AutomationAdapter();
    }
    return this._adapter;
  }

  // ── Pause / resume (human preemption) ────────────────────────────────
  setPaused(paused, reason = '') {
    this.paused = !!paused;
    this.pauseReason = paused ? reason : '';
    try {
      const adapter = this._adapterInstance();
      if (this.paused) {
        // Park the native virtual cursor + switch the status pill to "paused".
        adapter.cursorHide().catch(() => {});
        adapter.hudPause(true).catch(() => {});
      } else {
        adapter.hudPause(false).catch(() => {});
      }
    } catch (e) { /* helper unavailable */ }
    return { ok: true, paused: this.paused, reason: this.pauseReason };
  }

  pause(reason = 'user') {
    return this.setPaused(true, reason);
  }

  resume() {
    return this.setPaused(false);
  }

  abort() {
    return this.pause('user');
  }

  _ensureNotPaused() {
    if (this.paused) {
      const err = new Error('computer paused by user');
      err.code = 'computer_paused';
      err.reason = this.pauseReason;
      throw err;
    }
  }

  registerAutoPauseHandlers() {
    if (this._stopPauseListener) return;
    const onLock = () => { if (!this.paused) this.setPaused(true, 'screen-lock'); };
    const onSuspend = () => { if (!this.paused) this.setPaused(true, 'suspend'); };
    const onShutdown = () => { if (!this.paused) this.setPaused(true, 'shutdown'); };
    const onUnlock = () => { if (this.pauseReason === 'screen-lock') this.resume(); };
    powerMonitor.on('lock-screen', onLock);
    powerMonitor.on('suspend', onSuspend);
    powerMonitor.on('shutdown', onShutdown);
    powerMonitor.on('unlock-screen', onUnlock);
    this._stopPauseListener = () => {
      powerMonitor.removeListener('lock-screen', onLock);
      powerMonitor.removeListener('suspend', onSuspend);
      powerMonitor.removeListener('shutdown', onShutdown);
      powerMonitor.removeListener('unlock-screen', onUnlock);
    };
  }

  registerEmergencyHotkey() {
    // Default yield-control combo: Cmd/Ctrl+Shift+Escape. The renderer syncs the
    // user's configured shortcut via setStopShortcut(), which re-registers this
    // key so the OS hotkey always matches what the user sees in Settings.
    const accel = process.platform === 'darwin' ? 'Command+Shift+Escape' : 'Control+Shift+Escape';
    const label = process.platform === 'darwin' ? '⌘ + ⇧ Esc' : 'Ctrl + Shift + Esc';
    this.setStopShortcut(accel, label, true);
  }

  // Apply the effective "stop computer control" shortcut: (re)register the OS
  // globalShortcut and update the on-screen overlay pill so its hint text is
  // never hardcoded. Unregisters the previous binding first.
  setStopShortcut(accelerator, label, enabled = true) {
    const accel = enabled ? String(accelerator || '') : '';
    if (this._stopAccel) {
      try { globalShortcut.unregister(this._stopAccel); } catch (e) { /* ignore */ }
      this._stopAccel = null;
    }
    if (accel) {
      try {
        const ok = globalShortcut.register(accel, () => this.pause('user'));
        if (!ok) console.warn('[computer] stop shortcut registration failed:', accel);
        else this._stopAccel = accel;
      } catch (e) {
        console.warn('[computer] stop shortcut unavailable:', (e && e.message) || e);
      }
    }
    if (typeof label === 'string' && label) this._stopLabel = label;
    // Push the (possibly rebound) label to the native status pill so it always
    // shows the user's real shortcut, never a hardcoded glyph.
    try { this._adapterInstance().setStopLabel(this._stopLabel).catch(() => {}); } catch (e) { /* ignore */ }
    return { ok: true, acceler: this._stopAccel || null, label: this._stopLabel || '' };
  }

  // ── Observation ──────────────────────────────────────────────────────
  displays() {
    return screen.getAllDisplays().map((d) => ({
      index: this._indexOfDisplay(d),
      id: String(d.id),
      bounds: {
        x: Math.round(d.bounds.x), y: Math.round(d.bounds.y),
        width: Math.round(d.bounds.width), height: Math.round(d.bounds.height),
      },
      scale_factor: d.scaleFactor || 1,
      internal: !!d.internal,
    }));
  }

  _indexOfDisplay(d) {
    const all = screen.getAllDisplays();
    return all.findIndex((x) => x.id === d.id);
  }

  // Request (i.e. passively trigger) a macOS TCC permission when the agent
  // needs it and it is not complete. There is no "Allow/Deny" API a third
  // party can call, but macOS DOES present its own system dialog in these two
  // cases:
  //   * Screen Recording: the OS alert appears when the process actually
  //     attempts a capture (desktopCapturer.getSources) while status is
  //     "not determined".
  //   * Accessibility (input): AXIsProcessTrustedWithOptions({ prompt: true })
  //     — Electron's systemPreferences.isTrustedAccessibilityClient(true) —
  //     makes the OS show its accessibility prompt / open the pane.
  // If the user previously DENIED, macOS will not re-alert; we open the exact
  // System Settings pane so the user can toggle the switch off/on to reset.
  async requestAccess(kind) {
    const k = String(kind || '').toLowerCase();
    if (!isDarwin()) {
      return { ok: true, kind: k, status: 'authorized' };
    }
    if (k === 'screen') {
      const before = screenPermission().status;
      if (before === 'authorized' || before === 'restricted' || before === 'unknown') {
        return { ok: true, kind: k, status: before };
      }
      if (this.paused) return { ok: true, kind: k, status: before, paused: true };
      if (before === 'not determined') {
        // The real capture attempt is what makes macOS present its alert.
        try {
          await this.screenshot({ display: 0, maxWidth: 720, quality: 50 });
        } catch (e) {
          // alert shown (still pending) or capture empty — both expected here.
        }
        const after = screenPermission().status;
        if (after === 'authorized') return { ok: true, kind: k, status: 'authorized', granted: true };
        return { ok: true, kind: k, status: after === 'denied' ? 'denied' : 'not determined', prompt_shown: true };
      }
      // denied: no re-alert possible; guide the user to the reset switch.
      this.openPermissionSettings('screen');
      return { ok: true, kind: k, status: 'denied', opened_settings: true };
    }
    // accessibility (input)
    const st = inputPermission().status;
    if (st === 'authorized' || st === 'restricted' || st === 'unknown') {
      return { ok: true, kind: k, status: st };
    }
    if (st === 'not determined') {
      // AX prompt: macOS shows its accessibility consent alert/pane.
      try {
        this._promptAccessibility();
      } catch (e) {
        console.warn('[computer] accessibility prompt failed:', (e && e.message) || e);
      }
      const after = inputPermission().status;
      if (after === 'authorized') return { ok: true, kind: k, status: 'authorized', granted: true };
      if (after === 'denied') {
        this.openPermissionSettings('accessibility');
        return { ok: true, kind: k, status: 'denied', opened_settings: true };
      }
      return { ok: true, kind: k, status: 'not determined', prompt_shown: true };
    }
    // denied: macOS will not re-alert; try the AX prompt anyway (cheap, some
    // versions re-show it), then open the pane so the user can reset the entry.
    try {
      this._promptAccessibility();
    } catch (e) { /* ignore */ }
    const retried = inputPermission().status;
    if (retried === 'authorized') return { ok: true, kind: k, status: 'authorized', granted: true };
    this.openPermissionSettings('accessibility');
    return { ok: true, kind: k, status: 'denied', opened_settings: true };
  }

  // Class wrapper over the module-level helper (deep-link to System Settings).
  openPermissionSettings(kind) {
    return openPermissionSettings(kind);
  }

  // Raise the Accessibility consent prompt for the HELPER process (which posts
  // the synthetic input, so macOS attributes accessibility to it), with
  // Electron's own prompt as a fallback when the helper is unavailable.
  _promptAccessibility() {
    try { this._adapterInstance().requestPermission('accessibility').catch(() => {}); } catch (e) { /* ignore */ }
    try { systemPreferences.isTrustedAccessibilityClient(true); } catch (e) { /* ignore */ }
  }

  // Live permission status for the Settings permission list. Reads the native
  // TCC store fresh on every call (no caching), so toggling in System Settings
  // while the app runs is reflected immediately on the next query. Also reports
  // the running identity so users can tell dev (Electron) from packaged
  // (CoWorker) — macOS grants each identity separately.
  permissionStatus() {
    const input = inputPermission().status;
    const screen = screenPermission().status;
    let sysScreen = 'unknown';
    try {
      sysScreen = systemPreferences.getMediaAccessStatus('screen');
    } catch (e) { /* ignore */ }
    let identity = { packaged: app.isPackaged };
    try {
      const path = require('path');
      identity = {
        packaged: app.isPackaged,
        name: String(app.getName() || ''),
        version: String(app.getVersion() || ''),
        executable: path.basename(process.execPath || ''),
      };
    } catch (e) { /* ignore */ }
    return {
      ok: true,
      identity,
      permissions: {
        input,
        screen,
        sys_screen: sysScreen,
      },
    };
  }

  async state() {
    this._ensureNotPaused();
    const perms = { input: inputPermission(), screen: screenPermission() };
    const result = {
      ok: true,
      platform: process.platform,
      paused: this.paused,
      pause_reason: this.pauseReason,
      permissions: {
        input: perms.input.status,
        screen: perms.screen.status,
        input_detail: perms.input.detail || '',
        screen_detail: perms.screen.detail || '',
      },
      displays: this.displays(),
    };
    return result;
  }

  // Capture the chosen display, downscale to maxWidth, JPEG-encode.
  async screenshot({ display = 0, maxWidth = 1024, quality = 60 } = {}) {
    this._ensureNotPaused();
    const all = screen.getAllDisplays();
    const target = all[Number(display) || 0] || all[0];
    if (!target) {
      const err = new Error('no display available');
      err.code = 'computer_error';
      throw err;
    }
    // Ask for a crisp physical-pixel capture, then downscale for the model.
    const physW = Math.max(320, Math.round(target.bounds.width * (target.scaleFactor || 1)));
    const physH = Math.max(200, Math.round(target.bounds.height * (target.scaleFactor || 1)));
    let sources;
    try {
      sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: { width: physW, height: physH },
        fetchWindowIcons: false,
      });
    } catch (e) {
      // desktopCapturer throws when screen capture is not available/denied.
      console.warn('[computer] desktopCapturer failed:', (e && e.message) || e);
      const err = new Error('screen capture unavailable — Screen Recording permission is likely not granted');
      err.code = 'screen_permission';
      throw err;
    }
    if (!sources || !sources.length) {
      const err = new Error('screen capture returned no sources');
      err.code = 'screen_permission';
      throw err;
    }
    let source = sources.find((s) => String(s.display_id) === String(target.id));
    if (!source) source = sources.find((s) => String(s.display_id) === String(screen.getPrimaryDisplay().id));
    if (!source) source = sources[0];
    let img = source.thumbnail;
    if (!img || (typeof img.isEmpty === 'function' && img.isEmpty()) || !img.getSize || !img.getSize().width) {
      // macOS returns an empty thumbnail when Screen Recording is not granted.
      const err = new Error('screen capture is empty — Screen Recording permission is likely not granted');
      err.code = 'screen_permission';
      throw err;
    }
    const shot = this._encode(img, maxWidth, quality);
    if (!shot) {
      const err = new Error('screen capture could not be encoded');
      err.code = 'screen_permission';
      throw err;
    }
    const bounds = {
      x: Math.round(target.bounds.x), y: Math.round(target.bounds.y),
      width: Math.round(target.bounds.width), height: Math.round(target.bounds.height),
    };
    // The native virtual cursor is shown only while the agent acts; a capture
    // does not move it and no longer paints any full-display overlay.
    return {
      image: shot.dataUrl,
      shot: { width: shot.width, height: shot.height },
      display: { index: this._indexOfDisplay(target), id: String(target.id), bounds, scale_factor: target.scaleFactor || 1 },
    };
  }

  // Downscale preserving aspect ratio, encode JPEG, return data URL + size.
  _encode(image, maxWidth, quality) {
    const size = image.getSize();
    if (!size || !size.width || !size.height) return null;
    const scale = size.width > maxWidth ? maxWidth / size.width : 1;
    let out = image;
    if (scale < 1) {
      out = image.resize({ width: Math.round(size.width * scale) });
    }
    const jpeg = out.toJPEG(quality);
    if (!jpeg || !jpeg.length) return null;
    const outSize = out.getSize();
    return {
      dataUrl: `data:image/jpeg;base64,${jpeg.toString('base64')}`,
      width: outSize.width,
      height: outSize.height,
    };
  }

  // ── Injection ────────────────────────────────────────────────────────
  // Every mutating action goes here; while paused nothing is injected. All real
  // input is delivered by the native helper with CGEvent.postToPid — the real OS
  // cursor is never moved. Kept as a thin compatibility surface over the helper.
  async act(args) {
    this._ensureNotPaused();
    if (isDarwin() && inputPermission().status !== 'authorized') {
      const err = new Error('macOS Accessibility (input) permission is not granted');
      err.code = 'input_permission';
      throw err;
    }
    const action = String(args.action || '');
    const adapter = this._adapterInstance();
    const shot = args.shot || null;
    const displayIndex = Number(args.display) || 0;

    const toPoint = (px, py) => {
      const all = screen.getAllDisplays();
      const d = all[displayIndex] || all[0];
      const bounds = d ? {
        x: Math.round(d.bounds.x), y: Math.round(d.bounds.y),
        width: Math.round(d.bounds.width), height: Math.round(d.bounds.height),
      } : null;
      return shotToPoint(px, py, shot, bounds ? { bounds } : null);
    };

    try {
      switch (action) {
        case 'click':
        case 'double_click':
        case 'right_click': {
          const pt = toPoint(args.x, args.y);
          const kind = action === 'double_click' ? 'double' : (action === 'right_click' ? 'right' : 'left');
          await adapter.clickPoint(pt.x, pt.y, kind);
          break;
        }
        case 'move': {
          const pt = toPoint(args.x, args.y);
          await adapter.cursorMove(pt.x, pt.y);
          break;
        }
        case 'drag': {
          const p1 = toPoint(args.x, args.y);
          const p2 = toPoint(args.x2, args.y2);
          await adapter.dragPoint(p1.x, p1.y, p2.x, p2.y, String(args.button || 'left'));
          break;
        }
        case 'scroll': {
          await adapter.scroll(Number(args.dx) || 0, Number(args.dy) || 0);
          break;
        }
        case 'type': {
          await adapter.type(String(args.text || ''));
          break;
        }
        case 'key': {
          await adapter.press(String(args.key || ''), Array.isArray(args.modifiers) ? args.modifiers : []);
          break;
        }
        case 'clipboard_set': {
          clipboard.writeText(String(args.text || ''));
          break;
        }
        case 'paste': {
          await adapter.press('v', ['cmd']);
          break;
        }
        default: {
          const err = new Error(`unknown computer act: ${action}`);
          err.code = 'computer_error';
          throw err;
        }
      }
      return { ok: true, action };
    } catch (e) {
      if (e && e.code) throw e;
      const err = new Error(`computer act failed: ${e && e.message || e}`);
      err.code = 'computer_error';
      throw err;
    }
  }

  // ── Structure-first automation surface (native cw-automa) ─────────────
  // Observation + actuation by Accessibility element ref; coordinates are an
  // explicit fallback. The helper moves its virtual cursor as it acts.

  async axSnapshot(depth = 6) {
    if (process.platform !== 'darwin') return { frontmost: '', refs: 0, text: '', error: 'not on macOS' };
    const adapter = this._adapterInstance();
    return adapter.snapshotText(depth);
  }

  // get_app_state: key-window AX tree + window info + incremental AX diff.
  async axAppState(app = '', depth = 6) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    return this._adapterInstance().getAppState(app, depth);
  }

  async axAct(ref, op, params = {}) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().act({ ref, op, ...params });
  }

  async axPress(key, modifiers) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().press(key, modifiers);
  }

  async axType(text) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().type(text);
  }

  async axLaunch(app) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().launch(app);
  }

  // click_coords: (x,y) are in the SCREENSHOT pixel space the model saw, mapped
  // to display point space (the exact shotToPoint contract). Pass shot_width/
  // shot_height/display from the computer_observe screenshot result. Without a
  // shot the coordinates are treated as raw display points.
  async axClickCoords(x, y, opts = {}) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    let gx = Number(x);
    let gy = Number(y);
    const sw = Number(opts && opts.shot_width) || 0;
    const sh = Number(opts && opts.shot_height) || 0;
    if (sw > 0 && sh > 0) {
      const index = Number(opts && opts.display) || 0;
      const all = screen.getAllDisplays();
      const d = all[index] || all[0];
      const bounds = d ? { x: Math.round(d.bounds.x), y: Math.round(d.bounds.y), width: Math.round(d.bounds.width), height: Math.round(d.bounds.height) } : null;
      const pt = shotToPoint(x, y, { width: sw, height: sh }, bounds ? { bounds } : null);
      gx = pt.x;
      gy = pt.y;
    }
    return this._adapterInstance().clickCoords(gx, gy);
  }

  async axScroll(dx, dy) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().scroll(dx, dy);
  }

  async axScrollTo(app, dx, dy, x, y) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().scrollTo(app, dx, dy, x, y);
  }

  async axFrontmost() {
    if (process.platform !== 'darwin') return { pid: -1, app: '' };
    return this._adapterInstance().frontmost();
  }

  adapterState() {
    const diag = this._adapter ? this._adapter.diagnose() : { binary: '', exists: false, ready: false };
    return { ok: true, platform: process.platform, paused: this.paused, ...diag };
  }

  // ── Persistent JS surface (Codex-parity) ──────────────────────────────
  // The model runs JavaScript in an isolated worker; every native call below is
  // re-validated here (paused gate + app identity) before it reaches the helper.

  async axListApps(scope = 'running') {
    if (process.platform !== 'darwin') return { apps: [] };
    return this._adapterInstance().listApps(scope);
  }

  async axResolveApp(app) {
    if (process.platform !== 'darwin') return { selector: app, pid: null };
    return this._adapterInstance().resolveApp(app);
  }

  async axFocusApp(app) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().focusApp(app);
  }

  async axInputText(opts = {}) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().inputText(opts);
  }

  async axPressTo(app, key, modifiers, repeat) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().pressKeyTo(app, key, modifiers, repeat);
  }

  async axScrollTo(app, dx, dy, x, y) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().scrollTo(app, dx, dy, x, y);
  }

  async axDragTo(app, x1, y1, x2, y2) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().dragTo(app, x1, y1, x2, y2);
  }

  async axClickPointTo(app, x, y) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureNotPaused();
    return this._adapterInstance().clickPointTo(app, x, y);
  }

  async axUiSettle(opts = {}) {
    if (process.platform !== 'darwin') return { settled: true };
    return this._adapterInstance().uiSettle(opts);
  }

  // Dispatch table for native calls coming from the JS worker. Anything not
  // listed here is refused, so the sandbox cannot reach arbitrary helper verbs.
  async _replCall(method, args = {}) {
    switch (method) {
      case 'list_apps': return this.axListApps(args.scope || 'running');
      case 'resolve_app': return this.axResolveApp(String(args.app || ''));
      case 'frontmost': return this.axFrontmost();
      case 'get_app_state': {
        const res = await this.axAppState(String(args.app || ''), Number(args.depth) || 6);
        return res;
      }
      case 'screenshot': return this.screenshot({ display: Number(args.display) || 0, maxWidth: Number(args.max_width) || 1024, quality: 60 });
      case 'act': {
        if (process.platform !== 'darwin') throw new Error('not on macOS');
        this._ensureNotPaused();
        const extra = {};
        if (args.value !== undefined) extra.value = String(args.value);
        return this._adapterInstance().actFor(String(args.app || ''), String(args.ref || ''), String(args.op || 'click'), extra);
      }
      case 'input_text': {
        if (process.platform !== 'darwin') throw new Error('not on macOS');
        this._ensureNotPaused();
        return this._adapterInstance().inputText({
          app: String(args.app || ''),
          ref: String(args.ref || ''),
          text: String(args.text || ''),
          submit: !!args.submit,
        });
      }
      case 'press_key': return this.axPressTo(String(args.app || ''), String(args.key || ''), args.modifiers, args.repeat);
      case 'scroll': return this.axScrollTo(String(args.app || ''), Number(args.dx) || 0, Number(args.dy) || 0);
      case 'drag': return this.axDragTo(String(args.app || ''), Number(args.x1), Number(args.y1), Number(args.x2), Number(args.y2));
      case 'click_point': return this.axClickPointTo(String(args.app || ''), Number(args.x), Number(args.y));
      case 'ui_settle': return this.axUiSettle({ app: String(args.app || ''), quietMs: args.quiet_ms, timeoutMs: args.timeout_ms });
      default: {
        const err = new Error(`computer script: unsupported native call '${method}'`);
        err.code = 'computer_error';
        throw err;
      }
    }
  }

  _scriptInstance() {
    if (this._script) return this._script;
    const { ComputerScript } = require('./computer-repl');
    this._script = new ComputerScript({
      call: (method, args) => this._replCall(method, args),
      log: (m) => console.warn('[computer-script]', m),
    });
    return this._script;
  }

  async scriptRun(code, opts = {}) {
    if (process.platform !== 'darwin') {
      return { blocks: [{ type: 'text', text: 'computer script is macOS-only' }], error: 'unsupported_platform' };
    }
    this._ensureNotPaused();
    return this._scriptInstance().run(code, opts);
  }

  async scriptReset() {
    if (this._script) return this._script.reset();
    return { reset: true };
  }

  destroy() {
    if (this._script) {
      try { this._script.close(); } catch (e) { /* ignore */ }
      this._script = null;
    }
    if (this._adapter) {
      try { this._adapter.close(); } catch (e) { /* ignore */ }
      this._adapter = null;
    }
  }
}

module.exports = {
  DesktopController,
  shotToPoint,
};
