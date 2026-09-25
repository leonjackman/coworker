import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { SoundProvider } from './components/sound-provider';
import { CanvasEditorWindow } from './components/CanvasEditorWindow';
import { applyTheme, getThemeSettings } from './lib/theme';

const canvasMatch = /(?:^|[#&])canvas=([^&]+)/.exec(window.location.hash);
const canvasName = canvasMatch?.[1] ? decodeURIComponent(canvasMatch[1]) : null;

// Apply the persisted theme before first paint so the window matches the app.
if (canvasName) {
  applyTheme(getThemeSettings());
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {canvasName ? (
      <CanvasEditorWindow name={canvasName} />
    ) : (
      <SoundProvider>
        <App />
      </SoundProvider>
    )}
  </React.StrictMode>
);
