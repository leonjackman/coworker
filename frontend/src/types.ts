import type { Language } from './lib/i18n';

export type AgentMode = 'single';
export type WorkMode = 'plan' | 'build';
export type Autonomy = 'supervised' | 'guarded' | 'autonomous';
export type AppView = 'chat' | 'providers' | 'settings' | 'mcp' | 'skills' | 'memory' | 'org' | 'dashboard';

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

export interface RedoResponse {
  status: string;
  session_id: string;
  message_id: string;
  restored: Array<{ id?: string; path: string; kind?: string }>;
  conflicts: Array<{ status: string; path: string; reason: string; id?: string }>;
  restored_count: number;
  conflict_count: number;
}

export interface EditBeginResponse {
  status: string;
  session_id: string;
  message_id: string;
  reverted_count: number;
  conflict_count: number;
  total: number;
  reverted_paths?: string[];
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

export interface PartAgent {
  type: 'agent';
  /** Unique worker run id; key for the dedicated `/worker-events/{id}` stream. */
  workerRunId: string;
  from: string;
  to: string | string[];
  agent?: string;
  task?: string | undefined;
  status: 'running' | 'done' | 'error';
  parallel?: boolean | undefined;
  chars?: number | undefined;
  failed?: string[] | undefined;
  error?: string | undefined;
  /** Nested worker transcript, built lazily when the block is expanded. */
  parts: MessagePart[];
  /** True once the worker stream has been (or is being) fetched/subscribed. */
  transcriptLoaded?: boolean;
  /** True once the worker stream reached a terminal state (done/error/end). */
  done?: boolean;
}

export interface PartText {
  type: 'text';
  content: string;
}

/** A user interjection (插話) consumed by the running turn. Rendered as a small
 *  inline notice inside the assistant bubble so the steer is visible in the
 *  transcript (persisted via done.parts). */
export interface PartSteer {
  type: 'steer';
  content: string;
  steer_id?: string;
}

export type MessagePart = PartTool | PartReasoning | PartPlan | PartAgent | PartText | PartSteer;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  sessionId?: string;
  status?: 'waiting' | 'running' | 'done' | 'stopped' | 'error' | 'interrupted';
  streamStartAt?: number;
  streamEndAt?: number;
  work_mode?: WorkMode;
  autonomy?: Autonomy;
  provider?: string;
  model?: string;
  attachments?: ComposerAttachment[];
  parts?: MessagePart[];
  references?: SessionReference[];
  /** Number of file changes reverted by editing this user message (awaiting redo). */
  revertedFiles?: number;
  /** This user message was sent as an interjection (插話) into a running task. */
  interject?: boolean;
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
  provider_id?: string;
  model?: string;
  project_id?: string;
  agent?: string;
  attachments?: ComposerAttachment[];
  referenced_sessions?: string[];
  // 前端乐观渲染时生成的消息 id，回传后端以统一前后端 id（修复回退/重生成时 404）
  user_message_id?: string;
  assistant_message_id?: string;
  // 「文件体积上限」设置换算成的字节数，后端按此如实处理附件
  max_attachment_bytes?: number;
  // 插話 (interject) 自动续跑：user 消息已由 /chat/interject 持久化，后端不再
  // append，直接以 history 作为模型输入（避免重复写库与重复送上下文）。
  skip_user_append?: boolean;
}

/** 插話 (interject)：把一条排队消息插入正在进行的流式任务，引导 LLM 后续输出
 *  与思考方向，而不暂停/终止当前流。 */
export interface InterjectRequest {
  session_id: string;
  message: string;
  // 前端乐观渲染时生成的 user 消息 id，与后端 /chat/interject 持久化的 id 一致
  // （late-steer 自动续跑以同一 id + skip_user_append 复用 history）。
  user_message_id?: string;
  // 前端生成的 steer id：steer_injected 事件原样带回，前端据此从 pending 列表
  // 移除已注入的插话，避免被误判为 late-steer 而二次执行。
  steer_id?: string;
  attachments?: ComposerAttachment[];
  referenced_sessions?: string[];
  max_attachment_bytes?: number;
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
  context_window?: number;
  context_source?: string;
  context_error?: string;
  max_output_tokens?: number;
  /** Multimodal (vision) capability: screenshots/images are sent as native
   *  image blocks instead of being externalized to disk. */
  vision?: boolean;
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
  context_window?: number;
  max_output_tokens?: number;
  vision?: boolean;
}

export interface ProviderUpdatePayload {
  name?: string;
  base_url?: string;
  api_key?: string;
  model?: string;
  enabled?: boolean;
  context_window?: number;
  max_output_tokens?: number;
  vision?: boolean;
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
  agent_id?: string;
  work_mode?: string;
  autonomy?: string;
  message_count: number;
  todos?: Todo[];
  goal?: GoalState | null;
}

export type GoalStatus =
  | 'active'
  | 'paused'
  | 'blocked'
  | 'complete'
  | 'budget_limited'
  | 'usage_limited';

export interface GoalState {
  objective: string;
  status: GoalStatus;
  token_budget: number | null;
  tokens_used: number;
  time_used_seconds: number;
  round: number;
  created_at: number;
  updated_at: number;
}

export interface GoalResponse {
  status: string;
  goal: GoalState | null;
}

/** 前端为 /goal user 泡泡生成的元数据，随 /goal/set 持久化，保证重载后泡泡不消失。 */
export interface GoalSetMeta {
  userMessageId: string;
  provider: string;
  model: string;
  workMode: string;
  autonomy: string;
}

export type ProjectMode = 'single' | 'multi';

export interface ProjectEntry {
  id: string;
  name: string;
  workspace_path: string;
  workspace_available: boolean;
  created_at: string;
  updated_at: string;
  session_count: number;
  memory_dir?: string;
  mode?: ProjectMode;
  roster?: OrgRosterEntry[];
  is_chat?: boolean;
}

export interface CreateProjectRequest {
  name: string;
  workspace_path: string;
  mode?: ProjectMode;
}

export interface CreateSessionRequest {
  title?: string;
  project_id: string;
  agent_id?: string;
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
  agent_id?: string;
  /** 插話 (interject) 持久化标记：前端不把它渲染为独立用户泡泡。 */
  interject?: boolean;
}

export interface SessionDetailResponse {
  status: string;
  session: {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
    project_id: string;
    agent_id?: string;
    work_mode: string;
    autonomy: string;
    messages: SessionMessageRecord[];
  };
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

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size?: number | null;
  children?: FileTreeNode[];
}

export interface WorkspaceDirEntry {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size?: number | null;
}

export interface WorkspaceTreeResponse {
  status: string;
  root: string;
  tree: FileTreeNode;
}

export interface WorkspaceDirResponse {
  status: string;
  path: string;
  entries: WorkspaceDirEntry[];
}

export interface WorkspaceFileResponse {
  status: string;
  path: string;
  file: {
    content?: string | null;
    binary?: boolean;
    size?: number;
    truncated?: boolean;
    total_lines?: number;
    offset?: number;
    next_offset?: number;
    hint?: string;
  };
}

export type WorkspaceFilePreviewKind = 'text' | 'table' | 'image' | 'pdf' | 'audio' | 'video' | 'office' | 'design' | 'archive' | 'font' | 'executable' | 'other';

export interface WorkspaceFilePreview {
  kind: WorkspaceFilePreviewKind;
  mime: string;
  size: number;
  previewable?: boolean;
  too_large?: boolean;
  error?: string;
  data?: string;
  content?: string | null;
  binary?: boolean;
  truncated?: boolean;
  total_lines?: number;
  offset?: number;
  next_offset?: number;
  hint?: string;
}

export interface WorkspaceFilePreviewResponse {
  status: string;
  path: string;
  preview: WorkspaceFilePreview;
}

export interface WorkspaceBranchResponse {
  status: string;
  is_repo: boolean;
  branch: string | null;
  workspace?: string;
}

/** One entry in the dashboard's static builtin tool catalog. */
export interface DashboardBuiltinTool {
  name: string;
  description: string;
  group: string;
  access: 'read' | 'write' | 'exec' | 'ask';
  mode?: ProjectMode;
}

export interface DashboardAgent {
  id: string;
  name: string;
  role: string;
  team: string;
  status: string;
  session_count: number;
  is_default: boolean;
}

export interface DashboardCapabilities {
  mode: ProjectMode;
  memory_enabled: boolean;
  web_enabled: boolean;
  browser_enabled: boolean;
}

export interface DashboardGitStatus {
  git: boolean;
  is_repo?: boolean;
  branch: string | null;
  note?: string;
  files?: Array<{ path: string; added: number; removed: number; binary: boolean }>;
  untracked?: string[];
  truncated_diff?: boolean;
}

export interface ProjectDashboardData {
  status: string;
  project: ProjectEntry;
  git: DashboardGitStatus;
  agents: DashboardAgent[];
  capabilities: DashboardCapabilities;
  tools: {
    builtin: DashboardBuiltinTool[];
    mcp_servers: McpServerEntry[];
    skills: Array<{
      name: string;
      description: string;
      enabled: boolean;
      source?: string;
      status?: string;
      provenance?: string;
      sources?: string[];
      created_at?: string;
    }>;
  };
  sessions: SessionSummary[];
}

export type ProjectDashboardResponse = ProjectDashboardData;

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
    | { type: 'tool_end'; id: string; name?: string; output: string; status: string; duration_ms?: number; files?: PartFileChange[]; input?: string; session_id?: string }
    | { type: 'web_setup_hint'; status: 'disabled' | 'no_key'; session_id?: string }
    | { type: 'plan_start'; session_id?: string }
    | { type: 'plan_delta'; content: string; session_id?: string }
    | { type: 'plan_end'; content: string; session_id?: string }
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
    | { type: 'done'; content: string; session_id: string; mode?: AgentMode; provider?: string; model?: string; parts?: MessagePart[]; message_id?: string; compaction_notice?: string; loop_reason?: 'tool_calls' | 'repeated' | 'degenerate' | 'overflow' | 'hitl' | 'step_cap' | 'idle' | 'idle_hard' | 'final'; compaction?: { summary: string; count: number; fingerprints: string[]; failed: boolean } }
    | { type: 'error'; error: string; session_id?: string }
    | { type: 'goal_updated'; goal: GoalState; session_id?: string }
    | { type: 'goal_cleared'; session_id?: string }
    | { type: 'goal_stream_end'; session_id?: string }
    | { type: 'goal_round_start'; session_id?: string; round?: number; message_id?: string }
    | { type: 'worker_stream_end'; worker_run_id?: string }
    | { type: 'todos'; todos: Todo[]; session_id?: string }
    | { type: 'delegate_start'; from?: string; to?: string | string[]; task?: string; parallel?: boolean; session_id?: string; worker_run_id?: string }
    | { type: 'delegate_progress'; from: string; to?: string; status: string; chars?: number; error?: string; session_id?: string; worker_run_id?: string }
    | { type: 'delegate_end'; from?: string | string[]; to?: string; ok?: number | boolean; failed?: string[]; error?: string; parallel?: boolean; chars?: number; session_id?: string; worker_run_id?: string }
    | { type: 'context_usage'; used_chars: number; budget_chars: number; compressed: boolean; used_tokens: number; used_tokens_calibrated?: number; calibration_factor?: number; budget_tokens: number; active_budget_tokens: number; window_tokens: number; effective_window_tokens?: number; max_output_tokens?: number; compacted: boolean; compact_count: number; window_source: string; window_warning?: string; session_id?: string }
    | { type: 'context_guard'; status: string; measured_tokens?: number; limit_tokens?: number; calibration_factor?: number; steps?: string[]; session_id?: string }
    | { type: 'idle_warning'; seconds_idle: number; session_id?: string }
    | { type: 'steer_admitted'; session_id?: string; steer_id?: string; content?: string }
    | { type: 'steer_injected'; session_id?: string; steer_id?: string; content?: string }
    | {
        type: 'revert_summary';
        session_id: string;
        reverted_count: number;
        conflict_count: number;
        total: number;
        reverted_paths?: string[];
      };

/** Live context-budget usage surfaced by the backend's context-window middleware. */
export interface SessionContextUsageResponse {
  status: string;
  /** Raw `context_usage` event shape (snake_case) — map via mapContextUsage. */
  context_usage: Record<string, unknown>;
}

export interface ContextUsage {
  usedChars: number;
  budgetChars: number;
  compressed: boolean;
  /** Token estimate of the resident message set (better than chars; counts tool I/O). */
  usedTokens: number;
  /** Calibrated usage: raw estimate × the factor learned from the provider's
   *  real usage for this model. The bar fills against THIS — it tracks what
   *  the provider will actually bill, not the local guess. */
  usedTokensCalibrated?: number;
  /** Closed-loop calibration factor (actual usage / raw estimate). */
  calibrationFactor?: number;
  /** Safety budget in tokens (= (window − maxOutput) × 0.75); the bar fills against this. */
  budgetTokens: number;
  /** Token budget active after compaction (may be halved). Falls back to budgetTokens when absent. */
  activeBudgetTokens?: number;
  /** The model's REAL context window in tokens. */
  windowTokens: number;
  /** TRUE input ceiling: window − max_output (providers reserve output tokens). */
  effectiveWindowTokens?: number;
  /** Output tokens the provider reserves from the window on every request. */
  maxOutputTokens?: number;
  /** Whether any compression (trim or summarize) has happened this session. */
  compacted: boolean;
  /** Cumulative number of compressions this session (persisted in state). */
  compactCount: number;
  /** How the window was resolved: user | table | discovered | default. */
  windowSource: string;
  /** Human-readable warning about the window (unverified oversized override, server cap). */
  windowWarning?: string;
}

export interface Todo {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
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
  provenance?: string;
  status?: string;
  sources?: string[];
  created_at?: string;
}

export interface PendingSkill {
  name: string;
  description: string;
  version: string;
  file_path: string;
  provenance: string;
  status: string;
  sources: string[];
  created_at: string;
}

export interface PendingSkillsResponse {
  status: string;
  pending: PendingSkill[];
}

export interface PendingSkillResponse {
  status: string;
  name: string;
  content: string;
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
  /** Set by the backend: true when this skill is already installed locally. */
  installed?: boolean;
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
  /** Set when the upstream source could not be reached (e.g. ClawHub API down). */
  error?: string | null;
}

export interface MarketInstallResponse {
  status: string;
  skill: SkillEntry | null;
  message?: string;
}

// -- Long-term memory (memory library tree) -------------------------------

export type MemoryNodeKind = 'system' | 'base_file' | 'project_file' | 'agent_file' | 'session_file' | 'folder_file';

export interface MemoryNode {
  kind: MemoryNodeKind;
  name: string;
  rel: string;
  mtime: number;
  content: string;
  blocks: string[];
  char_count: number;
}

export interface MemoryFolderView {
  name: string;
  rel: string;
  files: MemoryNode[];
}

export interface MemoryAgentView {
  id: string;
  name: string;
  rel: string;
  soul: MemoryNode | null;
  agent: MemoryNode | null;
  memory: MemoryNode | null;
  base: MemoryNode[];
  sessions: MemoryNode[];
}

export interface MemoryTeamView {
  id: string;
  name: string;
  rel: string;
  goals: MemoryNode | null;
  context: MemoryNode | null;
  memory: MemoryNode | null;
  files: MemoryNode[];
}

export interface MemoryProjectView {
  name: string;
  rel: string;
  project_name: string;
  base: MemoryNode[];
  project: MemoryNode[];
  agents: MemoryAgentView[];
  folders: MemoryFolderView[];
  teams: MemoryTeamView[];
}

export interface MemoryDiscoverResponse {
  root: string;
  system: MemoryNode[];
  projects: MemoryProjectView[];
}

export interface MemoryStatusResponse {
  enabled: boolean;
  auto_extract: boolean;
  root: string;
  file_count: number;
  char_count: number;
  over_budget: boolean;
}

export interface MemoryFileContentResponse {
  path: string;
  rel: string;
  content: string;
  mtime: number;
  blocks: string[];
}

export interface MemoryFileSaveResponse {
  rel: string;
  content: string;
}

export interface MemoryDeleteResponse {
  status: string;
  rel: string;
}

export interface MemorySearchResult {
  rel: string;
  name: string;
  kind: MemoryNodeKind;
  location: string;
  snippet: string;
  match_count: number;
}

export interface MemorySearchResponse {
  query: string;
  results: MemorySearchResult[];
}

export interface MemoryMoveResponse {
  status: string;
  rel: string;
  new_rel: string;
}

export interface MemoryExportRequest {
  scope: 'all' | 'system' | 'projects';
  project_dirs?: string[];
}

export interface MemoryExportResult {
  path: string;
  filename: string;
  size: number;
  file_count: number;
  status?: string;
}

export interface MemoryImportPickResult {
  status: 'ok' | 'canceled' | 'unsupported';
  path?: string;
}

export interface MemoryImportPreviewResponse {
  token: string;
  files: { rel: string; exists: boolean }[];
}

export interface MemoryImportApplyResponse {
  imported: number;
  overwritten: number;
  skipped: number;
}

export interface MemorySettings {
  enabled: boolean;
  auto_extract: boolean;
}

export type MemorySettingsPatch = Partial<Pick<MemorySettings, 'enabled' | 'auto_extract'>>;

// ── Web search / fetch (Tavily) ─────────────────────────────────────────
export interface WebSettings {
  enabled: boolean;
  provider: string;
  max_results: number;
  search_depth: 'basic' | 'advanced';
  fetch_enabled: boolean;
  api_key_configured: boolean;
}

export type WebConfigPatch = Partial<Pick<WebSettings, 'enabled' | 'provider' | 'max_results' | 'search_depth' | 'fetch_enabled'>>;

export interface WebTestResult {
  ok: boolean;
  message: string;
  results_count: number;
}

// ── Right-side panel (browser-only multi-tab) ───────────────────────────
export type RightPanelTabKind = 'browser';

export interface RightPanelTab {
  id: string;
  kind: RightPanelTabKind;
  data?: { url?: string; title?: string };
}

// Right-click capture of the embedded browser (element or whole page).
export interface BrowserCaptureResult {
  url: string;
  title: string;
  element?: {
    tag: string;
    id: string;
    className: string;
    text: string;
    href: string;
    xpath: string;
    outerHTML: string;
    rect: { x: number; y: number; width: number; height: number };
  } | null;
  pageText?: string;
  screenshot?: string;
  error?: string;
}

// Right-click context-menu request forwarded from the guest webContents
// (main process) with the authoritative cursor position in client coords.
export interface BrowserContextMenuPayload {
  webContentsId: number;
  x: number;
  y: number;
  linkURL: string;
  selectionText: string;
  editFlags: {
    canCut: boolean;
    canCopy: boolean;
    canPaste: boolean;
    canSelectAll: boolean;
    canDelete: boolean;
  };
}

// ── Auto-update ─────────────────────────────────────────────────────────
export type UpdateStateStatus =
  | 'idle'
  | 'checking'
  | 'up-to-date'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'error';

export interface UpdateStateSnapshot {
  isDev: boolean;
  enabled: boolean;
  skippedVersion: string | null;
  currentVersion: string;
  state: UpdateStateStatus;
  availableVersion: string | null;
  releaseNotes: string | null;
  progress: { percent: number; bytesPerSecond: number; transferred: number; total: number } | null;
  errorMessage: string | null;
  errorCode: string | null;
}

// ── Org (multi-agent team) ───────────────────────────────────────────────
export type OrgAgentStatus = 'active' | 'disabled';

export interface OrgAgent {
  id: string;
  name: string;
  role: string;
  description: string;
  parent: string;
  team_id: string;
  status: OrgAgentStatus;
  created_at: string;
}

export interface OrgTeam {
  id: string;
  name: string;
  lead: string;
  parent_team_id: string;
  status: OrgAgentStatus;
}

export interface OrgConfig {
  mode: 'single' | 'multi';
  max_depth: number;
  max_concurrent: number;
  allow_agent_creation: boolean;
}

export interface OrgRosterEntry {
  id: string;
  name: string;
  role: string;
  team: string;
  status?: OrgAgentStatus;
}

export interface OrgSnapshot {
  agents: OrgAgent[];
  teams: OrgTeam[];
  config: OrgConfig;
  roster: OrgRosterEntry[];
}

export interface OrgAgentPayload {
  project_id: string;
  name: string;
  role?: string;
  description?: string;
  parent?: string;
  team_id?: string;
}

export interface OrgAgentUpdatePayload {
  project_id: string;
  id: string;
  name?: string;
  role?: string;
  description?: string;
  parent?: string;
  team_id?: string;
  status?: OrgAgentStatus;
}

export interface OrgTeamPayload {
  project_id: string;
  id: string;
  name: string;
  lead?: string;
  parent_team_id?: string;
}

export interface OrgTeamUpdatePayload {
  project_id: string;
  id: string;
  name?: string;
  lead?: string;
  parent_team_id?: string;
  status?: OrgAgentStatus;
}

export interface OrgConfigPayload {
  project_id: string;
  mode?: 'single' | 'multi';
  max_depth?: number;
  max_concurrent?: number;
  allow_agent_creation?: boolean;
}
