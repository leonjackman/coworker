// computer-repl-kernel.js — isolated worker that runs model-supplied JavaScript.
//
// This file is OUR code. The model's code is compiled inside a `node:vm` context
// whose sandbox exposes only `cua` (the computer-use facade) and a captured
// `console`/`emit`. There is no `require`, `process`, `module`, `fs`, or network
// in the sandbox. Native calls are RPC'd back to the main thread, where every
// action is re-validated (paused gate, permissions, app identity).
//
// The worker persists across `js` cells so bindings survive; `js_reset`
// terminates it and starts a fresh one. Cross-cell state uses `globalThis`
// (each cell compiles independently — document this to the model).
//
// Protocol (worker <-> main):
//   main -> worker : { type:'run', code, cellId }
//   worker -> main : { type:'rpc', id, method, args }
//   main -> worker : { type:'rpc-result', id, ok, result?, error? }
//   worker -> main : { type:'done', cellId, blocks, error? }

'use strict';

const { parentPort } = require('worker_threads');
const vm = require('vm');

let rpcSeq = 0;
const pending = new Map();

parentPort.on('message', (msg) => {
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'rpc-result') {
    const p = pending.get(msg.id);
    if (p) {
      pending.delete(msg.id);
      if (msg.ok) p.resolve(msg.result);
      else p.reject(Object.assign(new Error(msg.error || 'native call failed'), { nativeCode: msg.nativeCode }));
    }
    return;
  }
  if (msg.type === 'run') {
    runCell(msg.cellId, msg.code).catch(() => {});
  }
});

function rpc(method, args) {
  const id = ++rpcSeq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    parentPort.postMessage({ type: 'rpc', id, method, args: args || {} });
  });
}

// ── Element-ref helpers ─────────────────────────────────────────────────────
// `getAXState` stores the refs it returned; integer indices address that list.
// Strings are treated as opaque refs and passed through for native validation.

function parseRefs(text) {
  const refs = [];
  const re = /\[([^\]]+)\]/g;
  let m;
  while ((m = re.exec(String(text || ''))) !== null) refs.push(m[1]);
  return refs;
}

function makeBinding(selector, state) {
  const resolveTarget = (target) => {
    if (typeof target === 'string' && target.trim()) return target.trim();
    if (typeof target === 'number' && Number.isInteger(target) && target >= 0) {
      const ref = state.refs[target];
      if (!ref) {
        throw new Error(
          `element index ${target} has no current observation; call getAXState() first ` +
          `(observed ${state.refs.length} elements)`
        );
      }
      return ref;
    }
    throw new TypeError('element target must be an observed string ref or an integer index');
  };

  const app = async (method, args) => rpc(method, Object.assign({ app: selector }, args || {}));

  const observe = async (opts = {}) => {
    const res = await app('get_app_state', {
      depth: typeof opts.depth === 'number' ? opts.depth : 6,
      disable_diff: !!opts.disableDiffing,
    });
    state.refs = parseRefs(res && (res.text || res.snapshot));
    return res;
  };

  const textOf = (res) => {
    if (!res || typeof res !== 'object') return '';
    const lines = [];
    lines.push(`app=${res.app || selector} pid=${res.pid || '?'} frontmost=${res.frontmost || '?'} changed=${res.changed}`);
    if (Array.isArray(res.removed) && res.removed.length) lines.push(`removed: ${res.removed.join(', ')}`);
    lines.push(String(res.text || res.snapshot || ''));
    return lines.join('\n');
  };

  const binding = {
    selector,
    async getAXState(opts = {}) {
      const res = await observe(opts);
      return res;
    },
    async getAXStateText(opts = {}) {
      return textOf(await observe(opts));
    },
    async getScreenshot(opts = {}) {
      return app('screenshot', { display: opts.display || 0, max_width: opts.maxWidth || 1024 });
    },
    async getAXStateAndScreenshot(opts = {}) {
      const [state, shot] = await Promise.all([observe(opts), app('screenshot', { max_width: opts.maxWidth || 1024 })]);
      return { state, screenshot: shot };
    },
    async click(target, opts = {}) {
      if (Array.isArray(target)) return app('click_point', { x: target[0], y: target[1] });
      const count = Number(opts.clickCount || 1);
      const op = opts.mouseButton === 'right' ? 'right' : (count >= 2 ? 'double' : 'click');
      return app('act', { ref: resolveTarget(target), op });
    },
    async doubleClick(target) {
      return binding.click(target, { clickCount: 2 });
    },
    async rightClick(target) {
      return binding.click(target, { mouseButton: 'right' });
    },
    async focus(target) {
      return app('act', { ref: resolveTarget(target), op: 'focus' });
    },
    async setValue(target, value) {
      return app('act', { ref: resolveTarget(target), op: 'set_value', value: String(value) });
    },
    async performSecondaryAction(target, action) {
      return app('act', { ref: resolveTarget(target), op: 'show', action: String(action) });
    },
    async typeText(text, opts = {}) {
      return app('input_text', { text: String(text), submit: !!opts.submit });
    },
    async paste(text, opts = {}) {
      return app('input_text', { text: String(text), submit: !!opts.submit, prefer: 'clipboard' });
    },
    async selectText(target, text, opts = {}) {
      const ref = resolveTarget(target);
      if (opts.replace !== false) {
        await app('act', { ref, op: 'focus' });
        await app('input_text', { ref, text: String(text) });
      }
      return { performed: 'selectText', ref };
    },
    async pressKey(key, opts = {}) {
      return app('press_key', { key: String(key), repeat: opts.repeat || 1 });
    },
    async scroll(target, direction, pages = 1) {
      const dir = String(direction || 'down').toLowerCase();
      const unit = 120 * Math.max(1, Number(pages) || 1);
      const map = { down: [0, unit], up: [0, -unit], right: [unit, 0], left: [-unit, 0] };
      const [dx, dy] = map[dir] || map.down;
      return app('scroll', { dx, dy });
    },
    async scrollTo(target, dx, dy, x, y) {
      const appRef = resolveTarget(target);
      return app('scroll_to', { app: appRef, dx: Number(dx) || 0, dy: Number(dy) || 0, x: Number(x) || 0, y: Number(y) || 0 });
    },
    async drag(from, to) {
      return app('drag', { x1: from[0], y1: from[1], x2: to[0], y2: to[1] });
    },
    async settle(opts = {}) {
      return app('ui_settle', { quiet_ms: opts.quietMs || 250, timeout_ms: opts.timeoutMs || 3000 });
    },
  };
  return binding;
}

// ── Sandbox ─────────────────────────────────────────────────────────────────

const blocks = [];
function emit(type, value, meta) {
  if (type === 'text') blocks.push({ type: 'text', text: String(value) });
  else if (type === 'image') blocks.push(Object.assign({ type: 'image' }, value || {}, meta || {}));
}

const cua = {
  async listApps(opts = {}) {
    const res = await rpc('list_apps', { scope: opts.installed ? 'installed' : 'running' });
    return (res && res.apps) || [];
  },
  async getApp(selector) {
    if (typeof selector !== 'string' || !selector.trim()) {
      throw new TypeError('getApp requires an app name, bundle ID, or path');
    }
    const resolved = await rpc('resolve_app', { app: selector });
    if (!resolved || !resolved.pid) {
      const err = new Error(`app not running: ${selector}`);
      err.code = 'app_not_running';
      err.resolved = resolved || null;
      throw err;
    }
    return makeBinding(selector, { refs: [], pid: resolved.pid, bundleId: resolved.bundleId || '' });
  },
  async getState() {
    return rpc('frontmost', {});
  },
  emitText(value) { emit('text', value); },
  emitImage(value) { emit('image', value); },
  sleep(ms) { return new Promise((r) => setTimeout(r, Math.max(0, Math.min(60000, Number(ms) || 0)))); },
};

const sandbox = {
  cua,
  console: {
    log: (...a) => emit('text', a.map(stringify).join(' ')),
    error: (...a) => emit('text', '[error] ' + a.map(stringify).join(' ')),
    warn: (...a) => emit('text', '[warn] ' + a.map(stringify).join(' ')),
  },
  emit: (type, value) => emit(type, value),
  globalThis: undefined,
};
sandbox.globalThis = sandbox;
const context = vm.createContext(sandbox);

function stringify(v) {
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

async function runCell(cellId, code) {
  blocks.length = 0;
  try {
    const wrapped = `(async () => {\n${String(code || '')}\n})()`;
    const script = new vm.Script(wrapped, { filename: 'cell.js' });
    await script.runInContext(context, { timeout: 30000 });
    parentPort.postMessage({ type: 'done', cellId, blocks: blocks.slice() });
  } catch (err) {
    parentPort.postMessage({
      type: 'done', cellId, blocks: blocks.slice(),
      error: (err && err.message) ? String(err.message) : String(err),
      errorName: (err && err.name) || 'Error',
    });
  } finally {
    blocks.length = 0;
  }
}
