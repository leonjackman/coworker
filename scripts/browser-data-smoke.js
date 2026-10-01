// Node smoke tests for the embedded-browser helpers that have no Electron
// dependency: download filename de-duplication and the credential vault's
// encrypt/list/match/remove cycle (with a fake safeStorage).

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { dedupeFilename } = require('../electron/browser-data');
const { CredentialVault } = require('../electron/browser-credentials');

// ── dedupeFilename ─────────────────────────────────────────────────────
{
  const taken = new Set(['/d/f.zip', '/d/f (1).zip']);
  assert.strictEqual(dedupeFilename('/d', 'f.zip', (p) => taken.has(p)), '/d/f (2).zip');
  assert.strictEqual(dedupeFilename('/d', 'g.zip', () => false), '/d/g.zip');
  assert.strictEqual(dedupeFilename('/d', '', () => false), '/d/download');
}

// ── CredentialVault (fake safeStorage) ─────────────────────────────────
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cw-vault-'));
  const fakeSafeStorage = {
    isEncryptionAvailable: () => true,
    encryptString: (s) => Buffer.from(`enc:${s}`, 'utf8'),
    decryptString: (b) => String(b.toString('utf8')).replace(/^enc:/, ''),
  };
  const vault = new CredentialVault(dir, { safeStorage: fakeSafeStorage });

  assert.ok(vault.available());
  vault.save('https://a.example', 'alice', 's3cret');
  vault.save('https://a.example', 'bob', 'hunter2');
  vault.save('https://b.example', 'carol', 'pw');

  const list = vault.list();
  assert.strictEqual(list.length, 3);
  assert.ok(!('password' in list[0]), 'masked list must not expose passwords');

  const matches = vault.match('https://a.example');
  assert.strictEqual(matches.length, 2);
  assert.strictEqual(vault.match('https://a.example')[0].password, 's3cret');

  // Same host, different scheme/port still matches.
  assert.strictEqual(vault.match('http://a.example:8080').length, 2);

  // Upsert by (origin, username) replaces the password rather than adding.
  vault.save('https://a.example', 'alice', 'newpass');
  assert.strictEqual(vault.list().length, 3);
  assert.strictEqual(vault.match('https://a.example')[0].password, 'newpass');

  assert.ok(vault.remove(list[0].id));
  assert.strictEqual(vault.list().length, 2);

  vault.clear();
  assert.strictEqual(vault.list().length, 0);
}

// Unavailable encryption must disable the vault (never plaintext).
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cw-vault-off-'));
  const vault = new CredentialVault(dir, { safeStorage: { isEncryptionAvailable: () => false } });
  assert.ok(!vault.available());
  assert.throws(() => vault.save('https://x', 'u', 'p'), /encryption_unavailable/);
  assert.deepStrictEqual(vault.list(), []);
}

console.log('browser-data smoke: OK');
