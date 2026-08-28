// Preload scripts for Electron
const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  getRuntimeConfig: () => ipcRenderer.invoke('get-runtime-config'),
  clipboardReadText: () => ipcRenderer.invoke('clipboard-read-text'),
  clipboardWriteText: (text) => ipcRenderer.invoke('clipboard-write-text', text),
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
  streamWorkerEvents: (requestId, workerRunId, onEvent) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('worker-stream-event', listener);
    return ipcRenderer.invoke('start-worker-stream', { requestId, worker_run_id: workerRunId }).finally(() => {
      ipcRenderer.removeListener('worker-stream-event', listener);
    });
  },
  listProviders: () => ipcRenderer.invoke('list-providers'),
  createProvider: (payload) => ipcRenderer.invoke('create-provider', payload),
  updateProvider: (providerId, params) => ipcRenderer.invoke('update-provider', { provider_id: providerId, params }),
  discoverProviderContext: (providerId) => ipcRenderer.invoke('discover-provider-context', providerId || ''),
  getProviderTemplates: () => ipcRenderer.invoke('get-provider-templates'),
  deleteProvider: (providerId) => ipcRenderer.invoke('delete-provider', providerId),
  setDefaultProvider: (payload) => ipcRenderer.invoke('set-default-provider', payload),
  testProvider: (payload) => ipcRenderer.invoke('test-provider', payload),
  fetchProviderModels: (payload) => ipcRenderer.invoke('fetch-provider-models', payload),
  listSessions: () => ipcRenderer.invoke('list-sessions'),
  listActiveSessions: () => ipcRenderer.invoke('list-active-sessions'),
  createSession: (payload) => ipcRenderer.invoke('create-session', payload),
  deleteSession: (sessionId) => ipcRenderer.invoke('delete-session', sessionId),
  renameSession: (sessionId, title) => ipcRenderer.invoke('rename-session', { session_id: sessionId, title }),
  stopSessionStream: (sessionId) => ipcRenderer.invoke('stop-session-stream', sessionId),
  goalGet: (sessionId) => ipcRenderer.invoke('goal-get', sessionId),
  goalSet: (sessionId, objective, tokenBudget, meta) =>
    ipcRenderer.invoke('goal-set', {
      session_id: sessionId,
      objective,
      ...(tokenBudget != null ? { token_budget: tokenBudget } : {}),
      ...(meta
        ? {
            user_message_id: meta.userMessageId,
            provider: meta.provider || '',
            model: meta.model || '',
            work_mode: meta.workMode || '',
            autonomy: meta.autonomy || '',
          }
        : {}),
    }),
  goalPause: (sessionId) => ipcRenderer.invoke('goal-pause', { session_id: sessionId }),
  goalResume: (sessionId) => ipcRenderer.invoke('goal-resume', { session_id: sessionId }),
  goalClear: (sessionId) => ipcRenderer.invoke('goal-clear', { session_id: sessionId }),
  goalEdit: (sessionId, objective) => ipcRenderer.invoke('goal-edit', { session_id: sessionId, objective }),
  generateTitle: (sessionId, firstUserMessage, assistantResponse, language) =>
    ipcRenderer.invoke('generate-title', {
      session_id: sessionId,
      first_user_message: firstUserMessage,
      assistant_response: assistantResponse || '',
      language: language || 'zh',
    }),
  getSession: (sessionId) => ipcRenderer.invoke('get-session', sessionId),
  getContextUsage: (sessionId, providerId, model) => ipcRenderer.invoke('get-context-usage', sessionId, providerId, model),
  redoMessage: (sessionId, messageId) =>
    ipcRenderer.invoke('redo-message', { session_id: sessionId, message_id: messageId }),
  editMessageBegin: (sessionId, messageId, revertCode) =>
    ipcRenderer.invoke('edit-message-begin', { session_id: sessionId, message_id: messageId, revert_code: revertCode }),
  editMessageCancel: (sessionId, messageId) =>
    ipcRenderer.invoke('edit-message-cancel', { session_id: sessionId, message_id: messageId }),
  streamRegenerateMessage: (requestId, sessionId, messageId, onEvent, language, assistantMessageId, providerId, model) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    return ipcRenderer.invoke('start-regenerate-stream', { requestId, session_id: sessionId, message_id: messageId, language: language || 'zh', assistant_message_id: assistantMessageId || '', provider_id: providerId || '', model: model || '' }).finally(() => {
      ipcRenderer.removeListener('chat-stream-event', listener);
    });
  },
  streamEditMessage: (requestId, sessionId, messageId, content, onEvent, options, language) => {
    const listener = (_event, data) => {
      if (data.requestId !== requestId) return;
      onEvent(data.event);
    };
    ipcRenderer.on('chat-stream-event', listener);
    return ipcRenderer.invoke('start-edit-stream', { requestId, session_id: sessionId, message_id: messageId, content, work_mode: options?.work_mode, autonomy: options?.autonomy, revert_code: options?.revert_code, assistant_message_id: options?.assistant_message_id || '', language: language || 'zh', provider_id: options?.provider_id || '', model: options?.model || '' }).finally(() => {
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
  discoverMemory: (projectId) => ipcRenderer.invoke('discover-memory', projectId || ''),
  getMemoryFile: (rel) => ipcRenderer.invoke('get-memory-file', rel || ''),
  resolveMemoryPath: (rel) => ipcRenderer.invoke('resolve-memory-path', rel || ''),
  saveMemoryFile: (payload) => ipcRenderer.invoke('save-memory-file', payload),
  deleteMemoryFile: (payload) => ipcRenderer.invoke('delete-memory-file', payload),
  searchMemory: (query, limit) => ipcRenderer.invoke('search-memory', query || '', limit || 50),
  moveMemoryFile: (payload) => ipcRenderer.invoke('move-memory-file', payload),
  exportMemory: (payload) => ipcRenderer.invoke('export-memory', payload),
  importMemory: () => ipcRenderer.invoke('import-memory'),
  previewMemoryImport: (payload) => ipcRenderer.invoke('preview-memory-import', payload),
  applyMemoryImport: (payload) => ipcRenderer.invoke('apply-memory-import', payload),
  getMemorySettings: () => ipcRenderer.invoke('get-memory-settings'),
  saveMemorySettings: (payload) => ipcRenderer.invoke('save-memory-settings', payload),
  getWebSettings: () => ipcRenderer.invoke('get-web-settings'),
  saveWebSettings: (payload) => ipcRenderer.invoke('save-web-settings', payload),
  setWebTavilyKey: (apiKey) => ipcRenderer.invoke('set-web-tavily-key', apiKey),
  clearWebTavilyKey: () => ipcRenderer.invoke('clear-web-tavily-key'),
  testWebSearch: (query, apiKey) => ipcRenderer.invoke('test-web-search', { query: query || 'opencode web search', ...(apiKey ? { apiKey } : {}) }),
  browserSetActiveTab: (webContentsId) => ipcRenderer.invoke('browser:set-active-tab', webContentsId),
  browserMenuAction: (action) => ipcRenderer.invoke('browser:menu-action', action),
  browserCaptureElement: (payload) => ipcRenderer.invoke('browser:capture-element', payload),
  onBrowserContextMenu: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('browser:context-menu', listener);
    return () => ipcRenderer.removeListener('browser:context-menu', listener);
  },
  revealInFolder: (path) => ipcRenderer.invoke('reveal-in-folder', path),
  listMarketSources: () => ipcRenderer.invoke('list-market-sources'),
  listMarketCategories: (source) => ipcRenderer.invoke('list-market-categories', source),
  // Forward the whole query object — positional args used to drop `offset` here.
  searchMarketSkills: (query) => ipcRenderer.invoke('search-market-skills', query),
  listHotSkills: (query) => ipcRenderer.invoke('list-hot-skills', query),
  installMarketSkill: (source, slug, owner) =>
    ipcRenderer.invoke('install-market-skill', source, slug, owner ?? null),
  installSkill: (payload) => ipcRenderer.invoke('skills-install', payload),
  exportToolAudit: () => ipcRenderer.invoke('audit-tool-export'),
  clearToolAudit: () => ipcRenderer.invoke('audit-tool-clear'),
  exportAgentTraces: () => ipcRenderer.invoke('traces-agent-export'),
  clearAgentTraces: () => ipcRenderer.invoke('traces-agent-clear'),
  clearCheckpoints: () => ipcRenderer.invoke('checkpoints-clear'),
  getRetentionSettings: () => ipcRenderer.invoke('settings-retention-get'),
  saveRetentionSettings: (patch) => ipcRenderer.invoke('settings-retention-set', patch),
  checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
  cancelUpdateCheck: () => ipcRenderer.invoke('cancel-update-check'),
  getUpdateState: () => ipcRenderer.invoke('get-update-state'),
  setAutoUpdate: (enabled) => ipcRenderer.invoke('set-auto-update', enabled),
  downloadUpdate: () => ipcRenderer.invoke('download-update'),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  skipVersion: () => ipcRenderer.invoke('skip-version'),
  clearSkipVersion: () => ipcRenderer.invoke('clear-skip'),
  // Logging subsystem
  getLogSettings: () => ipcRenderer.invoke('getLogSettings'),
  setLogLevel: (level) => ipcRenderer.invoke('setLogLevel', level),
  readLogFile: (start = 0, count = 200) => ipcRenderer.invoke('readLogFile', start, count),
  truncateLog: (maxBytes) => ipcRenderer.invoke('truncateLog', maxBytes),
  onUpdateState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('app:update-state', listener);
    return () => ipcRenderer.removeListener('app:update-state', listener);
  },
});
