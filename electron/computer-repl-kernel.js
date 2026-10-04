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

  // Normalize a key chord into { key, modifiers } and map platform-specific
  // primary modifiers to the NEUTRAL `mod` token. The platform driver resolves
  // `mod` -> Cmd (macOS) / Ctrl (Windows). This is what makes the SAME script
  // correct on both OSes (no `cmd+f` string that breaks on Windows).
  const PRIMARY = new Set(['cmd', 'command', 'super', 'meta', 'win', 'windows']);
  const normalizeChord = (key, modifiers = []) => {
    let tokens = [String(key || '')];
    if (String(key || '').includes('+')) tokens = String(key).split('+');
    const keyToken = tokens.pop() || '';
    const mods = [];
    for (const t of [...tokens, ...(modifiers || [])]) {
      const low = String(t).trim().toLowerCase();
      if (!low) continue;
      mods.push(PRIMARY.has(low) ? 'mod' : (low === 'option' ? 'alt' : low));
    }
    return { key: keyToken.trim(), modifiers: mods };
  };

  const binding = {
    selector,
    async getAXState(opts = {}) {
      const res = await observe(opts);
      // `refs` is the helper's COUNT; `refs_list` is the ordered ref strings so
      // the model can address elements by label instead of regexing `.text`.
      return Object.assign({}, res, { refs_list: state.refs.slice() });
    },
    async getAXStateText(opts = {}) {
      return textOf(await observe(opts));
    },
    async getScreenshot(opts = {}) {
      return app('screenshot', { display: opts.display || 0, max_width: opts.maxWidth || 1024 });
    },
    async getAXStateAndScreenshot(opts = {}) {
      const [res, shot] = await Promise.all([observe(opts), app('screenshot', { max_width: opts.maxWidth || 1024 })]);
      return { state: Object.assign({}, res, { refs_list: state.refs.slice() }), screenshot: shot };
    },
    async click(target, opts = {}) {
      if (Array.isArray(target)) return app('click_point', { x: target[0], y: target[1] });
      const count = Number(opts.clickCount || 1);
      const op = opts.mouseButton === 'right' ? 'right' : (count >= 2 ? 'double' : 'click');
      return app('act', { ref: resolveTarget(target), op });
    },
    async doubleClick(target) {
      return app('act', { ref: resolveTarget(target), op: 'double' });
    },
    async rightClick(target) {
      return app('act', { ref: resolveTarget(target), op: 'right' });
    },
    async clickPoint(x, y) {
      return app('click_point', { x: Number(x), y: Number(y) });
    },
    async focus(target) {
      return app('act', { ref: resolveTarget(target), op: 'focus' });
    },
    async show(target) {
      return app('act', { ref: resolveTarget(target), op: 'show' });
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
    async typeInto(ref, text, opts = {}) {
      return app('input_text', { ref: resolveTarget(ref), text: String(text), submit: !!opts.submit });
    },
    async paste(text, opts = {}) {
      return app('input_text', { text: String(text), submit: !!opts.submit, prefer: 'clipboard' });
    },
    async selectText(target, text) {
      const ref = resolveTarget(target);
      await app('act', { ref, op: 'focus' });
      return app('input_text', { ref, text: String(text) });
    },
    async pressKey(key, opts = {}) {
      const chord = normalizeChord(key, opts.modifiers);
      return app('press_key', { key: chord.key, modifiers: chord.modifiers, repeat: opts.repeat || 1 });
    },
    async shortcut(name, opts = {}) {
      return app('shortcut', { name: String(name), repeat: opts.repeat || 1 });
    },
    async scroll(direction, pages = 1) {
      const dir = String(direction || 'down').toLowerCase();
      const unit = 120 * Math.max(1, Number(pages) || 1);
      const map = { down: [0, unit], up: [0, -unit], right: [unit, 0], left: [-unit, 0] };
      const [dx, dy] = map[dir] || map.down;
      return app('scroll_to', { app: selector, dx, dy });
    },
    async scrollTo(opts = {}) {
      const o = typeof opts === 'object' && opts !== null ? opts : {};
      return app('scroll_to', {
        app: selector,
        dx: Number(o.dx) || 0,
        dy: Number(o.dy) || 0,
        ...(o.x != null ? { x: Number(o.x) } : {}),
        ...(o.y != null ? { y: Number(o.y) } : {}),
      });
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
    // Binds an ALREADY-RUNNING app. To open one, call cua.launchApp(selector).
    if (typeof selector !== 'string' || !selector.trim()) {
      throw new TypeError('getApp requires an app name, bundle ID, or path');
    }
    const resolved = await rpc('resolve_app', { app: selector });
    if (!resolved || !resolved.pid) {
      const err = new Error(`app not running: ${selector} (use cua.launchApp to open it)`);
      err.code = 'app_not_running';
      err.resolved = resolved || null;
      throw err;
    }
    return makeBinding(selector, { refs: [], pid: resolved.pid, bundleId: resolved.bundleId || '' });
  },
  async launchApp(selector) {
    if (typeof selector !== 'string' || !selector.trim()) {
      throw new TypeError('launchApp requires an app name, bundle ID, path, or executable');
    }
    const res = await rpc('launch', { app: selector });
    if (res && res.ok === false) {
      const err = new Error(`could not launch ${selector}`);
      err.code = 'launch_failed';
      err.detail = res;
      throw err;
    }
    return res || {};
  },
  async focusApp(selector) {
    const res = await rpc('focus_app', { app: String(selector || ''), settle: true });
    if (res && res.focused === false) {
      const err = new Error(`could not focus ${selector}`);
      err.code = 'no_target';
      err.detail = res;
      throw err;
    }
    return res || {};
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

function cloneSafe(value) {
  if (value === undefined) return null;
  try { return JSON.parse(JSON.stringify(value)); } catch (e) { return null; }
}

async function runCell(cellId, code) {
  blocks.length = 0;
  try {
    const wrapped = `(async () => {\n${String(code || '')}\n})()`;
    const script = new vm.Script(wrapped, { filename: 'cell.js' });
    // The script's RESOLVED VALUE is the declared `result` output; text blocks
    // become the declared `text` output. (Previously the value was discarded.)
    const value = await script.runInContext(context, { timeout: 30000 });
    const text = blocks.filter((b) => b.type === 'text').map((b) => b.text).join('\n');
    parentPort.postMessage({ type: 'done', cellId, blocks: blocks.slice(), result: cloneSafe(value), text });
  } catch (err) {
    const text = blocks.filter((b) => b.type === 'text').map((b) => b.text).join('\n');
    parentPort.postMessage({
      type: 'done', cellId, blocks: blocks.slice(), result: null, text,
      error: (err && err.message) ? String(err.message) : String(err),
      errorName: (err && err.name) || 'Error',
    });
  } finally {
    blocks.length = 0;
  }
}
