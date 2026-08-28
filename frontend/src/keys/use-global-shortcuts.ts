import { useEffect, useRef } from 'react';
import { SHORTCUTS } from './config';

interface UseGlobalShortcutsOptions {
  onToggleWorkMode?: () => void;
}

export function useGlobalShortcuts({ onToggleWorkMode }: UseGlobalShortcutsOptions) {
  const onToggleWorkModeRef = useRef(onToggleWorkMode);
  onToggleWorkModeRef.current = onToggleWorkMode;

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const { key, metaKey, ctrlKey } = event;

      // Cmd+. on macOS or Ctrl+. on other platforms
      if (key !== SHORTCUTS.TOGGLE_WORK_MODE.key) return;
      const modPressed = (metaKey && SHORTCUTS.TOGGLE_WORK_MODE.mod === 'Meta')
        || (ctrlKey && SHORTCUTS.TOGGLE_WORK_MODE.mod === 'Control');
      if (!modPressed) return;

      event.preventDefault();
      event.stopPropagation();

      if (onToggleWorkModeRef.current) {
        onToggleWorkModeRef.current();
      }
    };

    window.addEventListener('keydown', handler, true); // capture phase to always fire first
    return () => window.removeEventListener('keydown', handler, true);
  }, []);
}
