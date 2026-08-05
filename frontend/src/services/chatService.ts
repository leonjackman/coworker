import type {
  AgentTraceResponse,
  ApprovalDecisionPayload,
  ChatRequest,
  ChatResponse,
  CommandApprovalResponse,
  CommandApprovalsResponse,
  CreateProjectRequest,
  CreateSessionRequest,
  CurrentDiffResponse,
  ProjectResponse,
  ProjectsListResponse,
  ProviderPayload,
  ProvidersListResponse,
  ProviderTestResult,
  ProviderUpdatePayload,
  RuntimeConfig,
  RuntimeConfigUpdate,
  RevertPreviewResponse,
  RollbackResponse,
  SessionChangesResponse,
  SessionDetailResponse,
  SessionMessageRecord,
  SessionResponse,
  SessionsListResponse,
  StreamEvent,
  ToolAuditResponse,
  WorkspaceCommandRequest,
  WorkspaceCommandResponse,
  WorkspaceDirResponse,
  WorkspaceFileResponse,
  WorkspaceTreeResponse,
  WorkspaceBranchResponse,
} from '../types';

const BACKEND_URL = import.meta.env.VITE_COWORKER_BACKEND_URL || 'http://localhost:9527';

export type StreamEventCallback = (event: StreamEvent) => void;

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
  sendMessage: (request: ChatRequest) => Promise<ChatResponse>;
  sendMessageStream: (request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  listProviders: () => Promise<ProvidersListResponse>;
  createProvider: (request: ProviderPayload) => Promise<void>;
  updateProvider: (providerId: string, request: ProviderUpdatePayload) => Promise<void>;
  deleteProvider: (providerId: string) => Promise<void>;
  setDefaultProvider: (providerId: string, model: string) => Promise<void>;
  testProvider: (request: { base_url: string; api_key: string; model: string }) => Promise<ProviderTestResult>;
  fetchProviderModels: (request: { base_url: string; api_key: string; provider_type: string }) => Promise<{ models: string[]; error?: string }>;
  openDirectoryPicker: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
  listSessions: () => Promise<SessionsListResponse>;
  createSession: (request: CreateSessionRequest) => Promise<SessionResponse>;
  deleteSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<SessionResponse>;
  getSession: (sessionId: string) => Promise<SessionDetailResponse>;
  generateTitle: (sessionId: string, firstUserMessage: string) => Promise<string>;
  listProjects: () => Promise<ProjectsListResponse>;
  createProject: (request: CreateProjectRequest) => Promise<ProjectResponse>;
  renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
  deleteProject: (projectId: string) => Promise<void>;
  getWorkspaceTree: (projectId?: string) => Promise<WorkspaceTreeResponse>;
  getWorkspaceDir: (path: string, projectId?: string) => Promise<WorkspaceDirResponse>;
  getWorkspaceFile: (path: string, projectId?: string) => Promise<WorkspaceFileResponse>;
  runWorkspaceCommand: (request: WorkspaceCommandRequest) => Promise<WorkspaceCommandResponse>;
  listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
  listAgentTraces: (limit?: number) => Promise<AgentTraceResponse>;
  listCommandApprovals: () => Promise<CommandApprovalsResponse>;
  resolveCommandApproval: (approvalId: string, decision: ApprovalDecisionPayload) => Promise<CommandApprovalResponse>;
  subscribeApprovalEvents: (resumeId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  getSessionChanges: (sessionId: string) => Promise<SessionChangesResponse>;
  getCurrentDiff: (options?: { projectId?: string; sessionId?: string }) => Promise<CurrentDiffResponse>;
  getWorkspaceBranch: (projectId?: string) => Promise<WorkspaceBranchResponse>;
  getRevertPreview: (sessionId: string, messageId: string) => Promise<RevertPreviewResponse>;
  rollbackMessage: (sessionId: string, messageId: string, withCode?: boolean) => Promise<RollbackResponse>;
  streamRegenerateMessage: (sessionId: string, messageId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike) => Promise<void>;
  streamEditMessage: (
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; accessMode?: string },
  ) => Promise<void>;
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

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    if (!window.electronAPI) {
      throw new Error('Electron API is unavailable');
    }
    return window.electronAPI.sendChatMessage(request);
  }

  async sendMessageStream(request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `stream-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    try {
      await window.electronAPI.streamChatMessage(requestId, request, onEvent);
    } finally {
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

  async getWorkspaceTree(projectId?: string): Promise<WorkspaceTreeResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceTree(projectId);
  }

  async getWorkspaceDir(path: string, projectId?: string): Promise<WorkspaceDirResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceDir(path, projectId);
  }

  async getWorkspaceFile(path: string, projectId?: string): Promise<WorkspaceFileResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceFile(path, projectId);
  }

  async runWorkspaceCommand(request: WorkspaceCommandRequest): Promise<WorkspaceCommandResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.runWorkspaceCommand(request);
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

  async getRevertPreview(sessionId: string, messageId: string): Promise<RevertPreviewResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getRevertPreview(sessionId, messageId);
  }

  async rollbackMessage(sessionId: string, messageId: string, withCode = false): Promise<RollbackResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.rollbackMessage(sessionId, messageId, withCode);
  }

  async listAgentTraces(limit = 100): Promise<AgentTraceResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listAgentTraces(limit);
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
    try {
      await window.electronAPI.streamApprovalEvents(requestId, resumeId, onEvent);
    } finally {
      detachAbortListener();
    }
  }

  async streamRegenerateMessage(sessionId: string, messageId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `regenerate-${messageId}-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    try {
      await window.electronAPI.streamRegenerateMessage(requestId, sessionId, messageId, onEvent);
    } finally {
      detachAbortListener();
    }
  }

  async streamEditMessage(
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; accessMode?: string },
  ): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const signal = options?.signal;
    if (signal?.aborted) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    const requestId = `edit-${messageId}-${Date.now()}`;
    const abortStream = () => window.electronAPI?.abortChatStream(requestId);
    const detachAbortListener = attachAbortListener(signal, abortStream);
    try {
      await window.electronAPI.streamEditMessage(requestId, sessionId, messageId, content, onEvent, {
        ...(options?.workMode ? { work_mode: options.workMode } : {}),
        ...(options?.accessMode ? { access_mode: options.accessMode } : {}),
      });
    } finally {
      detachAbortListener();
    }
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
    return response.error ? { models: response.models, error: response.error } : { models: response.models };
  }

  async generateTitle(sessionId: string, firstUserMessage: string): Promise<string> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.generateTitle(sessionId, firstUserMessage);
    return response.title;
  }
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

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async sendMessageStream(request: ChatRequest, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    const response = await fetch(`${BACKEND_URL}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      ...(signal instanceof AbortSignal ? { signal } : {}),
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
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const line = frame.split('\n').find((item) => item.startsWith('data:'));
          if (!line) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            onEvent(JSON.parse(raw) as StreamEvent);
          } catch {
            // skip malformed frames
          }
        }
      }
      const remaining = buffer.split('\n').find((item) => item.startsWith('data:'));
      if (remaining) {
        try {
          onEvent(JSON.parse(remaining.slice(5).trim()) as StreamEvent);
        } catch {
          // skip
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  async openDirectoryPicker(): Promise<string | null> {
    throw new Error('Directory picker is only available in the desktop app');
  }

  async listSessions(): Promise<SessionsListResponse> {
    return this.request<SessionsListResponse>('/sessions');
  }

  async createSession(request: CreateSessionRequest): Promise<SessionResponse> {
    return this.request<SessionResponse>('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: request.title || '', project_id: request.project_id }),
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

  async getWorkspaceTree(projectId?: string): Promise<WorkspaceTreeResponse> {
    const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return this.request<WorkspaceTreeResponse>(`/workspace/tree${params}`);
  }

  async getWorkspaceDir(path: string, projectId?: string): Promise<WorkspaceDirResponse> {
    const params = new URLSearchParams({ path });
    if (projectId) params.set('project_id', projectId);
    return this.request<WorkspaceDirResponse>(`/workspace/dir?${params.toString()}`);
  }

  async getWorkspaceFile(path: string, projectId?: string): Promise<WorkspaceFileResponse> {
    const params = new URLSearchParams({ path });
    if (projectId) params.set('project_id', projectId);
    return this.request<WorkspaceFileResponse>(`/workspace/file?${params.toString()}`);
  }

  async runWorkspaceCommand(request: WorkspaceCommandRequest): Promise<WorkspaceCommandResponse> {
    return this.request<WorkspaceCommandResponse>('/workspace/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
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

  async getRevertPreview(sessionId: string, messageId: string): Promise<RevertPreviewResponse> {
    return this.request<RevertPreviewResponse>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/revert-preview`);
  }

  async rollbackMessage(sessionId: string, messageId: string, withCode = false): Promise<RollbackResponse> {
    return this.request<RollbackResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/rollback`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ with_code: withCode }) },
    );
  }

  async listAgentTraces(limit = 100): Promise<AgentTraceResponse> {
    return this.request<AgentTraceResponse>(`/traces/agent?limit=${encodeURIComponent(limit)}`);
  }

  async listCommandApprovals(): Promise<CommandApprovalsResponse> {
    return this.request<CommandApprovalsResponse>('/command-approvals');
  }

  async resolveCommandApproval(approvalId: string, decision: ApprovalDecisionPayload): Promise<CommandApprovalResponse> {
    return this.request<CommandApprovalResponse>('/command-approvals/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId, decision }),
    });
  }

  async subscribeApprovalEvents(resumeId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    const response = await fetch(`${BACKEND_URL}/command-approvals/events/${encodeURIComponent(resumeId)}`, {
      ...(signal instanceof AbortSignal ? { signal } : {}),
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
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const line = frame.split('\n').find((item) => item.startsWith('data:'));
          if (!line) continue;
          const raw = line.slice(5).trim();
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
  }

  async streamRegenerateMessage(sessionId: string, messageId: string, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    await this.streamPost(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate`, {}, onEvent, signal);
  }

  async streamEditMessage(
    sessionId: string,
    messageId: string,
    content: string,
    onEvent: StreamEventCallback,
    options?: { signal?: AbortSignalLike; workMode?: string; accessMode?: string },
  ): Promise<void> {
    const payload: Record<string, unknown> = { content };
    if (options?.workMode) payload.work_mode = options.workMode;
    if (options?.accessMode) payload.access_mode = options.accessMode;
    await this.streamPost(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit`, payload, onEvent, options?.signal);
  }

  private async streamPost(path: string, payload: Record<string, unknown>, onEvent: StreamEventCallback, signal?: AbortSignalLike): Promise<void> {
    const response = await fetch(`${BACKEND_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      ...(signal instanceof AbortSignal ? { signal } : {}),
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
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const line = frame.split('\n').find((item) => item.startsWith('data:'));
          if (!line) continue;
          const raw = line.slice(5).trim();
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

  async generateTitle(sessionId: string, firstUserMessage: string): Promise<string> {
    const response = await this.request<{ status: string; title: string }>(`/sessions/${sessionId}/generateTitle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ first_user_message: firstUserMessage }),
    });
    return response.title;
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
