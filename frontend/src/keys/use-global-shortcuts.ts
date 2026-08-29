import { useEffect, useRef } from 'react';
import { SHORTCUT_REGISTRY } from './config';
import { eventMatchesBinding, getEffectiveShortcut } from './shortcuts-store';

export type GlobalShortcutHandlers = Record<string, (event: KeyboardEvent) => void>;

/**
 * Registry-driven global shortcut dispatcher. Handlers are keyed by the
 * shortcut id defined in `SHORTCUT_REGISTRY`; enabled state and custom
 * bindings are read live from the shortcuts store.
 */
export function useGlobalShortcuts(handlers: GlobalShortcutHandlers) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      for (const definition of SHORTCUT_REGISTRY) {
        const effective = getEffectiveShortcut(definition.id);
        if (!effective.enabled) continue;
        const callback = handlersRef.current[definition.id];
        if (!callback) continue;
        if (!eventMatchesBinding(event, effective.binding)) continue;
        event.preventDefault();
        event.stopPropagation();
        callback(event);
      }
    };

    window.addEventListener('keydown', handler, true); // capture phase to always fire first
    return () => window.removeEventListener('keydown', handler, true);
  }, []);
}
