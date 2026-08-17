/**
 * Dependency bootstrap for CoWorker Electron app.
 *
 * Runs before anything else — checks that every package listed in the
 * project-scope dependencies is present in node_modules and
 * auto-installs any missing ones (using the system npm/yarn/pnpm).
 *
 * This file must be required *before* any other module in main.js.
 */

(function () {
  'use strict';

  const { execSync, spawnSync } = require('child_process');
  const fs = require('fs');
  const path = require('path');

  // ── Helpers ──────────────────────────────────────────────────────────
  const ROOT = path.resolve(__dirname, '..');
  const LOCKFILES = [
    path.join(ROOT, 'package-lock.json'),
    path.join(ROOT, 'yarn.lock'),
    path.join(ROOT, 'pnpm-lock.yaml'),
  ];

  function fileExist(name) {
    return fs.existsSync(path.join(ROOT, name));
  }

  function commandAvailable(cmd) {
    const r = spawnAsync(cmd, ['--version'], { silent: true });
    return r.status === 0;
  }

  function spawnAsync(cmd, args, opts = {}) {
    const { silent } = opts;
    const child = spawnSync(cmd, args, {
      cwd: ROOT,
      stdio: silent
        ? ['pipe', 'pipe', 'pipe']
        : ['inherit', 'inherit', 'inherit'],
      encoding: 'utf8',
    });
    return child;
  }

  function detectPackageManager() {
    // Prefer a package manager that:
    //  1. is available as a command
    //  2. matches the project's lockfile (if any)

    // Lockfile detection
    const yarnLock = fileExist('yarn.lock');
    const pnpmLock = fileExist('pnpm-lock.yaml');
    const npmLock = fileExist('package-lock.json');

    let pm = null;

    // Check yarn (first available, if lockfile exists)
    if (commandAvailable('yarn')) {
      if (yarnLock) return 'yarn';
      // Fallback: use yarn even without lockfile
      pm = pm || 'yarn';
    }

    // Check pnpm
    if (commandAvailable('pnpm')) {
      if (pnpmLock) return 'pnpm';
      pm = pm || 'pnpm';
    }

    // npm is always available
    return 'npm';
  }

  function ensureDeps() {
    const pm = detectPackageManager();
    console.log(`[dep-check] Installing missing dependencies with ${pm}…`);

    if (pm === 'yarn') {
      execSync('yarn', { cwd: ROOT, stdio: 'inherit' });
    } else if (pm === 'pnpm') {
      execSync('pnpm install', { cwd: ROOT, stdio: 'inherit' });
    } else {
      // npm install: try --legacy-peer-deps first (electron-builder often
      // has conflicting peer deps), then fall back.
      try {
        execSync('npm install --legacy-peer-deps', {
          cwd: ROOT,
          stdio: 'inherit',
        });
      } catch {
        try {
          execSync('npm install', { cwd: ROOT, stdio: 'inherit' });
        } catch (err) {
          console.error('[dep-check] npm install failed:', err.message);
          throw err;
        }
      }
    }

    console.log('[dep-check] Dependencies installed successfully ✓');
  }

  // ── Check missing dependencies first (faster than full install) ─────
  try {
    const packagePath = path.join(ROOT, 'package.json');
    if (!fs.existsSync(packagePath)) {
      console.error('[dep-check] package.json not found');
      return;
    }

    const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    const allDeps = Object.assign({}, pkg.dependencies || {}, pkg.devDependencies || {});
    const missing = [];

    for (const name of Object.keys(allDeps)) {
      try {
        require.resolve(name, { paths: [ROOT] });
      } catch {
        missing.push(name);
      }
    }

    if (missing.length > 0) {
      console.log('[dep-check] Missing dependencies:', missing.join(', '));
      ensureDeps();
    } else {
      console.log('[dep-check] All dependencies present ✓');
    }
  } catch (err) {
    // In dev (COWORKER_DEV=1) we tolerate missing deps — the
    // process will fail later with a clear error if they're actually needed.
    if (process.env.COWORKER_DEV === '1') {
      console.log('[dep-check] Dev mode — skipping dependency validation');
    } else {
      console.error('[dep-check] Dependency check failed:', err.message);
      // In production we do a full install to self-heal
      try {
        ensureDeps();
      } catch (installErr) {
        console.error('[dep-check] Install failed:', installErr.message);
        // Let the process continue anyway — Electron will crash later
        // if a module is still missing, but with a cleaner stack trace.
      }
    }
  }
})();
