import { useEffect, useRef } from 'react';
import { SHORTCUT_REGISTRY } from './config';
import { eventMatchesBinding, getEffectiveShortcut, isShortcutRecording } from './shortcuts-store';

/**
 * A shortcut handler returns `true` when it handled the key (the dispatcher
 * then prevents the default), or `false`/`undefined` to let the key fall
 * through (e.g. a conditional shortcut like Esc-to-stop).
 */
export type GlobalShortcutHandlers = Record<string, (event: KeyboardEvent) => boolean | void>;

/** The embedded xterm owns the keyboard while focused (copy/paste, shell keys). */
function isInsideXterm(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest('.xterm'));
}

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
      // Leave terminal keystrokes alone (Ctrl+C / Ctrl+K / … belong to the shell).
      if (isInsideXterm(event.target)) return;
      // While a shortcut is being re-bound in the settings page, the recorder
      // owns the keyboard (Esc cancels, the combo is captured, nothing fires).
      if (isShortcutRecording()) return;

      for (const definition of SHORTCUT_REGISTRY) {
        const effective = getEffectiveShortcut(definition.id);
        if (!effective.enabled) continue;
        const callback = handlersRef.current[definition.id];
        if (!callback) continue;
        if (!eventMatchesBinding(event, effective.binding)) continue;
        if (callback(event)) {
          event.preventDefault();
          event.stopPropagation();
        }
      }
    };

    window.addEventListener('keydown', handler, true); // capture phase to always fire first
    return () => window.removeEventListener('keydown', handler, true);
  }, []);
}
