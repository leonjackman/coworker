import { useEffect } from 'react';
import { type ShortcutBinding } from './config';
import { formatBinding, getEffectiveShortcut, subscribeShortcutsChange } from './shortcuts-store';

// Map a rendered-key name to the Electron accelerator key name. Electron uses
// the ARROW names (Up/Down/…) and 'Escape'; single chars/letters/digits pass
// through (letters uppercased).
const KEY_ACCEL: Record<string, string> = {
  Escape: 'Escape',
  Enter: 'Enter',
  Return: 'Enter',
  Tab: 'Tab',
  Backspace: 'Backspace',
  Delete: 'Delete',
  ' ': 'Space',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  Home: 'Home',
  End: 'End',
  PageUp: 'PageUp',
  PageDown: 'PageDown',
};

function keyAccel(key: string): string {
  if (KEY_ACCEL[key]) return KEY_ACCEL[key];
  if (key.length === 1) return key.toUpperCase();
  return key;
}

/** e.g. {key:'Escape', mod:'Meta', shift:true} -> 'Command+Shift+Escape'. */
export function bindingToAccelerator(binding: ShortcutBinding): string {
  const mods: string[] = [];
  if (binding.mod === 'Meta') mods.push('Command');
  else if (binding.mod === 'Control') mods.push('Control');
  if (binding.shift) mods.push('Shift');
  const key = keyAccel(binding.key);
  if (!key) return '';
  return [...mods, key].join('+');
}

export interface ComputerStopShortcutPayload {
  enabled: boolean;
  accelerator: string;
  label: string;
}

/** Effective "stop computer control" shortcut (binding accel + human label). */
export function getComputerStopShortcut(): ComputerStopShortcutPayload {
  const effective = getEffectiveShortcut('stop-computer-control');
  const binding = effective.binding;
  return {
    enabled: effective.enabled,
    accelerator: bindingToAccelerator(binding),
    label: formatBinding(binding),
  };
}

/**
 * Push the user-configured "stop computer control" shortcut to the Electron main
 * process: main re-registers the OS globalShortcut to match and the on-screen
 * overlay pill text reflects the same combo (never hardcoded to ⌘⇧⎋).
 */
export function useComputerStopShortcutSyncer(): void {
  useEffect(() => {
    const push = () => {
      const payload = getComputerStopShortcut();
      window.electronAPI?.setComputerStopShortcut?.(payload);
    };
    push();
    return subscribeShortcutsChange(push);
  }, []);
}
