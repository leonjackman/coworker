
Plan: macOS 半透明磨砂窗口 (Acrylic/Vibrancy)

## Problem

Current translucent theme only uses CSS-level transparency. The window itself (`electron/main.js:186`) uses `backgroundColor: '#111417'` which is fully opaque. CSS `backdrop-filter: blur()` only blurs content *inside* the window, not the desktop behind it.

## Solution: System-level semi-transparent window with macOS Vibrancy

### File 1: `electron/main.js`

Add transparent window support and IPC handler:

```js
let currentVibrancy = null;

function setWindowVibrancy(vibrancy) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    currentVibrancy = vibrancy;
    mainWindow.setVibrancy(vibrancy);
    if (vibrancy === null) {
      mainWindow.setBackgroundColor({ backgroundColor: '#111417' });
    }
  }
}

// In createWindow(), add these to BrowserWindow options:
mainWindow = new BrowserWindow({
  // ... existing options ...
  transparent: true,               // NEW: enables transparent window
  backgroundColor: '#00000000',    // NEW: 8-digit RGBA (fully transparent)
  // ... rest of existing options ...
});

mainWindow.setBackgroundColor({ backgroundColor: '#00000000' }); // After window creation

// NEW: IPC handler for frontend toggle
ipcMain.on('update-translucent', (event, enabled) => {
  if (process.platform !== 'darwin') return;
  setWindowVibrancy(enabled ? 'under-window' : null);
});

// Fallback: if vibrancy is unsupported in Electron 43, window is still transparent
```

### File 2: `electron/preload.js`

Add IPC bridge:

```js
contextBridge.exposeInMainWorld('electronAPI', {
  // ... existing APIs ...
  updateTranslucent: (enabled) => ipcRenderer.send('update-translucent', enabled),
});
```

### File 3: `frontend/src/lib/theme.ts`

Notify Electron main process when toggling translucent:

```typescript
export function applyTheme(settings: ThemeSettings): void {
  // ... existing code ...
  
  // Notify host about translucent state (macOS vibrancy)
  if (typeof (window as any).electronAPI?.updateTranslucent === 'function') {
    (window as any).electronAPI.updateTranslucent(settings.translucent);
  }
}
```

Add global type declaration at top of file:

```typescript
declare global {
  interface Window {
    electronAPI: {
      // existing types inferred
      updateTranslucent?: (enabled: boolean) => void;
    };
  }
}
```

### File 4: `frontend/src/App.css`

Transparent window needs body to not block the view:

```css
/* Add to existing :root[data-translucent="true"] block */
:root[data-translucent="true"] body {
  background: transparent !important;
  /* remove decorative radial gradients - system blur handles the background */
}

:root[data-translucent="true"] .workspace-page {
  background: transparent;
}

/* settings-card already has backdrop-filter via CSS variables, no change needed */
```

## Non-macOS Fallback

- Window still set to `transparent: true` on Windows/Linux
- No `setVibrancy()` call (unsupported on these platforms)
- Current CSS-level semi-transparency remains as fallback
- Frontend only calls `updateTranslucent` on darwin

## Verification

1. Launch app, Settings → Translucent Theme → toggle on
2. Window should show frosted glass effect, desktop background visible (blurred)
3. Toggle off → window returns to opaque dark background
4. No errors on Windows/Linux (graceful no-op)
