// Preload scripts for Electron
const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  getRuntimeConfig: () => ipcRenderer.invoke('get-runtime-config'),
  updateRuntimeConfig: (payload) => ipcRenderer.invoke('update-runtime-config', payload),
  sendChatMessage: (payload) => ipcRenderer.invoke('send-chat-message', payload),
  streamChatMessage: (requestId, payload, onEvent) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    return ipcRenderer.invoke('start-chat-stream', { requestId, payload }).finally(() => {
      ipcRenderer.removeListener('chat-stream-event', listener);
    });
  },
  abortChatStream: (requestId) => ipcRenderer.send('abort-chat-stream', requestId),
  listProviders: () => ipcRenderer.invoke('list-providers'),
  createProvider: (payload) => ipcRenderer.invoke('create-provider', payload),
  updateProvider: (providerId, params) => ipcRenderer.invoke('update-provider', { provider_id: providerId, params }),
  deleteProvider: (providerId) => ipcRenderer.invoke('delete-provider', providerId),
  setDefaultProvider: (payload) => ipcRenderer.invoke('set-default-provider', payload),
  testProvider: (payload) => ipcRenderer.invoke('test-provider', payload),
  fetchProviderModels: (payload) => ipcRenderer.invoke('fetch-provider-models', payload),
  listSessions: () => ipcRenderer.invoke('list-sessions'),
  createSession: (title) => ipcRenderer.invoke('create-session', title),
  deleteSession: (sessionId) => ipcRenderer.invoke('delete-session', sessionId),
  renameSession: (sessionId, title) => ipcRenderer.invoke('rename-session', { session_id: sessionId, title }),
  getSession: (sessionId) => ipcRenderer.invoke('get-session', sessionId),
  moveSession: (sessionId, projectId) => ipcRenderer.invoke('move-session', { session_id: sessionId, project_id: projectId }),
  listProjects: () => ipcRenderer.invoke('list-projects'),
  createProject: (name) => ipcRenderer.invoke('create-project', name),
  renameProject: (projectId, name) => ipcRenderer.invoke('rename-project', { project_id: projectId, name }),
  deleteProject: (projectId) => ipcRenderer.invoke('delete-project', projectId),
  getWorkspaceTree: () => ipcRenderer.invoke('get-workspace-tree'),
  getWorkspaceDir: (path) => ipcRenderer.invoke('get-workspace-dir', path),
  getWorkspaceFile: (path) => ipcRenderer.invoke('get-workspace-file', path),
  runWorkspaceCommand: (payload) => ipcRenderer.invoke('run-workspace-command', payload),
  listToolAudit: (limit) => ipcRenderer.invoke('list-tool-audit', limit),
  listCommandApprovals: () => ipcRenderer.invoke('list-command-approvals'),
  approveCommand: (approvalId) => ipcRenderer.invoke('approve-command', approvalId),
  denyCommand: (approvalId) => ipcRenderer.invoke('deny-command', approvalId),
});
