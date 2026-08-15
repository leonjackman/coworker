import { useCallback, useEffect, useState } from 'react';
import type { UpdateStateSnapshot } from '../types';

const IDLE_SNAPSHOT: UpdateStateSnapshot = {
  isDev: false,
  enabled: true,
  skippedVersion: null,
  currentVersion: '',
  state: 'idle',
  availableVersion: null,
  releaseNotes: null,
  progress: null,
  errorMessage: null,
};

export interface UpdateCenter {
  state: UpdateStateSnapshot;
  hasApi: boolean;
  check: () => Promise<void>;
  download: () => Promise<void>;
  install: () => Promise<void>;
  skip: () => Promise<void>;
  clearSkip: () => Promise<void>;
  setAutoUpdate: (enabled: boolean) => Promise<void>;
}

/**
 * Single source of truth for auto-update in the renderer.
 * The Electron main process owns the authoritative state and pushes
 * snapshots over `app:update-state`; this hook mirrors them and exposes
 * the user actions (check / download / install / skip).
 */
export function useUpdateCenter(): UpdateCenter {
  const [state, setState] = useState<UpdateStateSnapshot>(IDLE_SNAPSHOT);
  const hasApi = typeof window.electronAPI?.getUpdateState === 'function';

  useEffect(() => {
    if (!hasApi) return;
    let alive = true;
    window.electronAPI!.getUpdateState().then((snapshot) => {
      if (alive) setState(snapshot);
    }).catch(() => { /* keep defaults */ });

    const unsubscribe = window.electronAPI!.onUpdateState((snapshot) => {
      setState(snapshot);
    });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [hasApi]);

  const check = useCallback(async () => {
    if (!hasApi) return;
    await window.electronAPI!.checkForUpdates();
  }, [hasApi]);

  const download = useCallback(async () => {
    if (!hasApi) return;
    await window.electronAPI!.downloadUpdate();
  }, [hasApi]);

  const install = useCallback(async () => {
    if (!hasApi) return;
    await window.electronAPI!.installUpdate();
  }, [hasApi]);

  const skip = useCallback(async () => {
    if (!hasApi) return;
    await window.electronAPI!.skipVersion();
  }, [hasApi]);

  const clearSkip = useCallback(async () => {
    if (!hasApi) return;
    await window.electronAPI!.clearSkipVersion();
  }, [hasApi]);

  const setAutoUpdate = useCallback(async (enabled: boolean) => {
    if (!hasApi) return;
    await window.electronAPI!.setAutoUpdate(enabled);
  }, [hasApi]);

  return { state, hasApi, check, download, install, skip, clearSkip, setAutoUpdate };
}
