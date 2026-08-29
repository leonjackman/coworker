export { useGlobalShortcuts } from './use-global-shortcuts';
export { SHORTCUT_REGISTRY, type ShortcutBinding, type ShortcutDefinition } from './config';
export {
  bindingFromKeyEvent,
  bindingsEqual,
  eventMatchesBinding,
  findConflicts,
  formatBinding,
  getEffectiveShortcut,
  getShortcutsSnapshot,
  hasCustomBinding,
  isShortcutRecording,
  setShortcutBinding,
  setShortcutEnabled,
  setShortcutRecording,
  subscribeShortcutsChange,
  useShortcut,
} from './shortcuts-store';
