#!/usr/bin/env node
// computer-repl-smoke.js — exercises the persistent JS surface end-to-end
// against the real native helper, WITHOUT Electron. It implements the same
// `call(method, args)` contract DesktopController provides, then runs a few
// cells and checks the kernel, limits, and sandbox.
//
// Usage: node scripts/computer-repl-smoke.js [path-to-cw-automa]

'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const { ComputerScript } = require('../electron/computer-repl');

const BIN = process.argv[2] || path.join(__dirname, '..', 'electron', 'cw-automa', 'build', 'cw-automa');
if (!fs.existsSync(BIN)) {
  console.error('helper not found at', BIN);
  process.exit(2);
}

// ── Minimal helper client (mirrors AutomationAdapter for the calls we use) ──
const proc = spawn(BIN, [], { stdio: ['pipe', 'pipe', 'inherit'] });
proc.stdout.setEncoding('utf8');
let seq = 0;
let buffer = '';
const pending = new Map();
proc.stdout.on('data', (chunk) => {
  buffer += chunk;
  let i;
  while ((i = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, i);
    buffer = buffer.slice(i + 1);
    if (!line.trim()) continue;
    let msg; try { msg = JSON.parse(line); } catch { continue; }
    const p = pending.get(msg.id);
    if (p) { pending.delete(msg.id); p(msg); }
  }
});
function invoke(method, params) {
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    proc.stdin.write(JSON.stringify({ id, method, params: params || {} }) + '\n');
  });
}

async function call(method, args = {}) {
  switch (method) {
    case 'list_apps': return (await invoke('list_apps', { scope: args.scope || 'running' })).result;
    case 'resolve_app': return (await invoke('resolve_app', { app: args.app })).result;
    case 'frontmost': return (await invoke('frontmost', {})).result;
    case 'get_app_state': {
      const m = await invoke('get_app_state', { app: args.app || '', depth: args.depth || 6 });
      if (!m.ok) throw new Error(m.error);
      return m.result;
    }
    case 'ui_settle': return (await invoke('ui_settle', { app: args.app, timeout_ms: args.timeout_ms || 800 })).result;
    default: throw new Error('smoke: unsupported call ' + method);
  }
}

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exitCode = 1; }
  else console.log('ok  :', msg);
}

(async () => {
  await invoke('ping', {});
  const script = new ComputerScript({ call });

  // 1) listApps + getApp + observe (read-only)
  const r1 = await script.run(`
    const apps = await cua.listApps();
    cua.emitText('apps=' + apps.length);
    const app = await cua.getApp('Finder');
    const st = await app.getAXState();
    cua.emitText('refs=' + (st.refs || 0) + ' app=' + st.app + ' frontmost=' + st.frontmost);
    return 'done';
  `);
  console.log('cell1 blocks:', JSON.stringify(r1.blocks));
  assert(!r1.error, 'cell1 no error');
  assert(r1.blocks.some((b) => /apps=\d+/.test(b.text || '')), 'cell1 listApps emitted a count');
  assert(r1.blocks.some((b) => /refs=\d+/.test(b.text || '')), 'cell1 observed AX refs');

  // 2) integer element addressing requires an observation
  const r2 = await script.run(`
    const app = await cua.getApp('Finder');
    try { await app.click(0); cua.emitText('unexpected'); }
    catch (e) { cua.emitText('guarded:' + e.message.slice(0, 40)); }
  `);
  assert(!r2.error, 'cell2 no error');
  assert(r2.blocks.some((b) => /guarded:/.test(b.text || '')), 'integer index guarded before observation');

  // 3) sandbox: require/process must be undefined
  const r3 = await script.run(`
    cua.emitText('require=' + (typeof require) + ' process=' + (typeof process) + ' fetch=' + (typeof fetch));
  `);
  assert(r3.blocks.some((b) => /require=undefined process=undefined/.test(b.text || '')), 'sandbox hides require/process');

  // 4) timeout resets bindings (runaway loop)
  const r4 = await script.run('while (true) {}', { timeoutMs: 1200 });
  assert(!!r4.error && /timed out/.test(r4.error), 'runaway loop times out');

  // 5) bindings reset after timeout; fresh worker works again
  const r5 = await script.run(`cua.emitText('alive');`);
  assert(r5.blocks.some((b) => (b.text || '') === 'alive'), 'session usable after timeout');

  await script.reset();
  script.close();
  proc.kill();
  console.log(process.exitCode ? 'SMOKE FAILED' : 'SMOKE PASSED');
  process.exit(process.exitCode || 0);
})();
