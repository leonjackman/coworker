import React, { useEffect, useRef } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { SoundProvider } from './components/sound-provider';
import { WorkflowEditorApp } from './components/workflows/WorkflowEditorApp';
import { openWorkflowEditorLocal, useWorkflowEditorTarget } from './lib/workflowEditorBus';
import { applyTheme, getThemeSettings } from './lib/theme';

// Apply the user's stored theme to THIS document before the first paint. A
// separate editor window is its own renderer (App never mounts there), so
// without this it would fall back to the default theme instead of the theme
// chosen in the main cw window. Theme settings live in shared localStorage, so
// we also re-apply on cross-window writes and on system light/dark changes.
function applyStoredTheme(): void {
  try {
    applyTheme(getThemeSettings());
  } catch {
    // A theme failure must never block the UI.
  }
}

applyStoredTheme();
window.addEventListener('storage', (event) => {
  if (event.key === 'coworker-theme-settings') applyStoredTheme();
});
try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyStoredTheme);
} catch {
  // matchMedia may be unavailable in restricted renderer contexts.
}

// The standalone editor can run in its own Electron window: it is the SAME
// bundle booted with `?window=workflow-editor` (optionally carrying the target
// workflow name). In that mode we render ONLY the editor and close the window
// when the user dismisses it.
const params = new URLSearchParams(window.location.search);
const isEditorWindow = params.get('window') === 'workflow-editor';

function EditorWindow() {
  const target = useWorkflowEditorTarget();
  // Only close the window on a real open → closed transition. Using a plain
  // `target === null` check would fire on the FIRST render (before the open
  // effect's state has propagated) and immediately close the window — the
  // "flash" bug.
  const hadTargetRef = useRef(false);
  useEffect(() => {
    const name = params.get('name') || '';
    openWorkflowEditorLocal({ name, isNew: params.get('isNew') === '1' || !name });
  }, []);
  useEffect(() => {
    if (target) {
      hadTargetRef.current = true;
      return;
    }
    if (hadTargetRef.current) {
      // The editor was closed → close this dedicated window.
      window.setTimeout(() => window.close(), 0);
    }
  }, [target]);
  return <WorkflowEditorApp mode="window" />;
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <SoundProvider>{isEditorWindow ? <EditorWindow /> : <App />}</SoundProvider>
  </React.StrictMode>,
);
