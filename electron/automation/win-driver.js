// WinDriver — Windows implementation of the shared HelperDriver contract.
//
// Wraps the native `cwautoma-win` helper (UI Automation first: element tree +
// refs + control patterns, SendInput coordinate fallback). The helper speaks the
// exact same JSON-lines protocol as macOS `cw-automa`, so the shared
// orchestration in desktop-controller.js is identical across platforms.
//
// The one real platform difference is coordinates: Electron reports display
// bounds in DIP (device-independent pixels), while Windows input injection
// (SendInput) works in physical pixels. The helper's `displays` method returns
// both, and this driver converts every display-local point DIP -> physical
// before dispatch.
//
//   dev       electron/cw-automa-win/bin/cwautoma-win.exe
//   packaged  <app Resources>/cw-automa-win/cwautoma-win.exe

'use strict';

const path = require('path');

const { HelperDriver, safeIsFile } = require('./driver');

function candidateBinaryPaths() {
  const { app } = require('electron');
  const isPackaged = !!(app && app.isPackaged);
  const candidates = [];
  if (isPackaged && process.resourcesPath) {
    const rp = process.resourcesPath;
    candidates.push(path.join(rp, 'cw-automa-win', 'cwautoma-win.exe'));
    candidates.push(path.join(rp, 'cw-automa-win', 'cwautoma-win'));
  }
  if (!isPackaged) {
    candidates.push(path.join(__dirname, '..', 'cw-automa-win', 'bin', 'cwautoma-win.exe'));
  }
  return candidates.filter((p) => !String(p).includes('app.asar'));
}

function resolveBinaryPath() {
  for (const p of candidateBinaryPaths()) {
    if (safeIsFile(p)) return p;
  }
  return null;
}

class WinDriver extends HelperDriver {
  constructor(options = {}) {
    super(options);
    this.platform = 'win32';
    this._monitorCache = null;
  }

  candidateBinaryPaths() { return candidateBinaryPaths(); }
  _resolve() { return resolveBinaryPath(); }
  _ensureExecutable() { /* Windows has no chmod bit */ }

  mapPoint(x, y) { // exposed for symmetry / diagnostics
    const p = this._toPhysical(Number(x) || 0, Number(y) || 0);
    return p;
  }

  // ── DIP -> physical conversion ────────────────────────────────────────
  // Electron reports DIP bounds; the helper reports physical monitor bounds.
  // Pair them by index (both follow OS monitor order) and map a point by its
  // fraction within the monitor, so absolute DIP/physical origins need not match.
  async _monitors() {
    if (this._monitorCache) return this._monitorCache;
    let physical = [];
    try {
      const res = await this.invoke('displays', {});
      physical = Array.isArray(res.displays) ? res.displays : [];
    } catch (e) {
      physical = [];
    }
    let logicalDisplays = [];
    try {
      logicalDisplays = require('electron').screen.getAllDisplays();
    } catch (e) {
      logicalDisplays = [];
    }
    const monitors = physical.map((m, i) => {
      const logicalDisplay = logicalDisplays[i];
      const logical = logicalDisplay ? {
        x: Math.round(logicalDisplay.bounds.x),
        y: Math.round(logicalDisplay.bounds.y),
        width: Math.max(1, Math.round(logicalDisplay.bounds.width)),
        height: Math.max(1, Math.round(logicalDisplay.bounds.height)),
      } : (m.logical_bounds || m.bounds || { x: 0, y: 0, width: 0, height: 0 });
      const phys = m.bounds || m.physical_bounds || logical;
      const sx = logical.width ? phys.width / logical.width : (Number(m.scale_factor) || 1);
      const sy = logical.height ? phys.height / logical.height : (Number(m.scale_factor) || 1);
      return { index: Number(m.index) || i, logical, physical: phys, scaleX: sx || 1, scaleY: sy || 1 };
    });
    this._monitorCache = monitors;
    return monitors;
  }

  _toPhysical(x, y) {
    const monitors = this._monitorCache;
    if (!monitors || !monitors.length) return { x: Math.round(x), y: Math.round(y) };
    let m = monitors.find((mm) => pointIn(mm.logical, x, y));
    if (!m) {
      // Nearest monitor by center distance (handles points just outside bounds).
      m = monitors.reduce((best, cur) =>
        distToCenter(cur.logical, x, y) < distToCenter(best.logical, x, y) ? cur : best, monitors[0]);
    }
    const px = m.physical.x + (x - m.logical.x) * m.scaleX;
    const py = m.physical.y + (y - m.logical.y) * m.scaleY;
    return { x: Math.round(px), y: Math.round(py) };
  }

  // Prime the monitor cache from the helper before the first coordinate action.
  async _ensureMonitors() {
    if (!this._monitorCache) { try { await this._monitors(); } catch (e) { /* ignore */ } }
  }

  // ── Coordinate-aware overrides (DIP -> physical) ──────────────────────
  async clickCoords(x, y) {
    await this._ensureMonitors();
    const p = this._toPhysical(Number(x) || 0, Number(y) || 0);
    return super.clickCoords(p.x, p.y);
  }

  async clickPoint(x, y, kind = 'left') {
    await this._ensureMonitors();
    const p = this._toPhysical(Number(x) || 0, Number(y) || 0);
    return super.clickPoint(p.x, p.y, kind);
  }

  async dragPoint(x1, y1, x2, y2, button = 'left') {
    await this._ensureMonitors();
    const a = this._toPhysical(Number(x1) || 0, Number(y1) || 0);
    const b = this._toPhysical(Number(x2) || 0, Number(y2) || 0);
    return super.dragPoint(a.x, a.y, b.x, b.y, button);
  }

  async cursorMove(x, y) {
    await this._ensureMonitors();
    const p = this._toPhysical(Number(x) || 0, Number(y) || 0);
    return super.cursorMove(p.x, p.y);
  }

  async clickPointTo(app, x, y) {
    await this._ensureMonitors();
    const p = this._toPhysical(Number(x) || 0, Number(y) || 0);
    return super.clickPointTo(app, p.x, p.y);
  }

  async dragTo(app, x1, y1, x2, y2, steps = 12) {
    await this._ensureMonitors();
    const a = this._toPhysical(Number(x1) || 0, Number(y1) || 0);
    const b = this._toPhysical(Number(x2) || 0, Number(y2) || 0);
    return super.dragTo(app, a.x, a.y, b.x, b.y, steps);
  }

  async scrollTo(app, dx, dy, x, y) {
    await this._ensureMonitors();
    if (typeof x !== 'number' || typeof y !== 'number') return super.scrollTo(app, dx, dy, x, y);
    const p = this._toPhysical(x, y);
    return super.scrollTo(app, dx, dy, p.x, p.y);
  }

  close() {
    this._monitorCache = null;
    return super.close();
  }
}

function pointIn(b, x, y) {
  return !!b && x >= b.x && x < b.x + b.width && y >= b.y && y < b.y + b.height;
}

function distToCenter(b, x, y) {
  const cx = b.x + b.width / 2;
  const cy = b.y + b.height / 2;
  const dx = x - cx;
  const dy = y - cy;
  return dx * dx + dy * dy;
}

module.exports = { WinDriver, candidateBinaryPaths, resolveBinaryPath };
