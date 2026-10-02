// Automation driver factory + platform selection.
//
// `createDriver()` returns the single driver for the current OS. Shared
// orchestration (desktop-controller.js, computer-repl.js) must depend only on
// this factory and the HelperDriver contract — never on process.platform or on
// a concrete driver module. Adding a platform is purely additive: implement
// HelperDriver, register it here, ship a helper binary.

'use strict';

const { HelperDriver } = require('./driver');
const { MacDriver, candidateBinaryPaths: macCandidates, resolveBinaryPath: macResolve } = require('./mac-driver');
const { WinDriver, candidateBinaryPaths: winCandidates, resolveBinaryPath: winResolve } = require('./win-driver');

// Fallback for platforms without a helper (Linux, etc.). Boots to a clear
// error instead of a platform-specific throw in shared code.
class UnsupportedDriver extends HelperDriver {
  constructor(options = {}) {
    super(options);
    this.platform = process.platform;
    this.supported = false;
  }
  candidateBinaryPaths() { return []; }
}

/**
 * Pick the driver for `platformTag` (defaults to process.platform). An explicit
 * tag is supported for tests/conformance.
 */
function createDriver(options = {}) {
  const tag = options.platform || process.platform;
  switch (tag) {
    case 'darwin': return new MacDriver(options);
    case 'win32': return new WinDriver(options);
    default: return new UnsupportedDriver(options);
  }
}

module.exports = {
  createDriver,
  HelperDriver,
  MacDriver,
  WinDriver,
  UnsupportedDriver,
  // Back-compat re-exports (formerly automation-adapter.js).
  candidateBinaryPaths: macCandidates,
  resolveBinaryPath: macResolve,
  winCandidateBinaryPaths: winCandidates,
  winResolveBinaryPath: winResolve,
};
