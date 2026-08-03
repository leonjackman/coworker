export type AgentMode = 'single';
export type Language = 'zh' | 'en';
export type WorkMode = 'plan' | 'build';
export type AccessMode = 'default' | 'full';
export type AppView = 'chat' | 'providers' | 'settings';

export interface ComposerAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
  content?: string;
  truncated?: boolean;
  binary?: boolean;
  error?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  status?: 'queued' | 'running' | 'done' | 'stopped' | 'error';
  work_mode?: WorkMode;
  access_mode?: AccessMode;
  provider?: string;
  model?: string;
  attachments?: ComposerAttachment[];
}

export interface RuntimeConfig {
  workspace: string;
  data_dir: string;
  default_mode: AgentMode;
  agent_provider: string;
  available_modes: AgentMode[];
  selected_provider_id: string;
  selected_model: string;
}

export interface RuntimeConfigUpdate {
  selected_provider_id?: string;
  selected_model?: string;
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  mode: AgentMode;
  language: Language;
  work_mode?: WorkMode;
  access_mode?: AccessMode;
  provider_id?: string;
  model?: string;
  project_id?: string;
  attachments?: ComposerAttachment[];
}

export interface ChatResponse {
  response: string;
  session_id: string;
  mode: AgentMode;
  provider: string;
}

export interface ProviderEntry {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  api_key_present: boolean;
  api_key_preview: string;
  model: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProvidersListResponse {
  status: string;
  providers: ProviderEntry[];
  default_provider_id: string;
  default_model: string;
}

export interface ProviderPayload {
  name: string;
  provider_type: string;
  base_url: string;
  api_key: string;
  model: string;
}

export interface ProviderUpdatePayload {
  name?: string;
  base_url?: string;
  api_key?: string;
  model?: string;
  enabled?: boolean;
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms?: number | null;
  error?: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  project_id: string;
  work_mode?: string;
  access_mode?: string;
  message_count: number;
}

export interface ProjectEntry {
  id: string;
  name: string;
  workspace_path: string;
  workspace_available: boolean;
  created_at: string;
  updated_at: string;
  session_count: number;
}

export interface CreateProjectRequest {
  name: string;
  workspace_path: string;
}

export interface CreateSessionRequest {
  title?: string;
  project_id: string;
}

export interface SessionMessageRecord {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  mode?: string;
  provider?: string;
  model?: string;
  attachments?: ComposerAttachment[];
}

export interface SessionDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  project_id: string;
  work_mode: string;
  access_mode: string;
  messages: SessionMessageRecord[];
}

export interface SessionsListResponse {
  status: string;
  sessions: SessionSummary[];
}

export interface ProjectsListResponse {
  status: string;
  projects: ProjectEntry[];
}

export interface ProjectResponse {
  status: string;
  project: ProjectEntry;
}

export interface SessionResponse {
  status: string;
  session: SessionSummary;
}

export interface SessionDetailResponse {
  status: string;
  session: SessionDetail;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size?: number | null;
  children?: FileTreeNode[];
}

export interface WorkspaceTreeResponse {
  status: string;
  root: string;
  tree: FileTreeNode;
}

export interface WorkspaceDirResponse {
  status: string;
  path: string;
  entries: FileTreeNode[];
}

export interface WorkspaceFileResponse {
  status: string;
  path: string;
  file: {
    content: string | null;
    binary: boolean;
    size: number;
    truncated?: boolean;
  };
}

export interface WorkspaceCommandRequest {
  command: string;
  cwd?: string;
  timeout_seconds?: number;
  project_id?: string;
}

export interface WorkspaceCommandResult {
  command: string[];
  cwd: string;
  return_code: number | null;
  timed_out: boolean;
  stdout: string;
  stderr: string;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  approval_required?: boolean;
  approval_id?: string;
  approval_status?: string;
}

export interface WorkspaceCommandResponse {
  status: string;
  result: WorkspaceCommandResult;
}

export interface ToolAuditEvent {
  timestamp: string;
  operation: string;
  status: string;
  context?: Record<string, unknown>;
  details?: Record<string, unknown>;
}

export interface ToolAuditResponse {
  status: string;
  events: ToolAuditEvent[];
}

export interface AgentTraceEvent {
  timestamp: string;
  event: string;
  status: string;
  context?: Record<string, unknown>;
  details?: Record<string, unknown>;
}

export interface AgentTraceResponse {
  status: string;
  events: AgentTraceEvent[];
}

export interface CommandApproval {
  id: string;
  digest: string;
  status: 'pending' | 'approved' | 'denied' | 'answered' | 'consumed';
  command: string[];
  cwd: string;
  timeout_seconds: number;
  context?: Record<string, unknown>;
  decision?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CommandApprovalsResponse {
  status: string;
  approvals: CommandApproval[];
}

export interface CommandApprovalResponse {
  status: string;
  approval: CommandApproval;
  events?: StreamEvent[];
  resumed?: boolean;
}

export type ApprovalDecisionType = 'approve' | 'reject' | 'respond' | 'always';

export interface ApprovalDecisionPayload {
  type: ApprovalDecisionType;
  message?: string;
}

export interface ApprovalOption {
  label: string;
  description?: string;
}

export interface PendingRequest {
  approval_id: string;
  kind: 'command' | 'question';
  session_id: string;
  approval_status: string;
  messageId: string;
  resolving?: boolean;
  command?: string[];
  cwd?: string;
  question?: string;
  header?: string;
  options?: ApprovalOption[];
  multiple?: boolean;
}

export type StreamEvent =
  | { type: 'start'; session_id: string; mode: AgentMode; provider: string; model: string }
  | { type: 'stage'; name: string; status: string }
  | { type: 'delta'; content: string }
  | {
      type: 'approval_required';
      approval_id: string;
      command: string[];
      cwd: string;
      approval_status: string;
      session_id?: string;
    }
  | {
      type: 'question_required';
      approval_id: string;
      question: string;
      header?: string;
      options?: ApprovalOption[];
      multiple?: boolean;
      approval_status: string;
      session_id?: string;
    }
  | { type: 'done'; content: string; session_id: string; mode?: AgentMode; provider?: string; model?: string }
  | { type: 'error'; error: string; session_id?: string };
