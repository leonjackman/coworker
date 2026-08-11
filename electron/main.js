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

const { app, BrowserWindow, ipcMain, Menu, Tray, dialog, nativeImage, nativeTheme } = require('electron');
const path = require('path');
const http = require('http');

// `app.isPackaged` is the only reliable packaged/dev signal in Electron —
// NODE_ENV and IS_PACKAGED are not set automatically.
const IS_DEV = !app.isPackaged || process.env.COWORKER_DEV === '1';

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

const BACKEND_HOST = process.env.COWORKER_BACKEND_HOST || 'localhost';
const BACKEND_PORT = Number(process.env.COWORKER_BACKEND_PORT || 9527);
const FRONTEND_URL = process.env.COWORKER_FRONTEND_URL || null;
const FRONTEND_DIST_ENTRY = path.join(__dirname, '../frontend/dist/index.html');

let mainWindow = null;
let tray = null;
let isQuitting = false;

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
  const iconPath = themedMonochromeAssetPath('cw-icon');
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
      <title>Coworker 启动失败</title>
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
        <h1>Coworker 前端未能正常加载</h1>
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
  tray.setToolTip('Coworker');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show Coworker', click: showMainWindow },
    { label: 'Hide Window', click: hideMainWindow },
    { type: 'separator' },
    { label: 'Quit Coworker', click: quitApp },
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

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createTray();
  createWindow();
  nativeTheme.on('updated', refreshBrandIcons);

  app.on('activate', () => {
    showMainWindow();
  });
});

app.on('before-quit', () => {
  isQuitting = true;
});

function requestBackend(pathname, method = 'GET', payload = undefined) {
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
    }, 10000);

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

    req.setTimeout(10000);
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

ipcMain.handle('send-chat-message', async (event, payload) => {
  return requestBackend('/chat', 'POST', payload);
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
        buffer += chunk;
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
        if (buffer) {
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
        buffer += chunk;
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
  return requestBackend('/sessions/active');
});

ipcMain.handle('create-session', async (event, payload) => {
  return requestBackend('/sessions', 'POST', {
    title: payload?.title || '',
    project_id: payload?.project_id || '',
  });
});

ipcMain.handle('delete-session', async (event, sessionId) => {
  return requestBackend(`/sessions/${sessionId}`, 'DELETE');
});

ipcMain.handle('rename-session', async (event, payload) => {
  return requestBackend(`/sessions/${payload.session_id}/rename`, 'POST', { title: payload.title });
});

ipcMain.handle('get-session', async (event, sessionId) => {
  return requestBackend(`/sessions/${sessionId}`);
});

ipcMain.handle('generate-title', async (event, payload) => {
  return requestBackend(`/sessions/${payload.session_id}/generateTitle`, 'POST', {
    first_user_message: payload.first_user_message,
    assistant_response: payload.assistant_response || '',
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
        buffer += chunk;
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
        if (buffer) {
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
  return requestBackend(`/sessions/${payload.session_id}/messages/${payload.message_id}/rollback`, 'POST', {
    with_code: !!payload?.with_code,
  });
});

ipcMain.handle('get-revert-preview', async (event, payload) => {
  return requestBackend(`/sessions/${payload.session_id}/messages/${payload.message_id}/revert-preview`);
});

ipcMain.handle('start-regenerate-stream', async (event, { requestId, session_id, message_id }) => {
  return startStreamingRequest(requestId, `/sessions/${session_id}/messages/${message_id}/regenerate`, {}, event.sender);
});

ipcMain.handle('start-edit-stream', async (event, { requestId, session_id, message_id, content, work_mode, autonomy }) => {
  const payload = { content };
  if (work_mode) payload.work_mode = work_mode;
  if (autonomy) payload.autonomy = autonomy;
  return startStreamingRequest(requestId, `/sessions/${session_id}/messages/${message_id}/edit`, payload, event.sender);
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
  return requestBackend(`/projects/${payload.project_id}/rename`, 'POST', { name: payload.name });
});

ipcMain.handle('delete-project', async (event, projectId) => {
  return requestBackend(`/projects/${projectId}`, 'DELETE');
});

ipcMain.handle('get-workspace-tree', async (event, projectId) => {
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return requestBackend(`/workspace/tree${suffix}`);
});

ipcMain.handle('get-workspace-dir', async (event, payload) => {
  const currentPath = typeof payload === 'string' ? payload : payload?.path || '';
  const projectId = typeof payload === 'object' && payload ? payload.project_id || '' : '';
  const params = new URLSearchParams({ path: currentPath });
  if (projectId) params.set('project_id', projectId);
  return requestBackend(`/workspace/dir?${params.toString()}`);
});

ipcMain.handle('get-workspace-file', async (event, payload) => {
  const currentPath = typeof payload === 'string' ? payload : payload?.path || '';
  const projectId = typeof payload === 'object' && payload ? payload.project_id || '' : '';
  const params = new URLSearchParams({ path: currentPath });
  if (projectId) params.set('project_id', projectId);
  return requestBackend(`/workspace/file?${params.toString()}`);
});

ipcMain.handle('run-workspace-command', async (event, payload) => {
  return requestBackend('/workspace/command', 'POST', payload);
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

ipcMain.handle('start-goal-resume', async (event, { requestId, sessionId }) => {
  const data = JSON.stringify({ session_id: sessionId });
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
        buffer += chunk;
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
      res.on('end', () => resolve({ ok: true }));
      res.on('error', (err) => reject(err));
    });
    req.on('error', (err) => reject(err));
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
  return requestBackend(`/providers/${payload.provider_id}`, 'PUT', payload.params);
});

ipcMain.handle('delete-provider', async (event, providerId) => {
  return requestBackend(`/providers/${providerId}`, 'DELETE');
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
