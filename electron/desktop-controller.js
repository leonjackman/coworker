// DesktopController — the agent's OS-level computer-use surface.
//
// Mirrors the BrowserController pattern: Electron main owns the hardware and
// the Python backend drives it over a separate loopback HTTP bridge (random
// port + bearer token), so the agent can see and click the real desktop —
// Finder, system dialogs, other IDEs, native apps the embedded <webview> can
// never reach.
//
//   capture    electron.desktopCapturer  (macOS Screen Recording TCC)
//   input      @nut-tree-fork/nut-js      (in-process; CGEvent on macOS,
//                                          SendInput on Windows, X11 on Linux;
//                                          macOS Accessibility TCC)
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

const NUT = '@nut-tree-fork/nut-js';
const MAC_PERMS = '@nut-tree-fork/node-mac-permissions';

let _nut = null;
function nut() {
  if (_nut === null) {
    _nut = require(NUT);
  }
  return _nut;
}

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

// Convert a JPEG's byte length into a rough "model token" estimate (1 token ~ 4
// chars of base64). Purely informational — the Python side owns real budgeting.
function dataUrlToJpegInfo(dataUrl) {
  if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/jpeg;base64,')) {
    return { width: 0, height: 0, bytes: 0 };
  }
  return { width: 0, height: 0, bytes: Math.floor(((dataUrl.length - 23) * 3) / 4) };
}

// ── Platform helpers ────────────────────────────────────────────────────────

function isDarwin() {
  return process.platform === 'darwin';
}

function inputPermission() {
  try {
    if (isDarwin()) {
      const perms = require(MAC_PERMS);
      return { status: perms.getAuthStatus('accessibility') };
    }
    return { status: 'granted' };
  } catch (e) {
    return { status: 'unknown', detail: String(e && e.message || e).slice(0, 120) };
  }
}

function screenPermission() {
  try {
    if (isDarwin()) {
      const perms = require(MAC_PERMS);
      return { status: perms.getAuthStatus('screen') };
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

// ── Key / modifier maps ─────────────────────────────────────────────────────

const NAMED_KEYS = {
  enter: 'Enter', return: 'Enter', escape: 'Escape', esc: 'Escape', tab: 'Tab',
  backspace: 'Backspace', delete: 'Delete', space: 'Space', ' ': 'Space',
  home: 'Home', end: 'End', pageup: 'PageUp', pagedown: 'PageDown',
  up: 'Up', down: 'Down', left: 'Left', right: 'Right',
  f1: 'F1', f2: 'F2', f3: 'F3', f4: 'F4', f5: 'F5', f6: 'F6',
  f7: 'F7', f8: 'F8', f9: 'F9', f10: 'F10', f11: 'F11', f12: 'F12',
  '0': 'Num0', '1': 'Num1', '2': 'Num2', '3': 'Num3', '4': 'Num4',
  '5': 'Num5', '6': 'Num6', '7': 'Num7', '8': 'Num8', '9': 'Num9',
};

const MODIFIERS = {
  cmd: 'LeftSuper', command: 'LeftSuper', super: 'LeftSuper', meta: 'LeftSuper',
  ctrl: 'LeftControl', control: 'LeftControl',
  alt: 'LeftAlt', option: 'LeftAlt',
  shift: 'LeftShift',
};

// ── Main controller ─────────────────────────────────────────────────────────

class DesktopController {
  constructor() {
    this.paused = false;
    this.pauseReason = '';
    this._stopPauseListener = null;
    this._overlay = null;
    this._stopAccel = null;
    this._stopLabel = process.platform === 'darwin' ? '⌘ + ⇧ Esc' : 'Ctrl + Shift + Esc';
    this._adapter = null;
    this._ensureAppPresentable();
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

  // macOS treats an app as a background/accessory app (and REMOVES its Dock
  // icon) when it only shows panel/overlay windows. Force the app back to a
  // regular activation policy + show the Dock icon so the main CoWorker icon is
  // never "closed" while the agent operates the desktop. No-op off macOS.
  _ensureAppPresentable() {
    if (process.platform !== 'darwin') return;
    try {
      if (typeof app.setActivationPolicy === 'function') app.setActivationPolicy('regular');
    } catch (e) { /* ignore */ }
    try {
      if (app.dock && typeof app.dock.show === 'function') app.dock.show();
    } catch (e) { /* ignore */ }
  }

  // Lazy ActivityOverlay (transparent on-screen "agent is operating" veil).
  _overlayInstance() {
    this._ensureAppPresentable();
    if (this._overlay === null) {
      const { ActivityOverlay } = require('./activity-overlay');
      this._overlay = new ActivityOverlay();
      // Adopt the current stop-shortcut label so the pill shows the real combo
      // (not the cryptic ⎋ glyph) even before the renderer syncs it.
      this._overlay.setStopShortcut(this._stopLabel);
    }
    return this._overlay;
  }

  // ── Pause / resume (human preemption) ────────────────────────────────
  setPaused(paused, reason = '') {
    this.paused = !!paused;
    this.pauseReason = paused ? reason : '';
    if (this._overlay) this._overlay.setPaused(this.paused);
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
    if (this._overlay) this._overlay.setStopShortcut(this._stopLabel);
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
        systemPreferences.isTrustedAccessibilityClient(true);
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
      systemPreferences.isTrustedAccessibilityClient(true);
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
    this._ensureAppPresentable();
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
    // Activity overlay: show the "agent is operating" frame while capturing.
    this._overlayInstance().veil({ id: String(target.id), bounds });
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
  // Every mutating action goes here; while paused nothing is injected.
  async act(args) {
    this._ensureNotPaused();
    this._ensureAppPresentable();
    // Preflight: nut.js (CGEvent) posts events SILENTLY when the process is not
    // an Accessibility-trusted client — a click would "succeed" while doing
    // nothing and the agent would never learn it needs permission. Surface the
    // missing permission up-front so the Python layer auto-triggers the OS
    // prompt instead of typing into the void.
    if (isDarwin()) {
      const input = inputPermission().status;
      if (input !== 'authorized') {
        const err = new Error(
          input === 'restricted'
            ? 'input permission is restricted by the system'
            : 'macOS Accessibility (input) permission is not granted',
        );
        err.code = 'input_permission';
        throw err;
      }
    }
    const { Point, Button, Key, mouse, keyboard } = nut();
    const action = String(args.action || '');
    const button = Button[{ left: 'LEFT', right: 'RIGHT', middle: 'MIDDLE' }[String(args.button || 'left')] || 'LEFT'];
    const shot = args.shot || null;
    const displayIndex = Number(args.display) || 0;

    const resolvePoint = (px, py) => {
      const all = screen.getAllDisplays();
      const d = all[displayIndex] || all[0];
      const bound = d ? { x: Math.round(d.bounds.x), y: Math.round(d.bounds.y), width: Math.round(d.bounds.width), height: Math.round(d.bounds.height) } : null;
      const pt = shotToPoint(px, py, shot, bound ? { bounds: bound } : null);
      return new Point(pt.x, pt.y);
    };

    const hasKey = (name) => !!(Key && Key[name] !== undefined);

    const realKey = (key) => {
      const k = String(key || '').toLowerCase();
      if (NAMED_KEYS[k]) return { name: NAMED_KEYS[k], typed: false };
      if (k.length === 1) {
        const upper = k.toUpperCase();
        const code = /^[A-Z]$/.test(upper) ? upper : null;
        if (code && keyboard.Key && keyboard.Key[code] !== undefined) return { name: code, typed: false };
        return { name: k, typed: true }; // symbol/unicode -> typeString
      }
      if (/^f(\d{1,2})$/.test(k)) return { name: k.toUpperCase(), typed: false };
      return { name: null, typed: true, text: String(key) };
    };

    const tap = async (key) => {
      const r = realKey(key);
      if (r.name && hasKey(r.name)) {
        await keyboard.pressKey(Key[r.name]);
        await keyboard.releaseKey(Key[r.name]);
        return;
      }
      if (r.typed !== false) {
        await keyboard.type(String(key));
        return;
      }
      const err = new Error(`unsupported key: ${key}`);
      err.code = 'computer_error';
      throw err;
    };

    const tapWithMods = async (key, mods) => {
      const r = realKey(key);
      if (r.typed) {
        // Modifier + free text is ambiguous; press modifiers, type text, release.
        const keys = (mods || []).map((m) => Key[MODIFIERS[String(m).toLowerCase()]]).filter(Boolean);
        for (const mk of keys) await keyboard.pressKey(mk);
        await keyboard.type(String(key));
        for (const mk of keys.slice().reverse()) await keyboard.releaseKey(mk);
        return;
      }
      if (!hasKey(r.name)) {
        const err = new Error(`unsupported key with modifiers: ${key}`);
        err.code = 'computer_error';
        throw err;
      }
      const keys = [Key[r.name]];
      for (const m of mods || []) {
        const n = MODIFIERS[String(m).toLowerCase()];
        if (n && Key[n] !== undefined) keys.push(Key[n]);
      }
      for (const k of keys) await keyboard.pressKey(k);
      for (const k of keys.slice().reverse()) await keyboard.releaseKey(k);
    };

    try {
      // Activity overlay: show the transparent "agent is operating" frame on the
      // target display and pulse a focus ring at the coordinate being acted on.
      const displayObj = screen.getAllDisplays()[displayIndex] || screen.getAllDisplays()[0];
      if (displayObj) {
        const ovDisplay = {
          id: String(displayObj.id),
          bounds: {
            x: Math.round(displayObj.bounds.x), y: Math.round(displayObj.bounds.y),
            width: Math.round(displayObj.bounds.width), height: Math.round(displayObj.bounds.height),
          },
        };
        const hasPoint = ['click', 'double_click', 'right_click', 'move', 'drag'].includes(action);
        if (hasPoint) {
          const gx = shotToPoint(args.x, args.y, shot, ovDisplay);
          this._overlayInstance().ping(ovDisplay, { x: gx.x - ovDisplay.bounds.x, y: gx.y - ovDisplay.bounds.y });
        } else {
          this._overlayInstance().veil(ovDisplay);
        }
      }
      switch (action) {
        case 'click': {
          const p = resolvePoint(args.x, args.y);
          await mouse.setPosition(p);
          await mouse.click(button);
          break;
        }
        case 'double_click': {
          const p = resolvePoint(args.x, args.y);
          await mouse.setPosition(p);
          await mouse.doubleClick(button);
          break;
        }
        case 'right_click': {
          const p = resolvePoint(args.x, args.y);
          await mouse.setPosition(p);
          await mouse.rightClick();
          break;
        }
        case 'move': {
          const p = resolvePoint(args.x, args.y);
          await mouse.setPosition(p);
          break;
        }
        case 'drag': {
          const p1 = resolvePoint(args.x, args.y);
          const p2 = resolvePoint(args.x2, args.y2);
          await mouse.setPosition(p1);
          await mouse.pressButton(button);
          await mouse.setPosition(p2);
          await mouse.releaseButton(button);
          break;
        }
        case 'scroll': {
          const dx = Number(args.dx) || 0;
          const dy = Number(args.dy) || 0;
          const lines = Math.max(1, Math.round(Math.abs(dy || dx) / 30) || 1);
          if (dy > 0) for (let i = 0; i < lines; i++) await mouse.scrollUp(1);
          else if (dy < 0) for (let i = 0; i < lines; i++) await mouse.scrollDown(1);
          if (dx > 0) for (let i = 0; i < lines; i++) await mouse.scrollRight(1);
          else if (dx < 0) for (let i = 0; i < lines; i++) await mouse.scrollLeft(1);
          break;
        }
        case 'type': {
          await keyboard.type(String(args.text || ''));
          break;
        }
        case 'key': {
          const mods = Array.isArray(args.modifiers) ? args.modifiers : [];
          if (mods.length) {
            await tapWithMods(args.key, mods);
          } else {
            await tap(args.key);
          }
          break;
        }
        case 'clipboard_set': {
          clipboard.writeText(String(args.text || ''));
          break;
        }
        case 'paste': {
          const modKey = process.platform === 'darwin' ? 'LeftSuper' : 'LeftControl';
          await keyboard.pressKey(keyboard.Key[modKey], keyboard.Key.V);
          await keyboard.releaseKey(keyboard.Key.V, keyboard.Key[modKey]);
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

  // ── Activity-overlay debug / lifecycle (used by the bridge) ───────────
  overlayShow(display) {
    this._overlayInstance().veil(display || (() => {
      const d = screen.getAllDisplays()[0];
      return d ? { id: String(d.id), bounds: { x: Math.round(d.bounds.x), y: Math.round(d.bounds.y), width: Math.round(d.bounds.width), height: Math.round(d.bounds.height) } } : null;
    })());
    return { ok: true };
  }

  overlayHide() {
    if (this._overlay) this._overlay.hide();
    return { ok: true };
  }

  overlayState() {
    return { ok: true, paused: this.paused, has_overlay: !!this._overlay };
  }

  async overlayCapture(display) {
    if (!this._overlay) return null;
    return this._overlay.capture(display);
  }

  // ── Structure-first automation surface (native cw-automa) ─────────────
  // Observation + actuation by Accessibility element ref; coordinates are an
  // explicit fallback. Every mutating action also bumps the activity overlay.

  _primaryDisplay() {
    const d = screen.getAllDisplays()[0];
    return d ? { id: String(d.id), bounds: { x: Math.round(d.bounds.x), y: Math.round(d.bounds.y), width: Math.round(d.bounds.width), height: Math.round(d.bounds.height) } } : null;
  }

  _overlayVeil() {
    const d = this._primaryDisplay();
    if (d) this._overlayInstance().veil(d);
  }

  async axSnapshot(depth = 6) {
    if (process.platform !== 'darwin') return { frontmost: '', refs: 0, text: '', error: 'not on macOS' };
    this._ensureAppPresentable();
    const adapter = this._adapterInstance();
    return adapter.snapshotText(depth);
  }

  async axAct(ref, op, params = {}) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureAppPresentable();
    this._overlayVeil();
    return this._adapterInstance().act({ ref, op, ...params });
  }

  async axPress(key, modifiers) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureAppPresentable();
    this._overlayVeil();
    return this._adapterInstance().press(key, modifiers);
  }

  async axType(text) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureAppPresentable();
    this._overlayVeil();
    return this._adapterInstance().type(text);
  }

  async axLaunch(app) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureAppPresentable();
    this._overlayVeil();
    return this._adapterInstance().launch(app);
  }

  // click_coords: (x,y) are in the SCREENSHOT pixel space the model saw, mapped
  // to display point space (the exact shotToPoint contract). Pass shot_width/
  // shot_height/display from the computer_observe screenshot result. Without a
  // shot the coordinates are treated as raw display points.
  async axClickCoords(x, y, opts = {}) {
    if (process.platform !== 'darwin') throw new Error('not on macOS');
    this._ensureAppPresentable();
    this._overlayVeil();
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
    return this._adapterInstance().scroll(dx, dy);
  }

  async axFrontmost() {
    if (process.platform !== 'darwin') return { pid: -1, app: '' };
    return this._adapterInstance().frontmost();
  }

  adapterState() {
    const diag = this._adapter ? this._adapter.diagnose() : { binary: '', exists: false, ready: false };
    return { ok: true, platform: process.platform, paused: this.paused, ...diag };
  }

  destroy() {
    if (this._overlay) {
      this._overlay.destroy();
      this._overlay = null;
    }
    if (this._adapter) {
      try { this._adapter.close(); } catch (e) { /* ignore */ }
      this._adapter = null;
    }
  }
}

module.exports = {
  DesktopController,
  ActivityOverlay: require('./activity-overlay').ActivityOverlay,
  shotToPoint,
  dataUrlToJpegInfo,
  NAMED_KEYS,
  MODIFIERS,
};
