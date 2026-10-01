// Contract smoke for the computer-script REPL result envelope.
//
// `computer.script` / `app.script` declare outputs `result` + `text`; this checks
// the REPL surfaces them (the kernel used to discard the script's return value).

'use strict';

const assert = require('assert');
const { ComputerScript } = require('../electron/computer-repl');

const repl = new ComputerScript({});

let resolved = null;
repl._pending = { cellId: 1, resolve: (v) => { resolved = v; }, reject: (e) => { throw e; }, actions: 0, timer: null };
repl._onMessage({ type: 'done', cellId: 1, blocks: [{ type: 'text', text: 'hi' }], result: { ok: true }, text: 'hi' });
assert.ok(resolved, 'done message did not resolve');
assert.deepStrictEqual(resolved.result, { ok: true }, 'result output missing');
assert.strictEqual(resolved.text, 'hi', 'text output missing');
assert.ok(Array.isArray(resolved.blocks) && resolved.blocks.length === 1, 'blocks missing');

let errored = null;
repl._pending = { cellId: 2, resolve: (v) => { errored = v; }, reject: () => {}, actions: 0, timer: null };
repl._onMessage({ type: 'done', cellId: 2, blocks: [], result: null, text: '', error: 'boom', errorName: 'Error' });
assert.strictEqual(errored.error, 'boom');
assert.strictEqual(errored.result, null);
assert.strictEqual(errored.text, '');

console.log('script-result smoke: OK');
