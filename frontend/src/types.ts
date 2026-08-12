export type AgentMode = 'single';
export type Language = 'zh' | 'en';
export type WorkMode = 'plan' | 'build';
export type Autonomy = 'supervised' | 'guarded' | 'autonomous';
export type AppView = 'chat' | 'providers' | 'settings' | 'mcp' | 'skills' | 'memory';

export interface McpToolEntry {
  name: string;
  description: string;
}

export interface McpServerEntry {
  id: string;
  name: string;
  transport: string;
  command: string;
  args: string;
  cwd: string;
  timeout: number | null;
  url: string;
  env: Record<string, string>;
  headers: Record<string, string>;
  enabled: boolean;
  status: string;
  error_message: string;
  tool_count: number;
  tools: McpToolEntry[];
  trusted: boolean;
  disabled_tools: string[];
  last_checked_at: string;
  created_at: string;
  updated_at: string;
}

export interface McpTemplateEntry {
  id: string;
  name: string;
  description: string;
  transport: string;
  command: string;
  args: string;
  url: string;
  env: Record<string, string>;
  headers?: Record<string, string>;
  color?: string;
  category?: string;
}

export interface McpServerCreateRequest {
  name: string;
  transport: string;
  command?: string;
  args?: string;
  cwd?: string;
  timeout?: number | null;
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
}

export interface McpServerUpdateRequest {
  name?: string;
  transport?: string;
  enabled?: boolean;
  command?: string;
  args?: string;
  cwd?: string;
  timeout?: number | null;
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
  trusted?: boolean;
  disabled_tools?: string[];
}

export interface McpTestRequest {
  transport: string;
  command?: string;
  args?: string;
  cwd?: string;
  timeout?: number | null;
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
  server_id?: string;
}

export interface McpTestResult {
  ok: boolean;
  latency_ms: number | null;
  error?: string;
  tool_count?: number;
}

export interface McpDiscoverPayload {
  status: string;
  servers: McpTemplateEntry[];
}

export interface McpServerListPayload {
  status: string;
  servers: McpServerEntry[];
}

export interface ComposerAttachment {
  id: string;
  name: string;
  size: number;
  type: string;
  /** 文本内容（文本附件）或 base64 data URL（二进制附件，可含图片） */
  content?: string;
  truncated?: boolean;
  binary?: boolean;
  /** 文件过大，未能内联字节（仅保留元信息） */
  tooLarge?: boolean;
  /** 正在读取字节（乐观占位，用于「读取中」反馈） */
  uploading?: boolean;
  /** 添加失败（超限或读取错误），不会进入发送列表，仅用于错误提示 */
  rejected?: boolean;
  error?: string;
}

export interface SessionReference {
  id: string;
  title: string;
}

export interface DiffLine {
  type: 'context' | 'del' | 'add';
  old_no: number | null;
  new_no: number | null;
  text: string;
}

export interface DiffHunk {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  lines: DiffLine[];
}

export interface PartFileChange {
  path: string;
  kind: 'write' | 'edit';
  added: number;
  removed: number;
  hunks?: DiffHunk[];
  truncated?: boolean;
  too_large?: boolean;
}

export interface SessionChangeRecord extends PartFileChange {
  id: string;
  session_id: string;
  turn_index: number;
  tool_name: string;
  file_path: string;
  before?: string;
  after?: string;
  timestamp: string;
}

export interface SessionChangesResponse {
  status: string;
  session_id: string;
  turns: Array<{ turn_index: number; changes: SessionChangeRecord[] }>;
  count: number;
}

export interface CurrentDiffFile {
  path: string;
  added: number;
  removed: number;
  binary: boolean;
  diff: string;
}

export interface CurrentDiffResponse {
  status: string;
  git: boolean;
  workspace: string;
  files: CurrentDiffFile[];
  untracked: string[];
  truncated_diff: boolean;
  note: string;
}

export interface RevertChangeItem {
  id: string;
  path: string;
  kind?: string;
  added: number;
  removed: number;
  deleted?: boolean;
  noop?: boolean;
}

export interface RevertPreviewResponse {
  status: string;
  changes: SessionChangeRecord[];
  count: number;
}

export interface RevertSummary {
  reverted: RevertChangeItem[];
  conflicts: Array<{ status: string; path: string; reason: string; id?: string }>;
  total: number;
  reverted_count: number;
  conflict_count: number;
}

export interface RollbackResponse {
  status: string;
  messages: SessionMessageRecord[];
  revert: RevertSummary;
}

export interface PartTool {
  type: 'tool';
  id: string;
  name: string;
  /** 'running' = actively executing (spinner); 'pending' = interrupted/awaiting
   *  approval or aborted before completion (static, non-spinning). */
  status: 'running' | 'success' | 'error' | 'pending';
  input: string;
  output?: string;
  duration_ms?: number;
  files?: PartFileChange[];
}

export interface PartReasoning {
  type: 'reasoning';
  content: string;
  heading?: string;
  done?: boolean;
}

export interface PartPlan {
  type: 'plan';
  content: string;
}

export interface PartText {
  type: 'text';
  content: string;
}

export type MessagePart = PartTool | PartReasoning | PartPlan | PartText;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  sessionId?: string;
  status?: 'queued' | 'running' | 'done' | 'stopped' | 'error';
  streamStartAt?: number;
  streamEndAt?: number;
  work_mode?: WorkMode;
  autonomy?: Autonomy;
  provider?: string;
  model?: string;
  attachments?: ComposerAttachment[];
  parts?: MessagePart[];
  references?: SessionReference[];
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
  autonomy?: Autonomy;
  access_mode?: 'default' | 'full';
  goal_mode?: boolean;
  goal_text?: string;
  provider_id?: string;
  model?: string;
  project_id?: string;
  attachments?: ComposerAttachment[];
  referenced_sessions?: string[];
  // 前端乐观渲染时生成的消息 id，回传后端以统一前后端 id（修复回退/重生成时 404）
  user_message_id?: string;
  assistant_message_id?: string;
  // 「文件体积上限」设置换算成的字节数，后端按此如实处理附件
  max_attachment_bytes?: number;
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
  autonomy?: string;
  message_count: number;
  goal_done?: boolean;
  goal_paused?: boolean;
  goal_text?: string;
  goal_todos?: GoalTodo[];
  goal_max_rounds?: number;
  goal_force_count?: number;
  goal_stopped?: boolean;
  goal_just_edited?: boolean;
  goal_stream_id?: string;
  goal_interrupted?: boolean;
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
  work_mode?: string;
  autonomy?: string;
  attachments?: ComposerAttachment[];
  parts?: MessagePart[];
  references?: SessionReference[];
}

export interface SessionDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  project_id: string;
  work_mode: string;
  autonomy: string;
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

export interface WorkspaceBranchResponse {
  status: string;
  is_repo: boolean;
  branch: string | null;
  workspace?: string;
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
  resume_id?: string;
}

export type ApprovalDecisionType = 'approve' | 'reject' | 'respond' | 'always' | 'regenerate' | 'continue_discuss';

export interface ApprovalDecisionPayload {
  type: ApprovalDecisionType;
  message?: string;
  autonomy?: Autonomy;
}

export interface ApprovalOption {
  label: string;
  description?: string;
}

export interface PendingRequest {
  approval_id: string;
  kind: 'command' | 'question' | 'plan' | 'mcp';
  session_id: string;
  approval_status: string;
  messageId: string;
  resolving?: boolean;
  command?: string[];
  cwd?: string;
  // MCP tool approval (kind === 'mcp'): the call leaves the workspace sandbox,
  // so the card shows tool + server + args instead of an argv line.
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  server_name?: string;
  server_id?: string;
  remote_name?: string;
  read_only?: boolean;
  destructive?: boolean;
  question?: string;
  header?: string;
  options?: ApprovalOption[];
  multiple?: boolean;
  allowCustom?: boolean;
  plan?: string;
  // Internal: preserved form state across remount (not sent to backend)
  _savedPicked?: number[];
  _savedAnswer?: string;
}

 export type StreamEvent =
    | { type: 'start'; session_id: string; mode: AgentMode; provider: string; model: string }
    | { type: 'stage'; name: string; status: string; session_id?: string }
    | { type: 'delta'; content: string; session_id?: string }
    | { type: 'reasoning_delta'; content: string; session_id?: string }
    | { type: 'tool_start'; id: string; name: string; input: string; session_id?: string }
    | { type: 'tool_delta'; id: string; input: string; session_id?: string }
    | { type: 'tool_end'; id: string; output: string; status: string; duration_ms?: number; files?: PartFileChange[]; session_id?: string }
    | { type: 'plan_start'; session_id?: string }
    | { type: 'plan_delta'; content: string; session_id?: string }
    | { type: 'plan_end'; content: string; session_id?: string }
    | { type: 'agent_activity'; name: string; status: string; session_id?: string }
    | {
        type: 'approval_required';
        approval_id: string;
        command: string[];
        cwd: string;
        approval_status: string;
        session_id?: string;
        /** `'mcp'` marks an external MCP tool call; omitted/`'command'` is a workspace command. */
        kind?: 'command' | 'mcp';
        tool_name?: string;
        tool_args?: Record<string, unknown>;
        server_name?: string;
        server_id?: string;
        remote_name?: string;
        read_only?: boolean;
        destructive?: boolean;
      }
    | {
        type: 'question_required';
        approval_id: string;
        question: string;
        header?: string;
        options?: ApprovalOption[];
        multiple?: boolean;
        allowCustom?: boolean;
        approval_status: string;
        session_id?: string;
      }
    | {
        type: 'plan_required';
        approval_id: string;
        plan: string;
        approval_status: string;
        session_id?: string;
      }
    | { type: 'done'; content: string; session_id: string; mode?: AgentMode; provider?: string; model?: string; parts?: MessagePart[] }
    | { type: 'error'; error: string; session_id?: string }
    | { type: 'goal_start'; goal: string; session_id?: string }
    | { type: 'goal_round'; round: number; goal: string; status?: string; session_id?: string }
    | { type: 'goal_checkpoint'; achieved: boolean; progress?: string; verification?: string; session_id?: string }
    | { type: 'goal_done'; goal?: string; content?: string; verification?: string; round?: number; session_id?: string; stalled?: boolean; reason?: string; already?: boolean }
    | { type: 'goal_paused'; goal?: string; round?: number; session_id?: string }
    | { type: 'goal_force'; round: number; reason: string; count: number; session_id?: string }
    | { type: 'todos'; todos: GoalTodo[]; session_id?: string }
    | { type: 'goal_stream_id'; stream_id: string; session_id: string }
    | { type: 'goal_system'; content: string; session_id?: string }
    | { type: 'goal_attached'; stream_id: string; session_id: string };

export interface GoalTodo {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

export interface GoalState {
  goalText: string;
  done: boolean;
  paused: boolean;
  todos: GoalTodo[];
  running: boolean;
  round: number;
  progress: string;
  stalled?: boolean;
  reason?: string;
  verification?: string;
  editingDraft?: boolean;
  recentToolNames?: string[];
}

export interface GoalStatusResponse {
  status: string;
  session_id: string;
  goal: GoalState;
  goal_stream_id?: string;
  goal_interrupted?: boolean;
}

// -- Skills -----------------------------------------------------------------

export interface SkillEntry {
  name: string;
  description: string;
  file_path: string;
  base_dir: string;
  source: string;
  version: string;
  disable_model_invocation: boolean;
  enabled: boolean;
  commands?: Array<{ name: string; description?: string }>;
  body?: string;
}

export interface SkillDiagnostic {
  type: string;
  name: string;
  path: string | null;
  message: string;
}

export interface SkillsListResponse {
  status: string;
  skills: SkillEntry[];
  diagnostics: SkillDiagnostic[];
  count: number;
}

export interface SkillDetailResponse {
  status: string;
  skill: SkillEntry;
}

export interface SkillDeleteResponse {
  status: string;
  name: string;
  removed: boolean;
}

export interface SkillUpdateRequest {
  enabled?: boolean;
  permission?: string;
}

export interface SkillValidateRequest {
  path: string;
  name?: string;
}

export interface SkillValidateResponse {
  status: string;
  valid: boolean;
  skill: SkillEntry | null;
  diagnostics: SkillDiagnostic[];
}

// -- Skill Market -------------------------------------------------------------

export interface MarketSource {
  id: string;
  name: string;
  description?: string;
}

export interface MarketSkill {
  /** Globally unique across sources. ClawHub slugs collide across owners, so
   *  never use `slug` as a React key or dedupe key — use `uid`. */
  uid: string;
  slug: string;
  name: string;
  description: string;
  score?: number;
  source: string;
  category?: string | null;
  /** Owner handle; required to disambiguate colliding ClawHub slugs on install. */
  owner?: string | null;
  icon_url?: string | null;
  version?: string | null;
  verified?: boolean;
}

export interface MarketCategory {
  key: string;
  name: string;
  name_en: string;
  sort_order?: number;
}

/** Single object carried unchanged through service → IPC → HTTP.
 *  Positional args previously let `offset` get silently dropped mid-chain. */
export interface MarketQuery {
  source: string;
  q?: string;
  limit?: number;
  offset?: number;
  /** Opaque cursor (ClawHub). Takes precedence over `offset` when present. */
  cursor?: string | null;
  category?: string | null;
  /** Ordering key, for sources whose facet is `sort` rather than `category`. */
  sort?: string | null;
}

export interface MarketSourceResponse {
  status: string;
  sources: MarketSource[];
}

/** Which dimension a source can actually slice on.
 *  SkillHub publishes a category vocabulary; ClawHub has none upstream but does
 *  expose a real server-side ordering, so its bar drives `sort` instead. */
export type MarketFacetKind = 'category' | 'sort';

export interface MarketCategoriesResponse {
  status: string;
  /** Absent on older backends — treat as 'category'. */
  kind?: MarketFacetKind;
  /** Tab selected on first paint ('all' for categories, a sort key otherwise). */
  default?: string;
  categories: MarketCategory[];
  count: number;
}

export interface MarketSkillsResponse {
  status: string;
  skills: MarketSkill[];
  count: number;
  /** Total matching rows upstream; null when the source cannot report it. */
  total?: number | null;
  /** Authoritative "load more" signal — never infer it from page length. */
  has_more?: boolean;
  next_cursor?: string | null;
}

export interface MarketInstallResponse {
  status: string;
  skill: SkillEntry | null;
  message?: string;
}

// -- Long-term memory --------------------------------------------------------

export type MemoryScope = 'project' | 'user';

export interface MemoryScopeInfo {
  path: string;
  mtime: number;
  entries: string[];
  char_count: number;
  entry_count: number;
}

export interface MemoryStatusResponse {
  enabled: boolean;
  auto_extract: boolean;
  nudge_interval: number;
  char_limit: number;
  scopes: Partial<Record<MemoryScope, MemoryScopeInfo>>;
  proposals_pending: number;
}

export interface MemoryWriteRequest {
  scope: MemoryScope;
  content: string;
  target?: string;
}

export interface MemoryWriteResponse {
  scope: MemoryScope;
  entries: string[];
}

export interface MemoryProposalRecord {
  id: string;
  kind: string;
  status: string;
  text: string;
  session_id: string;
  provider: string;
  model: string;
  workspace_path: string;
  created_at: string;
}

export interface MemoryProposalsResponse {
  proposals: MemoryProposalRecord[];
}

export interface MemoryProposalResolveRequest {
  proposal_id: string;
  status: 'approved' | 'rejected';
}
