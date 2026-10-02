// MacDriver — macOS implementation of the shared HelperDriver contract.
//
// Wraps the native `cw-automa` Swift helper (Accessibility-first). Binary
// resolution + dev auto-compile live here; everything else is inherited from
// HelperDriver, so the JSON-RPC contract is identical across platforms.
//
//   dev       electron/cw-automa/build/cw-automa          (auto-compiled if stale)
//   packaged  <app Resources>/cw-automa/cw-automa         (electron-builder copies
//             electron/cw-automa/build INTO "<Resources>/cw-automa")

'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const { HelperDriver, safeIsFile } = require('./driver');

function candidateBinaryPaths() {
  const { app } = require('electron');
  const isPackaged = !!(app && app.isPackaged);
  const candidates = [];
  if (isPackaged && process.resourcesPath) {
    const rp = process.resourcesPath;
    candidates.push(path.join(rp, 'cw-automa', 'cw-automa'));
    candidates.push(path.join(rp, 'cw-automa'));
    candidates.push(path.join(rp, 'bin', 'cw-automa'));
  }
  if (!isPackaged) {
    candidates.push(path.join(__dirname, '..', 'cw-automa', 'build', 'cw-automa'));
  }
  // Never try a path inside the asar: spawning an asar-embedded binary fails.
  return candidates.filter((p) => !String(p).includes('app.asar'));
}

function resolveBinaryPath() {
  for (const p of candidateBinaryPaths()) {
    if (safeIsFile(p)) return p;
  }
  return null;
}

// Compile the Swift helper from source (dev only). Returns true on success.
function buildHelper(sourceBinaryPath) {
  try {
    const srcDir = path.join(__dirname, '..', 'cw-automa', 'src');
    if (!fs.existsSync(path.join(srcDir, 'main.swift'))) return false;
    const sources = fs.readdirSync(srcDir)
      .filter((f) => f.endsWith('.swift'))
      .sort()
      .map((f) => path.join(srcDir, f));
    fs.mkdirSync(path.dirname(sourceBinaryPath), { recursive: true });
    execFileSync('swiftc', ['-O', '-o', sourceBinaryPath, ...sources], { timeout: 180000 });
    return fs.existsSync(sourceBinaryPath);
  } catch (e) {
    try { console.warn('[automa:darwin] swiftc build failed:', (e && e.message) || e); } catch (err) { /* ignore */ }
    return false;
  }
}

class MacDriver extends HelperDriver {
  constructor(options = {}) {
    super(options);
    this.platform = 'darwin';
  }

  candidateBinaryPaths() { return candidateBinaryPaths(); }

  _resolve() {
    const { app } = require('electron');
    const isDev = !(app && app.isPackaged);
    const srcDir = path.join(__dirname, '..', 'cw-automa', 'src');
    const found = resolveBinaryPath();
    const sourceBinaryPath = path.join(__dirname, '..', 'cw-automa', 'build', 'cw-automa');

    // Dev: (re)compile from src when the binary is missing or older than any
    // Swift source, so the app never runs a stale helper.
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
      if (stale && buildHelper(sourceBinaryPath)) return sourceBinaryPath;
    }
    return found || null;
  }

  _ensureExecutable() {
    try { if (this.binaryPath && fs.existsSync(this.binaryPath)) fs.chmodSync(this.binaryPath, 0o755); } catch (e) { /* ignore */ }
  }

  // macOS input injection already uses top-left logical point space — the same
  // space `shotToPoint` produces. No conversion.
  mapPoint(x, y) { return { x: Number(x) || 0, y: Number(y) || 0 }; }
}

module.exports = { MacDriver, candidateBinaryPaths, resolveBinaryPath, buildHelper };
