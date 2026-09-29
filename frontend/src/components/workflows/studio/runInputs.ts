import type { WorkflowInputSpec } from '../../../types';

/**
 * Manual "Test run" inputs for the Studio.
 *
 * The UI builds a typed form from the workflow's declared inputs; these helpers
 * translate between the form representation, the value object sent to the
 * backend, and the per-workflow remembered values.
 */

export const INPUT_TYPES = ['string', 'number', 'boolean', 'list', 'object', 'secret'] as const;

export type InputFormValue = string | boolean;
export type InputErrors = Record<string, 'required' | 'number' | 'json'>;

const STORAGE_KEY = 'cw-workflow-run-inputs';

function readStore(): Record<string, Record<string, unknown>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, Record<string, unknown>>) : {};
  } catch {
    return {};
  }
}

export function loadRememberedRunInputs(name: string): Record<string, unknown> {
  if (!name) return {};
  return readStore()[name] ?? {};
}

/** Persist the last-used values for a workflow (secrets are never stored). */
export function rememberRunInputs(name: string, specs: WorkflowInputSpec[], values: Record<string, unknown>): void {
  if (!name) return;
  try {
    const map = readStore();
    const filtered: Record<string, unknown> = {};
    for (const spec of specs) {
      if (spec.type === 'secret') continue;
      if (spec.name in values) filtered[spec.name] = values[spec.name];
    }
    map[name] = filtered;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* localStorage may be unavailable */
  }
}

function defaultFormValue(spec: WorkflowInputSpec): InputFormValue {
  if (spec.type === 'boolean') return typeof spec.default === 'boolean' ? spec.default : false;
  if (spec.default === null || spec.default === undefined) return '';
  if (typeof spec.default === 'object') return JSON.stringify(spec.default);
  return String(spec.default);
}

/** Initial form values: remembered values win, otherwise the declared default. */
export function initRunInputForm(
  specs: WorkflowInputSpec[],
  remembered: Record<string, unknown>,
): Record<string, InputFormValue> {
  const out: Record<string, InputFormValue> = {};
  for (const spec of specs) {
    if (!spec.name) continue;
    if (spec.name in remembered) {
      const v = remembered[spec.name];
      out[spec.name] =
        spec.type === 'boolean'
          ? Boolean(v)
          : v !== null && typeof v === 'object'
            ? JSON.stringify(v)
            : String(v ?? '');
    } else {
      out[spec.name] = defaultFormValue(spec);
    }
  }
  return out;
}

/** Keep existing entries, add defaults for newly declared inputs, drop removed. */
export function mergeRunInputForm(
  specs: WorkflowInputSpec[],
  prev: Record<string, InputFormValue>,
): Record<string, InputFormValue> {
  const out: Record<string, InputFormValue> = {};
  for (const spec of specs) {
    if (!spec.name) continue;
    out[spec.name] = spec.name in prev ? prev[spec.name]! : defaultFormValue(spec);
  }
  return out;
}

/** Coerce the typed form into the value object sent to the backend. */
export function collectRunInputs(
  specs: WorkflowInputSpec[],
  form: Record<string, InputFormValue>,
): { inputs: Record<string, unknown>; errors: InputErrors } {
  const inputs: Record<string, unknown> = {};
  const errors: InputErrors = {};
  for (const spec of specs) {
    if (!spec.name) continue;
    const raw = form[spec.name];
    if (spec.type === 'boolean') {
      inputs[spec.name] = Boolean(raw);
      continue;
    }
    const text = raw === undefined || raw === null ? '' : String(raw);
    if (text.trim() === '') {
      if (spec.required) errors[spec.name] = 'required';
      continue;
    }
    if (spec.type === 'number') {
      const n = Number(text);
      if (Number.isNaN(n)) errors[spec.name] = 'number';
      else inputs[spec.name] = n;
    } else if (spec.type === 'list') {
      if (text.trim().startsWith('[')) {
        try {
          const parsed = JSON.parse(text);
          if (Array.isArray(parsed)) inputs[spec.name] = parsed;
          else errors[spec.name] = 'json';
        } catch {
          errors[spec.name] = 'json';
        }
      } else {
        inputs[spec.name] = text
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
      }
    } else if (spec.type === 'object') {
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) inputs[spec.name] = parsed;
        else errors[spec.name] = 'json';
      } catch {
        errors[spec.name] = 'json';
      }
    } else {
      inputs[spec.name] = text;
    }
  }
  return { inputs, errors };
}

/** Coerce a text field into a correctly-typed default for the input editor. */
export function coerceInputDefault(type: string, raw: string): unknown {
  const text = raw.trim();
  if (text === '') return null;
  if (type === 'number') {
    const n = Number(text);
    return Number.isNaN(n) ? raw : n;
  }
  if (type === 'boolean') {
    if (text === 'true') return true;
    if (text === 'false') return false;
    return raw;
  }
  if (type === 'list' || type === 'object') {
    try {
      return JSON.parse(text);
    } catch {
      return raw;
    }
  }
  return raw;
}
