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
  streamApprovalEvents: (requestId, resumeId, onEvent) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('approval-stream-event', listener);
    return ipcRenderer.invoke('start-approval-stream', { requestId, resumeId }).finally(() => {
      ipcRenderer.removeListener('approval-stream-event', listener);
    });
  },
  listProviders: () => ipcRenderer.invoke('list-providers'),
  createProvider: (payload) => ipcRenderer.invoke('create-provider', payload),
  updateProvider: (providerId, params) => ipcRenderer.invoke('update-provider', { provider_id: providerId, params }),
  deleteProvider: (providerId) => ipcRenderer.invoke('delete-provider', providerId),
  setDefaultProvider: (payload) => ipcRenderer.invoke('set-default-provider', payload),
  testProvider: (payload) => ipcRenderer.invoke('test-provider', payload),
  fetchProviderModels: (payload) => ipcRenderer.invoke('fetch-provider-models', payload),
  listSessions: () => ipcRenderer.invoke('list-sessions'),
  createSession: (payload) => ipcRenderer.invoke('create-session', payload),
  deleteSession: (sessionId) => ipcRenderer.invoke('delete-session', sessionId),
  renameSession: (sessionId, title) => ipcRenderer.invoke('rename-session', { session_id: sessionId, title }),
  generateTitle: (sessionId, firstUserMessage) =>
    ipcRenderer.invoke('generate-title', { session_id: sessionId, first_user_message: firstUserMessage }),
  getSession: (sessionId) => ipcRenderer.invoke('get-session', sessionId),
  listProjects: () => ipcRenderer.invoke('list-projects'),
  createProject: (payload) => ipcRenderer.invoke('create-project', payload),
  openDirectoryPicker: (options) => ipcRenderer.invoke('open-directory-picker', options),
  renameProject: (projectId, name) => ipcRenderer.invoke('rename-project', { project_id: projectId, name }),
  deleteProject: (projectId) => ipcRenderer.invoke('delete-project', projectId),
  getWorkspaceTree: (projectId) => ipcRenderer.invoke('get-workspace-tree', projectId),
  getWorkspaceDir: (path, projectId) => ipcRenderer.invoke('get-workspace-dir', { path, project_id: projectId || '' }),
  getWorkspaceFile: (path, projectId) => ipcRenderer.invoke('get-workspace-file', { path, project_id: projectId || '' }),
  runWorkspaceCommand: (payload) => ipcRenderer.invoke('run-workspace-command', payload),
  listToolAudit: (limit) => ipcRenderer.invoke('list-tool-audit', limit),
  listAgentTraces: (limit) => ipcRenderer.invoke('list-agent-traces', limit),
  listCommandApprovals: () => ipcRenderer.invoke('list-command-approvals'),
  resolveCommandApproval: (approvalId, decision) => ipcRenderer.invoke('resolve-command-approval', { approval_id: approvalId, decision }),
});
