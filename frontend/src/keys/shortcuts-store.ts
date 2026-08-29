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

/** True when a user override (binding or enabled state) exists for the shortcut. */
export function hasShortcutOverride(id: string): boolean {
  return overrides[id] != null;
}

export function bindingsEqual(a: ShortcutBinding, b: ShortcutBinding): boolean {
  return a.key === b.key && a.mod === b.mod;
}

/** Enforce strict matching: the expected modifier present and no other modifier pressed. */
export function eventMatchesBinding(event: KeyboardEvent, binding: ShortcutBinding): boolean {
  if (event.key !== binding.key) return false;
  if (binding.mod === 'Meta') {
    return event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  }
  return event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
}

const MODIFIER_KEYS = new Set(['Meta', 'Control', 'Shift', 'Alt']);

/**
 * Parse a keydown into a bindable combination. Requires at least one of
 * Meta/Ctrl plus a real (non-modifier) key; returns `null` otherwise so the
 * recorder keeps waiting for the final key.
 */
export function bindingFromKeyEvent(event: KeyboardEvent): ShortcutBinding | null {
  if (MODIFIER_KEYS.has(event.key)) return null;
  const mod = event.metaKey ? 'Meta' : event.ctrlKey ? 'Control' : null;
  if (!mod) return null;
  return { key: event.key, mod };
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

/** Human-readable label, e.g. `⌘ + .` or `Ctrl + Space`. */
export function formatBinding(binding: ShortcutBinding): string {
  const mod = binding.mod === 'Meta' ? '⌘' : 'Ctrl';
  const key = KEY_LABELS[binding.key] ?? (binding.key.length === 1 ? binding.key.toUpperCase() : binding.key);
  return `${mod} + ${key}`;
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
