import { useSyncExternalStore } from 'react';
import { modKey, SHORTCUT_REGISTRY, type ShortcutBinding, type ShortcutDefinition } from './config';

/**
 * Per-shortcut user override. `binding === undefined` means the default binding
 * is still in effect; `enabled` controls whether the shortcut fires at all.
 */
interface ShortcutOverride {
  enabled: boolean;
  binding?: ShortcutBinding;
}

type OverrideMap = Record<string, ShortcutOverride>;

const STORAGE_KEY = 'cw.shortcuts.overrides';

function loadOverrides(): OverrideMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as OverrideMap;
  } catch {
    // storage unavailable — start from defaults
  }
  return {};
}

let overrides: OverrideMap = loadOverrides();
const listeners = new Set<() => void>();

function save(next: OverrideMap) {
  overrides = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
  } catch {
    // ignore
  }
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      // ignore
    }
  }
}

export function subscribeShortcutsChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

let recordingShortcutId: string | null = null;

/** Mark a shortcut as being re-bound right now (pauses all global shortcuts). */
export function setShortcutRecording(id: string | null): void {
  recordingShortcutId = id;
}

export function isShortcutRecording(): boolean {
  return recordingShortcutId !== null;
}

/** Stable reference that only changes when an override is saved. */
export function getShortcutsSnapshot(): Readonly<OverrideMap> {
  return overrides;
}

export interface EffectiveShortcut {
  enabled: boolean;
  binding: ShortcutBinding;
}

/** Effective configuration for a shortcut, applying user overrides on top of defaults. */
export function getEffectiveShortcut(id: string): EffectiveShortcut {
  const definition = SHORTCUT_REGISTRY.find((entry) => entry.id === id);
  if (!definition) return { enabled: true, binding: { key: '', mod: modKey() } };
  const override = overrides[id];
  return {
    enabled: override?.enabled ?? true,
    binding: override?.binding ?? definition.defaultBinding,
  };
}

export function setShortcutEnabled(id: string, enabled: boolean): void {
  if (!SHORTCUT_REGISTRY.some((entry) => entry.id === id)) return;
  const current = overrides[id];
  const override: ShortcutOverride = current ? { ...current, enabled } : { enabled };
  save({ ...overrides, [id]: override });
}

/** Set a custom binding, or pass `null` to restore the default binding. */
export function setShortcutBinding(id: string, binding: ShortcutBinding | null): void {
  if (!SHORTCUT_REGISTRY.some((entry) => entry.id === id)) return;
  const current = overrides[id];
  if (binding === null) {
    if (!current) return;
    const { binding: _removed, ...rest } = current;
    if (Object.keys(rest).length === 0) {
      const next = { ...overrides };
      delete next[id];
      save(next);
    } else {
      const override: ShortcutOverride = rest;
      save({ ...overrides, [id]: override });
    }
    return;
  }
  const override: ShortcutOverride = current ? { ...current, binding } : { enabled: true, binding };
  save({ ...overrides, [id]: override });
}

/** True when a custom binding that actually differs from the default exists. */
export function hasCustomBinding(id: string): boolean {
  const override = overrides[id];
  if (!override?.binding) return false;
  const definition = SHORTCUT_REGISTRY.find((entry) => entry.id === id);
  if (!definition) return false;
  return !bindingsEqual(override.binding, definition.defaultBinding);
}

export function bindingsEqual(a: ShortcutBinding, b: ShortcutBinding): boolean {
  return keysMatch(a.key, b.key) && a.mod === b.mod && (a.shift ?? false) === (b.shift ?? false);
}

/** Enforce strict matching: expected modifiers present and no extra modifier pressed. */
export function eventMatchesBinding(event: KeyboardEvent, binding: ShortcutBinding): boolean {
  // With Shift held, event.key is the shifted character (e.g. 'N' for n), so
  // compare single characters case-insensitively; the shift flag still enforces
  // whether Shift must actually be held.
  if (!keysMatch(event.key, binding.key)) return false;
  const shift = binding.shift ?? false;
  if (shift !== event.shiftKey) return false;
  if (binding.mod === 'None') {
    return !event.metaKey && !event.ctrlKey && !event.altKey;
  }
  if (binding.mod === 'Meta') {
    return event.metaKey && !event.ctrlKey && !event.altKey;
  }
  return event.ctrlKey && !event.metaKey && !event.altKey;
}

function keysMatch(a: string, b: string): boolean {
  if (a === b) return true;
  if (a.length === 1 && b.length === 1) return a.toLowerCase() === b.toLowerCase();
  return false;
}

const MODIFIER_KEYS = new Set(['Meta', 'Control', 'Shift', 'Alt']);
const SAFE_PLAIN_KEYS = new Set([
  'Escape',
  'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
  'F13', 'F14', 'F15', 'F16', 'F17', 'F18', 'F19', 'F20', 'F21', 'F22', 'F23', 'F24',
]);

/**
 * Parse a keydown into a bindable combination. Requires Meta/Ctrl (Shift is
 * captured as part of the combo) plus a real (non-modifier) key; bare keys are
 * only allowed for a small safe set (Escape / function keys). Returns `null`
 * otherwise so the recorder keeps waiting for the final key.
 */
export function bindingFromKeyEvent(event: KeyboardEvent): ShortcutBinding | null {
  if (MODIFIER_KEYS.has(event.key)) return null;
  if (event.metaKey) return { key: event.key, mod: 'Meta', shift: event.shiftKey };
  if (event.ctrlKey) return { key: event.key, mod: 'Control', shift: event.shiftKey };
  if (SAFE_PLAIN_KEYS.has(event.key)) return { key: event.key, mod: 'None' };
  return null;
}

/** Other enabled shortcuts that share the given binding (excluding `excludeId`). */
export function findConflicts(binding: ShortcutBinding, excludeId: string): ShortcutDefinition[] {
  return SHORTCUT_REGISTRY.filter((definition) => {
    if (definition.id === excludeId) return false;
    const effective = getEffectiveShortcut(definition.id);
    if (!effective.enabled) return false;
    return bindingsEqual(effective.binding, binding);
  });
}

const KEY_LABELS: Record<string, string> = {
  ' ': 'Space',
  ',': ',',
  '.': '.',
  '\\': '\\',
  '`': '`',
  Escape: 'Esc',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  Backspace: '⌫',
  Delete: 'Del',
  Tab: '⇥',
  Enter: '⏎',
  Home: 'Home',
  End: 'End',
  PageUp: 'PgUp',
  PageDown: 'PgDn',
};

/** Human-readable label, e.g. `⌘ + .`, `Ctrl + Shift + U` or `Esc`. */
export function formatBinding(binding: ShortcutBinding): string {
  const shift = binding.shift ? '⇧ ' : '';
  if (binding.mod === 'None') {
    return `${shift}${keyLabel(binding.key)}`.trim();
  }
  const mod = binding.mod === 'Meta' ? '⌘' : 'Ctrl';
  return `${mod} + ${shift}${keyLabel(binding.key)}`;
}

function keyLabel(key: string): string {
  return KEY_LABELS[key] ?? (key.length === 1 ? key.toUpperCase() : key);
}

/** Reactive hook — re-renders whenever a shortcut override changes. */
export function useShortcut(id: string): EffectiveShortcut & { label: string } {
  useSyncExternalStore(subscribeShortcutsChange, getShortcutsSnapshot);
  const effective = getEffectiveShortcut(id);
  return {
    enabled: effective.enabled,
    binding: effective.binding,
    label: formatBinding(effective.binding),
  };
}
