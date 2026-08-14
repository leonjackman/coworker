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

const { app, BrowserWindow, ipcMain, Menu, Tray, dialog, nativeImage, nativeTheme, shell } = require('electron');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');
const { autoUpdater } = require('electron-updater');

autoUpdater.autoDownload = false;
autoUpdater.allowDowngrade = false;

// `app.isPackaged` is the only reliable packaged/dev signal in Electron —
// NODE_ENV and IS_PACKAGED are not set automatically.
const IS_DEV = !app.isPackaged || process.env.COWORKER_DEV === '1';

// Only enable auto-updater in packaged builds (not in dev)
if (!IS_DEV) {
  autoUpdater.on('update-available', (info) => {
    console.log('Update available:', info.version);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('app:update-available', { version: info.version, releaseNotes: info.releaseNotes });
    }
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log('Update downloaded:', info.version);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('app:update-downloaded', { version: info.version });
    }
    // Silent install — quit and install
    setImmediate(() => autoUpdater.quitAndInstall());
  });

  autoUpdater.on('error', (err) => {
    console.error('Auto-update error:', err);
  });

  // Check for updates every 30 minutes
  setInterval(() => {
    autoUpdater.checkForUpdatesAndNotify().catch(console.error);
  }, 30 * 60 * 1000);

  // Check immediately on startup
  autoUpdater.checkForUpdatesAndNotify().catch((err) => {
    console.error('Failed to check for updates:', err);
  });
}

// Disable GPU to avoid IMKCFRunLoopWakeUpReliable crash on macOS
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-gpu-compositing');
app.commandLine.appendSwitch('disable-gpu-vsync');
app.commandLine.appendSwitch('disable-features', 'InputMethodServiceOverlay');

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

function applyAppIcon(targetWindow = mainWindow) {
  const iconPath = path.join(BRAND_ASSET_DIR, 'cw-icon-blue-white-background.png');
  const icon = nativeImage.createFromPath(iconPath);
  if (process.platform === 'darwin' && app.dock) {
    app.dock.setIcon(icon);
  }
  if (targetWindow && !targetWindow.isDestroyed()) {
    targetWindow.setIcon(icon);
  }
}

function refreshBrandIcons() {
  if (tray && !tray.isDestroyed()) {
    tray.setImage(createTrayIcon());
  }
  applyAppIcon();
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
    },
  });

  applyAppIcon(mainWindow);

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
    console.error('Frontend did-fail-load:', { errorCode, errorDescription, validatedURL });
    showStartupError({
      stage: 'did-fail-load',
      url: validatedURL,
      errorCode,
      errorDescription,
    });
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    console.error('Renderer process exited unexpectedly:', details);
    showStartupError({
      stage: 'render-process-gone',
      reason: details.reason,
      exitCode: details.exitCode,
    });
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

app.whenReady().then(async () => {
  if (!IS_DEV) {
    await startBundledBackend();
    if (backendProcess === null && !IS_DEV) return;
  }

  createTray();
  createWindow();
  nativeTheme.on('updated', refreshBrandIcons);

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
ipcMain.handle('check-for-updates', async () => {
  if (!IS_DEV && autoUpdater.isUpdaterActive()) {
    return { status: 'ok' };
  }
  return { status: IS_DEV ? 'dev-mode' : 'unavailable' };
});

ipcMain.handle('update-runtime-config', async (event, payload) => {
  return requestBackend('/config', 'PATCH', payload);
});

ipcMain.handle('fetchSettings', async () => {
  try {
    return await requestBackend('/settings');
  } catch (e) {
    return { goal_max_rounds: 50, max_attachment_mb: 25 };
  }
});

ipcMain.handle('saveSettings', async (event, payload) => {
  try {
    return await requestBackend('/settings', 'POST', payload);
  } catch (e) {
    return { status: 'error', goal_max_rounds: 50, max_attachment_mb: 25, detail: e.message };
  }
});

const activeStreams = new Map();

ipcMain.handle('start-chat-stream', async (event, { requestId, payload }) => {
  const data = JSON.stringify(payload);
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: '/chat/stream',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(data),
    },
  };

  const sender = event.sender;
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.setEncoding('utf8');
      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.replace(/\r\n/g, '\n');
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
          if (!dataLine) continue;
          const raw = dataLine.slice(5).trim();
          if (!raw) continue;
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            continue;
          }
          sender.send('chat-stream-event', { requestId, event: parsed });
        }
      });
      res.on('end', () => {
        if (res.statusCode >= 400) {
          // Non-SSE error body: surface it as an error event instead of
          // silently resolving "ok" and leaving the running bubble counting.
          let detail = `Backend returned ${res.statusCode}`;
          try {
            const parsed = JSON.parse(buffer);
            if (parsed && parsed.detail) detail = parsed.detail;
          } catch {
            // not JSON, keep the status-based message
          }
          sender.send('chat-stream-event', { requestId, event: { type: 'error', error: detail } });
        } else if (buffer) {
          const dataLine = buffer.split('\n').find((line) => line.startsWith('data:'));
          if (dataLine) {
            const raw = dataLine.slice(5).trim();
            if (raw) {
              try {
                sender.send('chat-stream-event', { requestId, event: JSON.parse(raw) });
              } catch {
                // ignore trailing partial frame
              }
            }
          }
        }
        activeStreams.delete(requestId);
        resolve({ status: 'ok' });
      });
    });

    req.on('error', (e) => {
      activeStreams.delete(requestId);
      sender.send('chat-stream-event', { requestId, event: { type: 'error', error: `Failed to connect to backend: ${e.message}` } });
      resolve({ status: 'error' });
    });

    activeStreams.set(requestId, req);
    req.write(data);
    req.end();
  });
});

ipcMain.on('abort-chat-stream', (event, requestId) => {
  const req = activeStreams.get(requestId);
  if (req) {
    req.destroy();
    activeStreams.delete(requestId);
  }
});

ipcMain.handle('start-approval-stream', async (event, { requestId, resumeId }) => {
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: `/command-approvals/events/${encodeURIComponent(resumeId)}`,
    method: 'GET',
  };

  const sender = event.sender;
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.setEncoding('utf8');
      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.replace(/\r\n/g, '\n');
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
          if (!dataLine) continue;
          const raw = dataLine.slice(5).trim();
          if (!raw) continue;
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            continue;
          }
          sender.send('approval-stream-event', { requestId, event: parsed });
        }
      });
      res.on('end', () => {
        if (buffer) {
          const dataLine = buffer.split('\n').find((line) => line.startsWith('data:'));
          if (dataLine) {
            const raw = dataLine.slice(5).trim();
            if (raw) {
              try {
                sender.send('approval-stream-event', { requestId, event: JSON.parse(raw) });
              } catch {
                // ignore trailing partial frame
              }
            }
          }
        }
        activeStreams.delete(requestId);
        resolve({ status: 'ok' });
      });
    });

    req.on('error', (e) => {
      activeStreams.delete(requestId);
      sender.send('approval-stream-event', { requestId, event: { type: 'error', error: `Failed to connect to backend: ${e.message}` } });
      resolve({ status: 'error' });
    });

    activeStreams.set(requestId, req);
    req.end();
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
  });
});

ipcMain.handle('delete-session', async (event, sessionId) => {
  return requestBackend(`/sessions/${encodeURIComponent(sessionId)}`, 'DELETE');
});

ipcMain.handle('rename-session', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/rename`, 'POST', { title: payload.title });
});

ipcMain.handle('get-session', async (event, sessionId) => {
  return requestBackend(`/sessions/${encodeURIComponent(sessionId)}`);
});

ipcMain.handle('generate-title', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/generateTitle`, 'POST', {
    first_user_message: payload.first_user_message,
    assistant_response: payload.assistant_response || '',
    language: payload.language || 'zh',
  });
});

function startStreamingRequest(requestId, path, payload, sender, eventName = 'chat-stream-event') {
  const data = JSON.stringify(payload);
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(data),
    },
  };

  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.setEncoding('utf8');
      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.replace(/\r\n/g, '\n');
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
          if (!dataLine) continue;
          const raw = dataLine.slice(5).trim();
          if (!raw) continue;
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            continue;
          }
          if (sender) sender.send(eventName, { requestId, event: parsed });
        }
      });
      res.on('end', () => {
        if (res.statusCode >= 400) {
          // Non-SSE error body (e.g. 409 while the same session is still
          // generating): surface it as an error event instead of silently
          // resolving "ok" and leaving the running bubble counting forever.
          let detail = `Backend returned ${res.statusCode}`;
          try {
            const parsed = JSON.parse(buffer);
            if (parsed && parsed.detail) detail = parsed.detail;
          } catch {
            // not JSON, keep the status-based message
          }
          if (sender) sender.send(eventName, { requestId, event: { type: 'error', error: detail } });
        } else if (buffer) {
          const dataLine = buffer.split('\n').find((line) => line.startsWith('data:'));
          if (dataLine) {
            const raw = dataLine.slice(5).trim();
            if (raw) {
              try {
                if (sender) sender.send(eventName, { requestId, event: JSON.parse(raw) });
              } catch {
                // ignore
              }
            }
          }
        }
        activeStreams.delete(requestId);
        resolve({ status: 'ok' });
      });
    });

    req.on('error', (e) => {
      activeStreams.delete(requestId);
      if (sender) sender.send(eventName, { requestId, event: { type: 'error', error: `Failed to connect to backend: ${e.message}` } });
      resolve({ status: 'error' });
    });

    activeStreams.set(requestId, req);
    req.write(data);
    req.end();
  });
}

ipcMain.handle('rollback-message', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/messages/${encodeURIComponent(payload.message_id)}/rollback`, 'POST', {
    with_code: !!payload?.with_code,
  }, 60000);
});

ipcMain.handle('get-revert-preview', async (event, payload) => {
  return requestBackend(`/sessions/${encodeURIComponent(payload.session_id)}/messages/${encodeURIComponent(payload.message_id)}/revert-preview`);
});

ipcMain.handle('start-regenerate-stream', async (event, { requestId, session_id, message_id, language }) => {
  return startStreamingRequest(requestId, `/sessions/${encodeURIComponent(session_id)}/messages/${encodeURIComponent(message_id)}/regenerate`, { language: language || 'zh' }, event.sender);
});

ipcMain.handle('start-edit-stream', async (event, { requestId, session_id, message_id, content, work_mode, autonomy, language }) => {
  const payload = { content, language: language || 'zh' };
  if (work_mode) payload.work_mode = work_mode;
  if (autonomy) payload.autonomy = autonomy;
  return startStreamingRequest(requestId, `/sessions/${encodeURIComponent(session_id)}/messages/${encodeURIComponent(message_id)}/edit`, payload, event.sender);
});

ipcMain.handle('list-projects', async () => {
  return requestBackend('/projects');
});

ipcMain.handle('create-project', async (event, payload) => {
  return requestBackend('/projects', 'POST', {
    name: payload?.name || '',
    workspace_path: payload?.workspace_path || '',
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

ipcMain.handle('goal-status', async (event, sessionId) => {
  return requestBackend(`/goal/status?session_id=${encodeURIComponent(sessionId || '')}`);
});

ipcMain.handle('goal-pause', async (event, sessionId) => {
  return requestBackend('/goal/pause', 'POST', { session_id: sessionId });
});

ipcMain.handle('goal-edit', async (event, payload) => {
  return requestBackend('/goal/edit', 'POST', { session_id: payload?.session_id || '', goal: payload?.goal || '' });
});

ipcMain.handle('goal-delete', async (event, sessionId) => {
  return requestBackend('/goal/delete', 'POST', { session_id: sessionId });
});

ipcMain.handle('start-goal-resume', async (event, { requestId, sessionId, language }) => {
  const data = JSON.stringify({ session_id: sessionId, language: language || 'zh' });
  const options = {
    hostname: BACKEND_HOST,
    port: BACKEND_PORT,
    path: '/goal/resume',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(data),
    },
  };
  const sender = event.sender;
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.setEncoding('utf8');
      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.replace(/\r\n/g, '\n');
        let sepIndex;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
          if (dataLine === undefined) continue;
          let payload;
          try {
            payload = JSON.parse(dataLine.slice(5).trim());
          } catch {
            continue;
          }
          sender.send('chat-stream-event', { requestId, event: payload });
        }
      });
      res.on('end', () => {
        activeStreams.delete(requestId);
        resolve({ ok: true });
      });
      res.on('error', (err) => {
        activeStreams.delete(requestId);
        reject(err);
      });
    });
    // Register so the Stop button's abort-chat-stream can cancel a running resume.
    activeStreams.set(requestId, req);
    req.on('error', (err) => {
      activeStreams.delete(requestId);
      reject(err);
    });
    req.write(data);
    req.end();
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
ipcMain.handle('write-memory-entry', (event, payload = {}) => requestBackend('/api/memory/write', 'POST', payload));
ipcMain.handle('remove-memory-entry', (event, payload = {}) => requestBackend('/api/memory/remove', 'POST', payload));
ipcMain.handle('clear-memory-scope', (event, payload = {}) => requestBackend('/api/memory/clear', 'POST', payload));
ipcMain.handle('list-memory-proposals', () => requestBackend('/api/memory/proposals', 'GET'));
ipcMain.handle('resolve-memory-proposal', (event, payload = {}) => requestBackend('/api/memory/proposals/resolve', 'POST', payload));
  ipcMain.handle('get-memory-files', (event, projectId = '') => {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return requestBackend(`/api/memory${query}`, 'GET');
  });
  ipcMain.handle('get-memory-file', (event, scope = 'project', projectId = '') => {
    const query = projectId ? `&project_id=${encodeURIComponent(projectId)}` : '';
    return requestBackend(`/api/memory/file?scope=${encodeURIComponent(scope)}${query}`, 'GET');
  });
ipcMain.handle('save-memory-file', (event, payload = {}) => requestBackend('/api/memory/file', 'POST', payload));
ipcMain.handle('get-memory-settings', () => requestBackend('/api/memory/settings', 'GET'));
ipcMain.handle('save-memory-settings', (event, payload = {}) => requestBackend('/api/memory/settings', 'POST', payload));
ipcMain.handle('reveal-in-folder', async (event, filePath) => {
  if (typeof filePath === 'string' && filePath) {
    shell.showItemInFolder(filePath);
  }
  return { status: 'ok' };
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
