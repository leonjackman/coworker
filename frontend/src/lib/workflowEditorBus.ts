import { useEffect, useState } from 'react';

/**
 * Cross-surface controller for the standalone workflow editor.
 *
 * The editor is a self-contained, full-window surface (its own design language)
 * that is hosted above the cw shell. Any surface can open it by name / new, and
 * any surface can listen for "workflows changed" to refresh in real time after
 * the editor autosaves.
 */
export interface WorkflowEditorTarget {
  name: string;
  isNew: boolean;
}

let current: WorkflowEditorTarget | null = null;

const editorListeners = new Set<() => void>();
const changeListeners = new Set<() => void>();

function notifyEditor() {
  for (const listener of editorListeners) listener();
}

function notifyChanged() {
  for (const listener of changeListeners) listener();
}

/** Force the in-renderer full-window editor (used inside the editor window). */
export function openWorkflowEditorLocal(target: WorkflowEditorTarget): void {
  current = { name: target.name, isNew: target.isNew };
  notifyEditor();
}

/**
 * Open the editor. In the Electron app this opens a dedicated OS window (the
 * A2 shell); in the browser it falls back to the in-app full-window editor.
 */
export function openWorkflowEditor(target: WorkflowEditorTarget): void {
  const api = (
    window as unknown as {
      electronAPI?: { openWorkflowEditor?: (p: WorkflowEditorTarget) => Promise<unknown> | void };
    }
  ).electronAPI;
  const call = api?.openWorkflowEditor;
  if (call) {
    // Prefer the dedicated OS window, but fall back to the in-app editor if the
    // IPC is unavailable/failed (e.g. an older build without the handler).
    Promise.resolve(call({ name: target.name, isNew: target.isNew })).catch(() => openWorkflowEditorLocal(target));
    return;
  }
  openWorkflowEditorLocal(target);
}

/** Emit a workflow change to other windows (Electron) as well as locally. */
export function broadcastWorkflowsChanged(): void {
  const api = (window as unknown as { electronAPI?: { emitWorkflowChanged?: () => void } }).electronAPI;
  api?.emitWorkflowChanged?.();
}

export function closeWorkflowEditor(): void {
  current = null;
  notifyEditor();
}

export function getWorkflowEditorTarget(): WorkflowEditorTarget | null {
  return current;
}

export function subscribeWorkflowEditor(listener: () => void): () => void {
  editorListeners.add(listener);
  return () => editorListeners.delete(listener);
}

/** Emitted after the editor persists a workflow, so cw refreshes immediately. */
export function emitWorkflowsChanged(): void {
  notifyChanged();
}

export function subscribeWorkflowsChanged(listener: () => void): () => void {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

/** React hook: the currently-open editor target (or null). */
export function useWorkflowEditorTarget(): WorkflowEditorTarget | null {
  const [target, setTarget] = useState<WorkflowEditorTarget | null>(current);
  useEffect(() => subscribeWorkflowEditor(() => setTarget(current)), []);
  return target;
}
