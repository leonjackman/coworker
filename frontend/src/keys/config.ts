/** Platform modifier key: Meta on macOS, Control on other platforms. */
export function modKey(): 'Meta' | 'Control' {
  if (typeof navigator === 'undefined') return 'Control';
  return navigator.platform.toLowerCase().includes('mac') ? 'Meta' : 'Control';
}

export type ShortcutMod = 'Meta' | 'Control';

/** A concrete key combination, e.g. Meta + '.'. */
export interface ShortcutBinding {
  key: string;
  mod: ShortcutMod;
}

/** Static registry entry — every global shortcut lives here. */
export interface ShortcutDefinition {
  id: string;
  /** i18n key for the shortcut display name. */
  labelKey: string;
  /** i18n key for the shortcut description. */
  descriptionKey: string;
  defaultBinding: ShortcutBinding;
}

/**
 * Shortcut registry — all global shortcuts are defined here so that the
 * dispatcher, the settings page and conflict detection share one source of truth.
 */
export const SHORTCUT_REGISTRY: readonly ShortcutDefinition[] = [
  {
    id: 'toggle-work-mode',
    labelKey: 'shortcuts.toggle_work_mode',
    descriptionKey: 'shortcuts.toggle_work_mode_desc',
    defaultBinding: { key: '.', mod: modKey() },
  },
];
