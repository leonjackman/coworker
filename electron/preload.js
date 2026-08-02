// Preload scripts for Electron
const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  getRuntimeConfig: () => ipcRenderer.invoke('get-runtime-config'),
  sendChatMessage: (payload) => ipcRenderer.invoke('send-chat-message', payload),
});
