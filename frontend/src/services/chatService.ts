import type {
  ChatRequest,
  ChatResponse,
  CommandApprovalResponse,
  CommandApprovalsResponse,
  ProjectResponse,
  ProjectsListResponse,
  ProviderPayload,
  ProvidersListResponse,
  ProviderTestResult,
  ProviderUpdatePayload,
  RuntimeConfig,
  RuntimeConfigUpdate,
  SessionDetailResponse,
  SessionResponse,
  SessionsListResponse,
  StreamEvent,
  ToolAuditResponse,
  WorkspaceCommandRequest,
  WorkspaceCommandResponse,
  WorkspaceDirResponse,
  WorkspaceFileResponse,
  WorkspaceTreeResponse,
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
  listSessions: () => Promise<SessionsListResponse>;
  createSession: (title?: string) => Promise<SessionResponse>;
  deleteSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<SessionResponse>;
  getSession: (sessionId: string) => Promise<SessionDetailResponse>;
  moveSession: (sessionId: string, projectId: string) => Promise<SessionResponse>;
  listProjects: () => Promise<ProjectsListResponse>;
  createProject: (name: string) => Promise<ProjectResponse>;
  renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
  deleteProject: (projectId: string) => Promise<void>;
  getWorkspaceTree: () => Promise<WorkspaceTreeResponse>;
  getWorkspaceDir: (path: string) => Promise<WorkspaceDirResponse>;
  getWorkspaceFile: (path: string) => Promise<WorkspaceFileResponse>;
  runWorkspaceCommand: (request: WorkspaceCommandRequest) => Promise<WorkspaceCommandResponse>;
  listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
  listCommandApprovals: () => Promise<CommandApprovalsResponse>;
  approveCommand: (approvalId: string) => Promise<CommandApprovalResponse>;
  denyCommand: (approvalId: string) => Promise<CommandApprovalResponse>;
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

  async listSessions(): Promise<SessionsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listSessions();
  }

  async createSession(title?: string): Promise<SessionResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.createSession(title || '');
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

  async moveSession(sessionId: string, projectId: string): Promise<SessionResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.moveSession(sessionId, projectId);
  }

  async listProjects(): Promise<ProjectsListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listProjects();
  }

  async createProject(name: string): Promise<ProjectResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.createProject(name);
  }

  async renameProject(projectId: string, name: string): Promise<ProjectResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.renameProject(projectId, name);
  }

  async deleteProject(projectId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteProject(projectId);
  }

  async getWorkspaceTree(): Promise<WorkspaceTreeResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceTree();
  }

  async getWorkspaceDir(path: string): Promise<WorkspaceDirResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceDir(path);
  }

  async getWorkspaceFile(path: string): Promise<WorkspaceFileResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.getWorkspaceFile(path);
  }

  async runWorkspaceCommand(request: WorkspaceCommandRequest): Promise<WorkspaceCommandResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.runWorkspaceCommand(request);
  }

  async listToolAudit(limit = 100): Promise<ToolAuditResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listToolAudit(limit);
  }

  async listCommandApprovals(): Promise<CommandApprovalsResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listCommandApprovals();
  }

  async approveCommand(approvalId: string): Promise<CommandApprovalResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.approveCommand(approvalId);
  }

  async denyCommand(approvalId: string): Promise<CommandApprovalResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.denyCommand(approvalId);
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

  async listSessions(): Promise<SessionsListResponse> {
    return this.request<SessionsListResponse>('/sessions');
  }

  async createSession(title?: string): Promise<SessionResponse> {
    return this.request<SessionResponse>('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title || '' }),
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

  async moveSession(sessionId: string, projectId: string): Promise<SessionResponse> {
    return this.request<SessionResponse>(`/sessions/${sessionId}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    });
  }

  async listProjects(): Promise<ProjectsListResponse> {
    return this.request<ProjectsListResponse>('/projects');
  }

  async createProject(name: string): Promise<ProjectResponse> {
    return this.request<ProjectResponse>('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
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

  async getWorkspaceTree(): Promise<WorkspaceTreeResponse> {
    return this.request<WorkspaceTreeResponse>('/workspace/tree');
  }

  async getWorkspaceDir(path: string): Promise<WorkspaceDirResponse> {
    return this.request<WorkspaceDirResponse>(`/workspace/dir?path=${encodeURIComponent(path)}`);
  }

  async getWorkspaceFile(path: string): Promise<WorkspaceFileResponse> {
    return this.request<WorkspaceFileResponse>(`/workspace/file?path=${encodeURIComponent(path)}`);
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

  async listCommandApprovals(): Promise<CommandApprovalsResponse> {
    return this.request<CommandApprovalsResponse>('/command-approvals');
  }

  async approveCommand(approvalId: string): Promise<CommandApprovalResponse> {
    return this.request<CommandApprovalResponse>('/command-approvals/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId }),
    });
  }

  async denyCommand(approvalId: string): Promise<CommandApprovalResponse> {
    return this.request<CommandApprovalResponse>('/command-approvals/deny', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: approvalId }),
    });
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
