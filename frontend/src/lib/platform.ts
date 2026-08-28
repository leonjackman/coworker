/** Returns true when running on macOS. */
export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  return navigator.platform.toLowerCase().includes('mac');
}

/** Platform modifier key label: "⌘" on macOS, "Ctrl+" on other platforms. */
export function modKeyLabel(): '⌘' | 'Ctrl+' {
  return isMac() ? '⌘' : 'Ctrl+';
}
