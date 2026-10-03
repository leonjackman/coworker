#!/usr/bin/env node
// computer-use-probe.js — behavioral acceptance probe for a native computer-use
// helper (macOS cw-automa / Windows cwautoma-win). Complements
// `automation-conformance.js` (which checks the FROZEN contract shape) by
// asserting the CAPABILITY MANIFEST and a few P1 behaviors against a live helper.
//
// Usage: node scripts/computer-use-probe.js <path-to-helper>
//
// Read-only / harmless: ping, list_apps(installed), a missing-app snapshot,
// permissions, a no-op scroll_to, and a sub-second cursor_demo.

'use strict';

const { spawn } = require('child_process');
const fs = require('fs');

const exe = process.argv[2];
let pass = 0;
let fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  \x1b[32mPASS\x1b[0m ${name}`); }
  else { fail++; console.error(`  \x1b[31mFAIL\x1b[0m ${name}  ${detail || ''}`); }
}

if (!exe || !fs.existsSync(exe)) {
  console.error(`helper not found: ${exe || '(none)'}`);
  process.exit(2);
}

const proc = spawn(exe, [], { stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true });
proc.stdout.setEncoding('utf8');
let seq = 0;
let buf = '';
const pending = new Map();
proc.stdout.on('data', (c) => {
  buf += c; let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    let m; try { m = JSON.parse(line); } catch { continue; }
    const p = pending.get(m.id); if (p) { pending.delete(m.id); p(m); }
  }
});
function invoke(method, params = {}, t = 8000) {
  const id = ++seq;
  return new Promise((res) => {
    const timer = setTimeout(() => { pending.delete(id); res({ ok: false, error: 'timeout' }); }, t);
    pending.set(id, (m) => { clearTimeout(timer); res(m); });
    proc.stdin.write(JSON.stringify({ id, method, params }) + '\n');
  });
}

(async () => {
  const ping = await invoke('ping', {});
  const f = (ping.result && ping.result.features) || {};
  check('ping.version >= 4', (ping.result.version || 0) >= 4, 'version=' + (ping.result && ping.result.version));
  // Values the P1 parity work flipped/kept aligned:
  check('clipboard_restore = true', f.clipboard_restore === true, String(f.clipboard_restore));
  check('target_confirmation = true', f.target_confirmation === true, String(f.target_confirmation));
  check('unicode_graphemes = true', f.unicode_graphemes === true, String(f.unicode_graphemes));
  check('middle_click = true', f.middle_click === true, String(f.middle_click));
  check('keypad_keys = true', f.keypad_keys === true, String(f.keypad_keys));
  check("permission_model in {tcc,uipi}", f.permission_model === 'tcc' || f.permission_model === 'uipi', String(f.permission_model));
  // Deliberate, documented divergence (P3 research) — must be advertised:
  if (f.permission_model === 'uipi') {
    check('input_model = global (documented P3)', f.input_model === 'global', String(f.input_model));
  }

  const inst = await invoke('list_apps', { scope: 'installed' });
  const n = ((inst.result && inst.result.apps) || []).length;
  check('list_apps(installed) returns an inventory (>0)', n > 0, 'count=' + n);

  const miss = await invoke('snapshot', { app: 'no-such-app-xyz-123', depth: 2 });
  check('missing app -> no_target (no silent retarget)', miss.ok === false && miss.error_code === 'no_target', JSON.stringify(miss));

  const perms = await invoke('permissions', {});
  const pr = perms.result || {};
  check('permissions exposes model + state', typeof pr.model === 'string' && (typeof pr.accessibility !== 'undefined'), JSON.stringify(pr));

  const scroll = await invoke('scroll_to', { dx: 0, dy: -1 });
  check('scroll_to without an explicit point succeeds', scroll.ok === true && scroll.result && scroll.result.performed === 'scroll_to', JSON.stringify(scroll.error || scroll.result));

  const demo = await invoke('cursor_demo', { seconds: 0.5 }, 6000);
  check('cursor_demo honors seconds', demo.ok === true && demo.result && demo.result.seconds === 0.5, JSON.stringify(demo.error || demo.result));

  proc.kill();
  console.log(`\nProbe: ${pass} passed, ${fail} failed.`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
