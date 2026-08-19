import type {
  AgentTraceResponse,
  ApprovalDecisionPayload,
  ChatRequest,
  CommandApprovalResponse,
  CommandApprovalsResponse,
  CreateProjectRequest,
  CreateSessionRequest,
  CurrentDiffResponse,
  MarketCategoriesResponse,
  MarketInstallResponse,
  MarketQuery,
  MarketSkill,
  MarketSource,
  MarketSourceResponse,
  MarketSkillsResponse,
  McpDiscoverPayload,  McpServerCreateRequest,
  McpServerEntry,
  McpServerListPayload,
  McpServerUpdateRequest,
  McpTestRequest,
  McpTestResult,
  ProjectResponse,
  ProjectsListResponse,
  ProviderPayload,
  ProviderEntry,
  ProvidersListResponse,
  ProviderTestResult,
  ProviderUpdatePayload,
  RuntimeConfig,
  RuntimeConfigUpdate,
  RedoResponse,
  EditBeginResponse,
  SessionChangesResponse,
  SessionDetailResponse,
  SessionMessageRecord,
  SessionResponse,
  SessionsListResponse,
  SkillDeleteResponse,
  SkillDetailResponse,
  SkillsListResponse,
  SkillUpdateRequest,
  SkillValidateRequest,
  SkillValidateResponse,
  StreamEvent,
  ToolAuditResponse,
  WorkspaceBranchResponse,
  GoalStatusResponse,
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
  OrgAgentPayload,
  OrgAgentUpdatePayload,
  OrgConfigPayload,
  OrgSnapshot,
  OrgTeamPayload,
  OrgTeamUpdatePayload,
} from '../types';
import { getLanguage } from '../lib/i18n';

const BACKEND_URL = import.meta.env.VITE_COWORKER_BACKEND_URL || 'http://localhost:9527';

export type StreamEventCallback = (event: StreamEvent) => void;

/** Build the WebSocket URL for the backend PTY terminal. */
function buildTerminalUrl(projectId?: string): string {
  const wsBase = BACKEND_URL.replace(/^http/, 'ws');
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return `${wsBase}/ws/terminal${query}`;
}

type AbortSignalLike = {
  aborted?: boolean;
  addEventListener?: AbortSignal['addEventListener'];
  removeEventListener?: AbortSignal['removeEventListener'];
};

function isAbortSignalLike(signal: unknown): signal is AbortSignalLike {
  return typeof signal === 'object' && signal !== null;
}

function attachAbortListener(signal: unknown, listener: () => void): () => void {
  if (!isAbortSignalLike(signal)) return () => {};
  if (typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function') {
    return () => {};
  }
  signal.addEventListener('abort', listener, { once: true });
  return () => signal.removeEventListener?.('abort', listener);
}

export interface ChatService {
  getRuntimeConfig: () => Promise<RuntimeConfig>;
  updateRuntimeConfig: (request: RuntimeConfigUpdate) => Promise<RuntimeConfig>;
  sendMessageStream: (request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  listProviders: () => Promise<ProvidersListResponse>;
  createProvider: (request: ProviderPayload) => Promise<void>;
  updateProvider: (providerId: string, request: ProviderUpdatePayload) => Promise<void>;
  deleteProvider: (providerId: string) => Promise<void>;
  setDefaultProvider: (providerId: string, model: string) => Promise<void>;
  testProvider: (request: { base_url: string; api_key: string; model: string }) => Promise<ProviderTestResult>;
  fetchProviderModels: (request: { base_url: string; api_key: string; provider_type: string }) => Promise<{ models: string[]; error?: string }>;
  discoverProviderContext: (providerId: string) => Promise<{ status: string; provider: ProviderEntry }>;
  openDirectoryPicker: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
  listSessions: () => Promise<SessionsListResponse>;
  listActiveSessions: () => Promise<string[]>;
  createSession: (request: CreateSessionRequest) => Promise<SessionResponse>;
  deleteSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<SessionResponse>;
  getSession: (sessionId: string) => Promise<SessionDetailResponse>;
  generateTitle: (sessionId: string, firstUserMessage: string, assistantResponse?: string) => Promise<string>;
  listProjects: () => Promise<ProjectsListResponse>;
  createProject: (request: CreateProjectRequest) => Promise<ProjectResponse>;
  renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
  deleteProject: (projectId: string) => Promise<void>;
  listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
  listAgentTraces: (limit?: number) => Promise<AgentTraceResponse>;
  listCommandApprovals: () => Promise<CommandApprovalsResponse>;
  resolveCommandApproval: (approvalId: string, decision: ApprovalDecisionPayload) => Promise<CommandApprovalResponse>;
  subscribeApprovalEvents: (resumeId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  getSessionChanges: (sessionId: string) => Promise<SessionChangesResponse>;
  getCurrentDiff: (options?: { projectId?: string; sessionId?: string }) => Promise<CurrentDiffResponse>;
  getWorkspaceBranch: (projectId?: string) => Promise<WorkspaceBranchResponse>;
  redoMessage: (sessionId: string, messageId: string) => Promise<RedoResponse>;
  beginEditMessage: (sessionId: string, messageId: string, revertCode: boolean) => Promise<EditBeginResponse>;
  cancelEditMessage: (sessionId: string, messageId: string) => Promise<RedoResponse>;
  exportToolAudit: () => Promise<string>;
  clearToolAudit: () => Promise<{ status: string }>;
  exportAgentTraces: () => Promise<string>;
  clearAgentTraces: () => Promise<{ status: string }>;
  clearCheckpoints: () => Promise<{ status: string }>;
  getRetentionSettings: () => Promise<{ trace_lines: number; audit_lines: number }>;
  saveRetentionSettings: (patch: { trace_lines?: number; audit_lines?: number }) => Promise<{ trace_lines: number; audit_lines: number }>;
  installSkill: (name: string, content: string, commands?: { name: string; description: string; body: string }[]) => Promise<{ status: string; message?: string }>;
  getTerminalUrl: (projectId?: string) => string;
  streamRegenerateMessage: (
    sessionId: string,
    messageId: string,
    onEvent: StreamEventCallback,
    signal?: AbortSignalLike,
    options?: { assistantMessageId?: string },
  ) => Promise<void>;
  streamEditMessage: (
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; autonomy?: string; revertCode?: boolean; assistantMessageId?: string },
  ) => Promise<void>;
  getGoalStatus: (sessionId: string) => Promise<GoalStatusResponse>;
  pauseGoal: (sessionId: string) => Promise<{ status: string }>;
  editGoal: (sessionId: string, goal: string) => Promise<{ status: string }>;
  deleteGoal: (sessionId: string) => Promise<{ status: string }>;
  resumeGoal: (sessionId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  fetchSettings: () => Promise<{ goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }>;
  saveSettings: (settings: { goal_max_rounds?: number; max_attachment_mb?: number; revert_code?: boolean }) => Promise<{ status: string; goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }>;
  listMcps: () => Promise<McpServerListPayload>;
  discoverMcps: () => Promise<McpDiscoverPayload>;
  createMcp: (request: McpServerCreateRequest) => Promise<McpServerEntry>;
  updateMcp: (serverId: string, request: McpServerUpdateRequest) => Promise<McpServerEntry>;
  deleteMcp: (serverId: string) => Promise<void>;
  testMcp: (request: McpTestRequest) => Promise<McpTestResult>;
  checkMcp: (serverId: string) => Promise<McpServerEntry>;
  checkAllMcps: () => Promise<McpServerListPayload>;
  reauthorizeMcp: (serverId: string) => Promise<McpServerEntry>;
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
  discoverMemory: (projectId?: string) => Promise<MemoryDiscoverResponse>;
  getMemoryFile: (rel: string) => Promise<MemoryFileContentResponse>;
  resolveMemoryPath: (rel: string) => Promise<{ rel: string; path: string }>;
  saveMemoryFile: (rel: string, content: string) => Promise<MemoryFileSaveResponse>;
  deleteMemoryFile: (rel: string) => Promise<MemoryDeleteResponse>;
  searchMemory: (query: string, limit?: number) => Promise<MemorySearchResponse>;
  moveMemoryFile: (rel: string, newRel: string) => Promise<MemoryMoveResponse>;
  exportMemory: (payload: MemoryExportRequest) => Promise<MemoryExportResult>;
  importMemory: () => Promise<MemoryImportPickResult>;
  previewMemoryImport: (path: string) => Promise<MemoryImportPreviewResponse>;
  applyMemoryImport: (token: string, decisions: Record<string, string>) => Promise<MemoryImportApplyResponse>;
  getMemorySettings: () => Promise<MemorySettings>;
  saveMemorySettings: (settings: MemorySettingsPatch) => Promise<MemorySettings>;
  revealInFolder: (path: string) => Promise<{ status: string }>;
  getOrg: (projectId: string) => Promise<OrgSnapshot>;
  createOrgAgent: (request: OrgAgentPayload) => Promise<OrgSnapshot>;
  updateOrgAgent: (request: OrgAgentUpdatePayload) => Promise<OrgSnapshot>;
  deleteOrgAgent: (projectId: string, id: string) => Promise<OrgSnapshot>;
  createOrgTeam: (request: OrgTeamPayload) => Promise<OrgSnapshot>;
  updateOrgTeam: (request: OrgTeamUpdatePayload) => Promise<OrgSnapshot>;
  deleteOrgTeam: (projectId: string, id: string) => Promise<OrgSnapshot>;
   updateOrgConfig: (request: OrgConfigPayload) => Promise<OrgSnapshot>;
   getLogSettings: () => Promise<{ log_level: string; log_file: string; log_max_bytes: number; log_backup_count: number; json_log: boolean }>;
   setLogLevel: (level: string) => Promise<{ log_level: string }>;
   readLogFile: (start?: number, count?: number) => Promise<{ total_lines: number; lines: string[]; truncated: boolean }>;
   truncateLog: (maxBytes?: number) => Promise<{ status: string }>;
}

class ElectronChatService implements ChatService {
  async getRuntimeConfig(): Promise<RuntimeConfig> {
    if (!window.electronAPI) {
      throw new Error('Electron API is unavailable');
    }
    return window.electronAPI.getRuntimeConfig();
  }

  async updateRuntimeConfig(request: RuntimeConfigUpdate): Promise<RuntimeConfig> {
    if (!window.electronAPI) {
      throw new Error('Electron API is unavailable');
    }
    return window.electronAPI.updateRuntimeConfig(request);
  }

  async sendMessageStream(request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `stream-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    // Idle watchdog mirrors the HTTP path: if the backend stops pushing events
    // (a hung provider/tool) abort the stream so the "running" bar cannot hang
    // forever in Electron either.
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => abortStream(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    const wrappedEvent: StreamEventCallback = (event) => {
      resetIdle();
      onEvent(event);
    };
    try {
      await window.electronAPI.streamChatMessage(requestId, request, wrappedEvent);
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachAbortListener();
    }
  }

  async openDirectoryPicker(options?: { title?: string; defaultPath?: string }): Promise<string | null> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.openDirectoryPicker(options);
  }

  async listSessions(): Promise<SessionsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listSessions();
  }

  async listActiveSessions(): Promise<string[]> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listActiveSessions();
  }

  async createSession(request: CreateSessionRequest): Promise<SessionResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.createSession(request);
  }

  async deleteSession(sessionId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteSession(sessionId);
  }

  async renameSession(sessionId: string, title: string): Promise<SessionResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.renameSession(sessionId, title);
  }

  async getSession(sessionId: string): Promise<SessionDetailResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getSession(sessionId);
  }

  async listProjects(): Promise<ProjectsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listProjects();
  }

  async createProject(request: CreateProjectRequest): Promise<ProjectResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.createProject(request);
  }

  async renameProject(projectId: string, name: string): Promise<ProjectResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.renameProject(projectId, name);
  }

  async deleteProject(projectId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteProject(projectId);
  }

  async listMcps(): Promise<McpServerListPayload> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listMcps();
  }

  async discoverMcps(): Promise<McpDiscoverPayload> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.discoverMcps();
  }

  async createMcp(request: McpServerCreateRequest): Promise<McpServerEntry> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.createMcp(request);
    return response.server;
  }

  async updateMcp(serverId: string, request: McpServerUpdateRequest): Promise<McpServerEntry> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.updateMcp(serverId, request);
    return response.server;
  }

  async deleteMcp(serverId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteMcp(serverId);
  }

  async testMcp(request: McpTestRequest): Promise<McpTestResult> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.testMcp(request);
    return response.result;
  }

  async checkMcp(serverId: string): Promise<McpServerEntry> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.checkMcp(serverId);
    return response.server;
  }

  async checkAllMcps(): Promise<McpServerListPayload> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.checkAllMcps();
  }

  async reauthorizeMcp(serverId: string): Promise<McpServerEntry> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.reauthorizeMcp(serverId);
    if (!response.server) throw new Error(response.error || 'Reauthorization failed');
    return response.server;
  }

  async listSkills(enabledOnly = false): Promise<SkillsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listSkills(enabledOnly);
  }

  async installSkill(name: string, content: string, commands?: { name: string; description: string; body: string }[]): Promise<{ status: string; message?: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.installSkill({ name, content, commands: commands || [] });
  }

  async getSkill(name: string, command?: string): Promise<SkillDetailResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getSkill(name, command);
  }

  async updateSkill(name: string, request: SkillUpdateRequest): Promise<SkillDetailResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.updateSkill(name, request);
  }

  async deleteSkill(name: string): Promise<SkillDeleteResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.deleteSkill(name);
  }

  async scanSkills(): Promise<SkillsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.scanSkills();
  }

  async validateSkill(request: SkillValidateRequest): Promise<SkillValidateResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.validateSkill(request);
  }

  async listMarketSources(): Promise<MarketSourceResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listMarketSources();
  }

  async listMarketCategories(source: string): Promise<MarketCategoriesResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listMarketCategories(source);
  }

  async searchMarketSkills(query: MarketQuery): Promise<MarketSkillsResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.searchMarketSkills(query);
  }

  async listHotSkills(query: MarketQuery): Promise<MarketSkillsResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listHotSkills(query);
  }

  async installMarketSkill(source: string, slug: string, owner?: string | null): Promise<MarketInstallResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.installMarketSkill(source, slug, owner ?? null);
  }

  async getMemoryStatus(): Promise<MemoryStatusResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getMemoryStatus();
  }

  async discoverMemory(projectId?: string): Promise<MemoryDiscoverResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.discoverMemory(projectId);
  }

  async getMemoryFile(rel: string): Promise<MemoryFileContentResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getMemoryFile(rel);
  }

  async resolveMemoryPath(rel: string): Promise<{ rel: string; path: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.resolveMemoryPath(rel);
  }

  async saveMemoryFile(rel: string, content: string): Promise<MemoryFileSaveResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.saveMemoryFile({ rel, content });
  }

  async deleteMemoryFile(rel: string): Promise<MemoryDeleteResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.deleteMemoryFile({ rel });
  }

  async searchMemory(query: string, limit = 50): Promise<MemorySearchResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.searchMemory(query, limit);
  }

  async moveMemoryFile(rel: string, newRel: string): Promise<MemoryMoveResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.moveMemoryFile({ rel, new_rel: newRel });
  }

  async exportMemory(payload: MemoryExportRequest): Promise<MemoryExportResult> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.exportMemory(payload);
  }

  async importMemory(): Promise<MemoryImportPickResult> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.importMemory();
  }

  async previewMemoryImport(path: string): Promise<MemoryImportPreviewResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.previewMemoryImport({ path });
  }

  async applyMemoryImport(token: string, decisions: Record<string, string>): Promise<MemoryImportApplyResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.applyMemoryImport({ token, decisions });
  }

  async getMemorySettings(): Promise<MemorySettings> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getMemorySettings();
  }

  async saveMemorySettings(settings: MemorySettingsPatch): Promise<MemorySettings> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.saveMemorySettings(settings);
  }

  async revealInFolder(path: string): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.revealInFolder(path);
  }

  private async _orgRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${BACKEND_URL}${path}`, init);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Backend returned ${response.status}`);
    }
    return payload as T;
  }

  async getOrg(projectId: string): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>(`/api/org?project_id=${encodeURIComponent(projectId)}`);
  }

  async createOrgAgent(request: OrgAgentPayload): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateOrgAgent(request: OrgAgentUpdatePayload): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/agent', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteOrgAgent(projectId: string, id: string): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/agent', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, id }),
    });
  }

  async createOrgTeam(request: OrgTeamPayload): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/team', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateOrgTeam(request: OrgTeamUpdatePayload): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/team', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteOrgTeam(projectId: string, id: string): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/team', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, id }),
    });
  }

  async updateOrgConfig(request: OrgConfigPayload): Promise<OrgSnapshot> {
    return this._orgRequest<OrgSnapshot>('/api/org/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }


  async listToolAudit(limit = 100): Promise<ToolAuditResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listToolAudit(limit);
  }

  async getSessionChanges(sessionId: string): Promise<SessionChangesResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getSessionChanges(sessionId);
  }

  async getCurrentDiff(options?: { projectId?: string; sessionId?: string }): Promise<CurrentDiffResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getCurrentDiff(options);
  }

  async getWorkspaceBranch(projectId?: string): Promise<WorkspaceBranchResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceBranch(projectId);
  }

  async redoMessage(sessionId: string, messageId: string): Promise<RedoResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.redoMessage(sessionId, messageId);
  }

  async beginEditMessage(sessionId: string, messageId: string, revertCode: boolean): Promise<EditBeginResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.editMessageBegin(sessionId, messageId, revertCode);
  }

  async cancelEditMessage(sessionId: string, messageId: string): Promise<RedoResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.editMessageCancel(sessionId, messageId);
  }

  getTerminalUrl(projectId?: string): string {
    return buildTerminalUrl(projectId);
  }

  async listAgentTraces(limit = 100): Promise<AgentTraceResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listAgentTraces(limit);
  }

  async exportToolAudit(): Promise<string> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.exportToolAudit();
  }

  async clearToolAudit(): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.clearToolAudit();
  }

  async exportAgentTraces(): Promise<string> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.exportAgentTraces();
  }

  async clearAgentTraces(): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.clearAgentTraces();
  }

  async clearCheckpoints(): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.clearCheckpoints();
  }

  async getRetentionSettings(): Promise<{ trace_lines: number; audit_lines: number }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getRetentionSettings();
  }

  async saveRetentionSettings(patch: { trace_lines?: number; audit_lines?: number }): Promise<{ trace_lines: number; audit_lines: number }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.saveRetentionSettings(patch);
  }

  async listCommandApprovals(): Promise<CommandApprovalsResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listCommandApprovals();
  }

  async resolveCommandApproval(approvalId: string, decision: ApprovalDecisionPayload): Promise<CommandApprovalResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.resolveCommandApproval(approvalId, decision);
  }

  async subscribeApprovalEvents(resumeId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const requestId = `approval-${resumeId}-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    // Idle watchdog: backend sends SSE ping every 1s, 300s timeout matches other streams
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => abortStream(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    const wrappedEvent: StreamEventCallback = (event) => { resetIdle(); onEvent(event); };
    try {
      await window.electronAPI.streamApprovalEvents(requestId, resumeId, wrappedEvent);
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachAbortListener();
    }
  }

  async streamRegenerateMessage(sessionId: string, messageId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike, options?: { assistantMessageId?: string }): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `regenerate-${messageId}-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    // Same idle watchdog as sendMessageStream: a backend stream that stops
    // pushing events (hung provider) must be aborted so the running bubble
    // cannot count seconds forever.
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => abortStream(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    const wrappedEvent: StreamEventCallback = (event) => {
      resetIdle();
      onEvent(event);
    };
    try {
      await window.electronAPI.streamRegenerateMessage(requestId, sessionId, messageId, wrappedEvent, getLanguage(), options?.assistantMessageId);
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachAbortListener();
    }
  }

  async streamEditMessage(
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; autonomy?: string; revertCode?: boolean; assistantMessageId?: string },
  ): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const signal = options?.signal;
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `edit-${messageId}-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    // Same idle watchdog as sendMessageStream: a backend stream that stops
    // pushing events (hung provider) must be aborted so the running bubble
    // cannot count seconds forever.
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => abortStream(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    const wrappedEvent: StreamEventCallback = (event) => {
      resetIdle();
      onEvent(event);
    };
    try {
      await window.electronAPI.streamEditMessage(requestId, sessionId, messageId, content, wrappedEvent, {
        ...(options?.workMode ? { work_mode: options.workMode } : {}),
        ...(options?.autonomy ? { autonomy: options.autonomy } : {}),
        ...(options?.revertCode !== undefined ? { revert_code: options.revertCode } : {}),
        ...(options?.assistantMessageId ? { assistant_message_id: options.assistantMessageId } : {}),
      }, getLanguage());
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachAbortListener();
    }
  }

  async getGoalStatus(sessionId: string): Promise<GoalStatusResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.goalStatus(sessionId);
  }

  async pauseGoal(sessionId: string): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.goalPause(sessionId);
  }
  async editGoal(sessionId: string, goal: string): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.goalEdit({ session_id: sessionId, goal });
  }

  async deleteGoal(sessionId: string): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.goalDelete(sessionId);
  }

  async resumeGoal(sessionId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const requestId = `goal-resume-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => abortStream(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    const wrappedEvent: StreamEventCallback = (event) => { resetIdle(); onEvent(event); };
    try {
      await window.electronAPI.goalResume(requestId, sessionId, wrappedEvent, getLanguage(), signal);
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachAbortListener();
    }
  }

  async fetchSettings(): Promise<{ goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.fetchSettings?.() ?? { goal_max_rounds: 50, max_attachment_mb: 25, revert_code: true };
  }

  async saveSettings(settings: { goal_max_rounds?: number; max_attachment_mb?: number; revert_code?: boolean }): Promise<{ status: string; goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.saveSettings?.(settings) ?? { status: 'ok', goal_max_rounds: 50, max_attachment_mb: 25, revert_code: true };
  }

  async listProviders(): Promise<ProvidersListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listProviders();
  }

  async createProvider(request: ProviderPayload): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.createProvider(request);
  }

  async updateProvider(providerId: string, request: ProviderUpdatePayload): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.updateProvider(providerId, request);
  }

  async deleteProvider(providerId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteProvider(providerId);
  }

  async setDefaultProvider(providerId: string, model: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.setDefaultProvider({ provider_id: providerId, model });
  }

  async testProvider(request: { base_url: string; api_key: string; model: string }): Promise<ProviderTestResult> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.testProvider(request);
    return response.result;
  }

  async fetchProviderModels(request: { base_url: string; api_key: string; provider_type: string }): Promise<{ models: string[]; error?: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.fetchProviderModels(request);
    return response;
  }

  async discoverProviderContext(providerId: string): Promise<{ status: string; provider: ProviderEntry }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.discoverProviderContext(providerId);
  }

  async generateTitle(sessionId: string, firstUserMessage: string, assistantResponse?: string): Promise<string> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.generateTitle(sessionId, firstUserMessage, assistantResponse, getLanguage());
    return response.title;
  }

  async getLogSettings(): Promise<{ log_level: string; log_file: string; log_max_bytes: number; log_backup_count: number; json_log: boolean }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getLogSettings();
  }

  async setLogLevel(level: string): Promise<{ log_level: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.setLogLevel(level);
  }

  async readLogFile(start = 0, count = 200): Promise<{ total_lines: number; lines: string[]; truncated: boolean }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.readLogFile(start, count);
  }

  async truncateLog(maxBytes?: number): Promise<{ status: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.truncateLog(maxBytes);
  }
}

// 空闲超时：后端超过该时长未推任何数据（既不 delta 也不 done）则视为挂起，主动断开，
// 避免前端「蓝条一直挂起不结束」。每次收到数据都会重置计时。
// Safety-net idle timeout: the backend now sends a keep-alive comment every
// ~15s while a task is alive, so a genuinely-running task never times out here.
// This is only a last-resort guard for a truly dead connection, hence 5 min.
const STREAM_IDLE_TIMEOUT_MS = 300_000;

/** Serialise a market query, dropping only genuinely absent values.
 *  `offset=0` must survive, so this tests for null/undefined/'' rather than
 *  truthiness — the original `if (offset)` was one of the places the caller's
 *  pagination silently vanished. */
function marketQueryString(query: MarketQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  return params.toString();
}

class HttpChatService implements ChatService {
  async getRuntimeConfig(): Promise<RuntimeConfig> {
    return this.request<RuntimeConfig>('/config');
  }

  async updateRuntimeConfig(request: RuntimeConfigUpdate): Promise<RuntimeConfig> {
    return this.request<RuntimeConfig>('/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async sendMessageStream(request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    // 合并外部 abort 信号与「空闲超时」：若后端长时间不推任何数据（既不 delta 也不 done），
    // 主动断开，避免前端「蓝条一直挂起不结束」。每次收到数据都会重置空闲计时。
    const internal = new AbortController();
    const detachExternal = attachAbortListener(signal, () => internal.abort());
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => internal.abort(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    try {
      const response = await fetch(`${BACKEND_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: internal.signal,
      });
      if (!response.ok) {
        let detail = `Backend returned ${response.status}`;
        try {
          const payload = await response.json();
          detail = payload.detail || detail;
        } catch {
          // ignore
        }
        throw new Error(detail);
      }
      if (!response.body) throw new Error('Backend returned no stream');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          resetIdle();
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            // SSE multi-line `data:` — collect ALL lines and join, not just first `.find()`
            const dataLines = frame.split('\n').filter((item) => item.startsWith('data:'));
            if (dataLines.length === 0) continue;
            const raw = dataLines.map((item) => item.slice(5).trim()).join('\n');
            if (!raw) continue;
            try {
              onEvent(JSON.parse(raw) as StreamEvent);
            } catch {
              console.warn('[ChatService] skipped malformed SSE frame:', raw?.slice(0, 200));
            }
          }
        }
        // Trailing partial buffer — also use multi-line rule
        const dataLines = buffer.split('\n').filter((item) => item.startsWith('data:'));
        if (dataLines.length > 0) {
          const raw = dataLines.map((item) => item.slice(5).trim()).join('\n');
          if (raw) {
            try {
              onEvent(JSON.parse(raw) as StreamEvent);
            } catch {
              // skip
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachExternal();
    }
  }

  async openDirectoryPicker(): Promise<string | null> {
    throw new Error('Directory picker is only available in the desktop app');
  }

  async listSessions(): Promise<SessionsListResponse> {
    return this.request<SessionsListResponse>('/sessions');
  }

  async listActiveSessions(): Promise<string[]> {
    const response = await this.request<{ status: string; session_ids: string[] }>('/sessions/active');
    return response.session_ids ?? [];
  }

  async createSession(request: CreateSessionRequest): Promise<SessionResponse> {
    return this.request<SessionResponse>('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: request.title || '', project_id: request.project_id, agent_id: request.agent_id || '' }),
    });
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/sessions/${sessionId}`, { method: 'DELETE' });
  }

  async renameSession(sessionId: string, title: string): Promise<SessionResponse> {
    return this.request<SessionResponse>(`/sessions/${sessionId}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
  }

  async getSession(sessionId: string): Promise<SessionDetailResponse> {
    return this.request<SessionDetailResponse>(`/sessions/${sessionId}`);
  }

  async listProjects(): Promise<ProjectsListResponse> {
    return this.request<ProjectsListResponse>('/projects');
  }

  async createProject(request: CreateProjectRequest): Promise<ProjectResponse> {
    return this.request<ProjectResponse>('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async renameProject(projectId: string, name: string): Promise<ProjectResponse> {
    return this.request<ProjectResponse>(`/projects/${projectId}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
  }

  async deleteProject(projectId: string): Promise<void> {
    await this.request(`/projects/${projectId}`, { method: 'DELETE' });
  }

  async listToolAudit(limit = 100): Promise<ToolAuditResponse> {
    return this.request<ToolAuditResponse>(`/audit/tool?limit=${encodeURIComponent(limit)}`);
  }

  async getSessionChanges(sessionId: string): Promise<SessionChangesResponse> {
    return this.request<SessionChangesResponse>(`/sessions/${encodeURIComponent(sessionId)}/changes`);
  }

  async getCurrentDiff(options?: { projectId?: string; sessionId?: string }): Promise<CurrentDiffResponse> {
    const params = new URLSearchParams();
    if (options?.projectId) params.set('project_id', options.projectId);
    if (options?.sessionId) params.set('session_id', options.sessionId);
    const query = params.toString();
    return this.request<CurrentDiffResponse>(`/diffs/current${query ? `?${query}` : ''}`);
  }

  async getWorkspaceBranch(projectId?: string): Promise<WorkspaceBranchResponse> {
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    const query = params.toString();
    return this.request<WorkspaceBranchResponse>(`/workspace/branch${query ? `?${query}` : ''}`);
  }

  async redoMessage(sessionId: string, messageId: string): Promise<RedoResponse> {
    return this.request<RedoResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/redo`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    );
  }

  async beginEditMessage(sessionId: string, messageId: string, revertCode: boolean): Promise<EditBeginResponse> {
    return this.request<EditBeginResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit-begin`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revert_code: revertCode }) },
    );
  }

  async cancelEditMessage(sessionId: string, messageId: string): Promise<RedoResponse> {
    return this.request<RedoResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit-cancel`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    );
  }

  getTerminalUrl(projectId?: string): string {
    return buildTerminalUrl(projectId);
  }

  async listAgentTraces(limit = 100): Promise<AgentTraceResponse> {
    return this.request<AgentTraceResponse>(`/traces/agent?limit=${encodeURIComponent(limit)}`);
  }

  async listCommandApprovals(): Promise<CommandApprovalsResponse> {
    return this.request<CommandApprovalsResponse>('/command-approvals');
  }

  async exportToolAudit(): Promise<string> {
    const res = await fetch(`${BACKEND_URL}/audit/tool/export`);
    return res.ok ? await res.text() : '';
  }

  async clearToolAudit(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/audit/tool/clear', { method: 'POST' });
  }

  async exportAgentTraces(): Promise<string> {
    const res = await fetch(`${BACKEND_URL}/traces/agent/export`);
    return res.ok ? await res.text() : '';
  }

  async clearAgentTraces(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/traces/agent/clear', { method: 'POST' });
  }

  async clearCheckpoints(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/checkpoints/clear', { method: 'POST' });
  }

  async getRetentionSettings(): Promise<{ trace_lines: number; audit_lines: number }> {
    return this.request<{ trace_lines: number; audit_lines: number }>('/settings/retention');
  }

  async saveRetentionSettings(patch: { trace_lines?: number; audit_lines?: number }): Promise<{ trace_lines: number; audit_lines: number }> {
    return this.request<{ trace_lines: number; audit_lines: number }>('/settings/retention', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
  }

  async resolveCommandApproval(approvalId: string, decision: ApprovalDecisionPayload): Promise<CommandApprovalResponse> {
    return this.request<CommandApprovalResponse>('/command-approvals/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, decision }),
    });
  }

  async subscribeApprovalEvents(resumeId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    // Merge external abort with idle watchdog + Duck-typed signal support (attachAbortListener)
    const internal = new AbortController();
    const detachExternal = attachAbortListener(signal, () => internal.abort());
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => internal.abort(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    try {
      const response = await fetch(`${BACKEND_URL}/command-approvals/events/${encodeURIComponent(resumeId)}`, {
        signal: internal.signal,
      });
      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}`);
      }
      if (!response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          resetIdle();
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            // SSE multi-line `data:` — collect ALL lines and join
            const dataLines = frame.split('\n').filter((item) => item.startsWith('data:'));
            if (dataLines.length === 0) continue;
            const raw = dataLines.map((item) => item.slice(5).trim()).join('\n');
            if (!raw) continue;
            try {
              onEvent(JSON.parse(raw) as StreamEvent);
            } catch {
              // skip malformed frames
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachExternal();
    }
  }

  async streamRegenerateMessage(sessionId: string, messageId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike, options?: { assistantMessageId?: string }): Promise<void> {
    await this.streamPost(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate`, { language: getLanguage(), ...(options?.assistantMessageId ? { assistant_message_id: options.assistantMessageId } : {}) }, onEvent, signal);
  }

  async streamEditMessage(
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; autonomy?: string; revertCode?: boolean; assistantMessageId?: string },
  ): Promise<void> {
    const payload: Record<string, unknown> = { content, language: getLanguage() };
    if (options?.workMode) payload.work_mode = options.workMode;
    if (options?.autonomy) payload.autonomy = options.autonomy;
    if (options?.revertCode !== undefined) payload.revert_code = options.revertCode;
    if (options?.assistantMessageId) payload.assistant_message_id = options.assistantMessageId;
    await this.streamPost(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit`, payload, onEvent, options?.signal);
  }

  async getGoalStatus(sessionId: string): Promise<GoalStatusResponse> {
    return this.request<GoalStatusResponse>(`/goal/status?session_id=${encodeURIComponent(sessionId)}`);
  }

  async pauseGoal(sessionId: string): Promise<{ status: string }> {
    return this.request<{ status: string }>('/goal/pause', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
  }

  async editGoal(sessionId: string, goal: string): Promise<{ status: string }> {
    return this.request<{ status: string }>('/goal/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, goal }),
    });
  }

  async deleteGoal(sessionId: string): Promise<{ status: string }> {
    return this.request<{ status: string }>('/goal/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
  }

  async resumeGoal(sessionId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    await this.streamPost('/goal/resume', { session_id: sessionId, language: getLanguage() }, onEvent, signal);
  }

  private async streamPost(path: string, payload: Record<string, unknown>, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    // Merge the external abort signal with an idle watchdog so a backend
    // stream that stops pushing events (hung provider) cannot leave a bubble
    // counting seconds forever. Every received chunk resets the timer.
    const internal = new AbortController();
    const detachExternal = attachAbortListener(signal, () => internal.abort());
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const resetIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => internal.abort(), STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();
    try {
      const response = await fetch(`${BACKEND_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: internal.signal,
      });
      if (!response.ok) {
        let detail = `Backend returned ${response.status}`;
        try {
          const body = await response.json();
          detail = body.detail || detail;
        } catch {
          // ignore
        }
        throw new Error(detail);
      }
      if (!response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          resetIdle();
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            // SSE multi-line `data:` — collect ALL lines and join
            const dataLines = frame.split('\n').filter((item) => item.startsWith('data:'));
            if (dataLines.length === 0) continue;
            const raw = dataLines.map((item) => item.slice(5).trim()).join('\n');
            if (!raw) continue;
            try {
              onEvent(JSON.parse(raw) as StreamEvent);
            } catch {
              // skip
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      detachExternal();
    }
  }

  async listProviders(): Promise<ProvidersListResponse> {
    return this.request<ProvidersListResponse>('/providers');
  }

  async createProvider(request: ProviderPayload): Promise<void> {
    await this.request('/providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateProvider(providerId: string, request: ProviderUpdatePayload): Promise<void> {
    await this.request(`/providers/${providerId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteProvider(providerId: string): Promise<void> {
    await this.request(`/providers/${providerId}`, { method: 'DELETE' });
  }

  async setDefaultProvider(providerId: string, model: string): Promise<void> {
    await this.request('/providers/default', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, model }),
    });
  }

  async testProvider(request: { base_url: string; api_key: string; model: string }): Promise<ProviderTestResult> {
    const response = await this.request<{ status: string; result: ProviderTestResult }>('/providers/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return response.result;
  }

  async fetchProviderModels(request: { base_url: string; api_key: string; provider_type: string }): Promise<{ models: string[]; error?: string }> {
    return this.request<{ status: string; models: string[]; error?: string }>('/providers/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async discoverProviderContext(providerId: string): Promise<{ status: string; provider: ProviderEntry }> {
    return this.request<{ status: string; provider: ProviderEntry }>(`/providers/${encodeURIComponent(providerId)}/discover-context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
  }

  async generateTitle(sessionId: string, firstUserMessage: string, assistantResponse?: string): Promise<string> {
    const response = await this.request<{ status: string; title: string }>(`/sessions/${sessionId}/generateTitle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_user_message: firstUserMessage,
        assistant_response: assistantResponse || '',
        language: getLanguage(),
      }),
    });
    return response.title;
  }

  async fetchSettings(): Promise<{ goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }> {
    return this.request<{ goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }>('/settings');
  }

  async saveSettings(settings: { goal_max_rounds?: number; max_attachment_mb?: number; revert_code?: boolean }): Promise<{ status: string; goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }> {
    return this.request<{ status: string; goal_max_rounds: number; max_attachment_mb: number; revert_code: boolean }>('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  }

  async listMcps(): Promise<McpServerListPayload> {
    return this.request<McpServerListPayload>('/mcp/servers');
  }

  async discoverMcps(): Promise<McpDiscoverPayload> {
    return this.request<McpDiscoverPayload>('/mcp/discover');
  }

  async createMcp(request: McpServerCreateRequest): Promise<McpServerEntry> {
    const response = await this.request<{ status: string; server: McpServerEntry }>('/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return response.server;
  }

  async updateMcp(serverId: string, request: McpServerUpdateRequest): Promise<McpServerEntry> {
    const response = await this.request<{ status: string; server: McpServerEntry }>(`/mcp/servers/${encodeURIComponent(serverId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return response.server;
  }

  async deleteMcp(serverId: string): Promise<void> {
    await this.request<void>(`/mcp/servers/${encodeURIComponent(serverId)}`, { method: 'DELETE' });
  }

  async testMcp(request: McpTestRequest): Promise<McpTestResult> {
    const response = await this.request<{ status: string; result: McpTestResult }>('/mcp/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return response.result;
  }

  async checkMcp(serverId: string): Promise<McpServerEntry> {
    const response = await this.request<{ status: string; server: McpServerEntry }>(
      `/mcp/servers/${encodeURIComponent(serverId)}/check`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) },
    );
    return response.server;
  }

  async checkAllMcps(): Promise<McpServerListPayload> {
    return this.request<McpServerListPayload>('/mcp/check-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
  }

  async reauthorizeMcp(serverId: string): Promise<McpServerEntry> {
    const response = await this.request<{ status: string; server: McpServerEntry }>(
      `/mcp/servers/${encodeURIComponent(serverId)}/reauthorize`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) },
    );
    return response.server;
  }

  async listSkills(enabledOnly = false): Promise<SkillsListResponse> {
    const query = enabledOnly ? '?enabled_only=1' : '';
    return this.request<SkillsListResponse>(`/skills${query}`);
  }

  async installSkill(name: string, content: string, commands?: { name: string; description: string; body: string }[]): Promise<{ status: string; message?: string }> {
    return this.request<{ status: string; message?: string }>('/skills/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, content, commands: commands || [] }),
    });
  }

  async getSkill(name: string, command?: string): Promise<SkillDetailResponse> {
    const query = command ? `?command=${encodeURIComponent(command)}` : '';
    return this.request<SkillDetailResponse>(`/skills/${encodeURIComponent(name)}${query}`);
  }

  async updateSkill(name: string, request: SkillUpdateRequest): Promise<SkillDetailResponse> {
    return this.request<SkillDetailResponse>(`/skills/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteSkill(name: string): Promise<SkillDeleteResponse> {
    return this.request<SkillDeleteResponse>(`/skills/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
  }

  async scanSkills(): Promise<SkillsListResponse> {
    return this.request<SkillsListResponse>('/skills/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
  }

  async validateSkill(request: SkillValidateRequest): Promise<SkillValidateResponse> {
    return this.request<SkillValidateResponse>('/skills/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async listMarketSources(): Promise<MarketSourceResponse> {
    return this.request<MarketSourceResponse>('/skills/market');
  }

  async listMarketCategories(source: string): Promise<MarketCategoriesResponse> {
    return this.request<MarketCategoriesResponse>(
      `/skills/market/categories?source=${encodeURIComponent(source)}`,
    );
  }

  async searchMarketSkills(query: MarketQuery): Promise<MarketSkillsResponse> {
    return this.request<MarketSkillsResponse>(
      `/skills/market/search?${marketQueryString(query)}`,
    );
  }

  async listHotSkills(query: MarketQuery): Promise<MarketSkillsResponse> {
    return this.request<MarketSkillsResponse>(
      `/skills/market/hot?${marketQueryString(query)}`,
    );
  }

  async installMarketSkill(source: string, slug: string, owner?: string | null): Promise<MarketInstallResponse> {
    return this.request<MarketInstallResponse>('/skills/market/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, slug, owner: owner ?? null }),
    });
  }

  async getMemoryStatus(): Promise<MemoryStatusResponse> {
    return this.request<MemoryStatusResponse>('/api/memory/status');
  }

  async discoverMemory(projectId?: string): Promise<MemoryDiscoverResponse> {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return this.request<MemoryDiscoverResponse>(`/api/memory/discover${query}`);
  }

  async getMemoryFile(rel: string): Promise<MemoryFileContentResponse> {
    return this.request<MemoryFileContentResponse>(`/api/memory/file?rel=${encodeURIComponent(rel)}`);
  }

  async resolveMemoryPath(rel: string): Promise<{ rel: string; path: string }> {
    return this.request<{ rel: string; path: string }>(`/api/memory/resolve?rel=${encodeURIComponent(rel)}`);
  }

  async saveMemoryFile(rel: string, content: string): Promise<MemoryFileSaveResponse> {
    return this.request<MemoryFileSaveResponse>('/api/memory/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rel, content }),
    });
  }

  async deleteMemoryFile(rel: string): Promise<MemoryDeleteResponse> {
    return this.request<MemoryDeleteResponse>('/api/memory/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rel }),
    });
  }

  async searchMemory(query: string, limit = 50): Promise<MemorySearchResponse> {
    return this.request<MemorySearchResponse>(`/api/memory/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  }

  async moveMemoryFile(rel: string, newRel: string): Promise<MemoryMoveResponse> {
    return this.request<MemoryMoveResponse>('/api/memory/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rel, new_rel: newRel }),
    });
  }

  async exportMemory(payload: MemoryExportRequest): Promise<MemoryExportResult> {
    return this.request<MemoryExportResult>('/api/memory/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  async importMemory(): Promise<MemoryImportPickResult> {
    // No local file picker in the web build.
    return { status: 'unsupported' };
  }

  async previewMemoryImport(path: string): Promise<MemoryImportPreviewResponse> {
    return this.request<MemoryImportPreviewResponse>('/api/memory/import/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
  }

  async applyMemoryImport(token: string, decisions: Record<string, string>): Promise<MemoryImportApplyResponse> {
    return this.request<MemoryImportApplyResponse>('/api/memory/import/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, decisions }),
    });
  }

  async getMemorySettings(): Promise<MemorySettings> {
    return this.request<MemorySettings>('/api/memory/settings');
  }

  async saveMemorySettings(settings: MemorySettingsPatch): Promise<MemorySettings> {
    return this.request<MemorySettings>('/api/memory/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  }

  async revealInFolder(path: string): Promise<{ status: string }> {
    // No local file manager in the web build — caller hides the action.
    return { status: 'unsupported' };
  }

  async getOrg(projectId: string): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>(`/api/org?project_id=${encodeURIComponent(projectId)}`);
  }

  async createOrgAgent(request: OrgAgentPayload): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateOrgAgent(request: OrgAgentUpdatePayload): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/agent', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteOrgAgent(projectId: string, id: string): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/agent', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, id }),
    });
  }

  async createOrgTeam(request: OrgTeamPayload): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/team', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateOrgTeam(request: OrgTeamUpdatePayload): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/team', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteOrgTeam(projectId: string, id: string): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/team', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, id }),
    });
  }

  async updateOrgConfig(request: OrgConfigPayload): Promise<OrgSnapshot> {
    return this.request<OrgSnapshot>('/api/org/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async getLogSettings(): Promise<{ log_level: string; log_file: string; log_max_bytes: number; log_backup_count: number; json_log: boolean }> {
    return this.request('/settings/log');
  }

  async setLogLevel(level: string): Promise<{ log_level: string }> {
    return this.request('/settings/log-level', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ log_level: level }),
    });
  }

  async readLogFile(start = 0, count = 200): Promise<{ total_lines: number; lines: string[]; truncated: boolean }> {
    return this.request(`/settings/log-file?start=${start}&count=${count}`);
  }

  async truncateLog(maxBytes?: number): Promise<{ status: string }> {
    return this.request('/settings/truncate-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...(maxBytes != null && { max_bytes: maxBytes }) }),
    });
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${BACKEND_URL}${path}`, init);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Backend returned ${response.status}`);
    }
    return payload as T;
  }
}

export function createChatService(): ChatService {
  return window.electronAPI ? new ElectronChatService() : new HttpChatService();
}

export const chatService = createChatService();
