// Encrypted password vault for the embedded browser.
//
// Passwords NEVER leave the Electron main process: the page world and the
// Python backend only ever see a masked site/username list or a boolean
// "filled" result. At rest the whole vault is encrypted with Electron's
// `safeStorage` (macOS Keychain / Windows DPAPI / libsecret). When the OS
// secret store is unavailable, the vault disables itself rather than writing
// credentials in plaintext.

'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

class CredentialVault {
  constructor(userDataDir, deps) {
    const resolved = deps || {};
    // eslint-disable-next-line global-require
    const electron = resolved.safeStorage ? null : require('electron');
    this.safeStorage = resolved.safeStorage || electron.safeStorage;
    this.userDataDir = userDataDir;
    this.file = path.join(userDataDir, 'browser-credentials.json');
  }

  available() {
    try {
      return !!this.safeStorage && this.safeStorage.isEncryptionAvailable();
    } catch {
      return false;
    }
  }

  _readEntries() {
    if (!this.available()) return [];
    let blob = null;
    try {
      const data = JSON.parse(fs.readFileSync(this.file, 'utf8') || '{}');
      blob = data && typeof data.blob === 'string' ? data.blob : null;
    } catch {
      return [];
    }
    if (!blob) return [];
    try {
      const json = this.safeStorage.decryptString(Buffer.from(blob, 'base64'));
      const entries = JSON.parse(json);
      return Array.isArray(entries) ? entries.filter((e) => e && typeof e === 'object') : [];
    } catch (err) {
      console.error('[browser] failed to decrypt credential vault:', err.message);
      return [];
    }
  }

  _writeEntries(entries) {
    if (!this.available()) throw new Error('encryption_unavailable');
    const encrypted = this.safeStorage.encryptString(JSON.stringify(entries));
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify({ version: 1, blob: encrypted.toString('base64') }, null, 2), 'utf8');
    try {
      fs.chmodSync(this.file, 0o600);
    } catch { /* best effort (Windows) */ }
  }

  list() {
    return this._readEntries().map((e) => ({
      id: e.id,
      origin: e.origin,
      username: e.username,
      updated_at: e.updated_at || e.created_at || '',
    }));
  }

  /** Credentials whose site matches `origin` (exact origin, then same host). */
  match(origin) {
    let host = '';
    try {
      host = new URL(origin).hostname;
    } catch {
      host = '';
    }
    return this._readEntries().filter((e) => {
      if (e.origin === origin) return true;
      if (!host) return false;
      try {
        return new URL(e.origin).hostname === host;
      } catch {
        return false;
      }
    });
  }

  save(origin, username, password) {
    const entries = this._readEntries();
    const existing = entries.find((e) => e.origin === origin && e.username === username);
    const now = new Date().toISOString();
    if (existing) {
      existing.password = password;
      existing.updated_at = now;
    } else {
      entries.push({
        id: crypto.randomUUID(),
        origin,
        username,
        password,
        created_at: now,
        updated_at: now,
      });
    }
    this._writeEntries(entries);
    return true;
  }

  remove(id) {
    const entries = this._readEntries();
    const remaining = entries.filter((e) => e.id !== id);
    if (remaining.length === entries.length) return false;
    this._writeEntries(remaining);
    return true;
  }

  getPassword(id) {
    const entry = this._readEntries().find((e) => e.id === id);
    return entry ? entry.password : null;
  }

  clear() {
    try {
      fs.unlinkSync(this.file);
    } catch { /* already gone */ }
  }
}

module.exports = { CredentialVault };
