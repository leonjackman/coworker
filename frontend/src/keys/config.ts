/** Platform modifier key: Meta on macOS, Control on other platforms. */
export function modKey(): 'Meta' | 'Control' {
  if (typeof navigator === 'undefined') return 'Control';
  return navigator.platform.toLowerCase().includes('mac') ? 'Meta' : 'Control';
}

export type ShortcutMod = 'Meta' | 'Control' | 'None';

/** A concrete key combination, e.g. Meta + '.' or a bare Escape. */
export interface ShortcutBinding {
  key: string;
  mod: ShortcutMod;
  /** Whether the Shift key must also be held. */
  shift?: boolean;
}

/** Static registry entry — every global shortcut lives here. */
export interface ShortcutDefinition {
  id: string;
  /** i18n key for the shortcut display name. */
  labelKey: string;
  /** i18n key for the shortcut description. */
  descriptionKey: string;
  defaultBinding: ShortcutBinding;
  /** Whether the key must be pressed twice (e.g. Esc Esc to stop). */
  doublePress?: boolean;
}

/**
 * Shortcut registry — all global shortcuts are defined here so that the
 * dispatcher, the settings page and conflict detection share one source of truth.
 * Entries are ordered by functional adjacency (generation → composer → messages
 * → sessions → navigation → panels → settings).
 *
 * Defaults follow mainstream agent app habits (Cursor / VS Code / ChatGPT):
 * - Cmd+.   toggle plan/build (same as opencode)
 * - Cmd+Enter interject, Esc+Esc stop, Cmd+Shift+R regenerate
 * - Cmd+L   focus composer, Cmd+Shift+U attach, Cmd+/ cycle autonomy
 * - Cmd+Shift+E edit last user msg, Cmd+Shift+C copy last response
 * - Cmd+N   new chat, Cmd+Shift+N new project
 * - Esc     back to chat (single) / stop generation (double)
 * - Cmd+1..3 view switching (providers / mcp / skills)
 * - Cmd+B   sidebar, Cmd+\ right panel, Cmd+J bottom panel, Cmd+, settings
 */
export const SHORTCUT_REGISTRY: readonly ShortcutDefinition[] = [
  {
    id: 'toggle-work-mode',
    labelKey: 'shortcuts.toggle_work_mode',
    descriptionKey: 'shortcuts.toggle_work_mode_desc',
    defaultBinding: { key: '.', mod: modKey() },
  },
  {
    id: 'send-message',
    labelKey: 'shortcuts.send_message',
    descriptionKey: 'shortcuts.send_message_desc',
    defaultBinding: { key: 'Enter', mod: modKey() },
  },
  {
    id: 'stop-agent',
    labelKey: 'shortcuts.stop_agent',
    descriptionKey: 'shortcuts.stop_agent_desc',
    defaultBinding: { key: 'Escape', mod: 'None' },
    doublePress: true,
  },
  {
    id: 'regenerate',
    labelKey: 'shortcuts.regenerate',
    descriptionKey: 'shortcuts.regenerate_desc',
    defaultBinding: { key: 'r', mod: modKey(), shift: true },
  },
  {
    id: 'focus-input',
    labelKey: 'shortcuts.focus_input',
    descriptionKey: 'shortcuts.focus_input_desc',
    defaultBinding: { key: 'l', mod: modKey() },
  },
  {
    id: 'attach-file',
    labelKey: 'shortcuts.attach_file',
    descriptionKey: 'shortcuts.attach_file_desc',
    defaultBinding: { key: 'u', mod: modKey(), shift: true },
  },
  {
    id: 'toggle-autonomy',
    labelKey: 'shortcuts.toggle_autonomy',
    descriptionKey: 'shortcuts.toggle_autonomy_desc',
    defaultBinding: { key: '/', mod: modKey() },
  },
  {
    id: 'edit-last-user-message',
    labelKey: 'shortcuts.edit_last_user_message',
    descriptionKey: 'shortcuts.edit_last_user_message_desc',
    defaultBinding: { key: 'e', mod: modKey(), shift: true },
  },
  {
    id: 'copy-last-response',
    labelKey: 'shortcuts.copy_last_response',
    descriptionKey: 'shortcuts.copy_last_response_desc',
    defaultBinding: { key: 'c', mod: modKey(), shift: true },
  },
  {
    id: 'new-chat',
    labelKey: 'shortcuts.new_chat',
    descriptionKey: 'shortcuts.new_chat_desc',
    defaultBinding: { key: 'n', mod: modKey() },
  },
  {
    id: 'new-project',
    labelKey: 'shortcuts.new_project',
    descriptionKey: 'shortcuts.new_project_desc',
    defaultBinding: { key: 'n', mod: modKey(), shift: true },
  },
  {
    id: 'view-chat',
    labelKey: 'shortcuts.view_chat',
    descriptionKey: 'shortcuts.view_chat_desc',
    defaultBinding: { key: 'Escape', mod: 'None' },
  },
  {
    id: 'view-providers',
    labelKey: 'shortcuts.view_providers',
    descriptionKey: 'shortcuts.view_providers_desc',
    defaultBinding: { key: '1', mod: modKey() },
  },
  {
    id: 'view-mcp',
    labelKey: 'shortcuts.view_mcp',
    descriptionKey: 'shortcuts.view_mcp_desc',
    defaultBinding: { key: '2', mod: modKey() },
  },
  {
    id: 'view-skills',
    labelKey: 'shortcuts.view_skills',
    descriptionKey: 'shortcuts.view_skills_desc',
    defaultBinding: { key: '3', mod: modKey() },
  },
  {
    id: 'open-dashboard',
    labelKey: 'shortcuts.open_dashboard',
    descriptionKey: 'shortcuts.open_dashboard_desc',
    defaultBinding: { key: 'o', mod: modKey() },
  },
  {
    id: 'toggle-sidebar',
    labelKey: 'shortcuts.toggle_sidebar',
    descriptionKey: 'shortcuts.toggle_sidebar_desc',
    defaultBinding: { key: 'b', mod: modKey() },
  },
  {
    id: 'toggle-right-panel',
    labelKey: 'shortcuts.toggle_right_panel',
    descriptionKey: 'shortcuts.toggle_right_panel_desc',
    defaultBinding: { key: '\\', mod: modKey() },
  },
  {
    id: 'toggle-bottom-panel',
    labelKey: 'shortcuts.toggle_bottom_panel',
    descriptionKey: 'shortcuts.toggle_bottom_panel_desc',
    defaultBinding: { key: 'j', mod: modKey() },
  },
  {
    id: 'open-settings',
    labelKey: 'shortcuts.open_settings',
    descriptionKey: 'shortcuts.open_settings_desc',
    defaultBinding: { key: ',', mod: modKey() },
  },
];
