import type {
  AgentTraceResponse,
  ApprovalDecisionPayload,
  ChatRequest,
  ChatResponse,
  CommandApprovalResponse,
  CommandApprovalsResponse,
  CreateProjectRequest,
  CreateSessionRequest,
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
} from './types';

export type StreamEventCallback = (event: StreamEvent) => void;

declare global {
  interface Window {
    electronAPI?: {
      platform: string;
      getRuntimeConfig: () => Promise<RuntimeConfig>;
      updateRuntimeConfig: (payload: RuntimeConfigUpdate) => Promise<RuntimeConfig>;
      sendChatMessage: (payload: ChatRequest) => Promise<ChatResponse>;
      streamChatMessage: (requestId: string, payload: ChatRequest, onEvent: StreamEventCallback) => Promise<void>;
      abortChatStream: (requestId: string) => void;
      listProviders: () => Promise<ProvidersListResponse>;
      createProvider: (payload: ProviderPayload) => Promise<{ status: string }>;
      updateProvider: (providerId: string, params: ProviderUpdatePayload) => Promise<{ status: string }>;
      deleteProvider: (providerId: string) => Promise<{ status: string }>;
      setDefaultProvider: (payload: { provider_id: string; model: string }) => Promise<{ status: string }>;
      testProvider: (payload: { base_url: string; api_key: string; model: string }) => Promise<{ status: string; result: ProviderTestResult }>;
      fetchProviderModels: (payload: { base_url: string; api_key: string; provider_type: string }) => Promise<{ status: string; models: string[]; error?: string }>;
      listSessions: () => Promise<SessionsListResponse>;
      createSession: (payload: CreateSessionRequest) => Promise<SessionResponse>;
      deleteSession: (sessionId: string) => Promise<{ status: string }>;
      renameSession: (sessionId: string, title: string) => Promise<SessionResponse>;
      getSession: (sessionId: string) => Promise<SessionDetailResponse>;
      listProjects: () => Promise<ProjectsListResponse>;
      createProject: (payload: CreateProjectRequest) => Promise<ProjectResponse>;
      openDirectoryPicker: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
      renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
      deleteProject: (projectId: string) => Promise<{ status: string }>;
      getWorkspaceTree: (projectId?: string) => Promise<WorkspaceTreeResponse>;
      getWorkspaceDir: (path: string, projectId?: string) => Promise<WorkspaceDirResponse>;
      getWorkspaceFile: (path: string, projectId?: string) => Promise<WorkspaceFileResponse>;
      runWorkspaceCommand: (payload: WorkspaceCommandRequest) => Promise<WorkspaceCommandResponse>;
      listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
      listAgentTraces: (limit?: number) => Promise<AgentTraceResponse>;
      listCommandApprovals: () => Promise<CommandApprovalsResponse>;
      resolveCommandApproval: (approvalId: string, decision: CommandApprovalDecision) => Promise<CommandApprovalResult>;
    };
  }
}

export {};
