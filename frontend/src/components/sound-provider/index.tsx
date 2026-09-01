import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { playSound as playSoundRaw, preloadSounds, setGlobalSoundEnabled, isSoundEnabled } from '../../lib/sound';

export type SoundEventType = 'reply_done' | 'reply_error' | 'card_popup' | 'user_pause';

interface SoundContextValue {
  enabled: boolean;
  playSound: (event: SoundEventType) => void;
  toggleEnabled: () => void;
}

const SoundContext = createContext<SoundContextValue | null>(null);

export function SoundProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(isSoundEnabled);

  // Initialize sound buffers once on mount
  useEffect(() => {
    preloadSounds();
  }, []);

  // Keep internal state in sync when user changes setting
  useEffect(() => {
    setGlobalSoundEnabled(enabled);
  }, [enabled]);

  const playSound = useCallback((event: SoundEventType) => {
    playSoundRaw(event as 'reply_done' | 'reply_error' | 'card_popup' | 'user_pause');
  }, []);

  const toggleEnabled = useCallback(() => {
    setEnabled((prev) => !prev);
  }, []);

  return (
    <SoundContext.Provider value={{ enabled, playSound, toggleEnabled }}>
      {children}
    </SoundContext.Provider>
  );
}

export function useSound(): SoundContextValue {
  const ctx = useContext(SoundContext);

  if (!ctx) {
    throw new Error('useSound must be used within <SoundProvider>');
  }

  return ctx;
}
