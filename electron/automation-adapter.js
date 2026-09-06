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

const { spawn } = require('child_process');
const path = require('path');

// Boot the helper until it answers a ping, so the bridge never races it.
const BOOT_TIMEOUT_MS = 8000;

class AutomationAdapter {
  constructor(options = {}) {
    this.binaryPath = options.binaryPath || defaultBinaryPath();
    this._proc = null;
    this._seq = 0;
    this._pending = new Map();
    this._buffer = '';
    this.ready = false;
    this._bootWaiters = [];
  }

  _spawn() {
    if (this._proc && !this._proc.killed) return;
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
    if (!ok) throw new Error('automation helper unavailable');
    const msg = await this._call(method, params);
    if (!msg.ok) throw new Error(msg.error || `${method} failed`);
    return msg.result ?? {};
  }

  // ── High-level API ───────────────────────────────────────────────────
  async snapshot(depth = 6) {
    return this.invoke('snapshot', { depth });
  }

  // Flatten the tree into compact `[ref] role label` lines the model can read.
  async snapshotText(depth = 6, maxLines = 400) {
    const res = await this.snapshot(depth);
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

function defaultBinaryPath() {
  if (process.platform !== 'darwin') return '';
  const { app } = require('electron');
  // Packaged: cw-automa is copied under the app resources (extraResources).
  if (app && app.isPackaged && process.resourcesPath) {
    return path.join(process.resourcesPath, 'cw-automa');
  }
  // Dev: build output under electron/cw-automa/build.
  return path.join(__dirname, 'cw-automa', 'build', 'cw-automa');
}

module.exports = { AutomationAdapter, defaultBinaryPath };
