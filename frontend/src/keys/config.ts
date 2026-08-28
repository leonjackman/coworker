/** Platform modifier key: Meta on macOS, Control on other platforms. */
export function modKey(): 'Meta' | 'Control' {
  if (typeof navigator === 'undefined') return 'Control';
  return navigator.platform.toLowerCase().includes('mac') ? 'Meta' : 'Control';
}

/** Shortcut definitions — all global shortcuts are defined here. */
export const SHORTCUTS = {
  TOGGLE_WORK_MODE: {
    key: '.',
    mod: modKey(),
    label: modKey() === 'Meta' ? '⌘ + .' : 'Ctrl + .',
  },
} as const;
