import type {
  AgentTraceResponse,
  ApprovalDecisionPayload,
  ChatRequest,
  CommandApprovalResponse,
  CommandApprovalsResponse,
  CreateProjectRequest,
  CreateSessionRequest,
  CurrentDiffResponse,
  McpDiscoverPayload,
  McpServerCreateRequest,
  McpServerEntry,
  McpServerListPayload,
  McpServerUpdateRequest,
  McpTestRequest,
  McpTestResult,
  ProjectResponse,
  ProjectDashboardResponse,
  ProjectsListResponse,
  ProviderPayload,
  ProvidersListResponse,
  ProviderTestResult,
  ProviderUpdatePayload,
  RuntimeConfig,
  RuntimeConfigUpdate,
  RedoResponse,
  SessionChangesResponse,
  SessionDetailResponse,
  SessionMessageRecord,
  SessionResponse,
  SessionsListResponse,
  SkillDetailResponse,
  SkillsListResponse,
  SkillUpdateRequest,
  SkillValidateRequest,
  SkillValidateResponse,
  StreamEvent,
  ToolAuditResponse,  MarketSourceResponse,
  MarketCategoriesResponse,
  MarketQuery,
  MarketSkillsResponse,
  MarketInstallResponse,
  WorkspaceBranchResponse,
  WorkspaceTreeResponse,
  WorkspaceDirResponse,
  WorkspaceFileResponse,
  WorkspaceFilePreviewResponse,
  MemoryStatusResponse,
  MemoryDiscoverResponse,
  MemoryDeleteResponse,
  MemoryFileContentResponse,
  MemoryFileSaveResponse,
  MemorySearchResponse,
  MemoryMoveResponse,
  MemoryExportRequest,
  MemoryExportResult,
  MemoryImportPickResult,
  MemoryImportPreviewResponse,
  MemoryImportApplyResponse,
  MemorySettings,
  MemorySettingsPatch,
  BrowserCaptureResult,
  BrowserContextMenuPayload,
  UpdateStateSnapshot,
  WebSettings,
  WebConfigPatch,
  WebTestResult,
  GoalResponse,
  GoalSetMeta,
} from './types';

export type StreamEventCallback = (event: StreamEvent) => void;

declare global {
  interface Window {
    electronAPI?: {
      platform: string;
      clipboardReadText: () => Promise<string>;
      clipboardWriteText: (text: string) => Promise<void>;
      getRuntimeConfig: () => Promise<RuntimeConfig>;
      updateRuntimeConfig: (payload: RuntimeConfigUpdate) => Promise<RuntimeConfig>;
      streamChatMessage: (requestId: string, payload: ChatRequest, onEvent: StreamEventCallback) => Promise<void>;
      abortChatStream: (requestId: string) => void;
      streamApprovalEvents: (requestId: string, resumeId: string, onEvent: StreamEventCallback) => Promise<void>;
      streamWorkerEvents: (requestId: string, workerRunId: string, onEvent: StreamEventCallback) => Promise<void>;
      listProviders: () => Promise<ProvidersListResponse>;
      createProvider: (payload: ProviderPayload) => Promise<{ status: string; provider: ProviderEntry }>;
      updateProvider: (providerId: string, params: ProviderUpdatePayload) => Promise<{ status: string }>;
      discoverProviderContext: (providerId: string) => Promise<{ status: string; provider: ProviderEntry }>;
      getProviderTemplates: () => Promise<{ status: string; templates: Array<{ key: string; name: string; base_url: string; icon: string | null }>; order: string[]; icon_aliases: Record<string, string | null> }>;
      deleteProvider: (providerId: string) => Promise<{ status: string }>;
      setDefaultProvider: (payload: { provider_id: string; model: string }) => Promise<{ status: string }>;
      testProvider: (payload: { base_url: string; api_key: string; model: string }) => Promise<{ status: string; result: ProviderTestResult }>;
      fetchProviderModels: (payload: { base_url: string; api_key: string; provider_type: string }) => Promise<{ status: string; models: string[]; error?: string }>;
      listSessions: () => Promise<SessionsListResponse>;
      listActiveSessions: () => Promise<string[]>;
      createSession: (payload: CreateSessionRequest) => Promise<SessionResponse>;
      deleteSession: (sessionId: string) => Promise<{ status: string }>;
      renameSession: (sessionId: string, title: string) => Promise<SessionResponse>;
      stopSessionStream: (sessionId: string) => Promise<{ status: string }>;
      getSession: (sessionId: string) => Promise<SessionDetailResponse>;
      getContextUsage: (sessionId: string, providerId: string, model: string) => Promise<SessionContextUsageResponse>;
      goalGet: (sessionId: string) => Promise<GoalResponse>;
      goalSet: (sessionId: string, objective: string, tokenBudget?: number | null, meta?: GoalSetMeta | null) => Promise<GoalResponse>;
      goalPause: (sessionId: string) => Promise<GoalResponse>;
      goalResume: (sessionId: string) => Promise<GoalResponse>;
      goalClear: (sessionId: string) => Promise<{ status: string; cleared: boolean }>;
      goalEdit: (sessionId: string, objective: string) => Promise<GoalResponse>;
      generateTitle: (sessionId: string, firstUserMessage: string, assistantResponse?: string, language?: string) => Promise<{ status: string; title: string }>;
      listProjects: () => Promise<ProjectsListResponse>;
      createProject: (payload: CreateProjectRequest) => Promise<ProjectResponse>;
      openDirectoryPicker: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
      renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
      deleteProject: (projectId: string) => Promise<{ status: string }>;
      getWorkspaceBranch: (projectId?: string) => Promise<WorkspaceBranchResponse>;
      getProjectDashboard: (projectId: string) => Promise<ProjectDashboardResponse>;
      getWorkspaceTree: (projectId: string, path?: string) => Promise<WorkspaceTreeResponse>;
      getWorkspaceDir: (projectId: string, path?: string) => Promise<WorkspaceDirResponse>;
      getWorkspaceFile: (projectId: string, path: string) => Promise<WorkspaceFileResponse>;
      getWorkspaceFilePreview: (projectId: string, path: string) => Promise<WorkspaceFilePreviewResponse>;
      openFileExternally: (filePath: string) => Promise<{ status: string }>;
      listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
      listAgentTraces: (limit?: number) => Promise<AgentTraceResponse>;
      listCommandApprovals: () => Promise<CommandApprovalsResponse>;
      resolveCommandApproval: (approvalId: string, decision: CommandApprovalDecision) => Promise<CommandApprovalResult>;
      getSessionChanges: (sessionId: string) => Promise<SessionChangesResponse>;
      getCurrentDiff: (options?: { projectId?: string; sessionId?: string }) => Promise<CurrentDiffResponse>;
      redoMessage: (sessionId: string, messageId: string) => Promise<RedoResponse>;
      editMessageBegin: (sessionId: string, messageId: string, revertCode: boolean) => Promise<EditBeginResponse>;
      editMessageCancel: (sessionId: string, messageId: string) => Promise<RedoResponse>;
      streamRegenerateMessage: (requestId: string, sessionId: string, messageId: string, onEvent: StreamEventCallback, language?: string, assistantMessageId?: string, providerId?: string, model?: string) => Promise<void>;
      streamEditMessage: (
        requestId: string,
        sessionId: string,
        messageId: string,
        content: string,
        onEvent: StreamEventCallback,
        options?: { work_mode?: string; autonomy?: string; revert_code?: boolean; assistant_message_id?: string; provider_id?: string; model?: string },
        language?: string,
      ) => Promise<void>;
      fetchSettings?: () => Promise<{ max_attachment_mb: number; revert_code: boolean; goal_enabled: boolean }>;
      saveSettings?: (settings: { max_attachment_mb?: number; revert_code?: boolean; goal_enabled?: boolean }) => Promise<{ status: string; max_attachment_mb: number; revert_code: boolean; goal_enabled: boolean }>;
      listMcps: () => Promise<McpServerListPayload>;
      discoverMcps: () => Promise<McpDiscoverPayload>;
      createMcp: (request: McpServerCreateRequest) => Promise<{ server: McpServerEntry }>;
      updateMcp: (serverId: string, request: McpServerUpdateRequest) => Promise<{ server: McpServerEntry }>;
      deleteMcp: (serverId: string) => Promise<{ status: string }>;
      testMcp: (request: McpTestRequest) => Promise<{ result: McpTestResult }>;
      checkMcp: (serverId: string) => Promise<{ server: McpServerEntry }>;
      checkAllMcps: () => Promise<McpServerListPayload>;
      reauthorizeMcp: (serverId: string) => Promise<{ status: string; ok: boolean; error?: string; needs_auth?: boolean; server?: McpServerEntry }>;
      listSkills: (enabledOnly?: boolean) => Promise<SkillsListResponse>;
      getSkill: (name: string, command?: string) => Promise<SkillDetailResponse>;
      updateSkill: (name: string, request: SkillUpdateRequest) => Promise<SkillDetailResponse>;
      deleteSkill: (name: string) => Promise<SkillDeleteResponse>;
      scanSkills: () => Promise<SkillsListResponse>;
      validateSkill: (request: SkillValidateRequest) => Promise<SkillValidateResponse>;
      listMarketSources: () => Promise<MarketSourceResponse>;
      listMarketCategories: (source: string) => Promise<MarketCategoriesResponse>;
      searchMarketSkills: (query: MarketQuery) => Promise<MarketSkillsResponse>;
      listHotSkills: (query: MarketQuery) => Promise<MarketSkillsResponse>;
      installMarketSkill: (source: string, slug: string, owner?: string | null) => Promise<MarketInstallResponse>;
      getMemoryStatus: () => Promise<MemoryStatusResponse>;
      discoverMemory: (projectId?: string, scope?: string) => Promise<MemoryDiscoverResponse>;
      getMemoryFile: (rel: string) => Promise<MemoryFileContentResponse>;
      resolveMemoryPath: (rel: string) => Promise<{ rel: string; path: string }>;
      saveMemoryFile: (payload: { rel: string; content: string }) => Promise<MemoryFileSaveResponse>;
      deleteMemoryFile: (payload: { rel: string }) => Promise<MemoryDeleteResponse>;
      searchMemory: (query: string, limit?: number) => Promise<MemorySearchResponse>;
      moveMemoryFile: (payload: { rel: string; new_rel: string }) => Promise<MemoryMoveResponse>;
      exportMemory: (payload: MemoryExportRequest) => Promise<MemoryExportResult>;
      importMemory: () => Promise<MemoryImportPickResult>;
      previewMemoryImport: (payload: { path: string }) => Promise<MemoryImportPreviewResponse>;
      applyMemoryImport: (payload: { token: string; decisions: Record<string, string> }) => Promise<MemoryImportApplyResponse>;
      getMemorySettings: () => Promise<MemorySettings>;
      saveMemorySettings: (payload: MemorySettingsPatch) => Promise<MemorySettings>;
      getWebSettings: () => Promise<WebSettings>;
      saveWebSettings: (payload: WebConfigPatch) => Promise<WebSettings>;
      setWebTavilyKey: (apiKey: string) => Promise<{ status: string; api_key_configured?: boolean; detail?: string }>;
      clearWebTavilyKey: () => Promise<{ status: string; api_key_configured?: boolean; detail?: string }>;
      testWebSearch: (query?: string, apiKey?: string) => Promise<WebTestResult>;
      browserSetActiveTab: (webContentsId: number) => Promise<{ ok: boolean }>;
      browserMenuAction: (action: string) => Promise<{ ok: boolean; error?: string }>;
      browserCaptureElement: (payload: { x: number; y: number; scope?: 'element' | 'page' }) => Promise<BrowserCaptureResult>;
      onBrowserContextMenu: (callback: (payload: BrowserContextMenuPayload) => void) => () => void;
      revealInFolder: (path: string) => Promise<{ status: string }>;
      installSkill: (payload: { name: string; content: string; commands?: { name: string; description: string; body: string }[] }) => Promise<{ status: string; message?: string }>;
      exportToolAudit: () => Promise<string>;
      clearToolAudit: () => Promise<{ status: string }>;
      exportAgentTraces: () => Promise<string>;
      clearAgentTraces: () => Promise<{ status: string }>;
      clearCheckpoints: () => Promise<{ status: string }>;
      getRetentionSettings: () => Promise<{ trace_lines: number; audit_lines: number }>;
      saveRetentionSettings: (patch: { trace_lines?: number; audit_lines?: number }) => Promise<{ trace_lines: number; audit_lines: number }>;
      checkForUpdates: () => Promise<{ status: string; error?: string }>;
      cancelUpdateCheck: () => Promise<{ status: string }>;
      getUpdateState: () => Promise<UpdateStateSnapshot>;
      setAutoUpdate: (enabled: boolean) => Promise<{ status: string; enabled: boolean }>;
      downloadUpdate: () => Promise<{ status: string; error?: string }>;
      installUpdate: () => Promise<{ status: string }>;
      skipVersion: () => Promise<{ status: string; skippedVersion: string | null }>;
      clearSkipVersion: () => Promise<{ status: string }>;
      getLogSettings: () => Promise<{ log_level: string; log_file: string; log_max_bytes: number; log_backup_count: number; json_log: boolean }>;
      setLogLevel: (level: string) => Promise<{ log_level: string }>;
      readLogFile: (start?: number, count?: number) => Promise<{ total_lines: number; lines: string[]; truncated: boolean }>;
      truncateLog: (maxBytes?: number) => Promise<{ status: string }>;
      onUpdateState: (callback: (state: UpdateStateSnapshot) => void) => () => void;
    };
  }
}

export {};
