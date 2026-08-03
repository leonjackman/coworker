import type { AccessMode } from '../types';

const ACCESS_MODE_KEY = 'coworker-access-mode';

export function getStoredAccessMode(fallback: AccessMode = 'default'): AccessMode {
  try {
    const stored = localStorage.getItem(ACCESS_MODE_KEY);
    return stored === 'full' || stored === 'default' ? stored : fallback;
  } catch {
    return fallback;
  }
}

export function persistAccessMode(mode: AccessMode): void {
  try {
    localStorage.setItem(ACCESS_MODE_KEY, mode);
  } catch {
    // localStorage may be unavailable in restricted renderer contexts.
  }
}