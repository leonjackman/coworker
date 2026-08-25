// Dependency bootstrap: ensure all required npm packages are installed.
require('./bootstrap');

// Patch for Electron 43: Remove node_modules/electron package from require path
// to allow Electron's built-in electron module to be used.
// Without this patch, require('electron') returns a binary path string instead
// of the electron module object (containing app, BrowserWindow, etc.)
(function patchElectronRequire() {
  const Module = require('module');
  const origResolve = Module._resolveFilename;
  const path = require('path');
  const projectRoot = __dirname;

  Module._resolveFilename = function(request, parent, isMain, options) {
    if (request === 'electron') {
      // In Electron process, skip node_modules/electron and use built-in module
      // We do this by temporarily removing electron from the resolution path
      const origPaths = parent ? parent.paths : [];
      const filteredPaths = origPaths.filter(p => {
        return !p.includes('/node_modules/electron') &&
               !p.includes('\\node_modules\\electron');
      });

      // Save original paths and use filtered ones
      if (filteredPaths.length < origPaths.length && parent) {
        const origResolvePaths = parent.paths;
        parent.paths = filteredPaths;
        try {
          const resolved = origResolve.call(this, request, parent, isMain, options);
          // Restore original paths
          parent.paths = origResolvePaths;
          return resolved;
        } catch (e) {
          parent.paths = origResolvePaths;
          // If resolution fails, try with original paths as fallback
          return origResolve.call(this, request, parent, isMain, options);
        }
      }
    }
    return origResolve.apply(this, arguments);
  };
})();

const { app, BrowserWindow, ipcMain, Menu, Tray, clipboard, dialog, nativeImage, nativeTheme, screen, shell } = require('electron');
const path = require('path');
const http = require('http');
const fs = require('fs');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { autoUpdater } = require('electron-updater');
const { CancellationToken } = require('builder-util-runtime');

// `app.isPackaged` is the only reliable packaged/dev signal in Electron —
// NODE_ENV and IS_PACKAGED are not set automatically.
const IS_DEV = !app.isPackaged || process.env.COWORKER_DEV === '1';

// ── Auto-update settings persistence ───────────────────────────────────
const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;

function updateSettingsFilePath() {
  return path.join(app.getPath('userData'), 'update-settings.json');
}

function defaultUpdateSettings() {
  return { autoUpdateEnabled: true, skippedVersion: null };
}

function readUpdateSettings() {
  try {
    const raw = fs.readFileSync(updateSettingsFilePath(), 'utf8');
    const parsed = JSON.parse(raw);
    return {
      autoUpdateEnabled: parsed.autoUpdateEnabled !== false,
      skippedVersion: typeof parsed.skippedVersion === 'string' ? parsed.skippedVersion : null,
    };
  } catch {
    return defaultUpdateSettings();
  }
}

function writeUpdateSettings(patch) {
  const next = { ...readUpdateSettings(), ...patch };
  try {
    fs.mkdirSync(path.dirname(updateSettingsFilePath()), { recursive: true });
    fs.writeFileSync(updateSettingsFilePath(), JSON.stringify(next, null, 2), 'utf8');
  } catch (err) {
    console.error('Failed to persist update settings:', err);
  }
  return next;
}

let updateSettings = readUpdateSettings();

// ── Auto-updater state (single source of truth for the renderer) ───────
let autoUpdateTimer = null;
let updateState = {
  state: 'idle', // idle | checking | up-to-date | available | downloading | downloaded | error
  availableVersion: null,
  releaseNotes: null,
  progress: null, // { percent, bytesPerSecond, transferred, total }
  errorMessage: null,
  errorCode: null,
};

// Classify auto-update failures so the renderer can show a clear, localized
// message instead of a raw upstream error string.
//
// 'UNREACHABLE' — the update source cannot be reached at all. GitHub returns
// 404 for every unauthenticated request against a private repo (or a repo
// with no public release), which surfaces here as ERR_UPDATER_LATEST_VERSION_NOT_FOUND.
function classifyUpdateError(err) {
  const message = String(err?.message || err);
  const code =
    message.includes('ERR_UPDATER_LATEST_VERSION_NOT_FOUND') || message.includes('404')
      ? 'UNREACHABLE'
      : null;
  return { code, message };
}

function getUpdateStateSnapshot() {
  return {
    isDev: IS_DEV,
    enabled: updateSettings.autoUpdateEnabled,
    skippedVersion: updateSettings.skippedVersion,
    currentVersion: app.getVersion(),
    state: updateState.state,
    availableVersion: updateState.availableVersion,
    releaseNotes: updateState.releaseNotes,
    progress: updateState.progress,
    errorMessage: updateState.errorMessage,
    errorCode: updateState.errorCode,
  };
}

function broadcastUpdateState() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('app:update-state', getUpdateStateSnapshot());
  }
}

function setUpdateState(patch) {
  updateState = { ...updateState, ...patch };
  broadcastUpdateState();
}

function setupAutoUpdater() {
  if (IS_DEV) return;

  autoUpdater.autoDownload = false;
  autoUpdater.allowDowngrade = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.logger = console;

  // Channel routing: prerelease builds (e.g. 1.1.0-beta.1, 1.1.0-rc.1) stay
  // on their own channel so they keep receiving matching prerelease updates
  // and never drop to stable by accident. Stable builds use the default
  // latest channel.
  const currentVersion = app.getVersion();
  const prereleaseMatch = /-([a-zA-Z]+)(\.[0-9]+)?$/.exec(currentVersion);
  if (prereleaseMatch) {
    autoUpdater.channel = prereleaseMatch[1];
    autoUpdater.allowPrerelease = true;
  }

  autoUpdater.on('checking-for-update', () => {
    if (epochGuardActive()) return;
    setUpdateState({ state: 'checking', errorMessage: null, errorCode: null });
  });

  autoUpdater.on('update-available', (info) => {
    if (epochGuardActive()) return;
    console.log('Update available:', info.version);
    const version = String(info.version || '');
    const releaseNotes = info.releaseNotes != null ? String(info.releaseNotes) : null;
    if (updateSettings.skippedVersion && updateSettings.skippedVersion === version) {
      // User asked to skip this version — surface it (with "undo" available)
      // but never auto-download it.
      setUpdateState({ state: 'available', availableVersion: version, releaseNotes });
      return;
    }
    setUpdateState({ state: 'available', availableVersion: version, releaseNotes });
    if (updateSettings.autoUpdateEnabled) {
      autoUpdater.downloadUpdate(downloadCancellationToken ?? undefined).catch((err) => {
        if (isCancellationError(err)) return;
        console.error('Auto-download failed:', err);
        if (epochGuardActive()) return;
        const { code, message } = classifyUpdateError(err);
        setUpdateState({ state: 'error', errorMessage: message, errorCode: code });
      });
    }
  });

  autoUpdater.on('update-not-available', () => {
    if (epochGuardActive()) return;
    console.log('No update available');
    setUpdateState({ state: 'up-to-date', availableVersion: null, releaseNotes: null, progress: null, errorMessage: null, errorCode: null });
  });

  autoUpdater.on('download-progress', (progressObj) => {
    if (epochGuardActive()) return;
    setUpdateState({
      state: 'downloading',
      progress: {
        percent: progressObj.percent,
        bytesPerSecond: progressObj.bytesPerSecond,
        transferred: progressObj.transferred,
        total: progressObj.total,
      },
    });
  });

  autoUpdater.on('update-downloaded', (info) => {
    if (epochGuardActive()) return;
    activeCancellationTokens.clear();
    downloadCancellationToken = null;
    console.log('Update downloaded:', info.version);
    setUpdateState({
      state: 'downloaded',
      availableVersion: String(info.version || ''),
      releaseNotes: info.releaseNotes != null ? String(info.releaseNotes) : null,
      progress: null,
      errorMessage: null,
      errorCode: null,
    });
  });

  autoUpdater.on('error', (err) => {
    if (isCancellationError(err)) return;
    console.error('Auto-update error:', err);
    if (epochGuardActive()) return;
    activeCancellationTokens.clear();
    downloadCancellationToken = null;
    // A download error should not clear an already-downloaded update.
    if (updateState.state !== 'downloaded') {
      const { code, message } = classifyUpdateError(err);
      setUpdateState({ state: 'error', errorMessage: message, errorCode: code });
    }
  });
}

function stopAutoUpdateTimer() {
  if (autoUpdateTimer) {
    clearInterval(autoUpdateTimer);
    autoUpdateTimer = null;
  }
}

// In-flight check tracking. `autoUpdater.isUpdaterActive()` is NOT a signal
// that a check is running — it only reports whether the updater is enabled
// at all (true in every packaged build). Track the real in-flight state here
// so a user-triggered check is never short-circuited.
//
// `checkEpoch` invalidates the events of a cancelled/stale check: bump it to
// make every `activeCheckEpoch !== checkEpoch` guard in the autoUpdater
// handlers ignore whatever the abandoned request settles with.
let inFlightCheck = false;
let checkStartTime = null;
let checkEpoch = 0;
let activeCheckEpoch = 0;
let downloadCancellationToken = null;
const activeCancellationTokens = new Set();

// A check that stays "active" longer than this is assumed stuck; a fresh
// check is then allowed to replace it so user-triggered checks are never
// silently swallowed by a hung background check.
const UPDATE_CHECK_STALE_MS = 60 * 1000;

function epochGuardActive() {
  return activeCheckEpoch !== checkEpoch;
}

function isCancellationError(err) {
  return err instanceof Error && (err.name === 'CancellationError' || err.message.includes('cancelled'));
}

async function checkForUpdates({ automatic = false } = {}) {
  if (IS_DEV) return { status: 'dev-mode' };
  if (automatic && !updateSettings.autoUpdateEnabled) return { status: 'disabled' };

  if (inFlightCheck) {
    const activeStale = checkStartTime != null && Date.now() - checkStartTime > UPDATE_CHECK_STALE_MS;
    if (!automatic) {
      // A manual click must always produce visible feedback.
      setUpdateState({ state: 'checking', errorMessage: null, errorCode: null });
      if (!activeStale) return { status: 'checking' };
      console.warn('[autoUpdater] previous check is stale; starting a fresh one');
    } else if (!activeStale) {
      return { status: 'checking' };
    }
  }

  inFlightCheck = true;
  checkStartTime = Date.now();
  activeCheckEpoch = ++checkEpoch;
  downloadCancellationToken = new CancellationToken();
  activeCancellationTokens.add(downloadCancellationToken);

  try {
    // Token is handed to the update-available handler so a hung download can
    // be aborted via cancel-update-check.
    await autoUpdater.checkForUpdates();
    return { status: 'ok' };
  } catch (err) {
    if (isCancellationError(err) || epochGuardActive()) {
      // User cancelled mid-check (or the check was invalidated); leave the UI
      // idle instead of surfacing a stale error.
      setUpdateState({ state: 'idle', errorMessage: null, errorCode: null, progress: null });
      return { status: 'cancelled' };
    }
    const { code, message } = classifyUpdateError(err);
    setUpdateState({ state: 'error', errorMessage: message, errorCode: code });
    return { status: 'error', error: message };
  } finally {
    inFlightCheck = false;
    checkStartTime = null;
  }
}

function cancelUpdateCheck() {
  // Invalidate any in-flight check so its late events are ignored.
  checkEpoch += 1;
  for (const token of activeCancellationTokens) {
    token.cancel();
  }
  activeCancellationTokens.clear();
  downloadCancellationToken = null;
  inFlightCheck = false;
  checkStartTime = null;
  setUpdateState({ state: 'idle', errorMessage: null, errorCode: null, progress: null });
  return { status: 'ok' };
}

function startAutoUpdateTimer() {
  stopAutoUpdateTimer();
  if (IS_DEV || !updateSettings.autoUpdateEnabled) return;
  checkForUpdates({ automatic: true }).catch(() => {});
  autoUpdateTimer = setInterval(() => {
    checkForUpdates({ automatic: true }).catch(() => {});
  }, UPDATE_CHECK_INTERVAL_MS);
}

// macOS: keep hardware acceleration so the embedded browser's compositor
// hit-testing works (disabling GPU(-compositing) broke mouse clicks in the
// <webview> — with GPU off every click needed Cmd+click, with GPU on plain
// clicks work). Retain the InputMethodServiceOverlay switch as the IME crash
// workaround. Set COWORKER_GPU_DISABLE=1 to restore the fully-disabled GPU
// config if the IMKCFRunLoopWakeUpReliable crash appears.
if (process.platform === 'darwin') {
  app.commandLine.appendSwitch('disable-features', 'InputMethodServiceOverlay');
  if (process.env.COWORKER_GPU_DISABLE === '1') {
    app.commandLine.appendSwitch('ignore-gpu-blocklist');
    app.commandLine.appendSwitch('disable-software-rasterizer');
    app.commandLine.appendSwitch('disable-gpu');
    app.commandLine.appendSwitch('disable-gpu-compositing');
    app.commandLine.appendSwitch('disable-gpu-vsync');
  }
}

// Opt-in only: both of these weaken security and must never ship enabled.
if (process.env.COWORKER_INSECURE_TLS === '1') {
  app.commandLine.appendSwitch('ignore-certificate-errors');
}
if (IS_DEV && process.env.COWORKER_REMOTE_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env.COWORKER_REMOTE_DEBUG_PORT);
}

const BACKEND_HOST = process.env.COWORKER_BACKEND_HOST || (IS_DEV ? 'localhost' : '127.0.0.1');
const BACKEND_PORT = Number(process.env.COWORKER_BACKEND_PORT || 9527);
const FRONTEND_URL = process.env.COWORKER_FRONTEND_URL || null;
const FRONTEND_DIST_ENTRY = path.join(__dirname, '../frontend/dist/index.html');

let mainWindow = null;
let tray = null;
let isQuitting = false;
let backendProcess = null;

const BRAND_ASSET_DIR = path.join(__dirname, '../assets/brand/png');

function themedMonochromeAssetPath(name) {
  const tone = nativeTheme.shouldUseDarkColors ? 'white' : 'black';
  return path.join(BRAND_ASSET_DIR, `${name}-${tone}.png`);
}

function createTrayIcon() {
  const trayPath = themedMonochromeAssetPath('cw-icon');
  const image = nativeImage.createFromPath(trayPath).resize({ width: 18, height: 18 });
  return image;
}

function refreshBrandIcons() {
  if (tray && !tray.isDestroyed()) {
    tray.setImage(createTrayIcon());
  }
}

// ---------------------------------------------------------------------------
// Bundled Python backend management (packaged builds only)
// ---------------------------------------------------------------------------

async function startBundledBackend() {
  if (IS_DEV) return;

  const backendName = 'bin/pybackend' + (process.platform === 'win32' ? '.exe' : '');
  const backendPath = path.join(process.resourcesPath, backendName);

  // Check if bundled backend exists
  if (!require('fs').existsSync(backendPath)) {
    dialog.showErrorBox(
      'Missing Backend',
      `Could not find the bundled backend at:\n${backendPath}\n\n` +
      'The application requires the Python backend to function.'
    );
    app.quit();
    return;
  }

  console.log('Starting bundled backend:', backendPath);

  backendProcess = spawn(backendPath, [], {
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: process.platform === 'win32',
    env: { ...process.env, PYTHONNOUSERSITE: '1' },
  });

  backendProcess.stdout.on('data', (data) => {
    const text = data.toString().trim();
    if (text) console.log('[backend]', text);
  });

  backendProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    if (text) console.error('[backend]', text);
  });

  backendProcess.on('error', (err) => {
    console.error('Failed to start bundled backend:', err.message);
    dialog.showErrorBox('Backend Error', `Failed to start the bundled backend:\n${err.message}`);
    app.quit();
  });

  backendProcess.on('exit', (code, signal) => {
    console.log(`Backend exited: code=${code} signal=${signal}`);
    backendProcess = null;
    if (code !== 0 && code !== null) {
      dialog.showErrorBox('Backend Error', `Backend process exited unexpectedly with code ${code}`);
      app.quit();
    }
  });

  // Wait for backend to be ready
  for (let attempt = 0; attempt < 60; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      await requestBackend('/health', 'GET', null, 1000);
      console.log('Backend is ready');
      return;
    } catch {
      // Not ready yet
    }
  }

  console.error('Backend failed to start within 30 seconds');
  dialog.showErrorBox('Backend Error', 'Backend failed to start within 30 seconds.');
  app.quit();
}

function stopBundledBackend() {
  if (backendProcess) {
    console.log('Stopping bundled backend...');
    backendProcess.kill('SIGTERM');
    backendProcess = null;
  }
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  mainWindow.show();
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
}

function hideMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.hide();
  }
}

function quitApp() {
  isQuitting = true;
  app.quit();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function showStartupError(details) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }

  const detailRows = Object.entries(details)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([label, value]) => {
      return `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(value)}</td></tr>`;
    })
    .join('');

  const html = `<!doctype html>
  <html lang="zh-CN">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>CoWorker 启动失败</title>
      <style>
        :root {
          color-scheme: dark;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #111417;
          color: #eef2f5;
        }
        body {
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          background:
            radial-gradient(circle at top, rgba(106, 166, 195, 0.2), transparent 40%),
            #111417;
        }
        main {
          width: min(760px, calc(100vw - 48px));
          padding: 28px;
          border: 1px solid rgba(255, 255, 255, 0.12);
          border-radius: 18px;
          background: rgba(23, 27, 32, 0.92);
          box-shadow: 0 24px 80px rgba(0, 0, 0, 0.34);
        }
        h1 {
          margin: 0 0 10px;
          font-size: 28px;
        }
        p {
          margin: 0 0 18px;
          color: rgba(238, 242, 245, 0.74);
          line-height: 1.6;
        }
        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }
        th, td {
          padding: 10px 0;
          border-top: 1px solid rgba(255, 255, 255, 0.08);
          vertical-align: top;
          text-align: left;
        }
        th {
          width: 148px;
          color: rgba(238, 242, 245, 0.68);
          font-weight: 600;
        }
        code {
          white-space: pre-wrap;
          word-break: break-word;
          color: #9fd0e4;
        }
      </style>
    </head>
    <body>
      <main>
        <h1>CoWorker 前端未能正常加载</h1>
        <p>当前没有再让窗口静默停在空白页。请根据下面的正式诊断信息继续排查启动链。</p>
        <table>${detailRows}</table>
      </main>
    </body>
  </html>`;

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  mainWindow.show();
}

function createTray() {
  if (tray) {
    return;
  }

  tray = new Tray(createTrayIcon());
  tray.setToolTip('CoWorker');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show CoWorker', click: showMainWindow },
    { label: 'Hide Window', click: hideMainWindow },
    { type: 'separator' },
    { label: 'Quit CoWorker', click: quitApp },
  ]));
  tray.on('click', showMainWindow);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 800,
    show: false,
    icon: themedMonochromeAssetPath('cw-icon'),
    backgroundColor: '#111417',
    ...(process.platform === 'darwin'
      ? {
          titleBarStyle: 'hidden',
          trafficLightPosition: { x: 14, y: 14 },
        }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      enableRemoteModule: false,
      sandbox: true,
      nodeIntegration: false,
      // Embedded browser: the frontend renders a real Chromium view via the
      // <webview> tag (partitioned, no nodeIntegration, no preload on the guest).
      webviewTag: true,
    },
  });

  if (FRONTEND_URL) {
    // Load from dev server when COWORKER_FRONTEND_URL is set
    mainWindow.loadURL(FRONTEND_URL);
  } else {
    mainWindow.loadFile(FRONTEND_DIST_ENTRY).catch((error) => {
      console.error('Failed to load frontend entry:', error);
      showStartupError({
        stage: 'loadFile',
        entry: FRONTEND_DIST_ENTRY,
        error: error.message,
      });
    });
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame) {
      return;
    }
    // During app quit the renderer is deliberately torn down; logging to a
    // closing stdout throws write EIO (uncaught exception at shutdown). Skip
    // entirely while quitting, and swallow any write error as a last resort.
    if (isQuitting) {
      return;
    }
    try {
      console.error('Frontend did-fail-load:', { errorCode, errorDescription, validatedURL });
    } catch {
      // stdout already closed during shutdown
    }
    showStartupError({
      stage: 'did-fail-load',
      url: validatedURL,
      errorCode,
      errorDescription,
    });
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    // Same as above: quit tears the renderer down intentionally — skip logging
    // and startup-error UI entirely during shutdown.
    if (isQuitting) {
      return;
    }
    try {
      console.error('Renderer process exited unexpectedly:', details);
    } catch {
      // stdout already closed during shutdown
    }
    showStartupError({
      stage: 'render-process-gone',
      reason: details.reason,
      exitCode: details.exitCode,
    });
  });

  // Embedded browser: track every <webview> guest that attaches so the agent
  // can drive it over CDP (loopback bridge) while the user watches it live.
  mainWindow.webContents.on('did-attach-webview', (event, guest) => {
    if (!browserController) browserController = new BrowserController();
    browserController.register(guest);
  });

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      hideMainWindow();
    }
  });

  // Open external links (markdown <a target="_blank">) in the system browser
  // instead of spawning uncontrolled Electron windows.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  // Never navigate the app window away from the bundled app / dev server.
  // Hostnames/ports are matched exactly so lookalike origins (e.g.
  // http://localhost:5173.evil.com) cannot navigate the window.
  const localOrigin = /^https?:\/\/(localhost|127\.0\.0\.1|::1)(:\d+)?\//;
  mainWindow.webContents.on('will-navigate', (event, url) => {
    let parsed = null;
    try {
      parsed = new URL(url);
    } catch {
      parsed = null;
    }
    const devAllowed =
      FRONTEND_URL && parsed !== null &&
      parsed.protocol === 'http:' && (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') &&
      parsed.port === new URL(FRONTEND_URL).port;
    const packagedAllowed = !FRONTEND_URL && url.startsWith('file:') && !parsed.pathname.includes('..');
    const allowed = devAllowed || packagedAllowed || localOrigin.test(url);
    if (!allowed) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) {
        shell.openExternal(url);
      }
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// Built-in browser (embedded <webview>)
// ---------------------------------------------------------------------------
// The browser guest is an Electron <webview> rendered by the frontend. The user
// drives it manually through the webview's DOM API; the AI agent drives it from
// the Python backend through a loopback HTTP bridge that issues CDP commands to
// the guest webContents. Both target the SAME guest, so agent actions are
// visible live in the panel.

let browserController = null;

// Hard cap for any single evaluate/get_text result (chars). A content-heavy
// page (e.g. bilibili) can yield 200k+ chars of innerText; one oversized tool
// result alone can exceed the LLM context window and 400 the request.
const BROWSER_OUTPUT_MAX_CHARS = 50000;
const BROWSER_TRUNCATION_NOTE =
  '\n[content truncated by Coworker to fit context; use scroll + targeted evaluate to read more]';

class BrowserController {
  constructor() {
    this.guests = new Map(); // webContentsId -> webContents
    this.activeGuestId = null;
  }

  register(guest) {
    this.guests.set(guest.id, guest);
    const becameActive = this.activeGuestId === null || !this.guests.has(this.activeGuestId);
    if (becameActive) {
      this.activeGuestId = guest.id;
    }
    this._setupGuestGuards(guest);
    // The guest is driven through native webContents APIs (loadURL, sendInputEvent,
    // executeJavaScript, capturePage) — never webContents.debugger, which disrupts
    // the guest's own mouse input. Focus it if it is the active view.
    if (becameActive) {
      try {
        guest.focus();
      } catch {
        // ignore — focus is best-effort
      }
    }
    guest.once('destroyed', () => {
      this.guests.delete(guest.id);
      if (this.activeGuestId === guest.id) {
        this.activeGuestId = this.guests.size ? this.guests.keys().next().value : null;
      }
    });
    console.log('[browser] registered guest webContents', guest.id);
  }

  setActive(webContentsId) {
    if (this.guests.has(webContentsId)) {
      this.activeGuestId = webContentsId;
    }
  }

  _setupGuestGuards(wc) {
    // Never let embedded pages open uncontrolled windows/popups — open them in
    // the same embedded view instead.
    wc.setWindowOpenHandler(({ url }) => {
      // New-window requests open in the same embedded view; allow the navigable
      // schemes (http(s), file://, data:, about:) — everything else is denied.
      if (/^(https?|file|data|about):/i.test(url) && this.guests.has(wc.id)) {
        wc.loadURL(url);
      }
      return { action: 'deny' };
    });
    wc.on('will-navigate', (event, url) => {
      let parsed = null;
      try {
        parsed = new URL(url);
      } catch {
        parsed = null;
      }
      // Fail-closed navigation guard. Allowed: http(s), file:// (local project
      // HTML), data: (inline HTML/preview) and about:blank. Anything else
      // (javascript:, chrome:, devtools:, mailto:, tel:, ftp:, ws:, blob:...)
      // is blocked.
      if (parsed && !/^(https?|file|data|about):$/.test(parsed.protocol)) {
        event.preventDefault();
      }
    });
    // Forward the guest's context-menu request to the renderer. Coordinates
    // come from the OS cursor position (authoritative), converted to the
    // renderer's client space so the app menu follows the mouse exactly.
    wc.on('context-menu', (event, params) => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      const client = this._contextMenuClientPosition();
      if (process.env.COWORKER_BROWSER_DEBUG === '1') {
        console.log(`[browser-debug] context-menu x=${params.x} y=${params.y} cursor=${JSON.stringify(client)} link=${params.linkURL || ''} sel=${(params.selectionText || '').slice(0, 30)}`);
      }
      mainWindow.webContents.send('browser:context-menu', {
        webContentsId: wc.id,
        x: client.x,
        y: client.y,
        linkURL: params.linkURL || '',
        selectionText: params.selectionText || '',
        editFlags: params.editFlags
          ? {
              canCut: !!params.editFlags.canCut,
              canCopy: !!params.editFlags.canCopy,
              canPaste: !!params.editFlags.canPaste,
              canSelectAll: !!params.editFlags.canSelectAll,
              canDelete: !!params.editFlags.canDelete,
            }
          : {},
      });
    });

    if (process.env.COWORKER_BROWSER_DEBUG === '1') {
      // Does the guest actually receive mouse/keyboard input? If plain clicks
      // do NOT produce before-input-event, the embedder is swallowing them.
      wc.on('before-input-event', (event, input) => {
        if (['mouseDown', 'mouseUp', 'mouseMove', 'contextMenu', 'keyDown'].includes(input.type)) {
          console.log(`[browser-debug] input type=${input.type} x=${input.x} y=${input.y} key=${input.key || ''}`);
        }
      });
      wc.on('did-start-navigation', (event, url, isInPlace, isMainFrame) => {
        console.log(`[browser-debug] nav mainFrame=${isMainFrame} url=${url}`);
      });
      wc.on('did-finish-load', () => {
        console.log('[browser-debug] load done');
      });
    }
  }

  _contextMenuClientPosition() {
    try {
      const point = screen.getCursorScreenPoint();
      const bounds = mainWindow.getContentBounds();
      return { x: Math.round(point.x - bounds.x), y: Math.round(point.y - bounds.y) };
    } catch {
      return { x: 0, y: 0 };
    }
  }

  get guest() {
    const wc = this.activeGuestId != null ? this.guests.get(this.activeGuestId) : null;
    return wc && !wc.isDestroyed() ? wc : null;
  }

  // Run JS in the guest via webContents.executeJavaScript. We deliberately do
  // NOT use webContents.debugger: attaching a debugger to a webview guest
  // disrupts its own mouse input (plain clicks stopped working; only Cmd+click
  // routed through the new-window path still navigated). The native webContents
  // API covers everything the bridge needs without touching the input pipeline.
  async _eval(expression) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    return await g.executeJavaScript(String(expression), true);
  }

  async _waitForLoad(timeoutMs = 15000) {
    const g = this.guest;
    if (!g) return;
    const done = new Promise((resolve) => {
      const timer = setTimeout(resolve, timeoutMs);
      g.once('did-finish-load', () => {
        clearTimeout(timer);
        resolve();
      });
      g.once('did-fail-load', () => {
        clearTimeout(timer);
        resolve();
      });
    });
    await done;
  }

  // The agent can call the bridge a moment before the frontend mounts the
  // <webview> (auto-open race). Wait briefly for a guest to attach.
  async _waitForGuest(timeoutMs = 3000) {
    const started = Date.now();
    while (!this.guest) {
      if (Date.now() - started > timeoutMs) return;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }

  async navigate(url) {
    await this._waitForGuest();
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    // Pass through the navigable schemes (http(s), file:// for local HTML,
    // data: for inline previews, about:blank); otherwise default to https.
    const normalized = /^(https?|file|data|about):/i.test(url) ? url : `https://${url}`;
    g.loadURL(normalized).catch(() => {});
    await this._waitForLoad();
    return this.getState();
  }

  async reload() {
    await this._waitForGuest();
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    g.reload();
    await this._waitForLoad();
    return this.getState();
  }

  async back() {
    await this._waitForGuest();
    const g = this.guest;
    if (g && g.canGoBack()) {
      g.goBack();
      await this._waitForLoad();
    }
    return this.getState();
  }

  async forward() {
    await this._waitForGuest();
    const g = this.guest;
    if (g && g.canGoForward()) {
      g.goForward();
      await this._waitForLoad();
    }
    return this.getState();
  }

  async getState() {
    const g = this.guest;
    if (!g) return { url: '', title: '', canGoBack: false, canGoForward: false, loading: false };
    return {
      url: g.getURL() || '',
      title: g.getTitle() || '',
      canGoBack: g.canGoBack(),
      canGoForward: g.canGoForward(),
      loading: g.isLoading(),
    };
  }

  async screenshot() {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    const image = await g.capturePage();
    const url = this._shotDataUrl(image);
    if (!url) throw new Error('screenshot_empty');
    return url;
  }

  // Downscale + JPEG-encode a captured frame. A full-size PNG of a busy page is
  // ~100-300KB of base64 (≈ 25-75k tokens in a tool result) — a huge share of
  // the model context window per screenshot. 720px JPEG is ~3-4x smaller while
  // staying readable for the agent's visual click workflow.
  //
  // Returns null when the capture is empty/invalid (hidden webview, panel
  // collapsed, page not painted): an empty NativeImage would otherwise produce
  // a `data:image/jpeg;base64,` data URL with an empty body, which vision
  // providers reject with "Failed to load image: cannot identify image file".
  _shotDataUrl(image, maxWidth = 720, quality = 70) {
    if (!image) return null;
    if (typeof image.isEmpty === 'function' && image.isEmpty()) return null;
    const size = image.getSize();
    if (!size || !size.width || !size.height) return null;
    const scale = size.width > maxWidth ? maxWidth / size.width : 1;
    let out = image;
    if (scale < 1) {
      out = image.resize({ width: Math.round(size.width * scale) });
    }
    const jpeg = out.toJPEG(quality);
    if (!jpeg || jpeg.length === 0) return null;
    return `data:image/jpeg;base64,${jpeg.toString('base64')}`;
  }

  async click(x, y) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    g.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
    g.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
    return { ok: true };
  }

  async type(text) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    g.insertText(String(text));
    return { ok: true };
  }

  async press(key) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    const keyMap = {
      Enter: 'Enter', Backspace: 'Backspace', Tab: 'Tab', Escape: 'Escape',
      ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
      Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown', Delete: 'Delete',
    };
    const mapped = keyMap[key] || key;
    g.sendInputEvent({ type: 'keyDown', keyCode: mapped });
    g.sendInputEvent({ type: 'keyUp', keyCode: mapped });
    return { ok: true };
  }

  async scroll(dx, dy) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    g.sendInputEvent({ type: 'mouseWheel', deltaX: Number(dx) || 0, deltaY: Number(dy) || 0 });
    return { ok: true };
  }

  async evaluate(expression) {
    return this._capResult(await this._eval(String(expression)));
  }

  _capResult(value, maxChars = BROWSER_OUTPUT_MAX_CHARS) {
    if (typeof value === 'string' && value.length > maxChars) {
      return value.slice(0, maxChars) + BROWSER_TRUNCATION_NOTE;
    }
    return value;
  }

  // Bounded, cleaned page text for the agent's "read the current page" flow.
  // Strips chrome nodes, then caps the size so the result never overflows the
  // LLM context window.
  async getText(maxChars = BROWSER_OUTPUT_MAX_CHARS) {
    const budget = Math.max(1000, Math.min(500000, Number(maxChars) || BROWSER_OUTPUT_MAX_CHARS));
    const marker = JSON.stringify(BROWSER_TRUNCATION_NOTE);
    const expr = `(() => {
      const clean = document.body ? document.body.cloneNode(true) : null;
      if (!clean) return { text: '', truncated: false };
      clean.querySelectorAll('script,style,iframe,noscript,svg,canvas,template').forEach(n => n.remove());
      const text = (clean.innerText || '').replace(/\\s{3,}/g, '  ').trim();
      if (text.length <= ${budget}) return { text, truncated: false };
      return {
        text: text.slice(0, ${budget}) + ${marker},
        truncated: true,
      };
    })()`;
    return this._eval(expr);
  }

  // Capture the element (or the whole page) at viewport (x, y) for the
  // renderer's right-click menu.
  async captureAt(x, y, scope) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    const url = g.getURL() || '';
    const title = g.getTitle() || '';

    let element = null;
    let pageText = null;
    let screenshot = null;

    if (scope === 'page') {
      try {
        pageText = String(await this._eval('(document.body && document.body.innerText || "").trim().slice(0, 20000)')) || '';
      } catch {
        pageText = '';
      }
      try {
        screenshot = this._shotDataUrl(await g.capturePage());
      } catch {
        screenshot = null;
      }
    } else {
      const elementExpr = `(() => {
        const el = document.elementFromPoint(${Number(x) || 0}, ${Number(y) || 0});
        if (!el || !el.tagName) return null;
        const r = el.getBoundingClientRect();
        const xpath = (() => {
          let node = el, parts = [];
          while (node && node.nodeType === 1 && node !== document.body && node !== document.documentElement) {
            let idx = 1;
            for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
              if (sib.localName === node.localName) idx++;
            }
            const tag = node.localName;
            parts.unshift(idx > 1 ? tag + '[' + idx + ']' : tag);
            node = node.parentNode;
          }
          return '/html/body/' + parts.join('/');
        })();
        const anchor = el.closest && el.closest('a');
        return {
          tag: String(el.tagName).toLowerCase(),
          id: el.id || '',
          className: typeof el.className === 'string' ? el.className : '',
          text: (el.textContent || '').trim().slice(0, 200),
          href: (anchor && anchor.href) || '',
          xpath,
          outerHTML: (el.outerHTML || '').slice(0, 4000),
          rect: { x: r.x, y: r.y, width: r.width, height: r.height },
        };
      })()`;
      try {
        element = await this._eval(elementExpr);
      } catch {
        element = null;
      }
      if (element && element.rect && element.rect.width > 0 && element.rect.height > 0) {
        const rect = element.rect;
        const inViewport =
          rect.x >= 0 && rect.y >= 0 &&
          rect.x < (await this._eval('window.innerWidth')) &&
          rect.y < (await this._eval('window.innerHeight'));
        if (inViewport) {
          try {
            screenshot = this._shotDataUrl(
              await g.capturePage({
                x: Math.max(0, rect.x),
                y: Math.max(0, rect.y),
                width: Math.max(1, rect.width),
                height: Math.max(1, rect.height),
              }),
            );
          } catch {
            screenshot = null;
          }
        }
      }
    }

    return { url, title, element, pageText, screenshot };
  }

  // Compact, clickable DOM snapshot for the agent (ego-browser / codex style):
  // a numbered list of interactive elements with their viewport centers and
  // visibility, so the model can navigate and click by reference instead of
  // pulling a full screenshot into context on every step.
  async snapshot(maxItems = 80) {
    const g = this.guest;
    if (!g) throw new Error('browser_not_attached');
    const budget = Math.max(10, Math.min(300, Number(maxItems) || 80));
    const expr = `(() => {
      const items = [];
      const seen = new Set();
      const sel = 'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick], [contenteditable], [tabindex]';
      document.querySelectorAll(sel).forEach((el) => {
        if (items.length >= ${budget}) return;
        if (seen.has(el)) return;
        seen.add(el);
        const r = el.getBoundingClientRect();
        if (r.width <= 1 || r.height <= 1) return;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none') return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
        const href = el.tagName === 'A' && el.href ? el.href.slice(0, 200) : '';
        const n = items.length + 1;
        items.push({
          n,
          tag: el.tagName.toLowerCase(),
          text: text || href || '',
          type: el.type || '',
          name: el.name || '',
          id: el.id || '',
          class: typeof el.className === 'string' ? el.className.slice(0, 80) : '',
          href,
          center: { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) },
          inViewport: r.x < window.innerWidth && r.y < window.innerHeight && r.x + r.width > 0 && r.y + r.height > 0,
        });
      });
      return {
        url: location.href,
        title: document.title,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        count: items.length,
        items,
      };
    })()`;
    try {
      return await g.executeJavaScript(expr, true);
    } catch {
      return { url: g.getURL() || '', title: g.getTitle() || '', viewport: {}, count: 0, items: [] };
    }
  }
}

// ── Loopback HTTP bridge (Python agent -> Electron main) ──────────────────
// Binds 127.0.0.1 with a random port + bearer token. The token is registered
// with the Python backend (POST /api/browser/bridge) at startup so only the
// Coworker backend can drive the embedded browser.

let browserBridgeServer = null;
let browserBridgeToken = null;

async function handleBridgeRequest(method, url, payload) {
  if (!browserController) throw new Error('browser_not_attached');
  const pathname = (url || '').split('?')[0];

  if (method === 'GET' && pathname === '/state') {
    return browserController.getState();
  }
  if (method !== 'POST') throw new Error('method_not_allowed');

  switch (pathname) {
    case '/navigate':
      return browserController.navigate(payload.url);
    case '/reload':
      return browserController.reload();
    case '/back':
      return browserController.back();
    case '/forward':
      return browserController.forward();
    case '/screenshot':
      return { image: await browserController.screenshot() };
    case '/evaluate':
      return { result: await browserController.evaluate(payload.expression) };
    case '/snapshot':
      return browserController.snapshot(payload.max_items);
    case '/get_text':
      return browserController.getText(payload.max_chars);
    case '/act':
      switch (payload.type) {
        case 'click':
          return browserController.click(payload.x, payload.y);
        case 'type':
          return browserController.type(payload.text);
        case 'press':
          return browserController.press(payload.key);
        case 'scroll':
          return browserController.scroll(payload.dx, payload.dy);
        default:
          throw new Error('unknown_act_type');
      }
    default:
      throw new Error('not_found');
  }
}

function startBrowserBridge() {
  if (browserBridgeServer) return browserBridgeServer;

  browserBridgeToken = crypto.randomBytes(32).toString('hex');

  const server = http.createServer((req, res) => {
    const respond = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    const auth = req.headers.authorization || '';
    if (auth !== `Bearer ${browserBridgeToken}`) {
      respond(401, { error: 'unauthorized' });
      return;
    }
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
    });
    req.on('end', async () => {
      let payload = {};
      if (raw) {
        try {
          payload = JSON.parse(raw);
        } catch {
          respond(400, { error: 'bad_json' });
          return;
        }
      }
      try {
        if (!browserController) throw new Error('browser_not_attached');
        const result = await handleBridgeRequest(req.method, req.url || '/', payload);
        respond(200, result);
      } catch (e) {
        respond(e.message === 'browser_not_attached' ? 503 : 500, { error: e.message });
      }
    });
  });

  server.on('error', (e) => {
    console.error('[browser] bridge server error:', e.message);
  });

  server.listen(0, '127.0.0.1', () => {
    const port = server.address().port;
    console.log('[browser] bridge listening on 127.0.0.1:', port);
  });

  browserBridgeServer = server;
  return server;
}

async function registerBrowserBridge(server) {
  try {
    const port = server.address().port;
    await requestBackend('/api/browser/bridge', 'POST', { port, token: browserBridgeToken }, 3000);
    console.log('[browser] bridge registered with backend');
  } catch (e) {
    // Dev starts the backend before Electron; registration can race the first
    // few attempts. Retry until the backend accepts it.
    console.warn('[browser] bridge registration deferred:', e.message);
    setTimeout(() => registerBrowserBridge(server), 2000);
  }
}

// Renderer tells main which <webview> tab is currently active so the agent
// drives the visible tab (BrowserView calls webview.getWebContentsId()).
ipcMain.handle('browser:set-active-tab', (event, webContentsId) => {
  if (browserController) {
    browserController.setActive(Number(webContentsId));
  }
  return { ok: true };
});

// Right-click menu clipboard/navigation actions against the guest webContents
// (the guest is a webContents, so the app's DOM-targeted clipboard IPC cannot
// reach it — we call the webContents methods directly instead).
ipcMain.handle('browser:menu-action', (event, action) => {
  const g = browserController ? browserController.guest : null;
  if (!g) return { ok: false, error: 'browser_not_attached' };
  try {
    switch (action) {
      case 'copy':
        g.copy();
        break;
      case 'cut':
        g.cut();
        break;
      case 'paste':
        g.paste();
        break;
      case 'selectAll':
        g.selectAll();
        break;
      case 'delete':
        g.delete();
        break;
      case 'reload':
        g.reload();
        break;
      case 'back':
        if (g.canGoBack()) g.goBack();
        break;
      case 'forward':
        if (g.canGoForward()) g.goForward();
        break;
      default:
        return { ok: false, error: 'unknown_action' };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

// Right-click "ask the agent" actions: capture the element/page under the
// cursor via webContents APIs (element metadata + optional screenshot) and hand
// it back to the renderer, which attaches it to the chat composer for the user
// to send.
ipcMain.handle('browser:capture-element', async (event, payload) => {
  if (!browserController) return { error: 'browser_not_attached' };
  const x = Number(payload && payload.x) || 0;
  const y = Number(payload && payload.y) || 0;
  const scope = payload && payload.scope === 'page' ? 'page' : 'element';
  try {
    return await browserController.captureAt(x, y, scope);
  } catch (e) {
    return { error: e.message };
  }
});

app.whenReady().then(async () => {
  if (!IS_DEV) {
    await startBundledBackend();
    if (backendProcess === null && !IS_DEV) return;
  }

  setupAutoUpdater();
  startAutoUpdateTimer();

  createTray();
  createWindow();
  nativeTheme.on('updated', refreshBrandIcons);

  // Built-in browser: start the loopback bridge and register it with the
  // Python backend so the agent's browser tool can drive the embedded view.
  const bridge = startBrowserBridge();
  registerBrowserBridge(bridge);

  app.on('activate', () => {
    showMainWindow();
  });
});

// Single-instance lock: double-launching the app (e.g. clicking the launcher
// twice) would otherwise open two windows/renderers racing against one backend.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      showMainWindow();
    }
  });
}

app.on('before-quit', () => {
  isQuitting = true;
  if (!IS_DEV) {
    stopBundledBackend();
  }
});

function requestBackend(pathname, method = 'GET', payload = undefined, timeoutMs = 10000) {
  const data = payload ? JSON.stringify(payload) : undefined;
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: pathname,
    method,
    headers: {
      'Content-Type': 'application/json',
    },
  };

  if (data) {
    options.headers['Content-Length'] = Buffer.byteLength(data);
  }

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      req.destroy();
      reject(new Error(`Backend request timed out: ${method} ${pathname}`));
    }, timeoutMs);

    const req = http.request(options, (res) => {
      clearTimeout(timeout);
      let responseData = '';
      res.on('data', (chunk) => {
        responseData += chunk;
      });
      res.on('end', () => {
        if (!responseData) {
          reject(new Error(`Backend returned ${res.statusCode} with an empty response`));
          return;
        }
        try {
          const parsed = JSON.parse(responseData);
          if (res.statusCode >= 400) {
            reject(new Error(parsed.detail || `Backend returned ${res.statusCode}`));
            return;
          }
          resolve(parsed);
        } catch (e) {
          reject(new Error(`Failed to parse backend response: ${e.message}`));
        }
      });
    });

    req.on('error', (e) => {
      clearTimeout(timeout);
      reject(new Error(`Failed to connect to backend: ${e.message}`));
    });

    req.setTimeout(timeoutMs);
    req.on('timeout', () => {
      clearTimeout(timeout);
      req.destroy();
      reject(new Error(`Backend request timed out: ${method} ${pathname}`));
    });

    if (data) {
      req.write(data);
    }
    req.end();
  });
}

ipcMain.handle('get-runtime-config', async () => {
  return requestBackend('/config');
});

// Clipboard read/write for the renderer's context-menu copy/paste slots.
// The renderer runs sandboxed with contextIsolation, so it cannot reach the
// Electron clipboard module directly — bridge it over IPC instead of relying
// on navigator.clipboard permission grants in the shell.
ipcMain.handle('clipboard-read-text', () => clipboard.readText());
ipcMain.handle('clipboard-write-text', (_event, text) => {
  clipboard.writeText(String(text ?? ''));
});

// Raw-text request helper for export endpoints (PlainTextResponse), which
// requestBackend would otherwise try to JSON-parse.
function requestBackendText(pathname, method = 'GET', timeoutMs = 30000) {
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: pathname,
    method,
    headers: {},
  };
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        if (res.statusCode >= 400) {
          reject(new Error(`Backend returned ${res.statusCode}`));
          return;
        }
        resolve(body);
      });
    });
    req.on('error', (e) => reject(new Error(`Failed to connect to backend: ${e.message}`)));
    req.setTimeout(timeoutMs);
    req.on('timeout', () => { req.destroy(); reject(new Error('Backend request timed out')); });
    req.end();
  });
}

ipcMain.handle('skills-install', async (event, payload) => {
  return requestBackend('/skills/install', 'POST', payload);
});
ipcMain.handle('audit-tool-export', async () => {
  return requestBackendText('/audit/tool/export');
});
ipcMain.handle('audit-tool-clear', async () => {
  return requestBackend('/audit/tool/clear', 'POST');
});
ipcMain.handle('traces-agent-export', async () => {
  return requestBackendText('/traces/agent/export');
});
ipcMain.handle('traces-agent-clear', async () => {
  return requestBackend('/traces/agent/clear', 'POST');
});
ipcMain.handle('checkpoints-clear', async () => {
  return requestBackend('/checkpoints/clear', 'POST');
});
ipcMain.handle('settings-retention-get', async () => {
  return requestBackend('/settings/retention');
});
ipcMain.handle('settings-retention-set', async (event, patch) => {
  return requestBackend('/settings/retention', 'POST', patch);
});

// ── Auto-update IPC handlers ───────────────────────────────────────────
ipcMain.handle('get-update-state', () => getUpdateStateSnapshot());

ipcMain.handle('set-auto-update', async (event, enabled) => {
  updateSettings = writeUpdateSettings({ autoUpdateEnabled: !!enabled });
  if (!IS_DEV) {
    if (updateSettings.autoUpdateEnabled) {
      startAutoUpdateTimer();
    } else {
      stopAutoUpdateTimer();
    }
  }
  broadcastUpdateState();
  return { status: 'ok', enabled: updateSettings.autoUpdateEnabled };
});

ipcMain.handle('check-for-updates', () => checkForUpdates({ automatic: false }));

ipcMain.handle('cancel-update-check', () => cancelUpdateCheck());

ipcMain.handle('download-update', async () => {
  if (IS_DEV) return { status: 'dev-mode' };
  if (updateState.state !== 'available') return { status: 'no-update' };
  try {
    await autoUpdater.downloadUpdate(downloadCancellationToken ?? undefined);
    return { status: 'ok' };
  } catch (err) {
    if (isCancellationError(err)) {
      setUpdateState({ state: 'idle', errorMessage: null, errorCode: null, progress: null });
      return { status: 'cancelled' };
    }
    const { code, message } = classifyUpdateError(err);
    setUpdateState({ state: 'error', errorMessage: message, errorCode: code });
    return { status: 'error', download: message };
  }
});

ipcMain.handle('install-update', async () => {
  if (IS_DEV) return { status: 'dev-mode' };
  if (updateState.state !== 'downloaded') return { status: 'no-update' };
  isQuitting = true;
  stopBundledBackend();
  autoUpdater.quitAndInstall();
  return { status: 'ok' };
});

ipcMain.handle('skip-version', async () => {
  if (updateState.availableVersion) {
    updateSettings = writeUpdateSettings({ skippedVersion: updateState.availableVersion });
  }
  setUpdateState({ state: 'idle', progress: null, errorMessage: null, errorCode: null });
  return { status: 'ok', skippedVersion: updateSettings.skippedVersion };
});

ipcMain.handle('clear-skip', async () => {
  updateSettings = writeUpdateSettings({ skippedVersion: null });
  broadcastUpdateState();
  return { status: 'ok' };
});

ipcMain.handle('update-runtime-config', async (event, payload) => {
  return requestBackend('/config', 'PATCH', payload);
});

ipcMain.handle('fetchSettings', async () => {
  try {
    return await requestBackend('/settings');
  } catch (e) {
    return { max_attachment_mb: 25, revert_code: true };
  }
});

ipcMain.handle('saveSettings', async (event, payload) => {
  try {
    return await requestBackend('/settings', 'POST', payload);
  } catch (e) {
    return { status: 'error', max_attachment_mb: 25, revert_code: true, detail: e.message };
  }
});

// ── Web settings IPC (Tavily) ──────────────────────────────────────────────
ipcMain.handle('get-web-settings', async () => {
  try {
    return await requestBackend('/api/web/config');
  } catch (e) {
    return { enabled: false, provider: 'tavily', max_results: 8, search_depth: 'basic', fetch_enabled: true, api_key_configured: false, error: e.message };
  }
});

ipcMain.handle('save-web-settings', async (event, payload) => {
  try {
    return await requestBackend('/api/web/config', 'POST', payload || {});
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

ipcMain.handle('set-web-tavily-key', async (event, apiKey) => {
  try {
    return await requestBackend('/api/web/tavily/key', 'POST', { api_key: String(apiKey || '') });
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

ipcMain.handle('clear-web-tavily-key', async () => {
  try {
    return await requestBackend('/api/web/tavily/key', 'DELETE');
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

ipcMain.handle('test-web-search', async (event, payload) => {
  const query = typeof payload === 'string' ? payload : (payload && payload.query);
  const apiKey = typeof payload === 'object' && payload ? payload.apiKey : undefined;
  try {
    return await requestBackend('/api/web/test', 'POST', { query: String(query || 'opencode web search'), ...(apiKey ? { api_key: apiKey } : {}) });
  } catch (e) {
    return { ok: false, message: e.message, results_count: 0 };
  }
});

// ── Logging subsystem IPC ────────────────────────────────────────────────
ipcMain.handle('get-settings-log', async () => {
  try {
    return await requestBackend('/settings/log');
  } catch (e) {
    return { log_level: 'INFO', log_file: '', log_max_bytes: 10485760, log_backup_count: 5, json_log: true, error: e.message };
  }
});

ipcMain.handle('set-log-level', async (event, level) => {
  try {
    return await requestBackend('/settings/log-level', 'POST', { log_level: level });
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

ipcMain.handle('read-log-file', async (event, start = 0, count = 200) => {
  try {
    return await requestBackend(`/settings/log-file?start=${start}&count=${count}`, 'GET');
  } catch (e) {
    return { total_lines: 0, lines: [], truncated: false, error: e.message };
  }
});

ipcMain.handle('truncate-log', async (event, maxBytes) => {
  try {
    return await requestBackend('/settings/truncate-log', 'POST', maxBytes != null ? { max_bytes: maxBytes } : {});
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

// ── Logging subsystem IPC (legacy aliases) ─────────────────────────────
ipcMain.handle('getLogSettings', async () => {
  try {
    return await requestBackend('/settings/log');
  } catch (e) {
    return { log_level: 'INFO', log_file: '', log_max_bytes: 10485760, log_backup_count: 5, json_log: true };
  }
});

ipcMain.handle('setLogLevel', async (event, level) => {
  try {
    return await requestBackend('/settings/log-level', 'POST', { log_level: level });
  } catch (e) {
    return { status: 'error', log_level: 'INFO' };
  }
});

ipcMain.handle('readLogFile', async (event, start = 0, count = 200) => {
  try {
    return await requestBackend(`/settings/log-file?start=${start}&count=${count}`, 'GET');
  } catch (e) {
    return { total_lines: 0, lines: [], truncated: false };
  }
});

ipcMain.handle('truncateLog', async (event, maxBytes) => {
  try {
    return await requestBackend('/settings/truncate-log', 'POST', maxBytes != null ? { max_bytes: maxBytes } : {});
  } catch (e) {
    return { status: 'error' };
  }
});

const activeStreams = new Map();

/**
 * Unified SSE stream opener — handles:
 *  • GET / POST HTTP requests
 *  • SSE frame parsing with multi-line `data:` concatenation
 *  • 60s idle watchdog
 *  • non-2xx → error event + error detail from body
 *  • trailing buffer forwarding (last incomplete frame)
 *  • consistent `{ status: 'ok' | 'error' }` return value
 */
function openSseStream({
  requestId,
  method = 'POST',
  path,
  payload,
  sender,
  eventName = 'chat-stream-event',
  idleTimeoutMs = 60_000,
}) {
  let idleTimer = null;

  const httpOptions = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path,
    method,
    headers: {
      'Content-Type': 'application/json',
    },
  };

  return new Promise((resolve, reject) => {
    let req;
    let terminalForwarded = false;
    // Terminal event types for the renderer's stream state machine. A stream
    // that ends without one of these (or `stream_end` for approval streams)
    // must be surfaced as a terminal error instead of silently leaving the
    // renderer guessing between "interrupted" and a successful commit.
    const TERMINAL_TYPES = new Set(['done', 'error', 'stream_end']);

    function forwardEvent(parsed) {
      if (parsed && parsed.type && TERMINAL_TYPES.has(parsed.type)) {
        terminalForwarded = true;
      }
      if (sender && !sender.isDestroyed()) {
        sender.send(eventName, { requestId, event: parsed });
      }
    }

    if (method === 'POST' || method === 'PUT') {
      const body = JSON.stringify(payload);
      httpOptions.headers['Content-Length'] = Buffer.byteLength(body);
      req = http.request(httpOptions, handleResponse);
      req.write(body);
      req.end();
    } else {
      // GET (approval events)
      req = http.request(httpOptions, handleResponse);
      req.end();
    }

    function handleResponse(res) {
      res.setEncoding('utf8');
      let buffer = '';
      let lastActivity = Date.now();

      // Idle timeout watchdog
      idleTimer = setInterval(() => {
        const elapsed = Date.now() - lastActivity;
        if (elapsed > idleTimeoutMs) {
          req.destroy(new Error(`Stream idle for ${idleTimeoutMs / 1000} seconds`));
        }
      }, 5000);

      function _pushSseData(chunk) {
        lastActivity = Date.now();
        buffer += chunk.replace(/\r\n/g, '\n');
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          // SSE multi-line `data:` – collect ALL `data:` lines and join with newline
          const dataLines = frame.split('\n').filter(l => l.startsWith('data:'));
          if (dataLines.length === 0) continue;
          const raw = dataLines.map(l => l.slice(5).trim()).join('\n');
          if (!raw) continue;
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            console.warn('[Electron] skipped malformed SSE frame:', raw?.slice(0, 200));
            continue;
          }
          forwardEvent(parsed);
        }
      }

      res.on('data', _pushSseData);

      res.on('end', () => {
        if (idleTimer) clearInterval(idleTimer);
        // Error response: surface as error event with body `.detail`
        if (res.statusCode >= 400) {
          let detail = `Backend returned ${res.statusCode}`;
          try {
            const parsed = JSON.parse(buffer);
            if (parsed && parsed.detail) detail = parsed.detail;
          } catch { /* not JSON, keep status-based */ }
          forwardEvent({ type: 'error', error: detail });
        } else if (buffer) {
          // Forward any remaining (trailing partial) buffer using multi-line rule
          const dataLines = buffer.split('\n').filter(l => l.startsWith('data:'));
          if (dataLines.length > 0) {
            const raw = dataLines.map(l => l.slice(5).trim()).join('\n');
            if (raw) {
              try {
                forwardEvent(JSON.parse(raw));
              } catch {
                // ignore trailing partial frame
              }
            }
          }
        } else if (!terminalForwarded) {
          // 2xx response ended cleanly but no terminal SSE event (done/error/
          // stream_end) was ever forwarded — the terminal frame was dropped in
          // transport. Do NOT synthesize an `error` here: the renderer's
          // stream settle now reconciles against the backend's committed
          // message (a present assistant_message_id means the reply succeeded
          // and is adopted as `done`), so an injected error would only mask a
          // successful commit. Log for diagnostics instead.
          console.warn(`[Electron] stream ended without a terminal event (requestId=${requestId} path=${path})`);
        }
        activeStreams.delete(requestId);
        resolve({ status: 'ok' });
      });
    }

    function handleError(e) {
      if (idleTimer) clearInterval(idleTimer);
      terminalForwarded = true;
      activeStreams.delete(requestId);
      forwardEvent({ type: 'error', error: `Failed to connect to backend: ${e.message}` });
      resolve({ status: 'error' });
    }

    req.on('error', handleError);

    // Register in activeStreams for abort (before potential end/error)
    activeStreams.set(requestId, req);
  });
}

ipcMain.handle('start-chat-stream', async (event, { requestId, payload }) => {
  return openSseStream({
    requestId,
    method: 'POST',
    path: '/chat/stream',
    payload,
    sender: event.sender,
    eventName: 'chat-stream-event',
    // A worker/tool sub-agent can occupy the shared LLM for minutes; the main
    // stream then only carries `: ping` heartbeats. 60s default is too tight —
    // match the renderer watchdog (300s) and the approval/worker streams.
    idleTimeoutMs: 300_000,
  });
});

ipcMain.on('abort-chat-stream', (event, requestId) => {
  const req = activeStreams.get(requestId);
  if (req) {
    req.destroy();
    activeStreams.delete(requestId);
  }
  // Also clear the idle timer for this stream
  // (idleTimer is scoped to the request handler above, but the stream is already aborted)
});

ipcMain.handle('start-approval-stream', async (event, { requestId, resumeId }) => {
  return openSseStream({
    requestId,
    method: 'GET',
    path: `/command-approvals/events/${encodeURIComponent(resumeId)}`,
    sender: event.sender,
    eventName: 'approval-stream-event',
    idleTimeoutMs: 300_000, // 5 min — backend sends SSE ping every 1s, frontend watchdog is 300s
  });
});

ipcMain.handle('start-worker-stream', async (event, { requestId, worker_run_id }) => {
  return openSseStream({
    requestId,
    method: 'GET',
    path: `/worker-events/${encodeURIComponent(worker_run_id)}`,
    sender: event.sender,
    eventName: 'worker-stream-event',
    idleTimeoutMs: 300_000,
  });
});


ipcMain.handle('list-sessions', async () => {
  return requestBackend('/sessions');
});

ipcMain.handle('list-active-sessions', async () => {
  const envelope = await requestBackend('/sessions/active');
  // Backend returns {status, session_ids}; unwrap so the renderer receives string[].
  return Array.isArray(envelope?.session_ids) ? envelope.session_ids : [];
});

ipcMain.handle('create-session', async (event, payload) => {
  return requestBackend('/sessions', 'POST', {
    title: payload?.title || '',
    project_id: payload?.project_id || '',
    agent_id: payload?.agent_id || '',
  });
});

ipcMain.handle('delete-session', async (event, sessionId) => {
  // Checkpoint teardown runs in the background on the backend, but keep a
  // generous timeout as a safety net in case the backend is under load.
  return requestBackend(`/sessions/${encodeURIComponent(sessionId)}`, 'DELETE', undefined, 60_000);
});

ipcMain.handle('stop-session-stream', async (event, sessionId) => {
  // Explicit Stop: ask the backend to force-cancel the session's in-flight
  // generation and release it, so a following edit/regenerate is not rejected
  // with 409 "session is still generating". Best-effort (idempotent).
  return requestBackend(`/sessions/${encodeURIComponent(sessionId)}/stop`, 'POST', undefined, 10_000);
});

// Goal 控制（Phase 1 IPC 桥接层）：前端经 electronAPI 调后端 /goal/*，与
// get-session / stop-session-stream 同构。
ipcMain.handle('goal-get', async (event, sessionId) => {
  return requestBackend(`/goal?session_id=${encodeURIComponent(sessionId)}`);
});

ipcMain.handle('goal-set', async (event, payload) => {
  return requestBackend('/goal/set', 'POST', {
    session_id: payload?.session_id,
    objective: payload?.objective || '',
    ...(payload?.token_budget != null ? { token_budget: payload.token_budget } : {}),
    ...(payload?.user_message_id ? { user_message_id: payload.user_message_id } : {}),
    ...(payload?.provider ? { provider: payload.provider } : {}),
    ...(payload?.model ? { model: payload.model } : {}),
    ...(payload?.work_mode ? { work_mode: payload.work_mode } : {}),
    ...(payload?.autonomy ? { autonomy: payload.autonomy } : {}),
  });
});

ipcMain.handle('goal-pause', async (event, payload) => {
  return requestBackend('/goal/pause', 'POST', { session_id: payload?.session_id });
});

ipcMain.handle('goal-resume', async (event, payload) => {
  return requestBackend('/goal/resume', 'POST', { session_id: payload?.session_id });
});

ipcMain.handle('goal-edit', async (event, payload) => {
  return requestBackend('/goal/edit', 'POST', { session_id: payload?.session_id, objective: payload?.objective || '' });
});

ipcMain.handle('goal-clear', async (event, payload) => {
  return requestBackend('/goal/clear', 'POST', { session_id: payload?.session_id });
});

ipcMain.handle('rename-session', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/rename`, 'POST', { title: payload.title });
});

 ipcMain.handle('get-session', async (event, sessionId) => {
   return requestBackend(`/sessions/${encodeURIComponent(sessionId)}`);
 });

 ipcMain.handle('get-context-usage', async (event, sessionId, providerId, model) => {
   return requestBackend(
     `/sessions/${encodeURIComponent(sessionId)}/context-usage?provider_id=${encodeURIComponent(providerId || '')}&model=${encodeURIComponent(model || '')}`,
   );
 });

ipcMain.handle('generate-title', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/generateTitle`, 'POST', {
    first_user_message: payload.first_user_message,
    assistant_response: payload.assistant_response || '',
    language: payload.language || 'zh',
  });
});

function startStreamingRequest(requestId, path, payload, sender, eventName = 'chat-stream-event') {
  // Same generous idle window as the chat stream: a sub-agent (worker) sharing
  // the LLM can leave the main stream silent (heartbeats only) for minutes.
  return openSseStream({ requestId, method: 'POST', path, payload, sender, eventName, idleTimeoutMs: 300_000 });
}

ipcMain.handle('start-regenerate-stream', async (event, { requestId, session_id, message_id, language, assistant_message_id, provider_id, model }) => {
  const payload = { language: language || 'zh' };
  if (assistant_message_id) payload.assistant_message_id = assistant_message_id;
  if (provider_id) payload.provider_id = provider_id;
  if (model) payload.model = model;
  return startStreamingRequest(requestId, `/sessions/${encodeURIComponent(session_id)}/messages/${encodeURIComponent(message_id)}/regenerate`, payload, event.sender);
});

ipcMain.handle('start-edit-stream', async (event, { requestId, session_id, message_id, content, work_mode, autonomy, revert_code, assistant_message_id, language, provider_id, model }) => {
  const payload = { content, language: language || 'zh' };
  if (work_mode != null) payload.work_mode = work_mode;
  if (autonomy != null) payload.autonomy = autonomy;
  if (revert_code != null) payload.revert_code = !!revert_code;
  if (assistant_message_id) payload.assistant_message_id = assistant_message_id;
  if (provider_id) payload.provider_id = provider_id;
  if (model) payload.model = model;
  return startStreamingRequest(requestId, `/sessions/${encodeURIComponent(session_id)}/messages/${encodeURIComponent(message_id)}/edit`, payload, event.sender);
});

ipcMain.handle('redo-message', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/messages/${encodeURIComponent(payload.message_id)}/redo`, 'POST', {});
});

ipcMain.handle('edit-message-begin', async (event, payload) => {
  return requestBackend(
    `/sessions/${encodeURIComponent(payload.session_id)}/messages/${encodeURIComponent(payload.message_id)}/edit-begin`,
    'POST',
    { revert_code: Boolean(payload.revert_code) },
  );
});

ipcMain.handle('edit-message-cancel', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/messages/${encodeURIComponent(payload.message_id)}/edit-cancel`, 'POST', {});
});

ipcMain.handle('list-projects', async () => {
  return requestBackend('/projects');
});

ipcMain.handle('create-project', async (event, payload) => {
  return requestBackend('/projects', 'POST', {
    name: payload?.name || '',
    workspace_path: payload?.workspace_path || '',
    mode: payload?.mode || 'single',
  });
});

ipcMain.handle('open-directory-picker', async (event, options = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: options.title || 'Choose a folder',
    defaultPath: options.defaultPath,
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

ipcMain.handle('rename-project', async (event, payload) => {
  return requestBackend(`/projects/${encodeURIComponent(payload.project_id)}/rename`, 'POST', { name: payload.name });
});

ipcMain.handle('delete-project', async (event, projectId) => {
  return requestBackend(`/projects/${encodeURIComponent(projectId)}`, 'DELETE');
});

ipcMain.handle('list-tool-audit', async (event, limit) => {
  return requestBackend(`/audit/tool?limit=${encodeURIComponent(limit || 100)}`);
});

ipcMain.handle('list-agent-traces', async (event, limit) => {
  return requestBackend(`/traces/agent?limit=${encodeURIComponent(limit || 100)}`);
});

ipcMain.handle('list-command-approvals', async () => {
  return requestBackend('/command-approvals');
});

ipcMain.handle('get-session-changes', async (event, sessionId) => {
  return requestBackend(`/sessions/${encodeURIComponent(sessionId)}/changes`);
});

ipcMain.handle('get-current-diff', async (event, options = {}) => {
  const params = new URLSearchParams();
  if (options?.projectId) params.set('project_id', options.projectId);
  if (options?.sessionId) params.set('session_id', options.sessionId);
  const query = params.toString();
  return requestBackend(`/diffs/current${query ? `?${query}` : ''}`);
});

ipcMain.handle('get-workspace-branch', async (event, projectId) => {
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  const query = params.toString();
  return requestBackend(`/workspace/branch${query ? `?${query}` : ''}`);
});

ipcMain.handle('resolve-command-approval', async (event, payload) => {
  return requestBackend('/command-approvals/resolve', 'POST', {
    approval_id: payload?.approval_id || '',
    decision: payload?.decision || {},
  });
});

ipcMain.handle('list-providers', async () => {
  return requestBackend('/providers');
});

ipcMain.handle('create-provider', async (event, payload) => {
  return requestBackend('/providers', 'POST', payload);
});

ipcMain.handle('update-provider', async (event, payload) => {
  return requestBackend(`/providers/${encodeURIComponent(payload.provider_id)}`, 'PUT', payload.params);
});

ipcMain.handle('discover-provider-context', async (event, providerId) => {
  return requestBackend(`/providers/${encodeURIComponent(providerId)}/discover-context`, 'POST', {});
});

ipcMain.handle('delete-provider', async (event, providerId) => {
  return requestBackend(`/providers/${encodeURIComponent(providerId)}`, 'DELETE');
});

ipcMain.handle('set-default-provider', async (event, payload) => {
  return requestBackend('/providers/default', 'PUT', payload);
});

ipcMain.handle('test-provider', async (event, payload) => {
  return requestBackend('/providers/test', 'POST', payload);
});

ipcMain.handle('fetch-provider-models', async (event, payload) => {
  return requestBackend('/providers/fetch-models', 'POST', payload);
});

ipcMain.handle('list-mcps', () => requestBackend('/mcp/servers', 'GET'));
ipcMain.handle('discover-mcps', () => requestBackend('/mcp/discover', 'GET'));
ipcMain.handle('create-mcp', (event, payload) => requestBackend('/mcp/servers', 'POST', payload));
ipcMain.handle('update-mcp', (event, payload = {}) => {
  const { server_id: serverId, ...body } = payload;
  return requestBackend(`/mcp/servers/${encodeURIComponent(serverId || '')}`, 'PATCH', body);
});
ipcMain.handle('delete-mcp', (event, serverId) =>
  requestBackend(`/mcp/servers/${encodeURIComponent(serverId || '')}`, 'DELETE'),
);
ipcMain.handle('test-mcp', (event, payload) => requestBackend('/mcp/test', 'POST', payload));
ipcMain.handle('check-mcp', (event, serverId) =>
  requestBackend(`/mcp/servers/${encodeURIComponent(serverId || '')}/check`, 'POST', {}),
);
ipcMain.handle('check-all-mcps', () => requestBackend('/mcp/check-all', 'POST', {}));
ipcMain.handle('reauthorize-mcp', (event, serverId) =>
  requestBackend(`/mcp/servers/${encodeURIComponent(serverId || '')}/reauthorize`, 'POST', {}),
);
ipcMain.handle('list-skills', (event, enabledOnly) =>
  requestBackend(`/skills${enabledOnly ? '?enabled_only=true' : ''}`, 'GET'),
);
ipcMain.handle('get-skill', (event, name, command) =>
  requestBackend(
    `/skills/${encodeURIComponent(name || '')}${command ? `?command=${encodeURIComponent(command)}` : ''}`,
    'GET',
  ),
);
ipcMain.handle('update-skill', (event, name, payload = {}) =>
  requestBackend(`/skills/${encodeURIComponent(name || '')}`, 'PATCH', payload),
);
ipcMain.handle('delete-skill', (event, name) =>
  requestBackend(`/skills/${encodeURIComponent(name || '')}`, 'DELETE'),
);
ipcMain.handle('scan-skills', () => requestBackend('/skills/scan', 'POST', {}));
ipcMain.handle('validate-skill', (event, payload) => requestBackend('/skills/validate', 'POST', payload));
ipcMain.handle('get-memory-status', () => requestBackend('/api/memory/status', 'GET'));
ipcMain.handle('discover-memory', (event, projectId = '') => {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return requestBackend(`/api/memory/discover${query}`, 'GET');
});
ipcMain.handle('get-memory-file', (event, rel = '') =>
  requestBackend(`/api/memory/file?rel=${encodeURIComponent(rel)}`, 'GET'),
);
ipcMain.handle('resolve-memory-path', (event, rel = '') =>
  requestBackend(`/api/memory/resolve?rel=${encodeURIComponent(rel)}`, 'GET'),
);
ipcMain.handle('save-memory-file', (event, payload = {}) => requestBackend('/api/memory/file', 'POST', payload));
ipcMain.handle('delete-memory-file', (event, payload = {}) => requestBackend('/api/memory/delete', 'POST', payload));
ipcMain.handle('get-memory-settings', () => requestBackend('/api/memory/settings', 'GET'));
ipcMain.handle('save-memory-settings', (event, payload = {}) => requestBackend('/api/memory/settings', 'POST', payload));
ipcMain.handle('reveal-in-folder', async (event, filePath) => {
  if (typeof filePath === 'string' && filePath) {
    shell.showItemInFolder(filePath);
  }
  return { status: 'ok' };
});

ipcMain.handle('search-memory', (event, query = '', limit = 50) => {
  const params = new URLSearchParams({ q: query ?? '' });
  if (limit) params.set('limit', String(limit));
  return requestBackend(`/api/memory/search?${params.toString()}`, 'GET');
});
ipcMain.handle('move-memory-file', (event, payload = {}) => requestBackend('/api/memory/move', 'POST', payload));
ipcMain.handle('preview-memory-import', (event, payload = {}) => requestBackend('/api/memory/import/preview', 'POST', payload));
ipcMain.handle('apply-memory-import', (event, payload = {}) => requestBackend('/api/memory/import/apply', 'POST', payload));

ipcMain.handle('export-memory', async (event, payload = {}) => {
  const exportResult = await requestBackend('/api/memory/export', 'POST', payload);
  const srcPath = exportResult && exportResult.path;
  if (!srcPath) throw new Error('Backend returned no export path');
  const defaultName = exportResult.filename || 'coworker-memory.zip';
  const { canceled, filePath } = await dialog.showSaveDialog({
    title: 'Export memory',
    defaultPath: defaultName,
    filters: [{ name: 'ZIP archive', extensions: ['zip'] }],
  });
  if (canceled || !filePath) return { status: 'canceled' };
  await fs.promises.copyFile(srcPath, filePath);
  // Best-effort cleanup of the backend temp archive.
  try {
    await fs.promises.unlink(srcPath);
  } catch (e) {
    // ignore
  }
  shell.showItemInFolder(filePath);
  return { status: 'ok', path: filePath, file_count: exportResult.file_count || 0 };
});

ipcMain.handle('import-memory', async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog({
    title: 'Import memory',
    properties: ['openFile'],
    filters: [{ name: 'ZIP archive', extensions: ['zip'] }],
  });
  if (canceled || !filePaths || filePaths.length === 0) return { status: 'canceled' };
  return { status: 'ok', path: filePaths[0] };
});

// Skill Market IPC handlers
// `offset` / `cursor` / `category` must survive this hop; serialise the whole
// query object instead of cherry-picking positional arguments.
function marketQueryString(query) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  return params.toString();
}

ipcMain.handle('list-market-sources', () => requestBackend('/skills/market', 'GET'));
ipcMain.handle('list-market-categories', (event, source) =>
  requestBackend(`/skills/market/categories?source=${encodeURIComponent(source)}`, 'GET'),
);
ipcMain.handle('search-market-skills', (event, query) =>
  requestBackend(`/skills/market/search?${marketQueryString(query)}`, 'GET'),
);
ipcMain.handle('list-hot-skills', (event, query) =>
  requestBackend(`/skills/market/hot?${marketQueryString(query)}`, 'GET'),
);
ipcMain.handle('install-market-skill', (event, source, slug, owner) =>
  requestBackend('/skills/market/install', 'POST', { source, slug, owner: owner ?? null }),
);
