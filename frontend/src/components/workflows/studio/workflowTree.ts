import { KIND_META } from '../kinds';
import type { WorkflowStep } from '../../../types';

/**
 * Pure tree helpers for the workflow step tree. Extracted verbatim from the
 * original WorkflowGraphEditor so behaviour is preserved and these can be
 * unit-tested in isolation.
 */

export type PathPart = number | 'then' | 'else' | 'body';
export type StepPath = PathPart[];

export const SLOTS: Array<'then' | 'else' | 'body'> = ['then', 'else', 'body'];

export function pathKey(path: StepPath): string {
  return path.join('.');
}

export function parsePath(key: string): StepPath {
  return key
    .split('.')
    .map((tok) => (tok === 'then' || tok === 'else' || tok === 'body' ? tok : Number(tok))) as StepPath;
}

export function getList(steps: WorkflowStep[], listPath: StepPath): WorkflowStep[] {
  let list = steps;
  let node: WorkflowStep | undefined;
  for (const tok of listPath) {
    if (typeof tok === 'number') node = list[tok];
    else list = (node?.[tok] as WorkflowStep[] | undefined) ?? [];
  }
  return list;
}

export function locate(steps: WorkflowStep[], path: StepPath): WorkflowStep | null {
  let list = steps;
  let node: WorkflowStep | undefined;
  for (const tok of path) {
    if (typeof tok === 'number') node = list[tok];
    else list = (node?.[tok] as WorkflowStep[] | undefined) ?? [];
  }
  return node ?? null;
}

export function updateLeaf(
  steps: WorkflowStep[],
  path: StepPath,
  patch: Partial<WorkflowStep>,
): WorkflowStep[] {
  const [head, ...rest] = path;
  if (typeof head !== 'number') return steps;
  const copy = [...steps];
  const node = copy[head];
  if (!node) return steps;
  if (rest.length === 0) {
    copy[head] = { ...node, ...patch };
    return copy;
  }
  const slot = rest[0] as 'then' | 'else' | 'body';
  copy[head] = {
    ...node,
    [slot]: updateLeaf((node[slot] as WorkflowStep[]) ?? [], rest.slice(1), patch),
  };
  return copy;
}

export function removeLeaf(steps: WorkflowStep[], path: StepPath): WorkflowStep[] {
  const [head, ...rest] = path;
  if (typeof head !== 'number') return steps;
  const copy = [...steps];
  if (rest.length === 0) {
    copy.splice(head, 1);
    return copy;
  }
  const slot = rest[0] as 'then' | 'else' | 'body';
  const node = copy[head];
  if (!node) return steps;
  copy[head] = { ...node, [slot]: removeLeaf((node[slot] as WorkflowStep[]) ?? [], rest.slice(1)) };
  return copy;
}

export function setList(steps: WorkflowStep[], listPath: StepPath, next: WorkflowStep[]): WorkflowStep[] {
  if (listPath.length === 0) return next;
  const slot = listPath[listPath.length - 1] as 'then' | 'else' | 'body';
  return updateLeaf(steps, listPath.slice(0, -1), { [slot]: next } as Partial<WorkflowStep>);
}

export function collectAllIds(steps: WorkflowStep[], out: Set<string> = new Set()): Set<string> {
  for (const step of steps) {
    out.add(step.id);
    for (const slot of SLOTS) {
      const kids = step[slot] as WorkflowStep[] | undefined;
      if (kids?.length) collectAllIds(kids, out);
    }
  }
  return out;
}

/**
 * Next system id in the ``id:N`` scheme (scans the whole tree).
 *
 * Defaults to ``agentic`` ("Let AI do it"): it is valid with no extra params, so
 * a brand-new blank workflow (and newly added branch/loop children) start in a
 * saveable state instead of showing a validation error immediately.
 */
export function newStep(steps: WorkflowStep[], kind = 'agentic'): WorkflowStep {
  let max = 0;
  for (const id of collectAllIds(steps)) {
    const match = /^id:(\d+)$/.exec(id);
    if (match) max = Math.max(max, Number(match[1]));
  }
  return { id: `id:${max + 1}`, kind, do: '', params: {}, mode: 'auto' };
}

/**
 * Materialise an explicit ``next`` chain ONLY when the list isn't wired yet.
 * Once wired (any step has ``next``), existing links are preserved so adding a
 * node or connecting/disconnecting one edge never rewires the others.
 */
export function wireList(list: WorkflowStep[]): WorkflowStep[] {
  if (list.some((step) => step.next)) return list;
  return list.map((step, index) => ({
    ...step,
    next: index + 1 < list.length ? list[index + 1]!.id : '',
  }));
}

/** Validate the whole tree; returns human-readable errors with a step path. */
export function validateTree(steps: WorkflowStep[]): string[] {
  const errors: string[] = [];
  const ids = new Set<string>();
  const walk = (list: WorkflowStep[], scope: string) => {
    list.forEach((step, index) => {
      const at = `${scope}/${step.id || `#${index + 1}`}`;
      if (!step.id) errors.push(`${at}: id is required`);
      else if (ids.has(step.id)) errors.push(`${at}: duplicate id '${step.id}'`);
      else ids.add(step.id);
      if (!KIND_META[step.kind]) errors.push(`${at}: unknown kind '${step.kind}'`);
      if (step.kind === 'branch' && (!step.when || !(step.then ?? []).length))
        errors.push(`${at}: branch needs a condition and 'then' steps`);
      if (step.kind === 'loop' && !(step.body ?? []).length) errors.push(`${at}: loop needs body steps`);
      if (step.kind === 'parallel' && !(step.body ?? []).length)
        errors.push(`${at}: parallel needs body steps`);
      SLOTS.forEach((slot) => {
        const children = step[slot] as WorkflowStep[] | undefined;
        if (children?.length) walk(children, `${at}.${slot}`);
      });
    });
  };
  walk(steps, '');
  return errors;
}

// ── params key/value ────────────────────────────────────────────────────
export interface KVRow {
  key: string;
  value: string;
}

export function paramsToRows(params: Record<string, unknown> | undefined): KVRow[] {
  return Object.entries(params ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

export function rowsToParams(rows: KVRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    if (!row.key.trim()) continue;
    try {
      out[row.key] = JSON.parse(row.value);
    } catch {
      out[row.key] = row.value;
    }
  }
  return out;
}
