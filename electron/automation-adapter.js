// AutomationAdapter — spawns the native macOS Automation helper (cw-automa) and
// exposes a clean, structure-first interface to the DesktopController / bridge.
//
// The agent observes via Accessibility (snapshot → element tree with stable refs)
// and acts by ref (click/type/press/launch) — no pixel guessing. Coordinates and
// raw typing remain available as explicit low-precision fallbacks.
//
// Protocol: newline-delimited JSON over the child's stdio:
//   {"id":N,"method":"snapshot","params":{...}}
//   {"id":N,"ok":true,"result":{...}} | {"id":N,"ok":false,"error":"..."}

'use strict';

const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Boot the helper until it answers a ping, so the bridge never races it.
const BOOT_TIMEOUT_MS = 8000;

// Candidate helper locations for each launch mode:
//   dev            electron/cw-automa/build/cw-automa   (auto-compiled if missing)
//   packaged       <app Resources>/cw-automa/cw-automa  (extraResources copies
//                  the whole electron/cw-automa/build dir INTO "<Resources>/cw-automa",
//                  so the binary is at "<Resources>/cw-automa/cw-automa")
// GitHub Actions packaged uses the same bundle layout as the local packaged build.
function candidateBinaryPaths() {
  const { app } = require('electron');
  const isPackaged = !!(app && app.isPackaged);
  const candidates = [];
  if (process.platform === 'darwin' && isPackaged && process.resourcesPath) {
    const rp = process.resourcesPath;
    // extraResources copies electron/cw-automa/build INTO "<Resources>/cw-automa",
    // so the executable lives at "<Resources>/cw-automa/cw-automa" (outside the
    // asar — files inside app.asar cannot be spawned).
    candidates.push(path.join(rp, 'cw-automa', 'cw-automa'));
    candidates.push(path.join(rp, 'cw-automa'));
    candidates.push(path.join(rp, 'bin', 'cw-automa'));
  }
  if (!isPackaged) {
    candidates.push(path.join(__dirname, 'cw-automa', 'build', 'cw-automa'));
  }
  // Never try a path inside the asar: spawning an asar-embedded binary fails.
  return candidates.filter((p) => !String(p).includes('app.asar'));
}

// Pick the first candidate that actually exists as a file.
function resolveBinaryPath() {
  for (const p of candidateBinaryPaths()) {
    try {
      if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
    } catch (e) { /* keep looking */ }
  }
  return null;
}

// Compile the Swift helper from source (dev only). Returns true on success.
// The helper is now a multi-file Swift target under `cw-automa/src`.
function buildHelper(sourceBinaryPath) {
  try {
    const srcDir = path.join(__dirname, 'cw-automa', 'src');
    if (!fs.existsSync(path.join(srcDir, 'main.swift'))) return false;
    const sources = fs.readdirSync(srcDir)
      .filter((f) => f.endsWith('.swift'))
      .sort()
      .map((f) => path.join(srcDir, f));
    fs.mkdirSync(path.dirname(sourceBinaryPath), { recursive: true });
    execFileSync('swiftc', ['-O', '-o', sourceBinaryPath, ...sources], { timeout: 180000 });
    return fs.existsSync(sourceBinaryPath);
  } catch (e) {
    try { console.warn('[automa] swiftc build failed:', (e && e.message) || e); } catch (err) { /* ignore */ }
    return false;
  }
}

class AutomationAdapter {
  constructor(options = {}) {
    this._proc = null;
    this._seq = 0;
    this._pending = new Map();
    this._buffer = '';
    this.ready = false;
    this._bootWaiters = [];
    this.sourceBinaryPath = path.join(__dirname, 'cw-automa', 'build', 'cw-automa');
    // Resolve at construction; log what we pick so failures are diagnosable.
    this.binaryPath = options.binaryPath || this._resolve();
    if (!this.binaryPath) {
      try { console.warn('[automa] no helper binary found; candidates:', candidateBinaryPaths().join(', ')); } catch (e) { /* ignore */ }
    } else {
      this._ensureExecutable();
    }
  }

  _resolve() {
    const { app } = require('electron');
    const isDev = !(app && app.isPackaged);
    const srcDir = path.join(__dirname, 'cw-automa', 'src');
    const found = resolveBinaryPath();

    // Dev: (re)compile from src. This also catches the "binary exists but the
    // Swift sources changed" case, which previously left the app on a stale
    // helper forever.
    if (isDev && fs.existsSync(path.join(srcDir, 'main.swift'))) {
      let stale = !found;
      if (found) {
        try {
          const binM = fs.statSync(found).mtimeMs;
          const newest = fs.readdirSync(srcDir)
            .filter((f) => f.endsWith('.swift'))
            .reduce((m, f) => Math.max(m, fs.statSync(path.join(srcDir, f)).mtimeMs), 0);
          if (newest > binM) stale = true;
        } catch (e) { /* ignore */ }
      }
      if (stale && buildHelper(this.sourceBinaryPath)) return this.sourceBinaryPath;
    }
    return found || null;
  }

  _ensureExecutable() {
    try {
      if (this.binaryPath && fs.existsSync(this.binaryPath)) fs.chmodSync(this.binaryPath, 0o755);
    } catch (e) { /* ignore */ }
  }

  diagnose() {
    const exists = !!(this.binaryPath && fs.existsSync(this.binaryPath) && fs.statSync(this.binaryPath).isFile());
    return { binary: this.binaryPath || '', exists, ready: this.ready };
  }

  _spawn() {
    if (this._proc && !this._proc.killed) return;
    if (!this.binaryPath || !fs.existsSync(this.binaryPath)) {
      this._flushError('helper binary missing: ' + (this.binaryPath || '(none)'));
      return;
    }
    this._ensureExecutable();
    this._proc = spawn(this.binaryPath, [], { stdio: ['pipe', 'pipe', 'pipe'] });
    this._proc.stdout.setEncoding('utf8');
    this._proc.stderr.setEncoding('utf8');
    this._proc.stdout.on('data', (chunk) => {
      this._buffer += chunk;
      let idx;
      while ((idx = this._buffer.indexOf('\n')) >= 0) {
        const line = this._buffer.slice(0, idx);
        this._buffer = this._buffer.slice(idx + 1);
        if (!line.trim()) continue;
        try {
          this._onMessage(JSON.parse(line));
        } catch (e) {
          console.warn('[automa] bad message:', line.slice(0, 200));
        }
      }
    });
    this._proc.stderr.on('data', (chunk) => {
      try { console.warn('[automa]', String(chunk).trim()); } catch (e) { /* ignore */ }
    });
    this._proc.on('error', (err) => {
      this.ready = false;
      this._flushError(`helper failed: ${err.message}`);
    });
    this._proc.on('exit', () => { this.ready = false; this._flushError('helper exited'); });
  }

  _onMessage(msg) {
    const p = this._pending.get(msg.id);
    if (p) { this._pending.delete(msg.id); p.resolve(msg); }
  }

  _flushError(message) {
    for (const p of this._pending.values()) p.resolve({ ok: false, error: message });
    this._pending.clear();
    this._resolveBoot(false);
  }

  _resolveBoot(ok) {
    this.ready = ok;
    const waiters = this._bootWaiters.splice(0);
    for (const w of waiters) w(ok);
  }

  async _boot() {
    if (this.ready) return true;
    this._spawn();
    return new Promise((resolve) => {
      this._bootWaiters.push(resolve);
      setTimeout(() => { if (!this.ready) { this._resolveBoot(false); } }, BOOT_TIMEOUT_MS);
      this._call('ping', {}).then(() => this._resolveBoot(true)).catch(() => this._resolveBoot(false));
    });
  }

  _call(method, params = {}) {
    const id = ++this._seq;
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve });
      const line = JSON.stringify({ id, method, params });
      const proc = this._proc;
      if (!proc || proc.killed) {
        this._pending.delete(id);
        reject(new Error('helper not running'));
        return;
      }
      proc.stdin.write(line + '\n');
      setTimeout(() => {
        if (this._pending.has(id)) {
          this._pending.delete(id);
          reject(new Error(`helper timeout: ${method}`));
        }
      }, 15000);
    });
  }

  async invoke(method, params = {}) {
    const ok = await this._boot();
    if (!ok) {
      let exists = false;
      try { exists = !!(this.binaryPath && fs.existsSync(this.binaryPath)); } catch (e) { /* ignore */ }
      throw new Error(`automation helper unavailable (binary=${this.binaryPath || '(none)'}, exists=${exists}, ready=${this.ready})`);
    }
    const msg = await this._call(method, params);
    if (!msg.ok) throw new Error(msg.error || `${method} failed`);
    return msg.result ?? {};
  }

  // ── High-level API ───────────────────────────────────────────────────
  async snapshot(depth = 6, app = '') {
    const params = { depth };
    if (app) params.app = app;
    return this.invoke('snapshot', params);
  }

  // get_app_state: the richer perception primitive (window info + AX diff).
  async getAppState(app = '', depth = 6) {
    const params = { depth };
    if (app) params.app = app;
    return this.invoke('get_app_state', params);
  }

  // Compact `[ref] role label value at (x,y)` lines the model can read. The
  // helper now renders this directly; the local walk is a fallback for older
  // helper builds.
  async snapshotText(depth = 6, maxLines = 400, app = '') {
    const res = await this.snapshot(depth, app);
    if (typeof res.text === 'string' && res.text.length > 0) {
      return { frontmost: res.frontmost || '', refs: res.refs || 0, text: res.text };
    }
    const lines = [];
    const walk = (node, indent) => {
      if (lines.length >= maxLines) return;
      const role = node.role || '';
      const label = (node.label || '').trim();
      const value = (node.value || '').trim();
      // Skip noisy menu-bar internals unless they're the only thing (menu bar
      // is captured separately as root children; keep it, it is useful).
      if (role === 'AXApplication' || role === 'AXWindow' || role === 'AXMenuBar') {
        lines.push(`${'  '.repeat(indent)}[${node.ref}] ${role}${label ? ' "' + label + '"' : ''}`);
      } else {
        let line = `${'  '.repeat(indent)}[${node.ref}] ${role}${label ? ' "' + label + '"' : ''}`;
        if (value) line += ` value="${value}"`;
        if (node.position) line += ` at ${node.position}`;
        lines.push(line);
      }
      for (const c of node.children || []) walk(c, indent + 1);
    };
    if (res.root) walk(res.root, 0);
    return { frontmost: res.frontmost || '', refs: res.refs || lines.length, text: lines.join('\n') };
  }

  async act(params) {
    return this.invoke('act', params);
  }
  async press(key, modifiers) {
    return this.invoke('press_hotkey', { key, modifiers: modifiers || [] });
  }
  async type(text) {
    return this.invoke('type_text', { text });
  }
  async launch(app) {
    return this.invoke('launch', { app });
  }
  async clickCoords(x, y) {
    return this.invoke('click_coords', { x, y });
  }
  async clickPoint(x, y, kind = 'left') {
    return this.invoke('click_point', { x, y, kind });
  }
  async dragPoint(x1, y1, x2, y2, button = 'left') {
    return this.invoke('drag_point', { x1, y1, x2, y2, button });
  }
  async cursorMove(x, y) {
    return this.invoke('cursor_move', { x, y });
  }
  async cursorShow() {
    return this.invoke('cursor_show', {});
  }
  async cursorHide() {
    return this.invoke('cursor_hide', {});
  }
  async cursorDebug() {
    return this.invoke('cursor_debug', {});
  }
  async cursorDemo(seconds = 3) {
    return this.invoke('cursor_demo', { seconds });
  }
  async hudShow() {
    return this.invoke('hud_show', {});
  }
  async hudPause(paused) {
    return this.invoke('hud_pause', { paused: !!paused });
  }
  async hudHide() {
    return this.invoke('hud_hide', {});
  }
  async setStopLabel(label) {
    return this.invoke('set_stop_label', { label: String(label || '') });
  }
  async permissions() {
    return this.invoke('permissions', {});
  }
  async requestPermission(kind) {
    return this.invoke('permissions_request', { kind: String(kind || 'accessibility') });
  }
  async scroll(dx, dy) {
    return this.invoke('scroll', { dx: dx || 0, dy: dy || 0 });
  }
  async frontmost() {
    return this.invoke('frontmost', {});
  }

  close() {
    if (this._proc && !this._proc.killed) { try { this._proc.kill(); } catch (e) { /* ignore */ } }
    this._proc = null;
  }
}

module.exports = { AutomationAdapter, defaultBinaryPath: candidateBinaryPaths, resolveBinaryPath };
