// automation-adapter.js — BACK-COMPAT SHIM.
//
// The macOS helper wrapper now lives in the shared, platform-neutral
// `electron/automation/` layer (HelperDriver + MacDriver + WinDriver + factory).
// This module is kept only so older call sites / external scripts that did
// `require('./automation-adapter').AutomationAdapter` keep working. New code
// should use `require('./automation').createDriver()`.
//
// `AutomationAdapter` here IS `MacDriver` (the macOS implementation).

'use strict';

const {
  MacDriver,
  candidateBinaryPaths,
  resolveBinaryPath,
} = require('./automation/mac-driver');

module.exports = {
  AutomationAdapter: MacDriver,
  defaultBinaryPath: candidateBinaryPaths,
  resolveBinaryPath,
};
