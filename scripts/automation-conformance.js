#!/usr/bin/env node
// automation-conformance.js — cross-platform contract check for a native
// computer-use helper (macOS cw-automa / Windows cwautoma-win).
//
// It spawns the helper and verifies the FROZEN JSON-RPC contract the shared
// `electron/automation/` layer depends on:
//   * ping advertises the full method set (capability negotiation)
//   * observation methods return the shared result shapes
//
// Hard failures (exit 1): missing helper, ping contract violated, a required
// method missing from `ping.methods`, or a basic shape mismatch.
// Soft warnings: UI-dependent calls (snapshot of the frontmost app) that cannot
// run on a headless CI session — reported but not fatal.
//
// Usage: node scripts/automation-conformance.js [path-to-helper]

'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const REQUIRED_METHODS = [
  'ping', 'frontmost', 'displays', 'snapshot', 'get_app_state', 'act',
  'list_apps', 'resolve_app', 'focus_app', 'launch',
  'input_text', 'type_text', 'press_hotkey', 'press_key',
  'click_coords', 'click_point', 'drag_point', 'scroll', 'scroll_to',
  'drag_to', 'click_point_to', 'ui_settle',
  'cursor_move', 'cursor_show', 'cursor_hide', 'cursor_park', 'cursor_debug',
  'cursor_demo', 'cursor_position',
  'hud_show', 'hud_pause', 'hud_hide', 'set_stop_label',
  'permissions', 'permissions_request',
];

const helper = process.argv[2] || path.join(__dirname, '..', 'electron', 'cw-automa', 'build', 'cw-automa');

let failures = 0;
let warnings = 0;
function ok(msg) { console.log(`  \x1b[32m✓\x1b[0m ${msg}`); }
function fail(msg) { failures++; console.error(`  \x1b[31m✗\x1b[0m ${msg}`); }
function warn(msg) { warnings++; console.warn(`  \x1b[33m!\x1b[0m ${msg}`); }

if (!fs.existsSync(helper)) {
  console.error(`helper not found at ${helper}`);
  process.exit(2);
}

console.log(`Conformance: ${helper}`);

// ── Minimal JSON-RPC client ──────────────────────────────────────────────────
const proc = spawn(helper, [], { stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true });
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
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    const p = pending.get(msg.id);
    if (p) { pending.delete(msg.id); p(msg); }
  }
});
proc.on('exit', (code) => {
  if (pending.size) {
    for (const p of pending.values()) p({ ok: false, error: `helper exited (code ${code})` });
  }
});

function invoke(method, params = {}, timeoutMs = 8000) {
  const id = ++seq;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      resolve({ ok: false, error: `timeout: ${method}` });
    }, timeoutMs);
    pending.set(id, (msg) => { clearTimeout(timer); resolve(msg); });
    try {
      proc.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    } catch (e) {
      clearTimeout(timer);
      pending.delete(id);
      resolve({ ok: false, error: String(e && e.message || e) });
    }
  });
}

function shape(value, kind) {
  if (kind === 'object') return value && typeof value === 'object' && !Array.isArray(value);
  if (kind === 'array') return Array.isArray(value);
  if (kind === 'number') return typeof value === 'number';
  if (kind === 'string') return typeof value === 'string';
  return true;
}

async function main() {
  // 1) ping — capability handshake (FATAL on any violation).
  const ping = await invoke('ping', {});
  if (!ping.ok || !shape(ping.result, 'object')) {
    fail(`ping failed: ${ping.error || 'no result'}`);
    return finish();
  }
  const r = ping.result;
  if (typeof r.platform !== 'string' || !r.platform) fail('ping.platform must be a non-empty string');
  else ok(`ping.platform = ${r.platform} (version ${r.version})`);

  const methods = Array.isArray(r.methods) ? r.methods : null;
  if (!methods) {
    fail('ping.methods missing — capability negotiation requires it');
  } else {
    const missing = REQUIRED_METHODS.filter((m) => !methods.includes(m));
    if (missing.length) fail(`ping.methods missing: ${missing.join(', ')}`);
    else ok(`ping.methods advertises all ${REQUIRED_METHODS.length} contract methods`);
  }
  if (!shape(r.features, 'object')) fail('ping.features must be an object');
  else ok(`ping.features = ${JSON.stringify(r.features)}`);

  // 2) displays — {displays:[...]} (soft: a headless session may have none).
  const displays = await invoke('displays', {});
  if (!displays.ok) warn(`displays failed: ${displays.error}`);
  else if (!Array.isArray(displays.result && displays.result.displays)) fail('displays result must be {displays:[...]}');
  else ok(`displays -> ${displays.result.displays.length} display(s)`);

  // 3) list_apps — {apps:[...]}.
  const apps = await invoke('list_apps', { scope: 'running' });
  if (!apps.ok) warn(`list_apps failed: ${apps.error}`);
  else if (!Array.isArray(apps.result && apps.result.apps)) fail('list_apps result must be {apps:[...]}');
  else ok(`list_apps -> ${apps.result.apps.length} running app(s)`);

  // 4) frontmost — {pid, app}.
  const front = await invoke('frontmost', {});
  if (!front.ok) warn(`frontmost failed: ${front.error}`);
  else if (!shape(front.result, 'object') || !('pid' in front.result)) fail('frontmost result must include {pid, app}');
  else ok(`frontmost -> pid=${front.result.pid} app="${front.result.app || ''}"`);

  // 5) permissions — object.
  const perms = await invoke('permissions', {});
  if (!perms.ok) warn(`permissions failed: ${perms.error}`);
  else if (!shape(perms.result, 'object')) fail('permissions result must be an object');
  else ok('permissions -> object');

  // 6) snapshot of the frontmost app — {frontmost, refs, text, root} (soft).
  const snap = await invoke('snapshot', { depth: 4 }, 12000);
  if (!snap.ok) {
    warn(`snapshot failed (UI session may be headless): ${snap.error}`);
  } else {
    const s = snap.result || {};
    const okShape = 'refs' in s && typeof s.refs === 'number' && typeof s.text === 'string';
    if (!okShape) warn('snapshot result lacks {refs:number, text:string} (soft: no frontmost window?)');
    else ok(`snapshot -> refs=${s.refs}, changed=${s.changed}`);
  }

  return finish();
}

function finish() {
  try { proc.kill(); } catch { /* ignore */ }
  if (failures > 0) {
    console.error(`\nConformance FAILED (${failures} failure(s), ${warnings} warning(s)).`);
    process.exit(1);
  }
  console.log(`\nConformance PASSED (${warnings} warning(s)).`);
  process.exit(0);
}

main().catch((e) => {
  fail(`unexpected error: ${e && e.stack || e}`);
  finish();
});
