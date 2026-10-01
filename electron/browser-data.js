// Browser session helpers for the embedded <webview> profile:
//   - download tracking (will-download on the persisted partition session)
//   - per-origin permission decisions (setPermissionRequestHandler)
//   - filename de-duplication for auto-saved downloads
//
// Kept as a plain CommonJS module (no Electron imports at load time) so the
// pure helpers are unit-testable with `node` and the module can be required
// from main.js after `app.whenReady()`.

'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

/**
 * Return a path inside `dir` for `filename` that does not collide with an
 * existing file (appends " (1)", " (2)", ... before the extension).
 */
function dedupeFilename(dir, filename, existsFn) {
  const exists = typeof existsFn === 'function' ? existsFn : (p) => fs.existsSync(p);
  const safeName = path.basename(String(filename || 'download').trim()) || 'download';
  const ext = path.extname(safeName);
  const stem = ext ? safeName.slice(0, -ext.length) : safeName;
  let candidate = path.join(dir, safeName);
  let index = 1;
  while (exists(candidate)) {
    candidate = path.join(dir, `${stem} (${index})${ext}`);
    index += 1;
  }
  return candidate;
}

/** Minimal persistent per-origin permission store (userData JSON file). */
function createPermissionStore(userDataDir) {
  const file = path.join(userDataDir, 'browser-permissions.json');
  const read = () => {
    try {
      const data = JSON.parse(fs.readFileSync(file, 'utf8') || '{}');
      return data && typeof data === 'object' ? data : {};
    } catch {
      return {};
    }
  };
  const write = (data) => {
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
    } catch (err) {
      console.error('[browser] failed to persist permissions:', err.message);
    }
  };
  return {
    file,
    get(origin, permission) {
      const perms = read()[origin];
      if (!perms || typeof perms !== 'object') return undefined;
      const value = perms[permission];
      return typeof value === 'boolean' ? value : undefined;
    },
    set(origin, permission, allowed) {
      const data = read();
      if (!data[origin] || typeof data[origin] !== 'object') data[origin] = {};
      data[origin][permission] = !!allowed;
      write(data);
    },
    reset(origin) {
      const data = read();
      if (origin) {
        delete data[origin];
      } else {
        for (const key of Object.keys(data)) delete data[key];
      }
      write(data);
    },
    list() {
      return read();
    },
  };
}

/**
 * Wire download tracking onto the browser partition session.
 *  - auto-saves to `getSettings().download_dir` (or the OS Downloads dir) with
 *    collision-safe names, unless `ask_where_to_save` is set;
 *  - records start/progress/completion in the backend store;
 *  - pushes live status to the renderer.
 */
function installDownloadHandler(browserSession, { getSettings, requestBackend, sendToRenderer, osDownloadsDir }) {
  // Live DownloadItem handles keyed by our id, so the renderer can pause /
  // resume / cancel an in-flight download. Cleared when the item finishes.
  const active = new Map();

  browserSession.on('will-download', (event, item) => {
    const settings = (typeof getSettings === 'function' && getSettings()) || {};
    const settingsDir = String(settings.download_dir || '').trim();
    const targetDir = settingsDir || osDownloadsDir;

    if (settings.ask_where_to_save && !settingsDir) {
      // A synchronous dialog keeps item.setSavePath usable; the user picks the
      // exact file. Cancelled → let Electron cancel the download.
      // eslint-disable-next-line global-require
      const { dialog } = require('electron');
      const picked = dialog.showSaveDialogSync({
        defaultPath: path.join(targetDir, item.getFilename()),
      });
      if (!picked) {
        item.cancel();
        return;
      }
      item.setSavePath(picked);
    } else {
      try {
        fs.mkdirSync(targetDir, { recursive: true });
      } catch { /* best effort */ }
      item.setSavePath(dedupeFilename(targetDir, item.getFilename()));
    }

    const id = crypto.randomUUID();
    const url = item.getURL();
    const filename = item.getFilename();
    const mime = item.getMimeType ? item.getMimeType() : '';
    const dest = item.getSavePath();

    const payload = { id, url, filename, path: dest, mime, total_bytes: item.getTotalBytes() || 0 };
    active.set(id, item);
    requestBackend('/api/browser/downloads', 'POST', payload).catch(() => {});
    sendToRenderer('browser:download', { ...payload, state: 'progressing', received_bytes: 0, can_resume: false });

    let paused = false;
    let lastSent = 0;
    item.on('updated', () => {
      const now = Date.now();
      if (now - lastSent < 500) return;
      lastSent = now;
      const progress = {
        id,
        state: paused ? 'paused' : (item.getState ? item.getState() : 'progressing'),
        received_bytes: item.getReceivedBytes(),
        total_bytes: item.getTotalBytes(),
        can_resume: !!(item.canResume && item.canResume()),
      };
      requestBackend(`/api/browser/downloads/${id}`, 'PATCH', progress).catch(() => {});
      sendToRenderer('browser:download', { ...payload, ...progress });
    });

    item.once('done', (_e, state) => {
      active.delete(id);
      const finalState = state === 'completed' ? 'completed' : state === 'cancelled' ? 'cancelled' : 'interrupted';
      const finalPatch = {
        state: finalState,
        received_bytes: item.getReceivedBytes(),
        total_bytes: item.getTotalBytes(),
        path: item.getSavePath(),
        ended_at: new Date().toISOString(),
        can_resume: false,
      };
      requestBackend(`/api/browser/downloads/${id}`, 'PATCH', finalPatch).catch(() => {});
      sendToRenderer('browser:download', { ...payload, ...finalPatch });
    });

    // Track the paused flag so progress/updated reports the right state.
    item.on('pause', () => { paused = true; });
    item.on('resume', () => { paused = false; });
  });

  return {
    pause(id) {
      const item = active.get(id);
      if (!item) return { ok: false, error: 'not_found' };
      try {
        item.pause();
        sendToRenderer('browser:download', { id, state: 'paused', can_resume: !!(item.canResume && item.canResume()) });
        return { ok: true, state: 'paused' };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    },
    resume(id) {
      const item = active.get(id);
      if (!item) return { ok: false, error: 'not_found' };
      try {
        if (item.canResume && !item.canResume()) return { ok: false, error: 'cannot_resume' };
        item.resume();
        sendToRenderer('browser:download', { id, state: 'progressing', can_resume: false });
        return { ok: true, state: 'progressing' };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    },
    cancel(id) {
      const item = active.get(id);
      if (!item) return { ok: false, error: 'not_found' };
      try {
        item.cancel();
        return { ok: true, state: 'cancelled' };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    },
  };
}

/**
 * Wire per-origin permission prompts onto the browser partition session.
 *  - a remembered decision is applied immediately;
 *  - otherwise the renderer is asked (in-app prompt) and the answer remembered
 *    when the user opts in; a missing/unresponsive renderer fails closed.
 */
function installPermissionHandlers(browserSession, { getSettings, permissionStore, askRenderer, allowedPermissions }) {
  const allowList = allowedPermissions || null;

  const decide = async (origin, permission) => {
    const settings = (typeof getSettings === 'function' && getSettings()) || {};
    if (settings.permissions_prompt === false) return true; // prompting disabled → allow
    const remembered = permissionStore.get(origin, permission);
    if (remembered !== undefined) return remembered;
    if (typeof askRenderer !== 'function') return false;
    const answer = await askRenderer({ origin, permission });
    if (!answer || typeof answer !== 'object') return false;
    if (answer.remember) permissionStore.set(origin, permission, !!answer.allow);
    return !!answer.allow;
  };

  browserSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    let origin = '';
    try {
      origin = new URL(details && details.requestingUrl ? details.requestingUrl : webContents.getURL()).origin;
    } catch {
      origin = '';
    }
    if (allowList && !allowList.has(permission)) {
      callback(false);
      return;
    }
    decide(origin, permission)
      .then((allow) => callback(allow))
      .catch(() => callback(false));
  });

  browserSession.setPermissionCheckHandler((_webContents, permission, requestingOrigin) => {
    let origin = requestingOrigin || '';
    try {
      origin = new URL(origin).origin;
    } catch { /* keep raw */ }
    const remembered = permissionStore.get(origin, permission);
    return remembered === true;
  });
}

module.exports = {
  dedupeFilename,
  createPermissionStore,
  installDownloadHandler,
  installPermissionHandlers,
};
