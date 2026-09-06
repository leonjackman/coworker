// ActivityOverlay — a transparent, non-interactive full-display veil that tells
// the human "the agent is operating right now".
//
// While the agent controls the desktop, the OS screen it is acting on gets a
// thin animated border frame + corner pill (「CoWorker 正在操作…」), plus a
// short focus ring at the exact point being clicked — the desktop mirror of the
// action-highlight animation ego-browser shows in the browser it owns.
//
// Implementation notes:
//  * One overlay BrowserWindow per display, keyed by display id, sized to the
//    display bounds in DIP ("points"). Electron DIPs == the CGEvent / nut.js
//    coordinate space we already use, so the ring can be aligned exactly where
//    the click lands by subtracting display.bounds from the global point.
//  * `transparent`, `frame:false`, `alwaysOnTop` at 'screen-saver' level,
//    `setVisibleOnAllWorkspaces`, `setFocusable(false)`, `showInactive()`.
//  * `setIgnoreMouseEvents(true)` — the veil NEVER steals a click. Pausing is
//    via the ⇧⌘⎋ hotkey / tray (see DesktopController), not by clicking the pill.
//  * No Screen Recording permission needed: we never capture the screen, we
//    simply draw our own transparent window (works even for the unsigned dev
//    build). Covering FULL-SCREEN apps may additionally require Accessibility.
//  * Auto-hides after a short idle so it never lingers between agent turns.

'use strict';

const { BrowserWindow } = require('electron');

const IDLE_MS = 4000;
const RING_MS = 700;

const STARTER = '<!doctype html><html><head><meta charset="utf-8"><style>';
const CSS = `
  * { margin:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; background:transparent; overflow:hidden;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
    -webkit-user-select:none; user-select:none; }
  .cu-frame { position:absolute; inset:6px; border:3px solid #4f8cff; border-radius:14px;
    box-shadow:0 0 18px rgba(79,140,255,.55), inset 0 0 14px rgba(79,140,255,.18);
    animation:cuHue 3s linear infinite; pointer-events:none; }
  @keyframes cuHue { 0%{filter:hue-rotate(0)} 50%{filter:hue-rotate(95deg)} 100%{filter:hue-rotate(0)} }
  .cu-frame:after { content:''; position:absolute; inset:5px; border:2px dashed rgba(255,255,255,.5);
    border-radius:10px; opacity:.55; animation:cuDashPulse 1.6s ease-in-out infinite; }
  @keyframes cuDashPulse { 0%,100%{opacity:.28} 50%{opacity:.7} }
  .cu-scan { position:absolute; left:14px; right:14px; height:2px; top:14px; opacity:.85;
    background:linear-gradient(90deg,transparent,#4f8cff,transparent); animation:cuScan 2.4s linear infinite; }
  @keyframes cuScan { 0%{top:16px} 100%{top:calc(100% - 16px)} }
  .cu-ring { position:absolute; width:44px; height:44px; transform:translate(-50%,-50%);
    border:3px solid #4f8cff; border-radius:50%; box-shadow:0 0 16px rgba(79,140,255,.75);
    opacity:0; pointer-events:none; }
  .cu-ring.show { animation:cuRipple .7s ease-out forwards; }
  @keyframes cuRipple { 0%{opacity:1; transform:translate(-50%,-50%) scale(.6)}
    100%{opacity:0; transform:translate(-50%,-50%) scale(1.7)} }
  .cu-pill { position:absolute; top:16px; left:50%; transform:translateX(-50%);
    display:flex; align-items:center; gap:8px; padding:7px 14px; border-radius:999px;
    background:rgba(20,24,34,.78); color:#eaf2ff; font-size:13px; font-weight:600;
    border:1px solid rgba(79,140,255,.5); backdrop-filter:blur(6px);
    pointer-events:none; white-space:nowrap; }
  .cu-dot { width:8px; height:8px; border-radius:50%; background:#4f8cff; animation:cuBlink 1s steps(2) infinite; }
  @keyframes cuBlink { 50%{opacity:.15} }
  .cu-pill.paused { background:rgba(64,22,22,.8); border-color:rgba(255,92,92,.6); color:#ffd9d9; }
  .cu-pill.paused .cu-dot { background:#ff5c5c; animation:none; }
`;
const ENDER = '</style></head><body>'
  + '<div class="cu-frame"></div><div class="cu-scan"></div>'
  + '<div class="cu-ring"></div>'
  + '<div class="cu-pill"><span class="cu-dot"></span><span class="cu-pill-text"></span></div>'
  + '</body></html>';

const HTML = STARTER + CSS + ENDER;

// Stop-shortcut hint shown in the pill. The user can rebind the "stop computer
// control" shortcut in Settings; the renderer syncs it here (setStopShortcut) so
// this text is NEVER hardcoded to the cryptic ⎋ glyph.
const DEFAULT_STOP_LABEL = '⌘ + ⇧ Esc';

class ActivityOverlay {
  constructor() {
    this.paused = false;
    this._stopLabel = DEFAULT_STOP_LABEL;
    this._wins = new Map();
    this._timer = null;
    this._busy = false;
  }

  setStopShortcut(label) {
    if (typeof label === 'string' && label) this._stopLabel = label;
    for (const win of this._wins.values()) {
      if (!win.isDestroyed()) this._setPill(win);
    }
  }

  _activeText() {
    return `CoWorker 正在操作這台電腦 · ${this._stopLabel} 可暫停`;
  }

  _pausedText() {
    return `已暫停 · ${this._stopLabel} 或 tray 恢復`;
  }

  _makeWindow(display) {
    const { id, bounds } = display;
    const win = new BrowserWindow({
      x: Math.round(bounds.x),
      y: Math.round(bounds.y),
      width: Math.round(bounds.width),
      height: Math.round(bounds.height),
      show: false,
      type: 'panel',           // NSPanel: floating, non-activating — must NOT be
                               // a regular app window (that confused macOS into
                               // treating the app as accessory & dropping the Docker icon)
      frame: false,
      transparent: true,
      backgroundColor: '#00000000',
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      hasShadow: false,
      focusable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        backgroundThrottling: false,
      },
    });
    win.setAlwaysOnTop(true, 'screen-saver');
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    win.setFocusable(false);
    win.setIgnoreMouseEvents(true);
    win.webContents.setVisualZoomLevelLimits(1, 1);
    win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(HTML)}`);
    win.webContents.once('did-finish-load', () => this._setPill(win));
    this._wins.set(id, win);
    return win;
  }

  _ensure(display) {
    const { id, bounds } = display;
    let win = this._wins.get(id);
    if (!win || win.isDestroyed()) {
      win = this._makeWindow(display);
    } else {
      const cur = win.getBounds();
      if (cur.x !== Math.round(bounds.x) || cur.y !== Math.round(bounds.y)
        || cur.width !== Math.round(bounds.width) || cur.height !== Math.round(bounds.height)) {
        win.setBounds({ x: Math.round(bounds.x), y: Math.round(bounds.y), width: Math.round(bounds.width), height: Math.round(bounds.height) });
      }
    }
    return win;
  }

  _setPill(win) {
    const text = this.paused ? this._pausedText() : this._activeText();
    if (win && !win.isDestroyed()) {
      try {
        win.webContents.executeJavaScript(
          `(function(){var p=document.querySelector('.cu-pill');if(!p)return;`
          + `p.classList.toggle('paused',${this.paused});`
          + `var t=p.querySelector('.cu-pill-text');if(t)t.textContent=${JSON.stringify(text)};})();`
        );
      } catch (e) { /* ignore */ }
    }
  }

  _bumpIdle() {
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(() => this.hide(), IDLE_MS);
  }

  _showWin(win, display) {
    win.showInactive();
    win.moveTop();
    this._setPill(win);
    this._bumpIdle();
  }

  veil(display) {
    if (!display || !display.bounds) return;
    const win = this._ensure(display);
    this._showWin(win, display);
  }

  ping(display, pointLocal) {
    if (!display || !display.bounds) return;
    const win = this._ensure(display);
    this._showWin(win, display);
    if (pointLocal && typeof pointLocal.x === 'number' && typeof pointLocal.y === 'number') {
      const cx = pointLocal.x;
      const cy = pointLocal.y;
      try {
        win.webContents.executeJavaScript(
          `(function(){var r=document.querySelector('.cu-ring');if(!r)return;`
          + `r.style.left=${JSON.stringify(cx)} + 'px';r.style.top=${JSON.stringify(cy)} + 'px';`
          + `r.classList.remove('show');void r.offsetWidth;r.classList.add('show');`
          + `setTimeout(function(){r.classList.remove('show');},${RING_MS});})();`
        );
      } catch (e) { /* ignore */ }
    }
  }

  setPaused(paused) {
    this.paused = !!paused;
    for (const win of this._wins.values()) {
      if (!win.isDestroyed()) this._setPill(win);
    }
  }

  show() {
    for (const win of this._wins.values()) {
      if (!win.isDestroyed()) this._showWin(win, this._displayFor(win));
    }
  }

  hide() {
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    for (const win of this._wins.values()) {
      if (!win.isDestroyed()) win.hide();
    }
  }

  _displayFor(win) {
    // Recompute bounds from the window itself (used by show()).
    const b = win.getBounds();
    return { id: String(win.id), bounds: b };
  }

  async capture(display) {
    const d = display && display.bounds ? display : null;
    const win = d ? this._ensure(d) : [...this._wins.values()][0];
    if (!win || win.isDestroyed()) return null;
    if (win.webContents.isLoading()) return null;
    const image = await win.webContents.capturePage();
    if (!image || !image.getSize || !image.getSize().width) return null;
    return `data:image/jpeg;base64,${image.toJPEG(70).toString('base64')}`;
  }

  destroy() {
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    for (const win of this._wins.values()) {
      if (!win.isDestroyed()) win.destroy();
    }
    this._wins.clear();
  }
}

module.exports = { ActivityOverlay, HTML };
