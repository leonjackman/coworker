#!/usr/bin/env node
// automation-conformance.js — cross-platform contract check for a native
// computer-use helper (macOS cw-automa / Windows cwautoma-win).
//
// It spawns the helper and verifies the FROZEN JSON-RPC contract the shared
// `electron/automation/` layer depends on:
//   * ping advertises the full method set (capability negotiation)
//   * ping advertises the full FEATURE MANIFEST (docs/COMPUTER-USE-PARITY.md)
//   * observation methods return the shared result shapes
//
// With `--parity <other-helper>` it additionally compares two helpers'
// manifests (macOS vs Windows): their METHOD SETS and FEATURE KEY SETS must
// match (values may legitimately differ per platform — that divergence is the
// whole point of the manifest).
//
// Hard failures (exit 1): missing helper, ping contract violated, a required
// method/feature missing, a basic shape mismatch, or a parity key-set mismatch.
// Soft warnings: UI-dependent calls (snapshot of the frontmost app) that cannot
// run on a headless CI session — reported but not fatal.
//
// Usage:
//   node scripts/automation-conformance.js [path-to-helper]
//   node scripts/automation-conformance.js <helperA> --parity <helperB>
//   node scripts/automation-conformance.js --json [path-to-helper]

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

// Feature manifest keys every helper must advertise (values may differ per OS).
// See docs/COMPUTER-USE-PARITY.md for the meaning of each key.
const REQUIRED_FEATURES = [
  'overlay', 'physical_displays', 'ax', 'uia',
  'input_model', 'background_input', 'clipboard_restore', 'target_confirmation',
  'unicode_graphemes', 'middle_click', 'keypad_keys', 'permission_model',
];

let failures = 0;
let warnings = 0;
let JSON_OUT = false;
const report = [];

function ok(msg) { if (!JSON_OUT) console.log(`  \x1b[32m✓\x1b[0m ${msg}`); report.push({ level: 'ok', msg }); }
function fail(msg) { failures++; if (!JSON_OUT) console.error(`  \x1b[31m✗\x1b[0m ${msg}`); report.push({ level: 'fail', msg }); }
function warn(msg) { warnings++; if (!JSON_OUT) console.warn(`  \x1b[33m!\x1b[0m ${msg}`); report.push({ level: 'warn', msg }); }

function safeIsFile(p) { try { return !!p && fs.existsSync(p) && fs.statSync(p).isFile(); } catch { return false; } }

// ── Minimal JSON-RPC client ─────────────────────────────────────────────────
function connect(helper) {
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
    if (pending.size) for (const p of pending.values()) p({ ok: false, error: `helper exited (code ${code})` });
  });
  function invoke(method, params = {}, timeoutMs = 8000) {
    const id = ++seq;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { pending.delete(id); resolve({ ok: false, error: `timeout: ${method}` }); }, timeoutMs);
      pending.set(id, (msg) => { clearTimeout(timer); resolve(msg); });
      try { proc.stdin.write(JSON.stringify({ id, method, params }) + '\n'); }
      catch (e) { clearTimeout(timer); pending.delete(id); resolve({ ok: false, error: String((e && e.message) || e) }); }
    });
  }
  return { invoke, close: () => { try { proc.kill(); } catch { /* ignore */ } } };
}

function shape(value, kind) {
  if (kind === 'object') return value && typeof value === 'object' && !Array.isArray(value);
  if (kind === 'array') return Array.isArray(value);
  if (kind === 'number') return typeof value === 'number';
  if (kind === 'string') return typeof value === 'string';
  return true;
}

// ── Single-helper contract ──────────────────────────────────────────────────
async function checkContract(helper) {
  if (!safeIsFile(helper)) { fail(`helper not found at ${helper}`); return null; }
  if (!JSON_OUT) console.log(`Conformance: ${helper}`);
  const client = connect(helper);
  let manifest = null;
  try {
    const ping = await client.invoke('ping', {});
    if (!ping.ok || !shape(ping.result, 'object')) { fail(`ping failed: ${ping.error || 'no result'}`); return null; }
    const r = ping.result;
    manifest = r;
    if (typeof r.platform !== 'string' || !r.platform) fail('ping.platform must be a non-empty string');
    else ok(`ping.platform = ${r.platform} (version ${r.version})`);

    const methods = Array.isArray(r.methods) ? r.methods : null;
    if (!methods) fail('ping.methods missing — capability negotiation requires it');
    else {
      const missing = REQUIRED_METHODS.filter((m) => !methods.includes(m));
      if (missing.length) fail(`ping.methods missing: ${missing.join(', ')}`);
      else ok(`ping.methods advertises all ${REQUIRED_METHODS.length} contract methods`);
    }

    const features = r.features;
    if (!shape(features, 'object')) fail('ping.features must be an object');
    else {
      const missingF = REQUIRED_FEATURES.filter((f) => !(f in features));
      if (missingF.length) fail(`ping.features missing keys: ${missingF.join(', ')}`);
      else ok(`ping.features advertises all ${REQUIRED_FEATURES.length} manifest keys`);
    }

    // Shape probes (soft on headless sessions).
    const displays = await client.invoke('displays', {});
    if (!displays.ok) warn(`displays failed: ${displays.error}`);
    else if (!Array.isArray(displays.result && displays.result.displays)) fail('displays result must be {displays:[...]}');
    else ok(`displays -> ${displays.result.displays.length} display(s)`);

    const apps = await client.invoke('list_apps', { scope: 'running' });
    if (!apps.ok) warn(`list_apps failed: ${apps.error}`);
    else if (!Array.isArray(apps.result && apps.result.apps)) fail('list_apps result must be {apps:[...]}');
    else ok(`list_apps -> ${apps.result.apps.length} running app(s)`);

    const front = await client.invoke('frontmost', {});
    if (!front.ok) warn(`frontmost failed: ${front.error}`);
    else if (!shape(front.result, 'object') || !('pid' in front.result)) fail('frontmost result must include {pid, app}');
    else ok(`frontmost -> pid=${front.result.pid} app="${front.result.app || ''}"`);

    const perms = await client.invoke('permissions', {});
    if (!perms.ok) warn(`permissions failed: ${perms.error}`);
    else if (!shape(perms.result, 'object')) fail('permissions result must be an object');
    else ok('permissions -> object');

    const snap = await client.invoke('snapshot', { depth: 4 }, 12000);
    if (!snap.ok) warn(`snapshot failed (UI session may be headless): ${snap.error}`);
    else {
      const s = snap.result || {};
      if (!('refs' in s && typeof s.refs === 'number' && typeof s.text === 'string')) {
        warn('snapshot result lacks {refs:number, text:string} (soft: no frontmost window?)');
      } else ok(`snapshot -> refs=${s.refs}, changed=${s.changed}`);
    }
    return manifest;
  } finally {
    client.close();
  }
}

// ── Two-helper parity (macOS vs Windows) ────────────────────────────────────
function diffSets(label, a, b, nameA, nameB) {
  const setA = new Set(a || []);
  const setB = new Set(b || []);
  const onlyA = [...setA].filter((x) => !setB.has(x));
  const onlyB = [...setB].filter((x) => !setA.has(x));
  if (onlyA.length) fail(`parity: ${label} only on ${nameA}: ${onlyA.join(', ')}`);
  if (onlyB.length) fail(`parity: ${label} only on ${nameB}: ${onlyB.join(', ')}`);
  if (!onlyA.length && !onlyB.length) ok(`parity: ${label} match (${setA.size})`);
}

function checkParity(a, b) {
  if (!a || !b) { fail('parity: need two helper manifests'); return; }
  const nameA = a.platform || 'A';
  const nameB = b.platform || 'B';
  diffSets('method set', a.methods, b.methods, nameA, nameB);
  diffSets('feature key set', Object.keys(a.features || {}), Object.keys(b.features || {}), nameA, nameB);
  // Value divergence is EXPECTED (that is what the manifest is for) — surface it.
  const keys = Object.keys(a.features || {}).filter((k) => k in (b.features || {}));
  const divergent = keys.filter((k) => JSON.stringify(a.features[k]) !== JSON.stringify(b.features[k]));
  if (divergent.length) {
    const detail = divergent.map((k) => `${k}: ${nameA}=${JSON.stringify(a.features[k])} ${nameB}=${JSON.stringify(b.features[k])}`).join(' | ');
    warn(`parity: expected manifest divergence — ${detail}`);
  }
}

// ── CLI ─────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const args = argv.slice(2);
  let json = false;
  let parity = null;
  const positional = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--json') json = true;
    else if (args[i] === '--parity') { parity = args[++i]; }
    else positional.push(args[i]);
  }
  return { json, parity, helper: positional[0], helperB: positional[1] };
}

async function main() {
  const { json, parity, helper, helperB } = parseArgs(process.argv);
  JSON_OUT = json;
  const helperA = helper || path.join(__dirname, '..', 'electron', 'cw-automa', 'build', 'cw-automa');

  const manifestA = await checkContract(helperA);
  let manifestB = null;
  if (parity) {
    if (!JSON_OUT) console.log(`\nParity vs: ${parity}`);
    manifestB = await checkContract(parity);
    checkParity(manifestA, manifestB);
  } else if (helperB && safeIsFile(helperB)) {
    // Two positional helpers imply parity.
    manifestB = await checkContract(helperB);
    checkParity(manifestA, manifestB);
  }

  if (JSON_OUT) {
    console.log(JSON.stringify({ failures, warnings, report, manifests: { a: manifestA, b: manifestB } }, null, 2));
  }
  if (failures > 0) {
    if (!JSON_OUT) console.error(`\nConformance FAILED (${failures} failure(s), ${warnings} warning(s)).`);
    process.exit(1);
  }
  if (!JSON_OUT) console.log(`\nConformance PASSED (${warnings} warning(s)).`);
  process.exit(0);
}

main().catch((e) => { fail(`unexpected error: ${(e && e.stack) || e}`); process.exit(1); });
