// HelperDriver — platform-neutral driver for a native computer-use helper.
//
// One shared JSON-lines-over-stdio contract is spoken by every platform helper
// (macOS `cw-automa`, Windows `cwautoma-win`); the only per-OS code is:
//   1. locating / building the helper binary  (subclass `_resolve`)
//   2. mapping a display-local point into the coordinate space the helper's
//      input injection expects  (subclass `mapPoint`; identity on macOS)
//
// Protocol (newline-delimited JSON over the child's stdio):
//   {"id":N,"method":"snapshot","params":{...}}
//   {"id":N,"ok":true,"result":{...}} | {"id":N,"ok":false,"error":"...","error_code":"..."}
//
// `ping` returns {platform, version, methods:[...], features:{...}}; the base
// records it so callers can ask `supports(method)` instead of branching on
// process.platform. Helpers that predate the capability field still work
// (`methods` absent => every method is assumed supported).

'use strict';

const { spawn } = require('child_process');
const fs = require('fs');

// Boot the helper until it answers a ping, so callers never race it.
const BOOT_TIMEOUT_MS = 8000;
const CALL_TIMEOUT_MS = 15000;

class HelperDriver {
  constructor(options = {}) {
    this._proc = null;
    this._seq = 0;
    this._pending = new Map();
    this._buffer = '';
    this.ready = false;
    this.supported = true;
    this._bootWaiters = [];
    this.platform = '';
    this.capabilities = { methods: null, features: {} };
    this.binaryPath = options.binaryPath || this._resolve();
    if (!this.binaryPath) {
      try { console.warn(`[automa:${this.platform}] no helper binary found; candidates:`, this.candidateBinaryPaths().join(', ')); } catch (e) { /* ignore */ }
    } else {
      this._ensureExecutable();
    }
  }

  // ── Subclass hooks ───────────────────────────────────────────────────
  /** Candidate helper binary locations, most specific first. */
  candidateBinaryPaths() { return []; }
  /** Dev-only: compile the helper from source. Return path or null. */
  _buildHelper() { return null; }
  /** Resolve the helper binary (may compile in dev). */
  _resolve() { return this.candidateBinaryPaths().find((p) => safeIsFile(p)) || null; }
  /** No-op on POSIX; overridden if needed. */
  _ensureExecutable() {
    try { if (this.binaryPath && fs.existsSync(this.binaryPath)) fs.chmodSync(this.binaryPath, 0o755); } catch (e) { /* ignore */ }
  }
  /**
   * Map a display-local point (the space `desktop-controller.shotToPoint`
   * produces) into the coordinate space the helper's input injection expects.
   * Identity on macOS; Windows converts DIP -> physical pixels.
   * `display` is the Electron display object ({ bounds, scaleFactor, index }).
   */
  mapPoint(x, y, display) { // eslint-disable-line no-unused-vars
    return { x: Number(x) || 0, y: Number(y) || 0 };
  }

  // ── Diagnostics ──────────────────────────────────────────────────────
  diagnose() {
    const exists = safeIsFile(this.binaryPath);
    return { binary: this.binaryPath || '', exists, ready: this.ready, platform: this.platform };
  }

  /** True when the helper advertises (or is assumed to support) a method. */
  supports(method) {
    const m = this.capabilities && this.capabilities.methods;
    if (!Array.isArray(m)) return true; // pre-capability helper => assume yes
    return m.indexOf(method) >= 0;
  }

  feature(name) {
    return !!(this.capabilities && this.capabilities.features && this.capabilities.features[name]);
  }

  // ── Process / JSON-RPC plumbing ──────────────────────────────────────
  _spawn() {
    if (this._proc && !this._proc.killed) return;
    if (!safeIsFile(this.binaryPath)) {
      this._flushError('helper binary missing: ' + (this.binaryPath || '(none)'));
      return;
    }
    this._ensureExecutable();
    this._proc = spawn(this.binaryPath, [], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
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
          console.warn(`[automa:${this.platform}] bad message:`, line.slice(0, 200));
        }
      }
    });
    this._proc.stderr.on('data', (chunk) => {
      try { console.warn(`[automa:${this.platform}]`, String(chunk).trim()); } catch (e) { /* ignore */ }
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
      setTimeout(() => { if (!this.ready) this._resolveBoot(false); }, BOOT_TIMEOUT_MS);
      this._call('ping', {})
        .then((msg) => {
          if (msg && msg.ok && msg.result && typeof msg.result === 'object') {
            this.platform = msg.result.platform || this.platform;
            this.capabilities = {
              methods: Array.isArray(msg.result.methods) ? msg.result.methods : null,
              features: msg.result.features || {},
            };
          }
          this._resolveBoot(true);
        })
        .catch(() => this._resolveBoot(false));
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
      }, CALL_TIMEOUT_MS);
    });
  }

  async invoke(method, params = {}) {
    const ok = await this._boot();
    if (!ok) {
      const exists = safeIsFile(this.binaryPath);
      throw new Error(
        `automation helper unavailable (platform=${this.platform || process.platform}, ` +
        `binary=${this.binaryPath || '(none)'}, exists=${exists}, ready=${this.ready})`
      );
    }
    if (!this.supports(method)) {
      const err = new Error(`helper does not support method '${method}' on ${this.platform || process.platform}`);
      err.code = 'unsupported_method';
      throw err;
    }
    const msg = await this._call(method, params);
    if (!msg.ok) {
      const err = new Error(msg.error || `${method} failed`);
      if (msg.error_code) err.code = msg.error_code;
      throw err;
    }
    return msg.result ?? {};
  }

  // ── High-level API (shared by every platform helper) ──────────────────
  async snapshot(depth = 6, app = '') {
    const params = { depth };
    if (app) params.app = app;
    return this.invoke('snapshot', params);
  }

  async getAppState(app = '', depth = 6) {
    const params = { depth };
    if (app) params.app = app;
    return this.invoke('get_app_state', params);
  }

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

  async act(params) { return this.invoke('act', params); }
  async press(key, modifiers) { return this.invoke('press_hotkey', { key, modifiers: modifiers || [] }); }
  async type(text) { return this.invoke('type_text', { text }); }
  async launch(app) { return this.invoke('launch', { app }); }
  async clickCoords(x, y) { return this.invoke('click_coords', { x, y }); }
  async clickPoint(x, y, kind = 'left') { return this.invoke('click_point', { x, y, kind }); }
  async dragPoint(x1, y1, x2, y2, button = 'left') { return this.invoke('drag_point', { x1, y1, x2, y2, button }); }
  async cursorMove(x, y) { return this.invoke('cursor_move', { x, y }); }
  async cursorShow() { return this.invoke('cursor_show', {}); }
  async cursorHide() { return this.invoke('cursor_hide', {}); }
  async cursorDebug() { return this.invoke('cursor_debug', {}); }
  async cursorDemo(seconds = 3) { return this.invoke('cursor_demo', { seconds }); }
  async hudShow() { return this.invoke('hud_show', {}); }
  async hudPause(paused) { return this.invoke('hud_pause', { paused: !!paused }); }
  async hudHide() { return this.invoke('hud_hide', {}); }
  async setStopLabel(label) { return this.invoke('set_stop_label', { label: String(label || '') }); }
  async permissions() { return this.invoke('permissions', {}); }
  async requestPermission(kind) { return this.invoke('permissions_request', { kind: String(kind || 'accessibility') }); }
  async scroll(dx, dy) { return this.invoke('scroll', { dx: dx || 0, dy: dy || 0 }); }
  async frontmost() { return this.invoke('frontmost', {}); }

  async listApps(scope = 'running') {
    return this.invoke('list_apps', { scope: scope === 'installed' ? 'installed' : 'running' });
  }
  async resolveApp(app) { return this.invoke('resolve_app', { app: String(app || '') }); }
  async focusApp(app, settle = true) { return this.invoke('focus_app', { app: String(app || ''), settle: !!settle }); }

  async inputText({ app = '', ref = '', text = '', submit = false } = {}) {
    const params = { text: String(text || ''), submit: !!submit };
    if (app) params.app = String(app);
    if (ref) params.ref = String(ref);
    return this.invoke('input_text', params);
  }

  async pressKeyTo(app, key, modifiers = [], repeat = 1) {
    const params = { key: String(key || ''), modifiers: modifiers || [], repeat: Number(repeat) || 1 };
    if (app) params.app = String(app);
    return this.invoke('press_key', params);
  }

  async scrollTo(app, dx, dy, x, y) {
    const params = { dx: Number(dx) || 0, dy: Number(dy) || 0 };
    if (app) params.app = String(app);
    if (typeof x === 'number') params.x = x;
    if (typeof y === 'number') params.y = y;
    return this.invoke('scroll_to', params);
  }

  async dragTo(app, x1, y1, x2, y2, steps = 12) {
    const params = { x1: Number(x1), y1: Number(y1), x2: Number(x2), y2: Number(y2), steps: Number(steps) || 12 };
    if (app) params.app = String(app);
    return this.invoke('drag_to', params);
  }

  async clickPointTo(app, x, y) {
    const params = { x: Number(x), y: Number(y) };
    if (app) params.app = String(app);
    return this.invoke('click_point_to', params);
  }

  async uiSettle({ app = '', quietMs = 250, timeoutMs = 3000 } = {}) {
    const params = { quiet_ms: Number(quietMs) || 250, timeout_ms: Number(timeoutMs) || 3000 };
    if (app) params.app = String(app);
    return this.invoke('ui_settle', params);
  }

  async actFor(app, ref, op, extra = {}) {
    const params = Object.assign({ ref: String(ref || ''), op: String(op || 'click') }, extra || {});
    if (app) params.app = String(app);
    return this.invoke('act', params);
  }

  close() {
    if (this._proc && !this._proc.killed) { try { this._proc.kill(); } catch (e) { /* ignore */ } }
    this._proc = null;
  }
}

function safeIsFile(p) {
  try { return !!p && fs.existsSync(p) && fs.statSync(p).isFile(); } catch (e) { return false; }
}

module.exports = { HelperDriver, safeIsFile, BOOT_TIMEOUT_MS, CALL_TIMEOUT_MS };
