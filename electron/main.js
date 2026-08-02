const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, nativeTheme } = require('electron');
const path = require('path');
const http = require('http');

const BACKEND_HOST = process.env.COWORKER_BACKEND_HOST || 'localhost';
const BACKEND_PORT = Number(process.env.COWORKER_BACKEND_PORT || 9527);
const FRONTEND_URL = process.env.COWORKER_FRONTEND_URL || 'http://localhost:3000';
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

  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL(FRONTEND_URL);
    mainWindow.webContents.openDevTools();
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
    const req = http.request(options, (res) => {
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
      reject(new Error(`Failed to connect to backend: ${e.message}`));
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

ipcMain.handle('list-sessions', async () => {
  return requestBackend('/sessions');
});

ipcMain.handle('create-session', async (event, title) => {
  return requestBackend('/sessions', 'POST', { title: title || '' });
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

ipcMain.handle('move-session', async (event, payload) => {
  return requestBackend(`/sessions/${payload.session_id}/move`, 'POST', { project_id: payload.project_id });
});

ipcMain.handle('list-projects', async () => {
  return requestBackend('/projects');
});

ipcMain.handle('create-project', async (event, name) => {
  return requestBackend('/projects', 'POST', { name });
});

ipcMain.handle('rename-project', async (event, payload) => {
  return requestBackend(`/projects/${payload.project_id}/rename`, 'POST', { name: payload.name });
});

ipcMain.handle('delete-project', async (event, projectId) => {
  return requestBackend(`/projects/${projectId}`, 'DELETE');
});

ipcMain.handle('get-workspace-tree', async () => {
  return requestBackend('/workspace/tree');
});

ipcMain.handle('get-workspace-dir', async (event, path) => {
  return requestBackend(`/workspace/dir?path=${encodeURIComponent(path || '')}`);
});

ipcMain.handle('get-workspace-file', async (event, path) => {
  return requestBackend(`/workspace/file?path=${encodeURIComponent(path)}`);
});

ipcMain.handle('run-workspace-command', async (event, payload) => {
  return requestBackend('/workspace/command', 'POST', payload);
});

ipcMain.handle('list-tool-audit', async (event, limit) => {
  return requestBackend(`/audit/tool?limit=${encodeURIComponent(limit || 100)}`);
});

ipcMain.handle('list-command-approvals', async () => {
  return requestBackend('/command-approvals');
});

ipcMain.handle('approve-command', async (event, approvalId) => {
  return requestBackend('/command-approvals/approve', 'POST', { approval_id: approvalId });
});

ipcMain.handle('deny-command', async (event, approvalId) => {
  return requestBackend('/command-approvals/deny', 'POST', { approval_id: approvalId });
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
