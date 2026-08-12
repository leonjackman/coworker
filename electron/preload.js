// Preload scripts for Electron
const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  getRuntimeConfig: () => ipcRenderer.invoke('get-runtime-config'),
  updateRuntimeConfig: (payload) => ipcRenderer.invoke('update-runtime-config', payload),
  fetchSettings: () => ipcRenderer.invoke('fetchSettings'),
  saveSettings: (payload) => ipcRenderer.invoke('saveSettings', payload),
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
  goalStatus: (sessionId) => ipcRenderer.invoke('goal-status', sessionId),
  goalPause: (sessionId) => ipcRenderer.invoke('goal-pause', sessionId),
  goalEdit: (payload) => ipcRenderer.invoke('goal-edit', payload),
  goalDelete: (sessionId) => ipcRenderer.invoke('goal-delete', sessionId),
  goalResume: (requestId, sessionId, onEvent, language, signal) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    const detachAbort = () => ipcRenderer.send('abort-chat-stream', requestId);
    if (signal) {
      if (signal.aborted) detachAbort();
      else signal.addEventListener('abort', detachAbort, { once: true });
    }
    return ipcRenderer.invoke('start-goal-resume', { requestId, sessionId, language: language || 'zh' }).finally(() => {
      ipcRenderer.removeListener('chat-stream-event', listener);
      if (signal) signal.removeEventListener('abort', detachAbort);
    });
  },
  createProvider: (payload) => ipcRenderer.invoke('create-provider', payload),
  updateProvider: (providerId, params) => ipcRenderer.invoke('update-provider', { provider_id: providerId, params }),
  deleteProvider: (providerId) => ipcRenderer.invoke('delete-provider', providerId),
  setDefaultProvider: (payload) => ipcRenderer.invoke('set-default-provider', payload),
  testProvider: (payload) => ipcRenderer.invoke('test-provider', payload),
  fetchProviderModels: (payload) => ipcRenderer.invoke('fetch-provider-models', payload),
  listSessions: () => ipcRenderer.invoke('list-sessions'),
  listActiveSessions: () => ipcRenderer.invoke('list-active-sessions'),
  createSession: (payload) => ipcRenderer.invoke('create-session', payload),
  deleteSession: (sessionId) => ipcRenderer.invoke('delete-session', sessionId),
  renameSession: (sessionId, title) => ipcRenderer.invoke('rename-session', { session_id: sessionId, title }),
  generateTitle: (sessionId, firstUserMessage, assistantResponse, language) =>
    ipcRenderer.invoke('generate-title', {
      session_id: sessionId,
      first_user_message: firstUserMessage,
      assistant_response: assistantResponse || '',
      language: language || 'zh',
    }),
  getSession: (sessionId) => ipcRenderer.invoke('get-session', sessionId),
  rollbackMessage: (sessionId, messageId, withCode) =>
    ipcRenderer.invoke('rollback-message', { session_id: sessionId, message_id: messageId, with_code: !!withCode }),
  getRevertPreview: (sessionId, messageId) =>
    ipcRenderer.invoke('get-revert-preview', { session_id: sessionId, message_id: messageId }),
  streamRegenerateMessage: (requestId, sessionId, messageId, onEvent, language) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    return ipcRenderer.invoke('start-regenerate-stream', { requestId, session_id: sessionId, message_id: messageId, language: language || 'zh' }).finally(() => {
      ipcRenderer.removeListener('chat-stream-event', listener);
    });
  },
  streamEditMessage: (requestId, sessionId, messageId, content, onEvent, options, language) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    return ipcRenderer.invoke('start-edit-stream', { requestId, session_id: sessionId, message_id: messageId, content, work_mode: options?.work_mode, autonomy: options?.autonomy, language: language || 'zh' }).finally(() => {
      ipcRenderer.removeListener('chat-stream-event', listener);
    });
  },
  listProjects: () => ipcRenderer.invoke('list-projects'),
  createProject: (payload) => ipcRenderer.invoke('create-project', payload),
  openDirectoryPicker: (options) => ipcRenderer.invoke('open-directory-picker', options),
  renameProject: (projectId, name) => ipcRenderer.invoke('rename-project', { project_id: projectId, name }),
  deleteProject: (projectId) => ipcRenderer.invoke('delete-project', projectId),
  getWorkspaceBranch: (projectId) => ipcRenderer.invoke('get-workspace-branch', projectId),
  listToolAudit: (limit) => ipcRenderer.invoke('list-tool-audit', limit),
  listAgentTraces: (limit) => ipcRenderer.invoke('list-agent-traces', limit),
  listCommandApprovals: () => ipcRenderer.invoke('list-command-approvals'),
  resolveCommandApproval: (approvalId, decision) => ipcRenderer.invoke('resolve-command-approval', { approval_id: approvalId, decision }),
  getSessionChanges: (sessionId) => ipcRenderer.invoke('get-session-changes', sessionId),
  getCurrentDiff: (options = {}) => ipcRenderer.invoke('get-current-diff', options),
  listMcps: () => ipcRenderer.invoke('list-mcps'),
  discoverMcps: () => ipcRenderer.invoke('discover-mcps'),
  createMcp: (payload) => ipcRenderer.invoke('create-mcp', payload),
  updateMcp: (serverId, payload) => ipcRenderer.invoke('update-mcp', { server_id: serverId, ...payload }),
  deleteMcp: (serverId) => ipcRenderer.invoke('delete-mcp', serverId),
  testMcp: (payload) => ipcRenderer.invoke('test-mcp', payload),
  checkMcp: (serverId) => ipcRenderer.invoke('check-mcp', serverId),
  checkAllMcps: () => ipcRenderer.invoke('check-all-mcps'),
  reauthorizeMcp: (serverId) => ipcRenderer.invoke('reauthorize-mcp', serverId),
  listSkills: (enabledOnly) => ipcRenderer.invoke('list-skills', enabledOnly),
  getSkill: (name, command) => ipcRenderer.invoke('get-skill', name, command),
  updateSkill: (name, request) => ipcRenderer.invoke('update-skill', name, request),
  deleteSkill: (name) => ipcRenderer.invoke('delete-skill', name),
  scanSkills: () => ipcRenderer.invoke('scan-skills'),
  validateSkill: (request) => ipcRenderer.invoke('validate-skill', request),
  getMemoryStatus: () => ipcRenderer.invoke('get-memory-status'),
  writeMemoryEntry: (payload) => ipcRenderer.invoke('write-memory-entry', payload),
  removeMemoryEntry: (payload) => ipcRenderer.invoke('remove-memory-entry', payload),
  clearMemoryScope: (payload) => ipcRenderer.invoke('clear-memory-scope', payload),
  listMemoryProposals: () => ipcRenderer.invoke('list-memory-proposals'),
  resolveMemoryProposal: (payload) => ipcRenderer.invoke('resolve-memory-proposal', payload),
  getMemoryFiles: (projectId) => ipcRenderer.invoke('get-memory-files', projectId || ''),
  getMemoryFile: (scope, projectId) => ipcRenderer.invoke('get-memory-file', scope, projectId || ''),
  saveMemoryFile: (payload) => ipcRenderer.invoke('save-memory-file', payload),
  getMemorySettings: () => ipcRenderer.invoke('get-memory-settings'),
  saveMemorySettings: (payload) => ipcRenderer.invoke('save-memory-settings', payload),
  revealInFolder: (path) => ipcRenderer.invoke('reveal-in-folder', path),
  listMarketSources: () => ipcRenderer.invoke('list-market-sources'),
  listMarketCategories: (source) => ipcRenderer.invoke('list-market-categories', source),
  // Forward the whole query object — positional args used to drop `offset` here.
  searchMarketSkills: (query) => ipcRenderer.invoke('search-market-skills', query),
  listHotSkills: (query) => ipcRenderer.invoke('list-hot-skills', query),
  installMarketSkill: (source, slug, owner) =>
    ipcRenderer.invoke('install-market-skill', source, slug, owner ?? null),
});
