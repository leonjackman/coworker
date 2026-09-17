// ComputerScript — main-process manager for the persistent JS computer-use
// surface (`js` / `js_reset`). It owns an isolated worker running
// `computer-repl-kernel.js`, forwards every native call through a single
// `call(method, args)` handler (the DesktopController re-validates each one),
// and enforces per-cell limits.
//
// Limits mirror the Codex/cc-haha contract:
//   native calls per cell : 256      (includes observations)
//   source bytes          : 256 KiB
//   emitted blocks        : 128
//   emitted bytes         : 16 MiB
//   wall timeout          : 30 s default, 60 s cap
//
// The worker (and therefore `globalThis` bindings) persists across cells;
// `reset()` discards it. A timeout/cancel also discards it, per the contract
// that partially executed work is never replayed.

'use strict';

const { Worker } = require('worker_threads');
const fs = require('fs');
const path = require('path');

const MAX_ACTIONS = 256;
const MAX_CODE_BYTES = 256 * 1024;
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const MAX_BLOCKS = 128;
const DEFAULT_TIMEOUT_MS = 30000;
const MAX_TIMEOUT_MS = 60000;

class ComputerScript {
  constructor(options = {}) {
    this._call = options.call;                 // async (method, args) => result
    this._log = options.log || (() => {});
    this._worker = null;
    this._cellSeq = 0;
    this._pending = null;                      // { cellId, resolve, reject, actions, timer }
    this._closed = false;
  }

  _kernelPath() {
    return path.join(__dirname, 'computer-repl-kernel.js');
  }

  // Read the kernel source and run it with `eval: true`. This works inside
  // app.asar (worker_threads cannot load a module path that lives in the
  // archive) while still giving the worker its own thread and module scope.
  _kernelSource() {
    return fs.readFileSync(this._kernelPath(), 'utf8');
  }

  _ensureWorker() {
    if (this._closed) throw new Error('computer script session is closed');
    if (this._worker) return this._worker;
    const worker = new Worker(this._kernelSource(), { eval: true });
    worker.on('message', (msg) => { if (this._worker === worker) this._onMessage(msg); });
    worker.on('error', (err) => {
      if (this._worker !== worker) return;
      this._failPending(err && err.message ? err.message : String(err));
    });
    worker.on('exit', () => {
      if (this._worker !== worker) return;
      this._worker = null;
      if (this._pending) this._failPending('computer script worker exited');
    });
    this._worker = worker;
    return worker;
  }

  _failPending(message) {
    if (!this._pending) return;
    clearTimeout(this._pending.timer);
    const { reject } = this._pending;
    this._pending = null;
    reject(new Error(message));
  }

  _onMessage(msg) {
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'rpc') {
      const pending = this._pending;
      if (!pending || msg.cellId !== undefined && msg.cellId !== pending.cellId) {
        // Stale call from a discarded cell — answer with an error, never run it.
        this._reply(msg.id, false, null, 'cell is no longer active');
        return;
      }
      if (++pending.actions > MAX_ACTIONS) {
        this._reply(msg.id, false, null, `native call limit reached (${MAX_ACTIONS} per cell)`);
        return;
      }
      Promise.resolve()
        .then(() => this._call(msg.method, msg.args || {}))
        .then((result) => this._reply(msg.id, true, result, null))
        .catch((err) => {
          const message = (err && err.message) ? err.message : String(err);
          this._reply(msg.id, false, null, message, err && err.code);
        });
      return;
    }

    if (msg.type === 'done') {
      const pending = this._pending;
      if (!pending || msg.cellId !== pending.cellId) return;
      clearTimeout(pending.timer);
      this._pending = null;
      const blocks = this._capBlocks(msg.blocks || []);
      pending.resolve({ blocks, error: msg.error || null, errorName: msg.errorName || null });
    }
  }

  _reply(id, ok, result, error, code) {
    if (!this._worker) return;
    try { this._worker.postMessage({ type: 'rpc-result', id, ok, result, error, nativeCode: code }); } catch (e) { /* worker gone */ }
  }

  _capBlocks(blocks) {
    const out = [];
    let bytes = 0;
    for (const b of blocks) {
      if (out.length >= MAX_BLOCKS) break;
      const size = b.type === 'image'
        ? String(b.data || b.image_url || '').length
        : String(b.text || '').length;
      if (bytes + size > MAX_OUTPUT_BYTES) break;
      bytes += size;
      out.push(b);
    }
    if (out.length < blocks.length) {
      out.push({ type: 'text', text: `[output truncated to ${out.length} of ${blocks.length} blocks]` });
    }
    return out;
  }

  // Run one cell. `timeoutMs` is clamped to the 60 s cap.
  async run(code, options = {}) {
    if (typeof code !== 'string' || !code.trim()) throw new Error('js requires non-empty code');
    if (Buffer.byteLength(code) > MAX_CODE_BYTES) {
      throw new Error(`js source exceeds ${MAX_CODE_BYTES} bytes`);
    }
    const timeoutMs = Math.min(
      Math.max(1000, Number(options.timeoutMs) || DEFAULT_TIMEOUT_MS),
      MAX_TIMEOUT_MS
    );
    if (this._pending) {
      throw new Error('a computer script cell is already running');
    }
    const worker = this._ensureWorker();
    const cellId = ++this._cellSeq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const wasPending = this._pending;
        this._killWorker();
        if (wasPending) {
          this._pending = null;
          resolve({
            blocks: [],
            error: `js timed out after ${timeoutMs} ms. Bindings were reset; observe the app before continuing.`,
            errorName: 'TimeoutError',
          });
        }
      }, timeoutMs);
      this._pending = { cellId, resolve, reject, actions: 0, timer };
      worker.postMessage({ type: 'run', cellId, code });
    });
  }

  async reset() {
    this._terminate();
    this._cellSeq = 0;
    return { reset: true };
  }

  _killWorker() {
    const worker = this._worker;
    this._worker = null;
    if (worker) { try { worker.terminate(); } catch (e) { /* ignore */ } }
  }

  _terminate() {
    const worker = this._worker;
    this._worker = null;
    if (this._pending) {
      clearTimeout(this._pending.timer);
      const { reject } = this._pending;
      this._pending = null;
      reject(new Error('computer script cell cancelled'));
    }
    if (worker) { try { worker.terminate(); } catch (e) { /* ignore */ } }
  }

  close() {
    this._closed = true;
    this._terminate();
  }
}

module.exports = { ComputerScript, REPL_LIMITS: { MAX_ACTIONS, MAX_CODE_BYTES, MAX_OUTPUT_BYTES, MAX_BLOCKS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS } };
