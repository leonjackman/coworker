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
  ToolAuditResponse,  WorkspaceCommandRequest,
  WorkspaceCommandResponse,
  WorkspaceDirResponse,
  WorkspaceFileResponse,
  WorkspaceTreeResponse,
  WorkspaceBranchResponse,
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
      streamApprovalEvents: (requestId: string, resumeId: string, onEvent: StreamEventCallback) => Promise<void>;
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
      generateTitle: (sessionId: string, firstUserMessage: string) => Promise<{ status: string; title: string }>;
      listProjects: () => Promise<ProjectsListResponse>;
      createProject: (payload: CreateProjectRequest) => Promise<ProjectResponse>;
      openDirectoryPicker: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
      renameProject: (projectId: string, name: string) => Promise<ProjectResponse>;
      deleteProject: (projectId: string) => Promise<{ status: string }>;
      getWorkspaceTree: (projectId?: string) => Promise<WorkspaceTreeResponse>;
      getWorkspaceDir: (path: string, projectId?: string) => Promise<WorkspaceDirResponse>;
      getWorkspaceFile: (path: string, projectId?: string) => Promise<WorkspaceFileResponse>;
      getWorkspaceBranch: (projectId?: string) => Promise<WorkspaceBranchResponse>;
      runWorkspaceCommand: (payload: WorkspaceCommandRequest) => Promise<WorkspaceCommandResponse>;
      listToolAudit: (limit?: number) => Promise<ToolAuditResponse>;
      listAgentTraces: (limit?: number) => Promise<AgentTraceResponse>;
      listCommandApprovals: () => Promise<CommandApprovalsResponse>;
      resolveCommandApproval: (approvalId: string, decision: CommandApprovalDecision) => Promise<CommandApprovalResult>;
      getSessionChanges: (sessionId: string) => Promise<SessionChangesResponse>;
      getCurrentDiff: (options?: { projectId?: string; sessionId?: string }) => Promise<CurrentDiffResponse>;
      getRevertPreview: (sessionId: string, messageId: string) => Promise<RevertPreviewResponse>;
      rollbackMessage: (sessionId: string, messageId: string, withCode?: boolean) => Promise<RollbackResponse>;
      streamRegenerateMessage: (requestId: string, sessionId: string, messageId: string, onEvent: StreamEventCallback) => Promise<void>;
      streamEditMessage: (
        requestId: string,
        sessionId: string,
        messageId: string,
        content: string,
        onEvent: StreamEventCallback,
        options?: { work_mode?: string; access_mode?: string },
      ) => Promise<void>;
    };
  }
}

export {};
