import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Sparkles, X } from 'lucide-react';
import { ChatInput, extractSessionIds, type CommandChip, type ComposerApi } from './components/ChatInput';
import { useGlobalShortcuts } from './keys';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { WebSetupHintBar } from './components/WebSetupHintBar';
import { TodoBlock } from './components/TodoBlock';
import { GoalCard } from './components/GoalCard';
import { ProvidersPanel } from './components/ProvidersPanel';
import { MCPPanel } from './components/MCPPanel';
import { SkillsPanel } from './components/SkillsPanel';
import { MemoryPanel } from './components/MemoryPanel';
import { CreateProjectDialog } from './components/CreateProjectDialog';
import { ProjectSessionList } from './components/ProjectSessionList';
import { FirstRunStart } from './components/FirstRunStart';
import { NewChatHero } from './components/NewChatHero';
import { SettingsView, type SettingsPage } from './components/settings/SettingsView';
import { OrgSettingsPage } from './components/settings/OrgSettingsPage';
import { ProjectDashboard } from './components/dashboard/ProjectDashboard';
import { WorkspaceTitlebar } from './components/WorkspaceTitlebar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { WorkspaceBottomPanel, type BottomPanelView } from './components/WorkspaceBottomPanel';
import { RightPanel } from './components/RightPanel';
import type { BrowserViewHandle } from './components/BrowserView';
import { ChangesPanel } from './components/ChangesPanel';
import { UpdateToastCard } from './components/UpdateToastCard';
import { getLanguage, initLanguage, t, tOrDefault, translateError, useLanguage } from './lib/i18n';
import { useUpdateCenter } from './lib/useUpdateCenter';
import { useSessionBadges } from './lib/useSessionBadges';
import { displayProjectName } from './lib/projectName';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { useSound } from './components/sound-provider';
import { chatService } from './services/chatService';
import type { AppView, ApprovalDecisionPayload, ApprovalOption, Autonomy, ChatMessage, CommandApproval, ComposerAttachment, ContextUsage, CreateProjectRequest, GoalSetMeta, GoalState, McpServerEntry, McpTemplateEntry, MemorySettings, MemorySettingsPatch, MessagePart, OrgRosterEntry, PartAgent, PendingRequest, ProjectEntry, ProviderEntry, RightPanelTab, RightPanelTabKind, RuntimeConfig, SessionDetailResponse, SessionReference, SessionSummary, SessionBadgeMap, SessionBadges, SkillDiagnostic, SkillEntry, SkillReviewSettings, SkillReviewSettingsPatch, StreamEvent, Todo, WorkMode } from './types';
import './App.css';

function mergeMessageParts(base: MessagePart[], extra: MessagePart[]): MessagePart[] {
  const merged = [...base];
  for (const part of extra) {
    if (part.type === 'tool') {
      const index = merged.findIndex((p) => p.type === 'tool' && (p as Extract<MessagePart, { type: 'tool' }>).id === part.id);
      if (index >= 0) {
        const prev = merged[index] as Extract<MessagePart, { type: 'tool' }>;
        merged[index] = {
          ...prev,
          ...part,
          name: part.name || prev.name || '',
          input: part.input || prev.input || '',
        } as MessagePart;
      } else {
        merged.push(part);
      }
    } else if (part.type === 'plan') {
      const index = merged.findIndex((p) => p.type === 'plan');
      if (index >= 0) {
        merged[index] = { ...merged[index], ...part };
      } else {
        merged.push(part);
      }
    } else if (part.type === 'reasoning') {
      const index = merged.findIndex((p) => p.type === 'reasoning');
      if (index >= 0) {
        merged[index] = { ...merged[index], ...part };
      } else {
        merged.push(part);
      }
    } else if (part.type === 'agent') {
      // The backend persists agent parts with `worker_run_id` (snake_case), but
      // the frontend PartAgent uses `workerRunId`. Normalize so the coalescing
      // key matches the streaming part — otherwise `done.parts` stacks a second
      // block and the reloaded block cannot subscribe to the worker stream.
      const authoritative = normalizeAgentPart(part) as Extract<MessagePart, { type: 'agent' }>;
      const index = merged.findIndex(
        (p) => p.type === 'agent' && (p as Extract<MessagePart, { type: 'agent' }>).workerRunId === authoritative.workerRunId,
      );
      if (index >= 0) {
        const prev = merged[index] as Extract<MessagePart, { type: 'agent' }>;
        // Keep the live-built nested transcript; adopt the authoritative summary.
        merged[index] = {
          ...prev,
          ...authoritative,
          parts: prev.parts ?? authoritative.parts,
          ...((prev.transcriptLoaded || authoritative.transcriptLoaded) ? { transcriptLoaded: true } : {}),
        };
      } else {
        merged.push(authoritative);
      }
    } else if (part.type === 'text') {
      // 各次 resume 会重放同一轮执行（工具按 id 去重），文本同样按内容去重，
      // 避免重放时 text part 重复叠加；多轮文本内容各不相同，天然追加。
      const exists = merged.some((p) => p.type === 'text' && p.content === part.content);
      if (!exists && part.content) {
        merged.push(part);
      }
    } else if (part.type === 'steer') {
      // steer 通知既通过 steer_injected 事件 live 加入，也会随 done.parts 回传，
      // 按 steer_id（无 id 时退化为内容）去重，避免重复叠加。
      const exists = merged.some(
        (p) =>
          p.type === 'steer' &&
          ((p.steer_id !== undefined && p.steer_id === part.steer_id) ||
            (p.steer_id === undefined && p.content === part.content)),
      );
      if (!exists) {
        merged.push(part);
      }
    } else {
      merged.push(part);
    }
  }
  return merged;
}

/**
 * Map a backend agent part's snake_case `worker_run_id` to the frontend
 * `workerRunId`. Backend `done.parts` / persisted session parts use the raw
 * JSON shape; without this the coalescing key mismatches the streaming part
 * (duplicate blocks) and the reloaded block cannot subscribe to its stream.
 */
function normalizeAgentPart(part: MessagePart): MessagePart {
  if (part.type !== 'agent') return part;
  const raw = part as Extract<MessagePart, { type: 'agent' }> & { worker_run_id?: string };
  const { worker_run_id, ...rest } = raw;
  return { ...rest, workerRunId: rest.workerRunId || worker_run_id || '', parts: rest.parts ?? [] };
}

/** Normalize a whole parts array (backend JSON → frontend MessagePart shapes). */
function normalizeParts(parts: MessagePart[]): MessagePart[] {
  return parts.map(normalizeAgentPart);
}

/**
 * Shared SSE→parts reducer for BOTH the main agent stream and worker sub-agent
 * streams, so a worker transcript renders with the exact same blocks
 * (text/reasoning/tool/plan) as the main message. Terminal/status handling
 * (done/error/approvals/todos) stays in each caller.
 */
function applyStreamEventToParts(parts: MessagePart[], event: StreamEvent): MessagePart[] {
  switch (event.type) {
    case 'delta': {
      const last = parts[parts.length - 1];
      if (last && last.type === 'text') {
        const next = [...parts];
        next[next.length - 1] = { ...last, content: last.content + event.content };
        return next;
      }
      return [...parts, { type: 'text', content: event.content }];
    }
    case 'reasoning_delta': {
      const last = parts[parts.length - 1];
      if (last && last.type === 'reasoning') {
        const next = [...parts];
        next[next.length - 1] = { ...last, content: event.content };
        return next;
      }
      return [...parts, { type: 'reasoning', content: event.content }];
    }
    case 'tool_start':
      return upsertToolPart(parts, event.id, event.name, event.input || '');
    case 'tool_delta':
      return parts.map((p) =>
        p.type === 'tool' && p.id === event.id ? { ...p, input: (p.input || '') + (event.input || '') } : p,
      );
    case 'tool_end':
      return parts.map((p) => {
        if (p.type !== 'tool' || p.id !== event.id) return p;
        return {
          ...p,
          status: event.status === 'success' ? 'success' : 'error',
          ...(event.input !== undefined ? { input: event.input } : {}),
          ...(event.output ? { output: event.output } : {}),
          ...(event.duration_ms !== undefined ? { duration_ms: event.duration_ms } : {}),
          ...(event.files !== undefined ? { files: event.files } : {}),
        };
      });
    case 'plan_start':
      return [...parts, { type: 'plan', content: '' }];
    case 'plan_delta':
      return parts.map((p) => (p.type === 'plan' ? { ...p, content: p.content + event.content } : p));
    case 'plan_end':
      return parts.map((p) => (p.type === 'plan' ? { ...p, content: event.content || p.content } : p));
    case 'steer_injected': {
      const steerPart: MessagePart = {
        type: 'steer',
        content: event.content || '',
        ...(event.steer_id ? { steer_id: event.steer_id } : {}),
      };
      return [...parts, steerPart];
    }
    default:
      return parts;
  }
}

function settleRunningTools(parts: MessagePart[]): MessagePart[] {
  // A tool that is still 'running' when the turn reaches a terminal state was
  // interrupted (awaiting approval) or aborted — never finish with a live
  // spinner. Demote it to 'pending' (static, non-spinning).
  return parts.map((part) => {
    if (part.type === 'tool' && part.status === 'running') {
      return { ...part, status: 'pending' as const };
    }
    // A worker (agent part) still 'running' when the turn terminates means the
    // turn was stopped/interrupted before delegate_end arrived — the main
    // stream is aborted client-side, so the backend's delegate_end never
    // reaches us. Settle it to a stopped/error state so the "Delegating…"
    // spinner stops instead of spinning forever.
    if (part.type === 'agent' && part.status === 'running') {
      return { ...part, status: 'error' as const, error: t('chat.meta_stopped'), done: true };
    }
    return part;
  });
}

/**
 * 流式文本渲染节流。每 token 都触发 setMessages 会让 React 对整段累积文本全量
 * 重解析 markdown（长回复/代码块尤其重），主线程被占满 → 文本「卡住不动、
 * 稍后一次性补齐」。这里数据仍实时累积（streamedContent/localParts），仅把
 * UI 推送限频；终态时 flushNow() 一次补齐。
 */
function createStreamThrottle(flush: () => void, intervalMs = 60) {
  let lastFlush = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const update = () => {
    const now = Date.now();
    const elapsed = now - lastFlush;
    if (elapsed >= intervalMs) {
      lastFlush = now;
      flush();
    } else if (timer === undefined) {
      timer = setTimeout(() => {
        timer = undefined;
        lastFlush = Date.now();
        flush();
      }, intervalMs - elapsed);
    }
  };
  const flushNow = () => {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
    flush();
  };
  return { update, flushNow };
}

/**
 * Apply a delegation frame (delegate_start / delegate_progress / delegate_end)
 * to a parts array: creates the worker PartAgent block on start, updates its
 * status/error/chars on progress/end. Shared by the main stream, edit/rerun and
 * resume paths so the worker block renders in every turn-start mode.
 */
function applyDelegateEventToParts(parts: MessagePart[], event: StreamEvent): MessagePart[] {
  if (event.type === 'delegate_start') {
    const runId =
      event.worker_run_id ||
      `delegate-${Date.now()}-${parts.length}-${Math.random().toString(16).slice(2)}`;
    const existingIdx = parts.findIndex((p) => p.type === 'agent' && p.workerRunId === runId);
    const delegatePart: PartAgent = {
      type: 'agent',
      workerRunId: runId,
      from: event.from || '',
      to: event.to || '',
      task: event.task,
      status: 'running',
      parallel: event.parallel,
      parts: [],
    };
    if (existingIdx >= 0) {
      return parts.map((p, i) =>
        i === existingIdx ? { ...delegatePart, parts: (p as PartAgent).parts } : p,
      );
    }
    return [...parts, delegatePart];
  }
  if (event.type !== 'delegate_progress' && event.type !== 'delegate_end') return parts;
  const runId = event.worker_run_id || '';
  // A delegation frame without a worker_run_id cannot be attributed to any
  // specific block — never apply it to *every* agent part (an empty runId used
  // to match all of them, corrupting sibling worker blocks in the same message).
  if (!runId) return parts;
  return parts.map((p) => {
    if (p.type !== 'agent') return p;
    if (p.workerRunId !== runId) return p;
    if (event.type === 'delegate_progress') {
      return {
        ...p,
        status: event.status === 'error' || event.error ? ('error' as const) : p.status,
        ...(event.error ? { error: event.error } : {}),
      };
    }
    if (event.type === 'delegate_end') {
      return {
        ...p,
        status: event.error ? ('error' as const) : ('done' as const),
        ...(typeof event.chars === 'number' ? { chars: event.chars } : {}),
        ...(event.failed !== undefined ? { failed: event.failed } : {}),
        ...(event.error ? { error: event.error } : {}),
      };
    }
    return p;
  });
}

function upsertToolPart(parts: MessagePart[], id: string, name: string, input: string): MessagePart[] {
  // Dedupe by tool call id: a resumed graph re-emits the same tool, so update
  // the existing part instead of stacking a duplicate card.
  const next = [...parts];
  const index = next.findIndex((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === id);
  const part: MessagePart = { type: 'tool', id, name, status: 'running' as const, input };
  if (index >= 0) {
    next[index] = part;
  } else {
    next.push(part);
  }
  return next;
}

/**
 * Merge the live worker transcript back into the main stream's parts write.
 *
 * The main stream accumulates a separate `localParts` closure (the delegate
 * *summary frame* only: `{workerRunId, status, chars, parts: []}`), while the
 * worker's internal transcript (nested `parts` + `transcriptLoaded`) is written
 * into React state by `subscribeWorkerTranscript`. A plain `parts: [...localParts]`
 * write therefore overwrites the worker transcript every time the main agent
 * streams — resetting `transcriptLoaded`, so an open worker block keeps falling
 * back to the "正在加载 worker 流…" placeholder. This merge makes the main
 * stream's write non-destructive: summary fields come from `nextParts`, the live
 * transcript state comes from the current message.
 */
function mergeLiveAgentTranscript(nextParts: MessagePart[], currentParts: MessagePart[] | undefined): MessagePart[] {
  if (!currentParts) return nextParts;
  return nextParts.map((np) => {
    if (np.type !== 'agent') return np;
    const cur = currentParts.find(
      (cp): cp is Extract<MessagePart, { type: 'agent' }> =>
        cp.type === 'agent' && cp.workerRunId === np.workerRunId,
    );
    if (!cur) return np;
    return {
      ...np,
      parts: cur.parts ?? np.parts,
      ...(cur.transcriptLoaded !== undefined ? { transcriptLoaded: cur.transcriptLoaded } : {}),
      ...(cur.done !== undefined ? { done: cur.done } : {}),
      ...(cur.error ? { error: cur.error } : {}),
    };
  });
}

/**
 * Reconcile a stream's terminal state against the backend's committed truth.
 *
 * The backend persists the assistant message (with the client-supplied
 * `assistant_message_id`) in its `done` handler BEFORE writing the `done` SSE
 * frame. That means by the time a stream settles, a completed reply is already
 * durable server-side regardless of whether the client ever received `done`.
 *
 * If the `done` frame is dropped somewhere in the untracked
 * SSE → Electron IPC → renderer hop, the old fallback marked the still-running
 * bubble `interrupted` (orange) even though the backend fully replied. This
 * helper re-fetches the session and, when the message id is present, adopts the
 * backend's committed content — turning the dropped-terminal case into a
 * successful commit (blue → done) instead of a false orange bar.
 *
 * Returns null when the backend has no record for this message id (genuinely
 * interrupted / aborted before commit / backend unreachable).
 */
async function findCommittedAssistantMessage(
  sessionId: string | undefined,
  assistantMessageId: string,
  timeoutMs = 3000,
): Promise<{ content: string; parts: MessagePart[] } | null> {
  if (!sessionId) return null;
  try {
    const response = await Promise.race([
      chatService.getSession(sessionId),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('reconcile timeout')), timeoutMs),
      ),
    ]);
    const committed = (response.session.messages ?? []).find(
      (record) => record.role === 'assistant' && record.id === assistantMessageId,
    );
    if (committed) {
      return {
        content: committed.content ?? '',
        parts: normalizeParts((committed.parts as MessagePart[] | undefined) ?? []),
      };
    }
  } catch {
    // Backend unreachable / reconcile timeout — caller falls back to "interrupted".
  }
  return null;
}

type ApprovalStreamEvent = Extract<
  StreamEvent,
  { type: 'approval_required' } | { type: 'question_required' }
>;

/**
 * Single mapping from an interrupt stream event to a `PendingRequest`.
 *
 * All call paths (stream, non-stream and resume) funnel through
 * here so a new interrupt field only has to be handled once.
 */
function pendingRequestFromEvent(
  event: ApprovalStreamEvent,
  sessionId: string,
  messageId: string,
): PendingRequest {
  const base: PendingRequest = {
    approval_id: event.approval_id,
    kind:
      event.type === 'approval_required'
        ? event.kind === 'mcp'
          ? 'mcp'
          : 'command'
        : event.type === 'question_required'
          ? 'question'
          : 'plan',
    session_id: event.session_id ?? sessionId,
    approval_status: event.approval_status,
    messageId,
  };
  if (event.type === 'question_required') {
    return {
      ...base,
      ...(event.question !== undefined ? { question: event.question } : {}),
      ...(event.header !== undefined ? { header: event.header } : {}),
      ...(event.options !== undefined ? { options: event.options } : {}),
      ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
      ...(event.allowCustom !== undefined ? { allowCustom: event.allowCustom } : {}),
    };
  }
  if (event.kind === 'mcp') {
    return {
      ...base,
      tool_name: event.tool_name ?? '',
      tool_args: event.tool_args ?? {},
      server_name: event.server_name ?? '',
      server_id: event.server_id ?? '',
      remote_name: event.remote_name ?? '',
      read_only: Boolean(event.read_only),
      destructive: Boolean(event.destructive),
    };
  }
  return {
    ...base,
    command: event.command,
    cwd: event.cwd,
    ...(event.tool_name ? { tool_name: event.tool_name } : {}),
    ...(event.tool_args && Object.keys(event.tool_args).length > 0 ? { tool_args: event.tool_args } : {}),
  };
}

function createMessage(
  role: ChatMessage['role'],
  content: string,
  metadata: Partial<Omit<ChatMessage, 'role' | 'content'>> & { id?: string } = {},
): ChatMessage {
  return {
    id: metadata.id ?? `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    timestamp: metadata.timestamp ?? Date.now(),
    ...metadata,
  };
}

/**
 * True when any modal / menu / overlay that should consume Esc is currently
 * open. All of the app's popups (DetailModal, SideDrawer, CreateProjectDialog,
 * context menu, radix menus) are only mounted while open, so their DOM markers
 * are a reliable "popup open" signal.
 */
function hasOpenOverlay(): boolean {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"], [role="menu"], [role="listbox"]'));
}

// Map a raw `context_usage` event (snake_case) into the frontend ContextUsage
// shape. Shared by the streaming event handler AND the session-open preview
// fetch so both paths render an identical indicator.
function mapContextUsage(e: any): ContextUsage {
  return {
    usedChars: e.used_chars,
    budgetChars: e.budget_chars,
    compressed: e.compressed,
    usedTokens: e.used_tokens,
    budgetTokens: e.budget_tokens,
    windowTokens: e.window_tokens,
    compacted: e.compacted,
    compactCount: e.compact_count,
    windowSource: e.window_source,
    ...(e.active_budget_tokens != null ? { activeBudgetTokens: e.active_budget_tokens } : {}),
    ...(e.window_warning ? { windowWarning: e.window_warning } : {}),
    ...(e.used_tokens_calibrated != null ? { usedTokensCalibrated: e.used_tokens_calibrated } : {}),
    ...(e.calibration_factor != null ? { calibrationFactor: e.calibration_factor } : {}),
    ...(e.effective_window_tokens != null ? { effectiveWindowTokens: e.effective_window_tokens } : {}),
    ...(e.max_output_tokens != null ? { maxOutputTokens: e.max_output_tokens } : {}),
  };
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  // Task-list (write_todos) shown by the TodoBlock card above the composer, in
  // every mode — the agent's self-decomposed checklist. The card is keyed by
  // session so a running task's todos survive switching sessions (the entry
  // belongs to the session that produced it, not to whatever session happens
  // to be on screen). `todos` is derived from the currently-viewed session's
  // entry; a per-session owner token lets the manual "x" dismiss only the
  // current task, and the card auto-hides whenever the stream is not actively
  // producing a reply (errors / failures / stopped).
  const [todosBySession, setTodosBySession] = useState<Record<string, Todo[]>>({});
  const [todosOwnerBySession, setTodosOwnerBySession] = useState<Record<string, string>>({});
  const [dismissedTodoOwners, setDismissedTodoOwners] = useState<Record<string, string>>({});
  // Goal 状态（严格会话隔离）：按 session 存，TodoBlock 只渲染当前会话 goal。
  const [goalsBySession, setGoalsBySession] = useState<Record<string, GoalState>>({});
  // 正在运行 goal 多轮续跑流的会话集合：期间普通消息只能排队（会话锁语义），
  // 队列自动发送 / stream-settle 在 goal_stream_end 前不触发。
  const [goalStreamSessions, setGoalStreamSessions] = useState<Set<string>>(new Set());
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<'connecting' | 'ready' | 'error'>('connecting');
  const [runtimeError, setRuntimeError] = useState<string>('');
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [streamIdleWarning, setStreamIdleWarning] = useState<string>('');
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | undefined>();
  const [createProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
  const [draftMode, setDraftMode] = useState(false);
  const [draftAgentId, setDraftAgentId] = useState<string>('default_agent');
  const [orgProjectId, setOrgProjectId] = useState<string | undefined>();
  const [dashboardProjectId, setDashboardProjectId] = useState<string | undefined>();
  const [selectedProjectId, setSelectedProjectId] = useState<string | undefined>();
  // useLanguage() 订阅语言变化以触发重渲染（返回值不直接使用）。
  useLanguage();
  const updateCenter = useUpdateCenter();
  const { playSound } = useSound();
  const [themeSettings, setThemeSettingsState] = useState<ThemeSettings>(() => getThemeSettings());
  // Keep latest theme settings reachable from event handlers without resubscribing.
  const themeSettingsRef = useRef(themeSettings);
  themeSettingsRef.current = themeSettings;
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(276);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [isNarrowViewport, setIsNarrowViewport] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 860px)').matches,
  );
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [rightTabs, setRightTabs] = useState<RightPanelTab[]>(() => [{ id: 'browser-1', kind: 'browser' }]);
  const [activeRightTabId, setActiveRightTabId] = useState<string>('browser-1');
  const browserHandlesRef = useRef<Map<string, BrowserViewHandle>>(new Map());
  const [browserAgentActive, setBrowserAgentActive] = useState(false);
  const [browserAgentClick, setBrowserAgentClick] = useState<{ x: number; y: number; key: number } | null>(null);
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false);
  const [bottomPanelView, setBottomPanelView] = useState<BottomPanelView>('terminal');
  const [bottomPanelHeight, setBottomPanelHeight] = useState(190);
  const [bottomPanelResizing, setBottomPanelResizing] = useState(false);
  const [changesPanelOpen, setChangesPanelOpen] = useState(false);
  const [changesRefreshKey, setChangesRefreshKey] = useState(0);
  const [inspectorWidth, setInspectorWidth] = useState(300);
  const [inspectorResizing, setInspectorResizing] = useState(false);
  const workspaceFrameRef = useRef<HTMLElement | null>(null);
  const [workspaceFrameWidth, setWorkspaceFrameWidth] = useState(0);
  const [changesPanelWidth, setChangesPanelWidth] = useState(380);
  const [changesPanelResizing, setChangesPanelResizing] = useState(false);
  const [autonomy, setAutonomy] = useState<Autonomy>(() => {
    const stored = localStorage.getItem('cw.autonomy') as Autonomy | null;
    return stored === 'supervised' || stored === 'guarded' || stored === 'autonomous' ? stored : 'guarded';
  });
  const [memorySettings, setMemorySettings] = useState<MemorySettings | null>(null);
  const [skillReviewSettings, setSkillReviewSettings] = useState<SkillReviewSettings | null>(null);
  const [pendingSkillCount, setPendingSkillCount] = useState(0);
  const [skillDraftNote, setSkillDraftNote] = useState<{ count: number; sessionId: string } | null>(null);
  const skillCountRef = useRef(0);
  const MAX_ATTACHMENT_MB_STORAGE_KEY = 'coworker-max-attachment-mb';
  const DEFAULT_MAX_ATTACHMENT_MB = 25;
  const MIN_MAX_ATTACHMENT_MB = 1;
  const MAX_MAX_ATTACHMENT_MB = 1024;
  const loadMaxAttachmentMb = (): number => {
    try {
      const raw = localStorage.getItem(MAX_ATTACHMENT_MB_STORAGE_KEY);
      if (raw == null) return DEFAULT_MAX_ATTACHMENT_MB;
      const parsed = parseInt(raw, 10);
      if (!Number.isFinite(parsed)) return DEFAULT_MAX_ATTACHMENT_MB;
      return Math.max(MIN_MAX_ATTACHMENT_MB, Math.min(MAX_MAX_ATTACHMENT_MB, parsed));
    } catch {
      return DEFAULT_MAX_ATTACHMENT_MB;
    }
  };
  const [maxAttachmentMb, setMaxAttachmentMb] = useState<number>(loadMaxAttachmentMb);
  // 编辑用户消息时是否回滚该轮被改动的文件（默认开，对齐 opencode/Codex）。
  const [revertCode, setRevertCode] = useState<boolean>(true);
  // goal 能力（多轮续跑）总开关：关闭后不能设定/续跑目标，续跑提示不再注入。
  const [goalEnabled, setGoalEnabled] = useState<boolean>(true);
  const [workMode, setWorkMode] = useState<WorkMode>(() => {
    const stored = localStorage.getItem('cw.workMode') as WorkMode | null;
    return stored === 'plan' || stored === 'build' ? stored : 'build';
  });
  useEffect(() => {
    try {
      localStorage.setItem('cw.autonomy', autonomy);
    } catch {
      // storage unavailable (privacy mode / quota) — ignore, state still works
    }
  }, [autonomy]);
  useEffect(() => {
    try {
      localStorage.setItem('cw.workMode', workMode);
    } catch {
      // ignore
    }
  }, [workMode]);
  const [selectedModel, setSelectedModel] = useState('');
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [webSetupHint, setWebSetupHint] = useState<'disabled' | 'no_key' | null>(null);
  const [webHintDismissed, setWebHintDismissed] = useState(false);
  const [settingsPage, setSettingsPage] = useState<SettingsPage>('main');
  const [references, setReferences] = useState<SessionReference[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerEntry[]>([]);
  const [mcpTemplates, setMcpTemplates] = useState<McpTemplateEntry[]>([]);
  const [skillEntries, setSkillEntries] = useState<SkillEntry[]>([]);
  const [skillDiagnostics, setSkillDiagnostics] = useState<SkillDiagnostic[]>([]);

  // Global keyboard shortcuts — registry-driven (see keys/config.ts). Handlers
  // return true when they consumed the key so conditional shortcuts (e.g.
  // Esc-to-stop) can let the key fall through when not applicable.
  const composerApiRef = useRef<ComposerApi | null>(null);
  // 停止生成需要连按两次 Esc：第一次记录时间并放行（菜单/弹窗仍可正常关闭），
  // 第二次在窗口期内的 Esc 才真正停止。
  const lastEscPressRef = useRef(0);
  useGlobalShortcuts({
    'toggle-work-mode': () => {
      setWorkMode((prev) => (prev === 'plan' ? 'build' : 'plan'));
      return true;
    },
    'new-chat': () => {
      setActiveView('chat');
      startNewChat();
      return true;
    },
    'new-project': () => {
      createProject();
      return true;
    },
    'open-settings': () => {
      openSettingsPage('main');
      return true;
    },
    'open-dashboard': () => {
      const target = selectedProjectId || currentProjectId;
      if (!target) return false;
      openDashboard(target);
      return true;
    },
    'focus-input': () => {
      setActiveView('chat');
      requestAnimationFrame(() => composerApiRef.current?.focus());
      return true;
    },
    'send-message': () => {
      const draft = editingMessage ? editDraft : input;
      if (!draft.trim() && attachments.length === 0) return false;
      if (editingMessage) {
        void commitEditMessage(editingMessage.id, editDraft);
        return true;
      }
      // Cmd+Enter = 插话：任务运行中立即入队并引导（steer）运行中的图；空闲时退回普通发送。
      const sid = sessionIdRef.current;
      if (isThinking && sid) {
        const text = draft.trim();
        if (!text) return false;
        const queuedAttachments = attachments;
        const queuedReferences = references;
        const entry = enqueueMessage(sid, text, { attachments: queuedAttachments, references: queuedReferences });
        setInput('');
        setCommandChip(null);
        commandChipRef.current = null;
        setAttachments([]);
        setReferences([]);
        if (entry) void interjectQueuedMessage(sid, entry.id);
        return true;
      }
      sendMessage();
      return true;
    },
    'stop-agent': () => {
      if (!isThinking) return false;
      // 弹窗/菜单/抽屉打开时 Esc 先用于关闭它们，不参与双击停止。
      if (hasOpenOverlay() || mobileSidebarOpen) return false;
      const now = Date.now();
      const isDoublePress = now - lastEscPressRef.current < 500;
      lastEscPressRef.current = now;
      if (!isDoublePress) return false;
      stopMessage();
      return true;
    },
    'toggle-sidebar': () => {
      if (isNarrowViewport) setMobileSidebarOpen((value) => !value);
      else setSidebarCollapsed((value) => !value);
      return true;
    },
    'toggle-right-panel': () => {
      setRightSidebarOpen((value) => !value);
      return true;
    },
    'toggle-bottom-panel': () => {
      setBottomPanelView('terminal');
      setBottomPanelOpen((value) => !value);
      return true;
    },
    'attach-file': () => {
      setActiveView('chat');
      requestAnimationFrame(() => composerApiRef.current?.attachFiles());
      return true;
    },
    'regenerate': () => {
      if (isThinking) return false;
      const currentId = sessionIdRef.current;
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const assistant = messages[index];
        if (!assistant || assistant.role !== 'assistant') continue;
        if (currentId && assistant.sessionId && assistant.sessionId !== currentId) continue;
        let hasTrigger = false;
        for (let j = index - 1; j >= 0; j -= 1) {
          const candidate = messages[j];
          if (!candidate) continue;
          if (currentId && candidate.sessionId && candidate.sessionId !== currentId) continue;
          if (candidate.role === 'user') {
            hasTrigger = true;
            break;
          }
        }
        if (!hasTrigger) return false;
        void handleRegenerateMessage(assistant.id);
        return true;
      }
      return false;
    },
    'edit-last-user-message': () => {
      if (isThinking) return false;
      const currentId = sessionIdRef.current;
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const user = messages[index];
        if (!user || user.role !== 'user' || !user.content) continue;
        if (currentId && user.sessionId && user.sessionId !== currentId) continue;
        setActiveView('chat');
        beginEditMessage(user.id, user.content);
        requestAnimationFrame(() => composerApiRef.current?.focus());
        return true;
      }
      return false;
    },
    'view-providers': () => {
      setActiveView('providers');
      return true;
    },
    'view-mcp': () => {
      setActiveView('mcp');
      return true;
    },
    'view-skills': () => {
      setActiveView('skills');
      return true;
    },
    'view-chat': () => {
      // Esc 语义：先关弹窗/菜单/抽屉，其次「返回上级」逐层退回，最后才到对话视图。
      if (hasOpenOverlay() || mobileSidebarOpen) return false;
      if (activeView === 'settings' && settingsPage !== 'main') {
        // 设置子页（快捷键/主题/网页/审计）→ 设置主页
        setSettingsPage('main');
        return true;
      }
      if (activeView !== 'chat') {
        setActiveView('chat');
        setSettingsPage('main');
        return true;
      }
      return false;
    },
    'copy-last-response': () => {
      const currentId = sessionIdRef.current;
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (!message) continue;
        if (message.role !== 'assistant' || !message.content?.trim()) continue;
        if (currentId && message.sessionId && message.sessionId !== currentId) continue;
        void navigator.clipboard.writeText(message.content).catch(() => {});
        return true;
      }
      return false;
    },
    'toggle-autonomy': () => {
      setAutonomy((prev) => (prev === 'supervised' ? 'guarded' : prev === 'guarded' ? 'autonomous' : 'supervised'));
      return true;
    },
  });

  // Keep the installed-skill catalog fresh at the app level (not just when the
  // Settings → Skills panel mounts) so the chat-input "/" command card can list
  // skills — including ones installed via chat in a previous turn.
  const refreshSkills = useCallback(async () => {
    try {
      const response = await chatService.listSkills();
      setSkillEntries(response.skills);
      setSkillDiagnostics(response.diagnostics);
    } catch {
      // Non-fatal: the slash menu simply won't show skill commands.
    }
  }, []);

  useEffect(() => {
    void refreshSkills();
  }, [refreshSkills]);

  // Global index of sub-command name -> owning package name, used to dispatch
  // the bare "/<command>" entries that the chat-input "/" card can insert.
  const skillSubCommandIndex = useMemo<Record<string, string>>(() => {
    const index: Record<string, string> = {};
    for (const skill of skillEntries) {
      for (const cmd of skill.commands ?? []) {
        if (!index[cmd.name]) index[cmd.name] = skill.name;
      }
    }
    return index;
  }, [skillEntries]);

  // Set of enabled skill names: the "/" card now lists each skill directly as
  // "/<name>" (the "skill" keyword is dropped), so a bare "/<name>" token must
  // be dispatched as a full-skill activation.
  const skillNameIndex = useMemo<Set<string>>(
    () => new Set(skillEntries.filter((skill) => skill.enabled !== false).map((skill) => skill.name)),
    [skillEntries],
  );

  // Only show messages that belong to the currently active session so that
  // running messages preserved from other sessions (after a session switch)
  // don't bleed into the current conversation. With no active session (hero /
  // new-chat draft) only ambient messages (no sessionId) are shown — background
  // running messages from other sessions stay hidden but keep their stream.
  const displayedMessages = useMemo(() => {
    // 插话消息（interject=true）不渲染为独立用户泡泡：内容由 assistant 气泡内的
    // 「收到插話」card 展示（无论当前轮注入还是 late-steer 自动续跑轮注入）。
    const visible = messages.filter((m) => !(m.role === 'user' && m.interject));
    if (!sessionId) return visible.filter((m) => !m.sessionId);
    return visible.filter((m) => !m.sessionId || m.sessionId === sessionId);
  }, [messages, sessionId]);

  const [pendingRequests, setPendingRequests] = useState<PendingRequest[]>([]);
  const [branchStatus, setBranchStatus] = useState<{ isRepo: boolean; branch: string | null } | null>(null);
  const requestSeqRef = useRef(0);
  // Per-session generation counters for stream staleness. A stream is "stale"
  // only when a NEWER stream started in the SAME session (or it was stopped).
  // A stream belonging to another session must keep processing its events so a
  // session switch does not freeze that conversation's in-progress reply.
  const sessionSeqRef = useRef<Record<string, number>>({});
  const bumpSessionSeq = (sessionId?: string | null) => {
    if (!sessionId) return;
    sessionSeqRef.current[sessionId] = (sessionSeqRef.current[sessionId] ?? 0) + 1;
  };
  const getSessionSeq = (sessionId?: string | null) =>
    sessionId ? sessionSeqRef.current[sessionId] ?? 0 : requestSeqRef.current;
  const isStreamStale = (sessionId: string | undefined, requestSeq: number) =>
    requestSeq !== getSessionSeq(sessionId);
  // Per-session stream bookkeeping so multiple sessions can stream in parallel:
  // starting/stopping one task must never touch a task running in another
  // session (previously a single global abortRef meant 新对话/暂停 killed the
  // wrong stream once more than one session was busy).
  const streamKey = (sessionId?: string | null) => sessionId || '__none__';
  const streamControllersRef = useRef<Record<string, AbortController>>({});
  const activeAssistantMessageIdsRef = useRef<Record<string, string>>({});
  const streamStartAtsRef = useRef<Record<string, number>>({});
  // Worker sub-agent streams: one AbortController per worker_run_id so a worker
  // transcript can be subscribed on demand (block expanded) and aborted when the
  // message/component is torn down.
  const workerStreamControllersRef = useRef<Record<string, AbortController>>({});
  // Bounded retries for the subscribe-before-publish race: when a worker stream
  // terminates empty (no done/error/parts) we allow ONE re-subscribe per run so a
  // slow-starting worker still gets its live transcript, but never loop forever.
  const workerStreamRetriesRef = useRef<Record<string, number>>({});

  /**
   * Subscribe to a worker sub-agent's dedicated SSE stream and fold its events
   * into the owning assistant message's `PartAgent.parts` (lazy, on expand).
   * Replays persisted history first (so a finished worker is still readable),
   * then follows live deltas. Terminal events (done/error/worker_stream_end)
   * settle the block.
   */
  const subscribeWorkerTranscript = useCallback((messageId: string, part: PartAgent) => {
    const workerRunId = part.workerRunId;
    if (!workerRunId) return;
    if (workerStreamControllersRef.current[workerRunId]) return;
    const controller = new AbortController();
    workerStreamControllersRef.current[workerRunId] = controller;
    // Mark loaded immediately so a re-render cannot double-subscribe.
    setMessages((current) =>
      current.map((m) => {
        if (m.id !== messageId || !m.parts) return m;
        const nextParts = m.parts.map((p) =>
          p.type === 'agent' && p.workerRunId === workerRunId ? { ...p, transcriptLoaded: true } : p,
        );
        return { ...m, parts: nextParts };
      }),
    );
    void chatService
      .subscribeWorkerStream(workerRunId, (event) => {
        setMessages((current) =>
          current.map((m) => {
            if (m.id !== messageId || !m.parts) return m;
            const parts = m.parts.map((p) => {
              if (p.type !== 'agent' || p.workerRunId !== workerRunId) return p;
              if (event.type === 'done') {
                const transcript =
                  event.parts && event.parts.length > 0
                    ? mergeMessageParts(p.parts, event.parts)
                    : applyStreamEventToParts(p.parts, event);
                return { ...p, parts: settleRunningTools(transcript), done: true };
              }
              if (event.type === 'error') {
                return { ...p, error: event.error, done: true };
              }
              if (event.type === 'worker_stream_end') {
                // Terminal frame with NO prior done/error and an empty (or still
                // empty) transcript usually means the subscribe raced the worker's
                // first event (subscribe-before-publish) and was handed a bogus
                // terminal. Reset transcriptLoaded so a re-expand re-subscribes and
                // replays the persisted run once it actually has events. A finished
                // worker always delivers done/error before worker_stream_end, so this
                // cannot flip a genuinely-finished block back to loading. Bounded to
                // one retry per run to avoid a re-subscribe loop on a truly-empty run.
                const hasRealTerminal = p.done || p.error || (p.parts && p.parts.length > 0);
                if (!hasRealTerminal) {
                  const retries = workerStreamRetriesRef.current[workerRunId] ?? 0;
                  if (retries < 1) {
                    workerStreamRetriesRef.current[workerRunId] = retries + 1;
                    return { ...p, transcriptLoaded: false };
                  }
                }
                return { ...p, done: true };
              }
              return { ...p, parts: applyStreamEventToParts(p.parts, event) };
            });
            return { ...m, parts };
          }),
        );
      }, controller.signal)
      .catch(() => {
        // Best-effort: a failed worker stream leaves the block with whatever
        // transcript it already has.
      })
      .finally(() => {
        if (workerStreamControllersRef.current[workerRunId] === controller) {
          delete workerStreamControllersRef.current[workerRunId];
        }
      });
  }, []);

  // Auto-subscribe every worker transcript as soon as its block exists — not on
  // expand. The subscription is therefore established the moment the block is
  // created (right after delegate_start), before (or concurrently with) the
  // worker's first bus event, so it can never race the worker's first event.
  // Expanding the block only reveals already-streamed/replayed content.
  // subscribeWorkerTranscript dedupes by worker_run_id and marks transcriptLoaded,
  // so this effect stays a no-op for blocks already subscribed or settled.
  useEffect(() => {
    for (const m of messages) {
      if (!m.parts || m.parts.length === 0) continue;
      for (const p of m.parts) {
        if (p.type !== 'agent') continue;
        if (p.workerRunId && !p.transcriptLoaded) {
          subscribeWorkerTranscript(m.id, p);
        }
      }
    }
  }, [messages, subscribeWorkerTranscript]);

  // Codex-style "queue while streaming": per-session FIFO of messages typed
  // while the agent is replying. Each entry auto-sends as the next request once
  // that session's stream finishes (done / goal_done / error / stopped). The
  // queue is surfaced in the TodoBlock above the composer, one entry per row.
  interface QueuedEntry {
    id: string;
    message: string;
    ts: number;
    attachments?: ComposerAttachment[];
    references?: SessionReference[];
  }
  const queuedMessagesRef = useRef<Record<string, QueuedEntry[]>>({});
  const [queuedEntries, setQueuedEntries] = useState<Record<string, QueuedEntry[]>>({});
  const enqueueMessage = (sessionId: string | undefined, message: string, extra?: { attachments?: ComposerAttachment[]; references?: SessionReference[] }): QueuedEntry | undefined => {
    const key = streamKey(sessionId);
    const entry: QueuedEntry = {
      id: `queued-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      message,
      ts: Date.now(),
      ...(extra?.attachments && extra.attachments.length > 0 ? { attachments: extra.attachments } : {}),
      ...(extra?.references && extra.references.length > 0 ? { references: extra.references } : {}),
    };
    const queue = [...(queuedMessagesRef.current[key] ?? []), entry];
    queuedMessagesRef.current[key] = queue;
    setQueuedEntries((current) => ({ ...current, [key]: queue }));
    return entry;
  };
  const dequeueMessage = (sessionId: string | undefined): QueuedEntry | null => {
    const key = streamKey(sessionId);
    const queue = queuedMessagesRef.current[key] ?? [];
    if (queue.length === 0) return null;
    const [next, ...rest] = queue;
    if (next == null) return null;
    queuedMessagesRef.current[key] = rest;
    setQueuedEntries((current) => ({ ...current, [key]: rest }));
    return next;
  };
  const removeQueuedMessage = (sessionId: string | undefined, id: string) => {
    const key = streamKey(sessionId);
    const queue = queuedMessagesRef.current[key] ?? [];
    queuedMessagesRef.current[key] = queue.filter((entry) => entry.id !== id);
    setQueuedEntries((current) => ({ ...current, [key]: queuedMessagesRef.current[key] ?? [] }));
  };
  const updateQueuedMessage = (sessionId: string | undefined, id: string, message: string) => {
    const key = streamKey(sessionId);
    const queue = queuedMessagesRef.current[key] ?? [];
    queuedMessagesRef.current[key] = queue.map((entry) =>
      entry.id === id ? { ...entry, message } : entry,
    );
    setQueuedEntries((current) => ({ ...current, [key]: queuedMessagesRef.current[key] ?? [] }));
  };
  const reorderQueuedMessage = (sessionId: string | undefined, orderedIds: string[]) => {
    const key = streamKey(sessionId);
    const queue = queuedMessagesRef.current[key] ?? [];
    const byId = new Map(queue.map((entry) => [entry.id, entry]));
    const next = orderedIds
      .map((id) => byId.get(id))
      .filter((entry): entry is QueuedEntry => Boolean(entry));
    queuedMessagesRef.current[key] = next;
    setQueuedEntries((current) => ({ ...current, [key]: next }));
  };
  const queuedMessagesFor = (sessionId: string | undefined) => queuedEntries[streamKey(sessionId)] ?? [];

  // 插話 (interject)：已提交给 /chat/interject、但尚未被运行中 graph 消费的
  // steer。当当前流的 assistant 消息进入终态（done/error/stopped）而该 steer
  // 仍未收到 `steer_injected` 时，自动续跑为下一轮，避免插话丢失。
  interface PendingSteer {
    id: string;
    message: string;
    userMessageId: string;
    attachments?: ComposerAttachment[];
    references?: SessionReference[];
  }
  const pendingSteersRef = useRef<Record<string, PendingSteer[]>>({});
  const markSteerConsumed = (sessionId: string | undefined, steerId: string) => {
    const key = streamKey(sessionId);
    pendingSteersRef.current[key] = (pendingSteersRef.current[key] ?? []).filter((s) => s.id !== steerId);
  };

  // 从队列中选择一条消息插话：立即以 user 气泡展示，同时推送到后端 steer 收件箱。
  // 后端在下一次模型呼叫边界注入到运行中的图（不中止当前流）；若 409（无活动任务）
  // 则退回队列并移除气泡。
  const interjectQueuedMessage = async (sessionId: string, queuedId: string) => {
    const key = streamKey(sessionId);
    const queue = queuedMessagesRef.current[key] ?? [];
    const entry = queue.find((q) => q.id === queuedId);
    if (!entry) return;
    // 插話前必須確認該 session 真的有在飛的流：interject 僅在後端任務進行中才
    // 有效（否則 409）。沒有活動流時直接以普通訊息送出，避免「卡在佇列 + 409 迴圈」。
    const streamActive =
      Boolean(streamControllersRef.current[key]) &&
      (goalStreamSessions.has(sessionId) ||
        messages.some((m) => (m.status === 'running' || m.status === 'waiting') && m.sessionId === sessionId));
    if (!streamActive) {
      removeQueuedMessage(sessionId, queuedId);
      void sendMessage({
        message: entry.message,
        ...(entry.attachments && entry.attachments.length > 0 ? { attachments: entry.attachments } : {}),
        ...(entry.references && entry.references.length > 0 ? { references: entry.references } : {}),
      });
      return;
    }
    removeQueuedMessage(sessionId, queuedId);
    const steerId = `steer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const userMessageId = `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    // pending 記錄在 interject「成功後」再加入：避免 auto-continue effect 在
    // HTTP 尚未回應時就把 steer 當新訊息送出（與 409 回退競態 → 重複/延遲）。
    try {
      await chatService.interject({
        session_id: sessionId,
        message: entry.message,
        user_message_id: userMessageId,
        steer_id: steerId,
        ...(entry.attachments && entry.attachments.length > 0 ? { attachments: entry.attachments } : {}),
        ...(entry.references && entry.references.length > 0 ? { referenced_sessions: entry.references.map((r) => r.id) } : {}),
        max_attachment_bytes: Math.max(1, maxAttachmentMb) * 1024 * 1024,
      });
      pendingSteersRef.current[key] = [
        ...(pendingSteersRef.current[key] ?? []),
        {
          id: steerId,
          message: entry.message,
          userMessageId,
          ...(entry.attachments && entry.attachments.length > 0 ? { attachments: entry.attachments } : {}),
          ...(entry.references && entry.references.length > 0 ? { references: entry.references } : {}),
        },
      ];
    } catch (error) {
      // 409 / network failure：不靜默重排（否則點擊迴圈卡死），改以普通訊息送出，
      // 讓使用者看到訊息確實發出。
      console.warn('interject failed; sending as a normal message:', error);
      void sendMessage({
        message: entry.message,
        ...(entry.attachments && entry.attachments.length > 0 ? { attachments: entry.attachments } : {}),
        ...(entry.references && entry.references.length > 0 ? { references: entry.references } : {}),
      });
    }
  };

  const abortStreamFor = (sessionId?: string | null) => {
    const key = streamKey(sessionId);
    streamControllersRef.current[key]?.abort();
    delete streamControllersRef.current[key];
    delete activeAssistantMessageIdsRef.current[key];
    delete streamStartAtsRef.current[key];
  };

  // Terminal-state settle for an assistant message after a stream ends.
  // Guards on `running`/`waiting` so a done/error/stopped set by event handlers
  // (or by stopMessage) is never overwritten. `waiting` is included: a stale
  // waiting (approval) message that never got a successful resume would
  // otherwise stick forever and keep isThinking/busy true, blocking the queue.
  // When the `done` frame was dropped, reconcile against the backend's committed
  // message instead of blindly showing an orange "interrupted" bar — the backend
  // persists the assistant message BEFORE writing `done`, so a present message
  // id means the reply actually succeeded and must be adopted as `done`.
  const settleAssistantMessage = async (opts: {
    sessionId: string | undefined;
    assistantMessageId: string;
    streamedContent: string;
    receivedDone: boolean;
    extraParts?: MessagePart[];
  }) => {
    const { sessionId: sid, assistantMessageId: mid, streamedContent: fallback, receivedDone, extraParts } = opts;
    // exactOptionalPropertyTypes: never assign `parts: undefined` to ChatMessage.
    const partsField = (item: ChatMessage, parts: MessagePart[]) => {
      const next = parts.length > 0 ? parts : item.parts;
      return next !== undefined ? { parts: next } : {};
    };
    if (receivedDone) {
      setMessages((current) =>
        current.map((item) =>
          item.id === mid && (item.status === 'running' || item.status === 'waiting')
            ? {
                ...item,
                content: fallback || item.content,
                status: 'done',
                ...(extraParts ? partsField(item, mergeMessageParts(item.parts || [], extraParts)) : {}),
                streamEndAt: Date.now(),
              }
            : item,
        ),
      );
      return;
    }
    const committed = await findCommittedAssistantMessage(sid, mid);
    setMessages((current) =>
      current.map((item) =>
        item.id === mid && (item.status === 'running' || item.status === 'waiting')
          ? committed
            ? {
                ...item,
                content: committed.content || fallback || item.content,
                status: 'done',
                ...partsField(item, committed.parts),
                streamEndAt: Date.now(),
              }
            : {
                ...item,
                content: fallback || t('chat.stream_interrupted'),
                status: 'interrupted',
                parts: settleRunningTools(item.parts ?? []),
                ...(extraParts ? partsField(item, mergeMessageParts(item.parts || [], extraParts)) : {}),
                streamEndAt: Date.now(),
              }
          : item,
      ),
    );
  };

  const sessionIdRef = useRef<string | undefined>(undefined);
  const pendingProjectIdRef = useRef<string | undefined>(undefined);

  const todosSessionKey = (sid?: string | null) => sid || '__ambient__';

  // Store the task list for a specific session (from a stream's `todos` event).
  // Works for background sessions too: the entry is kept keyed by session so it
  // reappears when the user switches back, instead of being lost.
  const setSessionTodos = (sessionIdValue: string | undefined, owner: string, value: Todo[]) => {
    const key = todosSessionKey(sessionIdValue);
    setTodosBySession((current) => {
      const next = { ...current };
      if (value.length > 0) next[key] = value;
      else delete next[key];
      return next;
    });
    setTodosOwnerBySession((current) => {
      const next = { ...current };
      if (value.length > 0) next[key] = owner;
      else delete next[key];
      return next;
    });
  };

  const clearSessionTodos = (sessionIdValue: string | undefined) => {
    const key = todosSessionKey(sessionIdValue);
    setTodosBySession((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
    setTodosOwnerBySession((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  };

  const goalsSessionKey = (sid?: string | null) => sid || '__ambient__';
  const goalsBySessionRef = useRef<Record<string, GoalState>>({});
  useEffect(() => {
    goalsBySessionRef.current = goalsBySession;
  }, [goalsBySession]);

  const setSessionGoal = (sessionIdValue: string | undefined, goal: GoalState | null) => {
    const key = goalsSessionKey(sessionIdValue);
    setGoalsBySession((current) => {
      const next = { ...current };
      if (goal) next[key] = goal;
      else delete next[key];
      return next;
    });
  };
  const currentGoal = goalsBySession[goalsSessionKey(sessionId)] ?? null;

  const markGoalStreamActive = (sid?: string) => {
    if (!sid) return;
    setGoalStreamSessions((current) => (current.has(sid) ? current : new Set(current).add(sid)));
  };
  const markGoalStreamEnded = (sid?: string) => {
    if (!sid) return;
    setGoalStreamSessions((current) => {
      if (!current.has(sid)) return current;
      const next = new Set(current);
      next.delete(sid);
      return next;
    });
  };

  // Task list of the currently-viewed session.
  const todos = todosBySession[todosSessionKey(sessionId)] ?? [];

  // Whether the CURRENT session is busy. Derived (not a hand-maintained flag)
  // so that a stream left running in another session (we keep it alive across
  // session switches instead of aborting it) never locks the composer here,
  // and a background stream completing never spuriously unlocks this session.
  const isThinking = useMemo(
    () =>
      messages.some(
        (m) => (m.status === 'running' || m.status === 'waiting') && (!m.sessionId || m.sessionId === sessionId),
      ) || (sessionId != null && goalStreamSessions.has(sessionId)),
    [messages, sessionId, goalStreamSessions],
  );

  // 任务卡仅在当前会话流式回复正常进行中（running/waiting）时显示：失败、
  // 报错、停止、结束后一律保持关闭。用户手动点 "x" 只隐藏当前任务（按 owner
  // 区分），下一次新任务自动重新出现。
  const currentTodoOwner = todosOwnerBySession[todosSessionKey(sessionId)];
  const showTodoCard = Boolean(
    currentTodoOwner &&
      todos.length > 0 &&
      isThinking &&
      dismissedTodoOwners[todosSessionKey(sessionId)] !== currentTodoOwner,
  );

  const dismissCurrentTodos = () => {
    const owner = todosOwnerBySession[todosSessionKey(sessionId)];
    if (owner) {
      setDismissedTodoOwners((current) => ({ ...current, [todosSessionKey(sessionId)]: owner }));
    }
  };

  // 后端轮询到的活跃会话 id 集合（兜底：前端刷新/重启后不知道哪些会话仍在
  // 后台运行）。只作为 running 徽章的补充来源。
  const [backendActiveSessionIds, setBackendActiveSessionIds] = useState<Set<string>>(new Set());

  // sessionId → 该会话的待审批记录（status === 'pending'）。来自
  // /command-approvals 的全域扫描（后端持久化在 command_approvals.json），
  // 让背景会话的待审批在「开启该会话之前」就可见。
  // 存完整记录而非笔数：开启会话时要拿它重建审批卡（需要 command / question /
  // options 等字段），存笔数会逼我们再打一次 API。
  const [pendingBySession, setPendingBySession] = useState<Map<string, CommandApproval[]>>(new Map());

  // 全域状态轮询：活跃会话 + 待审批。常驻运行，不再因集合为空而停止
  // （否则背景会话的待审批永远等不到下一次刷新）。
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const [active, approvals] = await Promise.all([
          chatService.listActiveSessions(),
          chatService.listCommandApprovals(),
        ]);
        if (cancelled) return;
        setBackendActiveSessionIds(new Set(active));
        const bySession = new Map<string, CommandApproval[]>();
        for (const approval of approvals.approvals) {
          if (approval.status !== 'pending') continue;
          const sessionId = approval.context?.session_id;
          if (typeof sessionId !== 'string' || !sessionId) continue;
          const list = bySession.get(sessionId);
          if (list) list.push(approval);
          else bySession.set(sessionId, [approval]);
        }
        setPendingBySession(bySession);
      } catch {
        /* 静默失败：下一次轮询重试，不打断指示器既有状态 */
      }
      if (!cancelled) {
        timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    const onFocus = () => {
      if (!cancelled) void poll();
    };
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', onFocus);
      if (timer) clearTimeout(timer);
    };
  }, [chatService]);

  // 会话徽章聚合：四种状态的唯一真源，三处会话列表（侧栏/整页/Dashboard）共用。
  const sessionBadges = useSessionBadges({
    sessions,
    messages,
    backendActiveSessionIds,
    pendingBySession,
  });

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // 窄屏（<=860px）时侧边栏切换为抽屉模式：进入窄屏解除折叠态，离开窄屏自动收起抽屉。
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const query = window.matchMedia('(max-width: 860px)');
    const apply = (matches: boolean) => {
      setIsNarrowViewport(matches);
      if (matches) {
        setSidebarCollapsed(false);
      } else {
        setMobileSidebarOpen(false);
      }
    };
    apply(query.matches);
    const onChange = (event: MediaQueryListEvent) => apply(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  // 窄屏下切换视图/会话/项目后自动收起抽屉，避免遮挡主内容。
  useEffect(() => {
    if (!isNarrowViewport) return;
    setMobileSidebarOpen(false);
  }, [isNarrowViewport, activeView, sessionId, activeProjectId]);

  // 抽屉展开时支持 ESC 关闭。
  useEffect(() => {
    if (!mobileSidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileSidebarOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileSidebarOpen]);

  const refreshProviders = async () => {
    try {
      const response = await chatService.listProviders();
      const enabledProviders = response.providers.filter((provider) => provider.enabled);
      setProviders(enabledProviders);
      setSelectedModel((current) => {
        if (current && enabledProviders.some((provider) => provider.id === current)) return current;
        return response.default_provider_id || enabledProviders[0]?.id || '';
      });
      setRuntimeStatus('ready');
      return response;
    } catch (error) {
      console.error('Failed to load providers:', error);
      return undefined;
    }
  };

  const refreshMcps = async () => {
    try {
      const [serversRes, templatesRes] = await Promise.all([
        chatService.listMcps().catch(() => ({ servers: [] as any[] })),
        chatService.discoverMcps().catch(() => ({ servers: [] as any[] })),
      ]);
      setMcpServers(serversRes?.servers || []);
      // discoverMcps returns { status, servers: McpTemplateEntry[] } — the `servers` key holds templates
      setMcpTemplates((templatesRes as any)?.servers || (templatesRes as any)?.templates || []);
    } catch {
      /* Best-effort */
    }
  };

  const refreshSessions = async () => {
    try {
      const response = await chatService.listSessions();
      setSessions(response.sessions);
    } catch (error) {
      console.error('Failed to load sessions:', error);
    }
  };

  // 標記會話已讀：後端更新 last_read_at（並順帶清空 last_error），前端本地
  // 立即歸零 unread_count / last_error，讓側欄角標即時消失（不必等下一輪輪詢）。
  // 失敗時靜默：未讀是軟狀態，不阻塞交互，下次輪詢會自然收斂。
  const markSessionReadLocal = useCallback((sid: string) => {
    void chatService.markSessionRead(sid).catch(() => {});
    setSessions((current) =>
      current.map((s) => (s.id === sid ? { ...s, unread_count: 0, last_error: null } : s)),
    );
  }, []);

  // 停留在当前会话时自动清除未读：若用户正看着某会话（或后台流又产生了
  // 新消息 / 新错误），不应显示未读/错误角标。sessions 每次轮询刷新都会
  // 触发本 effect，unread_count 归零后不再重复调用，无循环风险。
  useEffect(() => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    const session = sessions.find((s) => s.id === sid);
    if (session && ((session.unread_count ?? 0) > 0 || session.last_error)) {
      markSessionReadLocal(sid);
    }
  }, [sessions, markSessionReadLocal]);

  const refreshProjects = async () => {
    try {
      const response = await chatService.listProjects();
      setProjects(response.projects);
    } catch (error) {
      console.error('Failed to load projects:', error);
    }
  };

  useEffect(() => {
    let mounted = true;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    async function bootstrap() {
      applyTheme(themeSettings);
      await initLanguage();
      if (!mounted) return;

      try {
        const config = await chatService.getRuntimeConfig();
        if (!mounted) return;
        setRuntimeConfig(config);
        setSelectedModel(config.selected_provider_id);
        setRuntimeStatus('ready');
        attempt = 0;
        const providerResponse = await refreshProviders();
        if (providerResponse && mounted) {
          setSelectedModel(config.selected_provider_id || providerResponse.default_provider_id || providerResponse.providers.find((provider) => provider.enabled)?.id || '');
        }
        await refreshSessions();
        await refreshProjects();
        await refreshMcps();
        try {
          const settings = await chatService.fetchSettings();
          if (typeof settings.max_attachment_mb === 'number') {
            const fromBackend = Math.max(
              MIN_MAX_ATTACHMENT_MB,
              Math.min(MAX_MAX_ATTACHMENT_MB, Math.round(settings.max_attachment_mb)),
            );
            setMaxAttachmentMb(fromBackend);
            try {
              localStorage.setItem(MAX_ATTACHMENT_MB_STORAGE_KEY, String(fromBackend));
            } catch { /* ignore */ }
          }
          if (typeof settings.revert_code === 'boolean') {
            setRevertCode(settings.revert_code);
          }
          if (typeof settings.goal_enabled === 'boolean') {
            setGoalEnabled(settings.goal_enabled);
          }
        } catch { /* ignore */ }
        try {
          const memSettings = await chatService.getMemorySettings();
          if (mounted) setMemorySettings(memSettings);
        } catch { /* ignore */ }
        try {
          const skillSettings = await chatService.getSkillReviewSettings();
          if (mounted) setSkillReviewSettings(skillSettings);
        } catch { /* ignore */ }
        try {
          const count = await refreshPendingSkillCount();
          skillCountRef.current = count;
        } catch { /* ignore */ }
      } catch (error) {
        console.error('Failed to load runtime config:', error);
        if (!mounted) return;
        setRuntimeStatus('error');
        setRuntimeError(translateError(error));
        attempt += 1;
        const delay = Math.min(1500 * 2 ** (attempt - 1), 8000);
        retryTimer = setTimeout(bootstrap, delay);
      }
    }

    bootstrap();

    return () => {
      mounted = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    applyTheme(themeSettings);
  }, [themeSettings]);

  // In `system` mode, follow the OS light/dark preference live.
  useEffect(() => {
    if (themeSettings.mode !== 'system') return;
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => applyTheme(themeSettingsRef.current);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [themeSettings.mode]);

  useEffect(() => {
    document.title = t('app.title');
  }, []);

  useEffect(() => {
    if (sessionId || messages.length > 0 || projects.length === 0 || sessions.length > 0) return;
    if (activeProjectId && projects.some((project) => project.id === activeProjectId)) return;
    // 只自动选中真实项目；仅有系统聊天项目时不自动选中，保留空态 onboarding。
    const firstProject = projects.find((p) => !p.is_chat);
    if (!firstProject) return;
    pendingProjectIdRef.current = firstProject.id;
    setActiveProjectId(firstProject.id);
    setDraftMode(true);
  }, [activeProjectId, messages.length, projects, sessionId, sessions.length]);

  const resolveSessionReference = async (sessionId: string): Promise<SessionReference | null> => {
    try {
      const response = await chatService.getSession(sessionId);
      return { id: response.session.id, title: response.session.title };
    } catch {
      return null;
    }
  };

  const handleStreamWebEvents = (event: StreamEvent) => {
    if (event.type === 'web_setup_hint') {
      if (!webHintDismissed) setWebSetupHint(event.status);
    } else if (event.type === 'tool_end' && event.name === 'web_search' && event.output?.includes('tavily_key_missing')) {
      // Agent attempted a search while the key is missing — surface the hint
      // even if a previous hint was dismissed.
      setWebSetupHint('no_key');
    }
  };

  const openSettingsPage = (page: SettingsPage) => {
    setSettingsPage(page);
    setWebHintDismissed(true);
    setWebSetupHint(null);
    setActiveView('settings');
  };

  const sendMessage = async (override?: {
    message: string;
    projectId?: string;
    attachments?: ComposerAttachment[];
    references?: SessionReference[];
    sessionId?: string;
    /** 插話自动续跑：user 气泡已存在（interject 时创建的），不再新建。 */
    skipUserBubble?: boolean;
    /** 插話自动续跑：user 消息已由 /chat/interject 持久化，后端复用 history。 */
    skipUserAppend?: boolean;
    /** skipUserBubble 时复用的 user 消息 id（interject 时创建的）。 */
    userMessageId?: string;
  }) => {
    const typedMessage = (override?.message ?? input).trim();
    if (isThinking) {
      // Agent is streaming for this session — do NOT start a second stream. A
      // plain send here (Enter / programmatic fallback) queues the message so it
      // auto-sends once the current stream finishes.
      if (!override && sessionIdRef.current) {
        const queuedText = typedMessage || t('chat.attachment_only_message');
        const queuedAttachments = attachments;
        const queuedReferences = references;
        setInput('');
        // 排隊路徑必須一併清命令 chip（含同步 ref），否則運行中再發命令會
        // 留下一個看似「沒發出去」的殘留 chip，且訊息被當普通文字排隊。
        setCommandChip(null);
        commandChipRef.current = null;
        setAttachments([]);
        setReferences([]);
        enqueueMessage(sessionIdRef.current, queuedText, { attachments: queuedAttachments, references: queuedReferences });
        return;
      }
      if (!override) return;
    }

    // 命令 chip 路径：skill/子命令已作为真实 chip 提交，raw 文字不再携带
    // 命令 token，这里把 chip + 提示词组合回注入标记并走既有 handler（含气泡逻辑）。
    // 用同步 ref ?? state，避免 commitCommand 的异步 state 尚未落地时漏掉 chip。
    const chip = commandChipRef.current ?? commandChip;
    if (chip && !override) {
      const prompt = input.trim();
      setCommandChip(null);
      commandChipRef.current = null;
      if (chip.type === 'skill') {
        setInput('');
        if (chip.packageName) {
          void handleSubCommandSlash(chip.packageName, chip.command.slice(1), `${chip.command}${prompt ? ` ${prompt}` : ''}`);
        } else {
          void handleSkillSlash(`${chip.command}${prompt ? ` ${prompt}` : ''}`);
        }
        return;
      }
      if (chip.type === 'sys' && chip.command === '/goal') {
        // /goal chip：user 消息只显示目标文本（不带 /goal 前綴，对原版前端 UI），
        // 并生成持久化 id 随 /goal/set 落库（重载/重进会话后泡泡不消失）。
        const combined = `${chip.command}${prompt ? ` ${prompt}` : ''}`;
        setInput('');
        setCommandChip(null);
        commandChipRef.current = null;
        const provider = providers.find((p) => p.id === selectedModel);
        const model = provider?.model ?? runtimeConfig?.selected_model ?? '';
        const providerName = provider?.name ?? runtimeConfig?.agent_provider ?? '';
        const goalUserMessageId = `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        // 绑定当前会话（有会话时）：避免无 sessionId 的泡泡被 displayedMessages
        // 渲染到所有会话头部；新会话（尚无 session）在 handleSlashCommand 建会后重绑。
        setMessages((current) => [
          ...current,
          createMessage('user', prompt || combined, {
            id: goalUserMessageId,
            status: 'done',
            ...(sessionIdRef.current ? { sessionId: sessionIdRef.current } : {}),
            autonomy,
            provider: providerName,
            model,
          }),
        ]);
        handleSlashCommand(combined, {
          userMessageId: goalUserMessageId,
          provider: providerName,
          model,
          workMode,
          autonomy,
        });
        return;
      }
      return;
    }

    if (!typedMessage && attachments.length === 0 && !override?.skipUserAppend) return;

    if (typedMessage.startsWith('/')) {
      // 消息墙也要显示命令令牌（/new、/help 等系统命令显示原始令牌）。
      // 技能命令不显示原始令牌——由 handler 以「已加载技能」的干净标签气泡展示。
      const command = typedMessage.split(/\s+/)[0] ?? '';
      const bareCmd = command.startsWith('/') ? command.slice(1) : '';
      const isSkillCommand =
        command === '/skill' || Boolean(skillSubCommandIndex[bareCmd]) || skillNameIndex.has(bareCmd);
      const provider = providers.find((p) => p.id === selectedModel);
      const model = provider?.model ?? runtimeConfig?.selected_model ?? '';
      const providerName = provider?.name ?? runtimeConfig?.agent_provider ?? '';
      let goalUserMessageId = '';
      if (!isSkillCommand) {
        // /goal 的 user 消息只显示目标文本（不带 /goal 前綴，对原版前端 UI），
        // 并生成持久化 id 随 /goal/set 落库（重载/重进会话后泡泡不消失）。
        const displayText = command === '/goal' ? typedMessage.slice('/goal'.length).trim() || typedMessage : typedMessage;
        goalUserMessageId = `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        // 绑定当前会话（有会话时）：避免无 sessionId 的泡泡渲染到所有会话头部。
        setMessages((current) => [
          ...current,
          createMessage('user', displayText, {
            id: goalUserMessageId,
            status: 'done',
            ...(sessionIdRef.current ? { sessionId: sessionIdRef.current } : {}),
            autonomy,
            provider: providerName,
            model,
          }),
        ]);
      }
      handleSlashCommand(
        typedMessage,
        command === '/goal'
          ? { userMessageId: goalUserMessageId, provider: providerName, model, workMode, autonomy }
          : undefined,
      );
      return;
    }

    // 阻断发送：当前模型对应的 LLM 服务不可达（探测失败缓存），不发起请求，
    // 立即在消息墙提示用户更换模型。输入内容保留在 composer，换模型后可直接重发。
    const blockingProvider = providers.find((provider) => provider.id === selectedModel);
    if (blockingProvider?.context_error) {
      setMessages((current) => [
        ...current,
        createMessage('assistant', `${t('chat.model_unreachable')}：${blockingProvider.context_error}。${t('chat.model_unreachable_switch')}`, {
          status: 'error',
        }),
      ]);
      return;
    }

    const requestProjectId = override?.projectId || pendingProjectIdRef.current;

    // 未选择 workspace 时不发送，停留在草稿态提示用户先选工作空间
    if (!sessionIdRef.current && !requestProjectId) {
      setDraftMode(true);
      return;
    }

    // 首次发消息：先创建 session，防止 agent 已开始但前端不知 session_id 导致对话丢失
    if (requestProjectId && !sessionIdRef.current) {
      try {
        const sessionResp = await chatService.createSession({ project_id: requestProjectId, agent_id: draftAgentId });
        const newSession = sessionResp.session;
        if (newSession) {
          sessionIdRef.current = newSession.id;
          setSessionId(newSession.id);
          // 立即加入本地会话列表，保证侧栏即时可见
          setSessions((current) => [newSession, ...current]);
        }
      } catch (error) {
        // 创建失败不影响消息发送（后端会兜底创建）
        console.error('Failed to auto-create session:', error);
      }
    }

    setDraftMode(false);

    const message = typedMessage || t('chat.attachment_only_message');
    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    const selectedProvider = providers.find((provider) => provider.id === selectedModel);
    const requestAttachments = override?.attachments ?? attachments;
    const requestModel = selectedProvider?.model ?? runtimeConfig?.selected_model ?? '';
    const requestProvider = selectedProvider?.name ?? runtimeConfig?.agent_provider ?? '';
    const requestSessionId = override?.sessionId || sessionIdRef.current;
    // New generation for this session: any older stream of the SAME session is
    // now stale, but streams of OTHER sessions (kept alive after a switch)
    // stay valid and keep updating their own message in the background.
    bumpSessionSeq(requestSessionId);
    const myRequestSeq = getSessionSeq(requestSessionId);

    // 引用会话：先采用 composer 中已确认的 chips，再兜底扫描消息文本里出现的会话 id
    const requestReferences = override?.references ?? [...references];
    const referencedIds = new Set(requestReferences.map((reference) => reference.id));
    for (const sessionIdInText of extractSessionIds(message)) {
      if (referencedIds.has(sessionIdInText)) continue;
      const resolved = await resolveSessionReference(sessionIdInText);
      if (resolved) {
        requestReferences.push(resolved);
        referencedIds.add(resolved.id);
      }
    }

    // 在消息墙展示用户输入（含 /new、/help 等命令令牌）
    // 前端生成稳定的消息 id，回传后端以统一前后端 id（修复按 id 回退/重生成时 404）
    const userMessageId =
      override?.skipUserBubble && override.userMessageId
        ? override.userMessageId
        : `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    if (!override?.skipUserBubble) {
      setMessages((current) => [
        ...current,
        createMessage('user', message, {
          id: userMessageId,
        status: 'done',
        ...(requestSessionId ? { sessionId: requestSessionId } : {}),
        autonomy,
        provider: requestProvider,
        model: requestModel,
        attachments: requestAttachments,
        ...(requestReferences.length > 0 ? { references: requestReferences } : {}),
      }),
    ]);
    }
    if (!override?.skipUserBubble) {
      setInput('');
      // 发送即清空输入框里的附件与引用：它们已被捕获进用户消息气泡（上方
      // setMessages 的 attachments/references）和即将发出的请求体，无需再停留
      // 在 composer。放在此处（而非等流式结束后）可彻底避免两类问题：
      // 1) 长任务的整段流式期间附件 chip 一直挂在输入框，看起来像「没发出去」；
      // 2) 流式出错/被中止时 catch/finally 不会清附件，导致 chip 永久残留，
      //    且残留的附件会在下一次发送时被 requestAttachments 误带重发。
      setAttachments([]);
      setReferences([]);
    }

    setMessages((current) => [
      ...current,
      createMessage('assistant', '', {
        streamStartAt: Date.now(),
        id: assistantMessageId,
        status: 'running',
        ...(requestSessionId ? { sessionId: requestSessionId } : {}),
        autonomy,
        provider: requestProvider,
        model: requestModel,
      }),
    ]);
    // 新一轮任务开始：清掉该会话上一轮的残留任务卡（若上一轮异常退出未清理），
    // 让卡片只在本次流真正产出 todos 后出现。
    clearSessionTodos(requestSessionId);

    // 发送瞬间即把会话提前到列表顶部：对本地产出的新会话乐观置顶；
    // 已存在会话则更新其 updated_at 并重排，无需等 agent 回复结束。
    if (requestSessionId) {
      const now = new Date().toISOString();
      setSessions((current) => {
        const target = current.find((s) => s.id === requestSessionId);
        if (!target) return current;
        const rest = current.filter((s) => s.id !== requestSessionId);
        return [{ ...target, updated_at: now }, ...rest];
      });
    }

    const controller = new AbortController();
    streamControllersRef.current[streamKey(requestSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(requestSessionId)] = assistantMessageId;
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let receivedDone = false;
    // goal 单流多轮：首轮是否已 done（避免 Stop 把已完成的轮覆盖成 stopped）、
    // 以及本条流是否处于 goal 多轮模式（未到 goal_stream_end 前不 stream-settle）。
    let round1Done = false;
    let goalStreamActiveLocal = false;
    // 当前正在流式渲染的 assistant 消息 id：首轮 = 前端预建的 assistantMessageId；
    // 续跑轮 = 后端 goal_round_start 提前下发的 round id（让 delta 流式进该轮气泡）。
    let currentRoundAssistantId: string = assistantMessageId;
    // 运行中模型把目标置为 complete/blocked 时暂存：流真正结束（goal_stream_end）
    // 才应用到 GoalCard，避免「卡片已完成但流还在跑」的错位。
    let pendingTerminalGoal: GoalState | null = null;
    const markLocalGoalStream = () => {
      if (!goalStreamActiveLocal) {
        goalStreamActiveLocal = true;
        markGoalStreamActive(requestSessionId);
      }
    };
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(requestSessionId)] = streamStartAt;
    // 文本渲染限频：避免每 token 全量重解析 markdown 导致主线程卡顿
    // （表现：文本中途卡住、稍后一次性补齐）。
    const flushText = () => {
      commit(localParts);
    };
    const textThrottle = createStreamThrottle(flushText);
    // Non-destructive parts write for the main stream: preserves live worker
    // transcripts (mergeLiveAgentTranscript) instead of overwriting them with
    // the main stream's delegate summary frames. Extra status/content/streamEndAt
    // patches cover the terminal variants (done/error/stopped/waiting).
    // 目标续跑：delta/工具帧流式写进「当前轮」气泡（首轮 = assistantMessageId，
    // 续跑轮 = goal_round_start 下发的 round id）。
    const commit = (
      nextParts: MessagePart[],
      patch?: { content?: string; status?: ChatMessage['status']; streamEndAt?: number },
    ) =>
      setMessages((current) =>
        current.map((item) =>
          item.id === currentRoundAssistantId
            ? {
                ...item,
                content: patch?.content !== undefined ? patch.content : streamedContent,
                parts: mergeLiveAgentTranscript(nextParts, item.parts),
                ...(patch?.status ? { status: patch.status } : {}),
                ...(patch?.streamEndAt !== undefined ? { streamEndAt: patch.streamEndAt } : {}),
              }
            : item,
        ),
      );

    const handleEvent = (event: StreamEvent) => {
      trackBrowserToolEvent(event);
      // Stale guard: only superseded streams of the SAME session are ignored.
      // Events from a stream belonging to another session MUST be processed —
      // they update that session's own message by id (kept alive across a
      // switch), so the background reply streams to completion instead of
      // freezing at status "running" forever.
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      handleStreamWebEvents(event);
      if (event.type === 'context_usage') {
        if (!event.session_id || event.session_id === sessionIdRef.current) {
          setContextUsage(mapContextUsage(event));
        }
        return;
      }
      if (event.type === 'start') {
        streamStartAt = Date.now();
        // Only the stream started by THIS sendMessage may bind the view to a
        // session; a delayed start event from a background session must not
        // yank the user away from the hero/draft.
        if (event.session_id && !sessionIdRef.current && event.session_id === requestSessionId) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
        // 发送瞬间即把会话提前到列表顶部：后端在收到请求时已追加 user 消息并
        // 刷新 updated_at，此处立刻拉取让排序立即生效（无需等 agent 回复结束）。
        void refreshSessions();
      } else if (event.type === 'delta') {
        streamedContent += event.content;
        // 与 worker 流共用同一 reducer，保证主流与 worker 转录用相同方式渲染。
        localParts = applyStreamEventToParts(localParts, event);
        textThrottle.update();
      } else if (event.type === 'reasoning_delta') {
        localParts = applyStreamEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'tool_start') {
        localParts = applyStreamEventToParts(localParts, event);
        // Built-in browser: auto-open the right-side browser tab so the user
        // watches the agent browse live.
        if (event.name === 'browser') {
          let url: string | undefined;
          try {
            const args = JSON.parse(event.input || '{}') as { url?: string };
            url = typeof args?.url === 'string' && args.url ? args.url : undefined;
          } catch {
            url = undefined;
          }
          ensureBrowserTab(url);
        }
        commit(localParts);
      } else if (event.type === 'tool_delta') {
        localParts = applyStreamEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'tool_end') {
        localParts = applyStreamEventToParts(localParts, event);
        // 装完即见：agent 安装技能后立刻刷新技能列表，侧栏无需手动刷新。
        const finishedTool = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (finishedTool?.name === 'install_skill') void refreshSkills();
        commit(localParts);
      } else if (event.type === 'plan_start' || event.type === 'plan_delta' || event.type === 'plan_end') {
        localParts = applyStreamEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'delegate_start' || event.type === 'delegate_progress' || event.type === 'delegate_end') {
        // 每个 worker run 一个独立 PartAgent block。worker_run_id 作为订阅
        // /worker-events/{id} 的键；缺失时（旧后端）生成一个稳定的 fallback key。
        localParts = applyDelegateEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const sessionIdValue = event.session_id ?? sessionIdRef.current ?? '';
        const pending = pendingRequestFromEvent(event, sessionIdValue, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        playSound('card_popup');
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: t('chat.waiting_resolution'), status: 'waiting' });
      } else if (event.type === 'done') {
        textThrottle.flushNow();
        receivedDone = true;
        // Only confirm the session for the currently-viewed session's stream;
        // a background stream from another session must never hijack the view.
        if (event.session_id && event.session_id === sessionIdRef.current) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
        streamedContent = event.content || streamedContent;
        let mergedParts = localParts;
        if (event.parts && event.parts.length > 0) {
          mergedParts = mergeMessageParts(localParts, event.parts);
        }
        mergedParts = settleRunningTools(mergedParts);
        // goal 单流多轮：done 帧回带该轮 assistant 消息 id。续跑轮的气泡已由
        // goal_round_start 以 running 态创建（delta 流式渲染），这里 commit 收尾；
        // 兜底：若未收到 goal_round_start（旧后端）则补建气泡。
        const roundMessageId = event.message_id || currentRoundAssistantId;
        const isContinuationRound = Boolean(event.message_id && event.message_id !== assistantMessageId);
        if (isContinuationRound) {
          markLocalGoalStream();
          currentRoundAssistantId = roundMessageId;
          setMessages((current) => {
            if (current.some((m) => m.id === roundMessageId)) return current;
            const roundSessionId = event.session_id || requestSessionId;
            return [
              ...current,
              createMessage('assistant', event.content || '', {
                id: roundMessageId,
                status: 'running',
                ...(roundSessionId ? { sessionId: roundSessionId } : {}),
                parts: mergedParts,
                streamStartAt: Date.now(),
                autonomy,
                provider: requestProvider,
                model: requestModel,
              }),
            ];
          });
        } else {
          round1Done = true;
        }
        commit(mergedParts, { status: 'done', streamEndAt: Date.now() });
        // C3: 压缩摘要生成失败（默认模型不可用/出错）时提示用户更换可用模型。
        // 与既有错误提示一致使用 window.alert（该场景罕见，无需去重）。
        if (event.compaction_notice) {
          window.alert(event.compaction_notice);
        }
        // 终态副作用：goal 流延后到 goal_stream_end（避免每轮播声音/清 todos）；
        // 普通流（无 goal）在此立即执行，保持原行为。
        const isGoalSession = goalsBySessionRef.current[goalsSessionKey(event.session_id ?? requestSessionId)] != null;
        if (isGoalSession) {
          markLocalGoalStream();
        } else if (!goalStreamActiveLocal) {
          clearSessionTodos(event.session_id ?? requestSessionId);
          playSound('reply_done');
        }
      } else if (event.type === 'goal_round_start') {
        // 续跑轮开始：后端提前下发该轮 assistant 消息 id → 以 running 态建泡，
        // 本轮 delta 流式渲染（done 前不折叠进「思考过程」组）。
        const roundId = event.message_id;
        if (roundId) {
          markLocalGoalStream();
          currentRoundAssistantId = roundId;
          streamedContent = '';
          localParts = [];
          const roundSessionId = event.session_id || requestSessionId;
          setMessages((current) => {
            if (current.some((m) => m.id === roundId)) return current;
            return [
              ...current,
              createMessage('assistant', '', {
                id: roundId,
                status: 'running',
                ...(roundSessionId ? { sessionId: roundSessionId } : {}),
                parts: [],
                streamStartAt: Date.now(),
                autonomy,
                provider: requestProvider,
                model: requestModel,
              }),
            ];
          });
        }
      } else if (event.type === 'goal_updated') {
        // 目标在运行中（流未结束）被模型置为 complete/blocked：先暂存，等
        // goal_stream_end 真正结束时再应用到卡片，避免「卡片已完成但流还在跑」。
        const g = event.goal;
        if (goalStreamActiveLocal && g && (g.status === 'complete' || g.status === 'blocked')) {
          pendingTerminalGoal = g;
        } else {
          setSessionGoal(event.session_id ?? requestSessionId, event.goal);
          // 非流式场景（例如 /goal 命令）下 goal 已完成：展示「已完成」片刻后自动关闭 GoalCard。
          if (g && g.status === 'complete') {
            window.setTimeout(() => {
              setSessionGoal(event.session_id ?? requestSessionId, null);
            }, 2500);
          }
        }
        markLocalGoalStream();
      } else if (event.type === 'goal_cleared') {
        setSessionGoal(event.session_id ?? requestSessionId, null);
      } else if (event.type === 'goal_stream_end') {
        // 整条 goal 续跑链结束：此刻才是终态（settle / 队列已由 goalStreamSessions
        // 闸门保护），补终态副作用并应用暂存的 complete/blocked。
        markGoalStreamEnded(event.session_id ?? requestSessionId);
        clearSessionTodos(event.session_id ?? requestSessionId);
        playSound('reply_done');
        if (pendingTerminalGoal) {
          const sid = event.session_id ?? requestSessionId;
          setSessionGoal(sid, pendingTerminalGoal);
          if (pendingTerminalGoal.status === 'complete') {
            // 目标已完成：展示「已完成」片刻后自动关闭 GoalCard。
            window.setTimeout(() => {
              setSessionGoal(sid, null);
            }, 2500);
          }
          pendingTerminalGoal = null;
        }
      } else if (event.type === 'steer_injected') {
        // 插話已被运行中 graph 消费：从 pending 列表移除（不再自动续跑），
        // 并在当前 assistant 气泡内追加一条「收到插話」notice。
        const consumedSessionId = event.session_id ?? requestSessionId;
        if (event.steer_id) markSteerConsumed(consumedSessionId, event.steer_id);
        if (!event.session_id || event.session_id === requestSessionId) {
          localParts = applyStreamEventToParts(localParts, event);
          commit(localParts);
        }
      } else if (event.type === 'todos') {
        // Task list is keyed by session (works for background streams too): the
        // TodoBlock card above the composer shows it in every mode. The
        // per-session store must always be updated so the card reappears after
        // switching back.
        setSessionTodos(event.session_id ?? requestSessionId, assistantMessageId, event.todos);
      } else if (event.type === 'error') {
        textThrottle.flushNow();
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: event.error || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() });
        clearSessionTodos(event.session_id ?? requestSessionId);
      } else if (event.type === 'idle_warning') {
        // Backend detected N seconds of inactivity on the SSE stream.
        // The client's idle watchdog will fire in (300 - seconds_idle) seconds.
        const remaining = Math.max(0, 300 - event.seconds_idle);
        setStreamIdleWarning(t('chat.idle_warning', { seconds: remaining }));
        // Auto-dismiss after 8 seconds so the warning doesn't stick around.
        setTimeout(() => setStreamIdleWarning(''), 8000);
      }
    };

    try {
      await chatService.sendMessageStream(
        {
          message,
          mode: runtimeConfig?.default_mode ?? 'single',
          language: getLanguage(),
          work_mode: workMode,
          autonomy,
          ...(selectedProvider
            ? {
                provider_id: selectedProvider.id,
                model: selectedProvider.model,
              }
            : {}),
          ...(requestAttachments.length > 0 ? { attachments: requestAttachments } : {}),
          max_attachment_bytes: Math.max(1, maxAttachmentMb) * 1024 * 1024,
          ...(requestReferences.length > 0 ? { referenced_sessions: requestReferences.map((reference) => reference.id) } : {}),
          ...(requestSessionId ? { session_id: requestSessionId } : {}),
          ...(requestProjectId ? { project_id: requestProjectId } : {}),
          ...(draftAgentId ? { agent: draftAgentId } : {}),
          user_message_id: userMessageId,
          assistant_message_id: assistantMessageId,
          ...(override?.skipUserAppend ? { skip_user_append: true } : {}),
        },
        handleEvent,
        controller.signal,
      );
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      // 附件/引用已在发送即清空（见上方），此处无需重复。
      setRuntimeStatus('ready');
      await refreshSessions();
      await refreshProjects();
      setChangesRefreshKey((value) => value + 1);
      _generateSessionTitleIfNeeded(message, streamedContent, requestSessionId);
    } catch (error) {
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      console.error('Failed to stream message:', error);
      if ((error as Error).name === 'AbortError') {
        // goal 多轮流中 Stop：只把仍 running 的首轮标记为 stopped；已完成的轮
        // （round1Done）不再覆盖。
        if (!round1Done) {
          localParts = settleRunningTools(localParts);
          commit(localParts, { content: streamedContent || t('chat.stopped'), status: 'stopped', streamEndAt: Date.now() });
        }
      } else {
        setRuntimeStatus('error');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: translateError(error) || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
              : item,
          ),
        );
        playSound('reply_error');
      }
    } finally {
      stopBrowserAgent();
      // 安全网：强制把这条流命中的 assistant 消息退出 running，避免「蓝条一直挂起不结束」。
      // 按消息 id 收尾（每个流持有独立 id），因此切走会话后后台流结束时也会被正确收尾，
      // 侧栏 running 指示随之清除；不会误伤其它流的消息。
      // 若流结束却从未收到 done（断线、后端重启、终态事件在
      // SSE→IPC→renderer 链路丢失），先与后端已落库的消息对账：后端在写出 done
      // 帧之前就已持久化 assistant 消息，若该消息 id 已存在则回复其实成功了，
      // 采纳后端内容并标记 done；只有后端也无记录时才算 interrupted。
      // Guard against batched updates: only transition if the message is still
      // running (not already marked done/error by event handlers above).
      void settleAssistantMessage({
        sessionId: sessionIdRef.current || requestSessionId,
        assistantMessageId,
        streamedContent,
        receivedDone,
      });
      // 目标多轮：流终止时把仍 running 的续跑轮气泡标记为 interrupted（这些轮
      // 的气泡由 goal_round_start 创建，settle 只对账首轮 assistantMessageId）。
      if (goalStreamActiveLocal) {
        setMessages((current) =>
          current.map((item) =>
            item.status === 'running' && item.sessionId === requestSessionId && item.id !== assistantMessageId
              ? { ...item, status: 'interrupted', streamEndAt: Date.now() }
              : item,
          ),
        );
      }
      if (streamControllersRef.current[streamKey(requestSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(requestSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(requestSessionId)];
        delete streamStartAtsRef.current[streamKey(requestSessionId)];
      }
      // 该会话的 goal 流（若有）已结束，解除会话锁语义。
      markGoalStreamEnded(requestSessionId);
    }
  };

  // 启动/恢复 goal 续跑流（三个入口共用：/goal set|resume、会话加载恢复、HITL
  // resume 后重查）。防重入：该会话已有进行中 stream 或 goal 流则不重复启动。
  const kickGoalContinuation = async (sessionIdValue: string) => {
    if (!goalEnabled) return;
    const busy = messages.some(
      (m) => (m.status === 'running' || m.status === 'waiting') && m.sessionId === sessionIdValue,
    );
    if (busy || goalStreamSessions.has(sessionIdValue)) return;
    try {
      const resp = await chatService.getGoal(sessionIdValue);
      if (resp?.goal?.status !== 'active') return;
      void sendMessage({ sessionId: sessionIdValue, skipUserAppend: true, skipUserBubble: true, message: '' });
    } catch {
      // best-effort：拉取失败不打扰用户。
    }
  };

  // Auto-send the next queued message once a session's stream settles. Watched
  // via messages/queued state so the session is guaranteed idle (no running /
  // waiting message) before dequeuing — a queued message must never race the
  // stream it is waiting for.
  // 插話 late-steer 也在这里兜底：流已 settle 但某条插话未被运行中 graph 消费时，
  // 自动续跑为下一轮（user 气泡已存在 → skipUserBubble + skipUserAppend 复用同一
  // 条消息，不重复建气泡/写库）。插话优先于普通队列；每轮 effect run 每会话只发
  // 一条，避免同一会话并发双流被后端 409。
  useEffect(() => {
    const keys = new Set<string>([
      ...Object.keys(queuedMessagesRef.current),
      ...Object.keys(pendingSteersRef.current),
    ]);
    for (const key of keys) {
      const sessionId = key === '__none__' ? undefined : key;
      if (sessionId == null) continue;
      const busy = messages.some(
        (m) => (m.status === 'running' || m.status === 'waiting') && m.sessionId === sessionId,
      );
      // goal 多轮续跑流：整条流未收到 goal_stream_end 前不算 settle，队列消息
      // 必须等待（会话锁语义，见设计文档 §3.0）。
      const goalBusy = sessionId != null && goalStreamSessions.has(sessionId);
      if (busy || goalBusy) continue;
      const pendingSteers = pendingSteersRef.current[key];
      if (pendingSteers && pendingSteers.length > 0) {
        pendingSteersRef.current[key] = [];
        const steer = pendingSteers[0];
        if (!steer) continue;
        void sendMessage({
          message: steer.message,
          ...(steer.attachments && steer.attachments.length > 0 ? { attachments: steer.attachments } : {}),
          ...(steer.references && steer.references.length > 0 ? { references: steer.references } : {}),
          sessionId,
          skipUserBubble: true,
          skipUserAppend: true,
          userMessageId: steer.userMessageId,
        });
        continue;
      }
      const queue = queuedMessagesRef.current[key];
      if (!queue || queue.length === 0) continue;
      const dequeued = dequeueMessage(sessionId);
      if (dequeued) {
        void sendMessage({
          message: dequeued.message,
          ...(dequeued.attachments && dequeued.attachments.length > 0 ? { attachments: dequeued.attachments } : {}),
          ...(dequeued.references && dequeued.references.length > 0 ? { references: dequeued.references } : {}),
        });
      }
    }
  }, [messages, queuedEntries, goalStreamSessions]);

  // 卡死兜底：某条 assistant 消息仍卡在 running、但其流 controller 已不存在
  // （流已结束但 settle 未生效——例如 done 帧丢失/消息 id 不匹配/SSE 早断），
  // 則強制 settle，避免 isThinking/busy 永久為 true 把佇列與插話都卡死。
  // 只處理 running（不含 waiting：waiting = 待審批，須保留給使用者 resolve，
  // 其卡死由 resolvePendingRequest 失敗時的 settle 兜底）。
  useEffect(() => {
    if (sessionId == null) return;
    const key = streamKey(sessionId);
    const timer = setInterval(() => {
      if (streamControllersRef.current[key]) return; // 仍有在飛的流，不碰
      const stuck = messages.filter(
        (m) => m.status === 'running' && m.sessionId === sessionId,
      );
      for (const m of stuck) {
        void settleAssistantMessage({
          sessionId,
          assistantMessageId: m.id,
          streamedContent: m.content,
          receivedDone: false,
        });
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [messages, sessionId]);

  // Queue the current composer content; it auto-sends after the stream ends.
  const handleSendQueued = () => {
    if (!isThinking) {
      void sendMessage();
      return;
    }
    const sid = sessionIdRef.current;
    const messageText = input.trim();
    if (!sid || !messageText) return;
    const queuedAttachments = attachments;
    const queuedReferences = references;
    enqueueMessage(sid, messageText, { attachments: queuedAttachments, references: queuedReferences });
    setInput('');
    // 排隊時一併清命令 chip（含同步 ref），避免運行中發命令殘留 chip。
    setCommandChip(null);
    commandChipRef.current = null;
    setAttachments([]);
    setReferences([]);
  };

  const stopMessage = (opts?: { silent?: boolean }) => {
    // PendingDocks 的關閉/拒絕按鈕會同時觸發 onStop 與 reject，失敗音效由
    // reject 路徑統一播一次；這裡僅在「真正暫停運行」時（Stop 鍵 / 雙按 Esc）播放。
    if (!opts?.silent) playSound('user_pause');
    const key = streamKey(sessionIdRef.current);
    const assistantMessageId = activeAssistantMessageIdsRef.current[key];
    const streamStartAt = streamStartAtsRef.current[key];
    abortStreamFor(sessionIdRef.current);
    // Explicitly tell the backend to stop this session's generation. Aborting
    // the local stream alone can leave the session "active" on the backend for
    // a while (the disconnect teardown can stall), which would make the next
    // edit/regenerate fail with 409 "session is still generating".
    if (sessionIdRef.current) {
      void chatService.stopSessionStream(sessionIdRef.current);
    }
    // Also abort every worker transcript subscription for this session: the
    // worker blocks are being stopped, so their SSE streams (/worker-events)
    // must close too — otherwise the "Delegating…" spinner and any open
    // transcript keep running. The backend also closes each worker run on
    // cancellation (see WorkerAgent._execute CancelledError), so a later
    // re-expand replays the partial transcript from disk.
    for (const workerRunId of Object.keys(workerStreamControllersRef.current)) {
      workerStreamControllersRef.current[workerRunId]?.abort();
      delete workerStreamControllersRef.current[workerRunId];
    }
    if (assistantMessageId) {
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: item.content || t('chat.stopped'), status: 'stopped', parts: settleRunningTools(item.parts ?? []), streamStartAt: streamStartAt ?? Date.now(), streamEndAt: Date.now() }
            : item,
        ),
      );
    }
    // 目标多轮：Stop 时也把当前 running 的续跑轮气泡切到 stopped（这些轮由
    // goal_round_start 创建、id 是 goal-round-*，首轮 done 后首轮标记是 no-op）。
    setMessages((current) =>
      current.map((item) =>
        item.status === 'running' && item.sessionId === sessionIdRef.current && item.id !== assistantMessageId
          ? { ...item, content: item.content || t('chat.stopped'), status: 'stopped', parts: settleRunningTools(item.parts ?? []), streamEndAt: Date.now() }
          : item,
      ),
    );
    // 立即解除 goal 流会话锁：isThinking 依赖 goalStreamSessions，不等流真正
    // 断开就把 composer 的 Stop 按钮切回 Send，避免「停止后按钮无法切换状态」。
    markGoalStreamEnded(sessionIdRef.current);
    clearSessionTodos(sessionIdRef.current);
    requestSeqRef.current += 1;
    bumpSessionSeq(sessionIdRef.current);
  };

  const [editingMessage, setEditingMessage] = useState<{ id: string; content: string } | null>(null);
  const [editDraft, setEditDraft] = useState('');
  // 已提交到 composer 的命令 chip（skill/子命令）。真实 DOM 元素渲染在
  // ChatInput 里，这里持有权威状态供 sendMessage / 编辑流程使用。
  const [commandChip, setCommandChip] = useState<CommandChip | null>(null);
  // 同步镜像：ChatInput 在输入事件里同步删除编辑器中的命令 token 并异步
  // setCommandChip(chip)；若用户在 React re-render 前按下 Enter（例如运行中
  // 排隊），sendMessage 闭包会读到旧的 null，把命令当普通文字送出。ref 在
  // handleCommandCommit 被调用时同步写入，sendMessage 读 ref ?? state，彻底
  // 消除这个竞态。
  const commandChipRef = useRef<CommandChip | null>(null);
  // 点编辑键即回滚（edit-begin）后，记下待恢复的 (session, message)，取消编辑 /
  // 内容未变退出 / 切换会话时自动调用 edit-cancel 恢复文件。
  const pendingEditRevertRef = useRef<{ sessionId: string; messageId: string } | null>(null);

  const beginEditMessage = (messageId: string, content: string) => {
    setEditingMessage({ id: messageId, content });
    // skill 命令消息存储为注入标记：编辑时反解成 chip + 剩余提示词，与 composer 一致。
    const parsed = parseSkillMarker(content);
    setEditDraft(parsed?.text ?? content);
    setCommandChip(parsed?.chip ?? null);
    commandChipRef.current = parsed?.chip ?? null;
    // 点击编辑即回滚：立即还原该消息之后回合的代码改动，让用户在干净的
    // 文件态下编辑。取消/内容未变/切走会话时自动恢复（restorePendingEdit）。
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || !revertCode) return;
    void (async () => {
      try {
        const response = await chatService.beginEditMessage(currentSessionId, messageId, revertCode);
        if (response.reverted_count > 0) {
          pendingEditRevertRef.current = { sessionId: currentSessionId, messageId };
          setMessages((current) =>
            current.map((item) => (item.id === messageId ? { ...item, revertedFiles: response.reverted_count } : item)),
          );
        }
        if (response.conflict_count > 0) {
          window.alert(
            t('edit.revert_conflicts', {
              reverted: response.reverted_count,
              conflicts: response.conflict_count,
            }),
          );
        }
      } catch (error) {
        // 回滚失败不阻塞编辑；发送时后端仍会再试一次（幂等）。
        console.error('Failed to begin edit revert:', error);
      }
    })();
  };

  // 取消待处理的编辑回滚：恢复文件并把记录/快照对还原为 active，供下次再回滚。
  const restorePendingEdit = async () => {
    const pending = pendingEditRevertRef.current;
    if (!pending) return;
    pendingEditRevertRef.current = null;
    try {
      const response = await chatService.cancelEditMessage(pending.sessionId, pending.messageId);
      if (response.conflict_count > 0) {
        window.alert(
          t('edit.redo_conflicts', {
            restored: response.restored_count,
            conflicts: response.conflict_count,
          }),
        );
      }
      if (response.restored_count > 0) {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== pending.messageId) return item;
            const { revertedFiles: _ignored, ...next } = item;
            return next;
          }),
        );
        setChangesRefreshKey((value) => value + 1);
      }
    } catch (error) {
      console.error('Failed to cancel edit revert:', error);
    }
  };

  const commitEditMessage = async (messageId: string, content: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;
    // 编辑时 chip 存在 → 把「chip + 编辑文字」编码回注入标记作为新消息内容。
    const chip = commandChip;
    const encoded =
      chip?.type === 'skill'
        ? `${encodeSkillMarker(chip)}${content.trim() ? `\n\n${content.trim()}` : ''}`
        : content;
    const trimmed = encoded.trim();
    setCommandChip(null);
    if (!trimmed) return;
    setEditingMessage(null);
    setEditDraft('');
    // 已进入发送流程：清掉待恢复标记，避免切换会话时重复恢复。
    pendingEditRevertRef.current = null;

    // 编辑会重跑该会话，任何同会话仍在跑的流都会被本次编辑取代。先中止旧流，
    // 否则后端 _guard_session_not_streaming 会拒绝这次 /edit（409），且旧流的
    // 事件会被陈旧守卫丢弃，气泡悬在 running 计秒。
    abortStreamFor(currentSessionId);
    // 显式通知后端停止该会话的在跑流：仅靠本地 abort（socket 断开）时后端清理
    // 可能滞后，导致紧接着的 /edit 仍被 409 拒绝。等待其完成，确保旧流已释放。
    try {
      await chatService.stopSessionStream(currentSessionId);
    } catch {
      // 幂等接口；失败时由 socket 断开路径兜底清理。
    }

    // 编辑模式下，如果内容包含斜杠命令，走 sendMessage 路径
    if (trimmed.startsWith('/')) {
      // 斜杠命令路径不做 /edit 截断重跑：先恢复点编辑时已回滚的文件，
      // 避免旧回合改动停留在已回滚态。
      void restorePendingEdit();
      handleSlashCommand(trimmed);
      return;
    }

    bumpSessionSeq(currentSessionId);
    const myRequestSeq = getSessionSeq(currentSessionId);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setMessages((current) => {
      // 只截断「当前会话」的消息历史，保留其它会话仍在后台运行的消息，
      // 否则一次编辑会把其它会话并行中的任务从前端状态里整体抹掉。
      const others = current.filter((m) => m.sessionId && m.sessionId !== currentSessionId);
      const thisSession = current.filter((m) => !m.sessionId || m.sessionId === currentSessionId);
      const index = thisSession.findIndex((m) => m.id === messageId);
      if (index < 0) return current;
      const truncated = thisSession.slice(0, index + 1).map((m) =>
        m.id === messageId ? { ...m, content: trimmed, status: 'done' as const } : m,
      );
      return [
        ...others,
        ...truncated,
        createMessage('assistant', '', {
        streamStartAt: Date.now(),
          id: assistantMessageId,
          status: 'running',
            autonomy,
            ...(currentSessionId ? { sessionId: currentSessionId } : {}),
        }),
      ];
    });
    clearSessionTodos(currentSessionId);
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let receivedDone = false;
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    // 文本渲染限频（与主流一致），避免长回复 markdown 全量重解析卡住主线程。
    const flushText = () => {
      commit(localParts);
    };
    const textThrottle = createStreamThrottle(flushText);
    // Non-destructive parts write: preserves live worker transcripts (see
    // mergeLiveAgentTranscript) instead of overwriting them with the delegate
    // summary frames accumulated in localParts.
    const commit = (
      nextParts: MessagePart[],
      patch?: { content?: string; status?: ChatMessage['status']; streamEndAt?: number },
    ) =>
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                content: patch?.content !== undefined ? patch.content : streamedContent,
                parts: mergeLiveAgentTranscript(nextParts, item.parts),
                ...(patch?.status ? { status: patch.status } : {}),
                ...(patch?.streamEndAt !== undefined ? { streamEndAt: patch.streamEndAt } : {}),
              }
            : item,
        ),
      );
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      handleStreamWebEvents(event);
      trackBrowserToolEvent(event);
      if (event.type === 'context_usage') {
        if (!event.session_id || event.session_id === sessionIdRef.current) {
          const cu2 = { usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, compactCount: event.compact_count, windowSource: event.window_source, ...(event.active_budget_tokens != null ? { activeBudgetTokens: event.active_budget_tokens } : {}), ...(event.window_warning ? { windowWarning: event.window_warning } : {}), ...(event.used_tokens_calibrated != null ? { usedTokensCalibrated: event.used_tokens_calibrated } : {}), ...(event.calibration_factor != null ? { calibrationFactor: event.calibration_factor } : {}), ...(event.effective_window_tokens != null ? { effectiveWindowTokens: event.effective_window_tokens } : {}), ...(event.max_output_tokens != null ? { maxOutputTokens: event.max_output_tokens } : {}) }; setContextUsage(cu2);
        }
        return;
      }
      if (event.type === 'revert_summary') {
        // 编辑触发的文件回滚结果：把待恢复的改动数挂到被编辑的用户消息上，
        // 以便展示「恢复」按钮。
        if (event.reverted_count > 0) {
          setMessages((current) =>
            current.map((item) =>
              item.id === messageId ? { ...item, revertedFiles: event.reverted_count } : item,
            ),
          );
        }
        if (event.conflict_count > 0) {
          window.alert(
            t('edit.revert_conflicts', {
              reverted: event.reverted_count,
              conflicts: event.conflict_count,
            }),
          );
        }
        return;
      }
      if (event.type === 'delta') {
        streamedContent += event.content;
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'text') {
          last.content += event.content;
        } else {
          localParts.push({ type: 'text', content: event.content });
        }
        textThrottle.update();
      } else if (event.type === 'reasoning_delta') {
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'reasoning') {
          last.content = event.content;
        } else {
          localParts.push({ type: 'reasoning', content: event.content });
        }
        commit(localParts);
      } else if (event.type === 'tool_start') {
        // 编辑/重生成路径支持 tool_delta（P1 修复）
        localParts = upsertToolPart(localParts, event.id, event.name, event.input || '');
        commit(localParts);
      } else if (event.type === 'tool_delta') {
        const td = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (td) td.input = (td.input || '') + (event.input || '');
        commit(localParts);
      } else if (event.type === 'tool_end') {
        const toolPart = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (toolPart) {
          toolPart.status = event.status === 'success' ? 'success' : 'error';
          if (event.input !== undefined) toolPart.input = event.input;
          if (event.output) toolPart.output = event.output;
          if (event.duration_ms !== undefined) toolPart.duration_ms = event.duration_ms;
          if (event.files !== undefined) toolPart.files = event.files;
        }
        // 装完即见：agent 安装技能后立刻刷新技能列表，侧栏无需手动刷新。
        if (toolPart?.name === 'install_skill') void refreshSkills();
        commit(localParts);
      } else if (event.type === 'plan_start' || event.type === 'plan_delta' || event.type === 'plan_end') {
        if (event.type === 'plan_start') {
          localParts.push({ type: 'plan', content: '' });
        } else {
          const planPart = localParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
          if (planPart) {
            if (event.type === 'plan_delta') planPart.content += event.content;
            else if (event.type === 'plan_end' && event.content) planPart.content = event.content;
          }
        }
        commit(localParts);
      } else if (event.type === 'delegate_start' || event.type === 'delegate_progress' || event.type === 'delegate_end') {
        localParts = applyDelegateEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'steer_injected') {
        if (event.steer_id) markSteerConsumed(event.session_id ?? currentSessionId, event.steer_id);
        localParts = applyStreamEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        playSound('card_popup');
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: t('chat.waiting_resolution'), status: 'waiting' });
      } else if (event.type === 'done') {
        textThrottle.flushNow();
        receivedDone = true;
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        commit(localParts, { status: 'done', streamEndAt: Date.now() });
        clearSessionTodos(event.session_id ?? currentSessionId);
        playSound('reply_done');
        void maybeCheckSkillDrafts(event.session_id ?? currentSessionId);
      } else if (event.type === 'todos') {
        setSessionTodos(event.session_id ?? currentSessionId, assistantMessageId, event.todos);
      } else if (event.type === 'stage') {
        // P1 补充 stage 处理（编辑/重生成路径）
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: `${t('chat.waiting_resolution')} · ${event.name}`, status: 'running' as const }
              : item,
          ),
        );
      } else if (event.type === 'error') {
        textThrottle.flushNow();
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: event.error || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() });
        clearSessionTodos(event.session_id ?? currentSessionId);
        playSound('reply_error');
      }
    };
    try {
      const selectedProvider = providers.find((p) => p.id === selectedModel);
      await chatService.streamEditMessage(currentSessionId, messageId, trimmed, handleEvent, {
        signal: controller.signal,
        workMode,
        autonomy,
        revertCode,
        assistantMessageId,
        providerId: selectedModel,
        ...(selectedProvider?.model ? { model: selectedProvider.model } : {}),
      });
      await refreshSessions();
      await refreshProjects();
      setChangesRefreshKey((value) => value + 1);
    } catch (error) {
      console.error('Failed to edit message:', error);
      if ((error as Error).name !== 'AbortError') {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: translateError(error) || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
              : item,
          ),
        );
        playSound('reply_error');
      }
    } finally {
      stopBrowserAgent();
      if (streamControllersRef.current[streamKey(currentSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(currentSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(currentSessionId)];
        delete streamStartAtsRef.current[streamKey(currentSessionId)];
      }
      // Safety net: ensure the assistant message leaves the "running" state
      // even if the terminal event was dropped. Reconcile against the backend's
      // committed message first: a present id means the reply succeeded and
      // must be adopted as `done`; only a genuine miss becomes `interrupted`.
      void settleAssistantMessage({
        sessionId: currentSessionId,
        assistantMessageId,
        streamedContent,
        receivedDone,
      });
      requestSeqRef.current += 1;
      bumpSessionSeq(currentSessionId);
    }
  };

  const handleRegenerateMessage = async (messageId: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || isThinking) return;
    // 触发这次重新生成的用户消息（重生成的回滚结果挂到它上面，展示「恢复」按钮）。
    const thisSessionNow = messages.filter((m) => !m.sessionId || m.sessionId === currentSessionId);
    const idxNow = thisSessionNow.findIndex((m) => m.id === messageId);
    // 重新生成的目标是触发它的 user 消息：向后端回溯最近一条 user 消息 id
    //（与后端 /regenerate 的 walk-back 逻辑一致）。user 消息 id 必然持久存在，
    // 即使上次重跑流失败（如 provider 503）未持久化 assistant，也不会 404。
    let triggerUserMessageId: string | undefined;
    for (let i = idxNow - 1; i >= 0; i -= 1) {
      const candidate = thisSessionNow[i];
      if (candidate?.role === 'user') {
        triggerUserMessageId = candidate.id;
        break;
      }
    }
    bumpSessionSeq(currentSessionId);
    const myRequestSeq = getSessionSeq(currentSessionId);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    abortStreamFor(currentSessionId);
    // 显式通知后端停止该会话的在跑流（理由同上：避免 /regenerate 被 409 拒绝）。
    try {
      await chatService.stopSessionStream(currentSessionId);
    } catch {
      // 幂等接口；失败时由 socket 断开路径兜底清理。
    }
    setMessages((current) => {
      // 只截断「当前会话」的消息历史，保留其它会话仍在后台运行的消息。
      const others = current.filter((m) => m.sessionId && m.sessionId !== currentSessionId);
      const thisSession = current.filter((m) => !m.sessionId || m.sessionId === currentSessionId);
      const index = thisSession.findIndex((m) => m.id === messageId);
      if (index < 0) return current;
      const truncated = thisSession.slice(0, index);
      return [
        ...others,
        ...truncated,
        createMessage('assistant', '', {
        streamStartAt: Date.now(),
          id: assistantMessageId,
          status: 'running',
            autonomy,
            ...(currentSessionId ? { sessionId: currentSessionId } : {}),
        }),
      ];
    });
    clearSessionTodos(currentSessionId);
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let receivedDone = false;
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    // 文本渲染限频（与主流一致），避免长回复 markdown 全量重解析卡住主线程。
    const flushText = () => {
      commit(localParts);
    };
    const textThrottle = createStreamThrottle(flushText);
    // Non-destructive parts write: preserves live worker transcripts (see
    // mergeLiveAgentTranscript) instead of overwriting them with the delegate
    // summary frames accumulated in localParts.
    const commit = (
      nextParts: MessagePart[],
      patch?: { content?: string; status?: ChatMessage['status']; streamEndAt?: number },
    ) =>
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                content: patch?.content !== undefined ? patch.content : streamedContent,
                parts: mergeLiveAgentTranscript(nextParts, item.parts),
                ...(patch?.status ? { status: patch.status } : {}),
                ...(patch?.streamEndAt !== undefined ? { streamEndAt: patch.streamEndAt } : {}),
              }
            : item,
        ),
      );
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      handleStreamWebEvents(event);
      trackBrowserToolEvent(event);
      if (event.type === 'context_usage') {
        if (!event.session_id || event.session_id === sessionIdRef.current) {
          const cu2 = { usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, compactCount: event.compact_count, windowSource: event.window_source, ...(event.active_budget_tokens != null ? { activeBudgetTokens: event.active_budget_tokens } : {}), ...(event.window_warning ? { windowWarning: event.window_warning } : {}), ...(event.used_tokens_calibrated != null ? { usedTokensCalibrated: event.used_tokens_calibrated } : {}), ...(event.calibration_factor != null ? { calibrationFactor: event.calibration_factor } : {}), ...(event.effective_window_tokens != null ? { effectiveWindowTokens: event.effective_window_tokens } : {}), ...(event.max_output_tokens != null ? { maxOutputTokens: event.max_output_tokens } : {}) }; setContextUsage(cu2);
        }
        return;
      }
      if (event.type === 'revert_summary') {
        // 重新生成也回滚（与编辑一致）：把待恢复的改动数挂到触发它的用户消息上。
        if (event.reverted_count > 0 && triggerUserMessageId) {
          setMessages((current) =>
            current.map((item) =>
              item.id === triggerUserMessageId ? { ...item, revertedFiles: event.reverted_count } : item,
            ),
          );
        }
        if (event.conflict_count > 0) {
          window.alert(
            t('edit.revert_conflicts', {
              reverted: event.reverted_count,
              conflicts: event.conflict_count,
            }),
          );
        }
        return;
      }
      if (event.type === 'delta') {
        streamedContent += event.content;
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'text') {
          last.content += event.content;
        } else {
          localParts.push({ type: 'text', content: event.content });
        }
        textThrottle.update();
      } else if (event.type === 'reasoning_delta') {
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'reasoning') {
          last.content = event.content;
        } else {
          localParts.push({ type: 'reasoning', content: event.content });
        }
        commit(localParts);
      } else if (event.type === 'tool_start') {
        // 编辑/重生成路径支持 tool_delta（P1 修复）
        localParts = upsertToolPart(localParts, event.id, event.name, event.input || '');
        commit(localParts);
      } else if (event.type === 'tool_delta') {
        const td = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (td) td.input = (td.input || '') + (event.input || '');
        commit(localParts);
      } else if (event.type === 'tool_end') {
        const toolPart = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (toolPart) {
          toolPart.status = event.status === 'success' ? 'success' : 'error';
          if (event.input !== undefined) toolPart.input = event.input;
          if (event.output) toolPart.output = event.output;
          if (event.duration_ms !== undefined) toolPart.duration_ms = event.duration_ms;
          if (event.files !== undefined) toolPart.files = event.files;
        }
        // 装完即见：agent 安装技能后立刻刷新技能列表，侧栏无需手动刷新。
        if (toolPart?.name === 'install_skill') void refreshSkills();
        commit(localParts);
      } else if (event.type === 'plan_start' || event.type === 'plan_delta' || event.type === 'plan_end') {
        if (event.type === 'plan_start') {
          localParts.push({ type: 'plan', content: '' });
        } else {
          const planPart = localParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
          if (planPart) {
            if (event.type === 'plan_delta') planPart.content += event.content;
            else if (event.type === 'plan_end' && event.content) planPart.content = event.content;
          }
        }
        commit(localParts);
      } else if (event.type === 'delegate_start' || event.type === 'delegate_progress' || event.type === 'delegate_end') {
        localParts = applyDelegateEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'steer_injected') {
        if (event.steer_id) markSteerConsumed(event.session_id ?? currentSessionId, event.steer_id);
        localParts = applyStreamEventToParts(localParts, event);
        commit(localParts);
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        playSound('card_popup');
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: t('chat.waiting_resolution'), status: 'waiting' });
      } else if (event.type === 'done') {
        textThrottle.flushNow();
        receivedDone = true;
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        commit(localParts, { status: 'done', streamEndAt: Date.now() });
        clearSessionTodos(event.session_id ?? currentSessionId);
        playSound('reply_done');
        void maybeCheckSkillDrafts(event.session_id ?? currentSessionId);
      } else if (event.type === 'todos') {
        setSessionTodos(event.session_id ?? currentSessionId, assistantMessageId, event.todos);
      } else if (event.type === 'stage') {
        // P1 补充 stage 处理（编辑/重生成路径）
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: `${t('chat.waiting_resolution')} · ${event.name}`, status: 'running' as const }
              : item,
          ),
        );
      } else if (event.type === 'error') {
        textThrottle.flushNow();
        localParts = settleRunningTools(localParts);
        commit(localParts, { content: event.error || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() });
        clearSessionTodos(event.session_id ?? currentSessionId);
        playSound('reply_error');
      }
    };
    try {
      const selProv = providers.find((p) => p.id === selectedModel);
      await chatService.streamRegenerateMessage(currentSessionId, triggerUserMessageId || messageId, handleEvent, controller.signal, {
        assistantMessageId,
        providerId: selectedModel,
        ...(selProv?.model ? { model: selProv.model } : {}),
      });
      await refreshSessions();
      await refreshProjects();
      setChangesRefreshKey((value) => value + 1);
    } catch (error) {
      console.error('Failed to regenerate message:', error);
      if ((error as Error).name !== 'AbortError') {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: translateError(error) || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
              : item,
          ),
        );
        playSound('reply_error');
      }
    } finally {
      stopBrowserAgent();
      if (streamControllersRef.current[streamKey(currentSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(currentSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(currentSessionId)];
        delete streamStartAtsRef.current[streamKey(currentSessionId)];
      }
      // Safety net: ensure the assistant message leaves the "running" state
      // even if the terminal event was dropped. Reconcile against the backend's
      // committed message first: a present id means the reply succeeded and
      // must be adopted as `done`; only a genuine miss becomes `interrupted`.
      void settleAssistantMessage({
        sessionId: currentSessionId,
        assistantMessageId,
        streamedContent,
        receivedDone,
      });
      requestSeqRef.current += 1;
      bumpSessionSeq(currentSessionId);
    }
  };

  const handleRedoMessage = async (messageId: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;
    try {
      const response = await chatService.redoMessage(currentSessionId, messageId);
      if (response.conflict_count > 0) {
        window.alert(
          t('edit.redo_conflicts', {
            restored: response.restored_count,
            conflicts: response.conflict_count,
          }),
        );
      }
      if (response.restored_count > 0) {
        // 恢复成功：清除待恢复标记并刷新变更面板。
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== messageId) return item;
            const { revertedFiles: _ignored, ...next } = item;
            return next;
          }),
        );
        setChangesRefreshKey((value) => value + 1);
      }
    } catch (error) {
      console.error('Failed to redo message changes:', error);
      window.alert(translateError(error) || t('edit.redo_failed'));
    }
  };

  const resolvingRef = useRef(false);

  const _generateSessionTitleIfNeeded = (firstMessageContent: string, assistantResponse: string, sessionSessionId?: string) => {
    if (!sessionSessionId) return;
    // Only auto-title a session that still has its default placeholder title.
    // The previous guard checked the *global* messages array, so after ANY
    // session had been used no new session was ever titled.
    const target = sessions.find((s) => s.id === sessionSessionId);
    if (target && target.title && target.title !== '新会话' && target.title !== 'New chat' && target.title !== '新对话') return;
    chatService.generateTitle(sessionSessionId, firstMessageContent, assistantResponse).then(
      (newTitle) => {
        if (newTitle && newTitle !== '新会话') {
          setSessions((current) => current.map((s) => (s.id === sessionSessionId ? { ...s, title: newTitle } : s)));
        }
      },
      () => {},
    );
  };

  const resolvePendingRequest = async (request: PendingRequest, decision: ApprovalDecisionPayload) => {
    if (resolvingRef.current) return;
    const targetMessageId = request.messageId || [...messages].reverse().find((m) => m.role === 'assistant')?.id || '';
    // 即时回显：用户提交回答后立刻把回复贴到 ask_user 工具卡的 Question 下方，
    // 让用户马上确认「回答已被收到」，而不是等到 resume 流重放才出现（甚至丢失）。
    // 覆盖所有路径：单选/「其他」填空/多选/无选项自由文本。
    const answer = decision.type === 'respond' ? decision.message || '' : '';
    if (answer && targetMessageId) {
      setMessages((current) =>
        current.map((item) =>
          item.id !== targetMessageId
            ? item
            : {
                ...item,
                parts: (item.parts || []).map((p) =>
                  p.type === 'tool' && p.name === 'ask_user' ? { ...p, output: answer, status: 'success' as const } : p,
                ),
              },
        ),
      );
    }
    resolvingRef.current = true;
    const isReject = decision.type === 'reject';
    let rejectFeedbackPlayed = false;
    setPendingRequests((current) =>
      current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: true } : item)),
    );
    let resumeId: string | undefined;
    try {
      const response = await chatService.resolveCommandApproval(request.approval_id, decision);
      resumeId = response.resume_id;
      // 拒絕/關閉卡片：後端已確認，立即播放一次失敗音效（後續 resume 流的
      // done/error 由 rejectFeedbackPlayed 攔截，不會再補播）。
      if (isReject && !rejectFeedbackPlayed) {
        rejectFeedbackPlayed = true;
        playSound('reply_error');
      }
      const chained = (response.events ?? []).filter(
        (event): event is Extract<StreamEvent, { type: 'approval_required' } | { type: 'question_required' }> =>
          event.type === 'approval_required' || event.type === 'question_required',
      );
      setPendingRequests((current) => [
        ...current.filter((item) => item.approval_id !== request.approval_id),
        ...chained.map((event): PendingRequest => pendingRequestFromEvent(event, request.session_id, targetMessageId)),
      ]);
      if (chained.length > 0 && !isReject) playSound('card_popup');
    } catch (error) {
      console.error('Failed to resolve approval:', error);
      setPendingRequests((current) =>
        current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: false } : item)),
      );
      // 解析失敗：把卡在 waiting 的訊息 settle 掉，否則 isThinking/busy 永久為
      // true，佇列與插話都被卡死。
      if (targetMessageId) {
        setMessages((current) =>
          current.map((item) =>
            item.id !== targetMessageId
              ? item
              : {
                  ...item,
                  status: 'interrupted' as const,
                  parts: settleRunningTools(item.parts ?? []),
                  streamEndAt: Date.now(),
                },
          ),
        );
      }
      return;
    } finally {
      stopBrowserAgent();
      resolvingRef.current = false;
    }

    if (!resumeId) {
      // 后端未调度 resume（非 langgraph 审批，或同一 interrupt 的其它 sibling 尚未决定）。
      // 若当前没有其它待审批卡，把气泡收尾为 done，避免永久停在「等待处理中」；
      // 否则保持 waiting，等其余提问卡回答后统一 resume。
      const hasOtherPending = pendingRequests.some((item) => item.approval_id !== request.approval_id);
      if (!hasOtherPending) {
        setMessages((current) =>
          current.map((item) =>
            item.id === targetMessageId && (item.status === 'waiting' || item.status === 'running')
              ? { ...item, status: 'done' as const, streamEndAt: Date.now() }
              : item,
          ),
        );
      }
      return;
    }
    // 用户已作答：气泡从「等待处理中」切到「运行中」，让 UI 立即反映 agent 正在继续。
    setMessages((current) =>
      current.map((item) =>
        item.id === targetMessageId && item.status === 'waiting'
          ? { ...item, status: 'running' as const }
          : item,
      ),
    );
    // 将 resume 流的 controller 按会话登记，使会话切换能正确中断它（幽灵流修复）
    const resumeSessionId = request.session_id || sessionIdRef.current || '';
    const resumeController = new AbortController();
    streamControllersRef.current[streamKey(resumeSessionId)] = resumeController;
    activeAssistantMessageIdsRef.current[streamKey(resumeSessionId)] = targetMessageId;
    bumpSessionSeq(resumeSessionId);
    const resumeRequestSeq = getSessionSeq(resumeSessionId);
    let resumeContent = '';
    let resumeParts: MessagePart[] = [];
    let resumeDone = false;
    const applyResume = (status: 'running' | 'done') => {
      setMessages((current) =>
        current.map((item) =>
          item.id !== targetMessageId
            ? item
            : { ...item, content: resumeContent, status, parts: mergeMessageParts(item.parts || [], resumeParts) },
        ),
      );
    };
    // 文本渲染限频（与主流一致），避免长回复 markdown 全量重解析卡住主线程。
    const resumeThrottle = createStreamThrottle(() => applyResume('running'));
    try {
      await chatService.subscribeApprovalEvents(
        resumeId,
        (event) => {
          // P1 陈旧请求守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
          if (isStreamStale(resumeSessionId, resumeRequestSeq)) return;
          handleStreamWebEvents(event);
          if (event.type === 'context_usage') {
            if (!event.session_id || event.session_id === sessionIdRef.current) {
              const cu = { usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, compactCount: event.compact_count, windowSource: event.window_source, ...(event.active_budget_tokens != null ? { activeBudgetTokens: event.active_budget_tokens } : {}), ...(event.window_warning ? { windowWarning: event.window_warning } : {}), ...(event.used_tokens_calibrated != null ? { usedTokensCalibrated: event.used_tokens_calibrated } : {}), ...(event.calibration_factor != null ? { calibrationFactor: event.calibration_factor } : {}), ...(event.effective_window_tokens != null ? { effectiveWindowTokens: event.effective_window_tokens } : {}), ...(event.max_output_tokens != null ? { maxOutputTokens: event.max_output_tokens } : {}) };
              setContextUsage(cu);
            }
            return;
          }
          if (event.type === 'done') {
            resumeThrottle.flushNow();
            resumeDone = true;
            resumeContent = event.content || resumeContent;
            if (event.parts && event.parts.length > 0) {
              resumeParts = event.parts;
            }
            resumeParts = settleRunningTools(resumeParts);
            applyResume('done');
            clearSessionTodos(event.session_id ?? resumeSessionId);
            void maybeCheckSkillDrafts(event.session_id ?? resumeSessionId);
            // 拒絕/關閉卡片：只回饋失敗音效，避免 resume 流同時發出 done（成功）與
            // error（失敗）導致兩種音效疊在一起。
            if (isReject) {
              if (!rejectFeedbackPlayed) {
                rejectFeedbackPlayed = true;
                playSound('reply_error');
              }
            } else {
              playSound('reply_done');
            }
          } else if (event.type === 'todos') {
            setSessionTodos(event.session_id ?? resumeSessionId, targetMessageId, event.todos);
          } else if (event.type === 'steer_injected') {
            if (event.steer_id) markSteerConsumed(event.session_id ?? resumeSessionId, event.steer_id);
            resumeParts = [
              ...resumeParts,
              { type: 'steer' as const, content: event.content || '', ...(event.steer_id ? { steer_id: event.steer_id } : {}) },
            ];
            applyResume('running');
          } else if (event.type === 'delta') {
            resumeContent += event.content;
            const last = resumeParts[resumeParts.length - 1];
            if (last && last.type === 'text') {
              last.content += event.content;
            } else {
              resumeParts.push({ type: 'text', content: event.content });
            }
            resumeThrottle.update();
          } else if (event.type === 'reasoning_delta') {
            const last = resumeParts[resumeParts.length - 1];
            if (last && last.type === 'reasoning') {
              last.content = event.content;
            } else {
              resumeParts.push({ type: 'reasoning', content: event.content });
            }
            applyResume('running');
          } else if (event.type === 'tool_start') {
            resumeParts = upsertToolPart(resumeParts, event.id, event.name, event.input || '');
            applyResume('running');
          } else if (event.type === 'tool_delta') {
            const tp = resumeParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
            if (tp) tp.input = (tp.input || '') + (event.input || '');
            applyResume('running');
          } else if (event.type === 'tool_end') {
            const tp = resumeParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
            if (tp) {
              tp.status = event.status === 'success' ? 'success' : 'error';
              if (event.input !== undefined) tp.input = event.input;
              if (event.output) tp.output = event.output;
            } else {
              // Resume 只重放了 tool_end（未重放 tool_start）：工具卡存在于已有的
              // message parts 里，这里构造一个最小 tool part，让 applyResume 的
              // mergeMessageParts 把 output 合并进已有的卡——否则 ask_user 的回答
              // 会在 resume 重放时被丢弃，用户看不到自己的回复。
              const synthesized: Extract<MessagePart, { type: 'tool' }> = {
                type: 'tool',
                id: event.id,
                name: event.name || '',
                status: event.status === 'success' ? 'success' : 'error',
                input: event.input || '',
                ...(event.output ? { output: event.output } : {}),
              };
              resumeParts.push(synthesized);
            }
            // 装完即见：agent 安装技能后立刻刷新技能列表。
            if ((tp?.name || event.name) === 'install_skill') void refreshSkills();
            applyResume('running');
          } else if (event.type === 'plan_start') {
            resumeParts.push({ type: 'plan', content: '' });
            applyResume('running');
          } else if (event.type === 'plan_delta') {
            const pp = resumeParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
            if (pp) pp.content += event.content;
            applyResume('running');
          } else if (event.type === 'plan_end') {
            const pp = resumeParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
            if (pp && event.content) pp.content = event.content;
            applyResume('running');
          } else if (event.type === 'delegate_start' || event.type === 'delegate_progress' || event.type === 'delegate_end') {
            resumeParts = applyDelegateEventToParts(resumeParts, event);
            applyResume('running');
          } else if (event.type === 'stage') {
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: `${t('chat.waiting_resolution')} · ${event.name}`, status: 'running' as const, parts: mergeMessageParts(item.parts || [], resumeParts) }
                  : item,
              ),
            );
          } else if (event.type === 'approval_required' || event.type === 'question_required') {
            setPendingRequests((current) => {
              if (current.some((item) => item.approval_id === event.approval_id)) return current;
              return [...current, pendingRequestFromEvent(event, resumeSessionId, targetMessageId)];
            });
            playSound('card_popup');
            resumeParts = settleRunningTools(resumeParts);
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: t('chat.waiting_resolution'), status: 'waiting', parts: mergeMessageParts(item.parts || [], resumeParts) }
                  : item,
              ),
            );
      } else if (event.type === 'error') {
        resumeThrottle.flushNow();
        resumeParts = settleRunningTools(resumeParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === targetMessageId
              ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', parts: mergeMessageParts(item.parts || [], resumeParts), streamEndAt: Date.now() }
              : item,
          ),
        );
        clearSessionTodos(event.session_id ?? resumeSessionId);
        if (!isReject || !rejectFeedbackPlayed) {
          rejectFeedbackPlayed = true;
          playSound('reply_error');
        }
          }
        },
        resumeController.signal,
      );
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        console.error('Approval event stream failed:', error);
      }
    } finally {
      stopBrowserAgent();
      // P0 并发双流修复：resume 流结束后清除该会话的 controller
      if (streamControllersRef.current[streamKey(resumeSessionId)] === resumeController) {
        delete streamControllersRef.current[streamKey(resumeSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(resumeSessionId)];
        delete streamStartAtsRef.current[streamKey(resumeSessionId)];
      }
      // 断线兜底：resume 流结束却没收到 done，先与后端已落库消息对账再收尾——
      // 后端已在 done 帧写出前持久化 assistant 消息，若该 id 已存在则采纳为
      // done，避免「后端已回复完却显示橙条 interrupted」；只有后端也无记录时
      // 才标记 interrupted，避免 spinner 永久挂起。
      if (!resumeDone) {
        void settleAssistantMessage({
          sessionId: resumeSessionId,
          assistantMessageId: targetMessageId,
          streamedContent: resumeContent,
          receivedDone: false,
          extraParts: resumeParts,
        });
      }
    }
  };

  const dismissPendingRequest = (request: PendingRequest) => {
    void resolvePendingRequest(request, { type: 'reject' });
  };

  const startProjectDraft = (projectId?: string, firstMessage = '', agentId?: string) => {
    // 新开对话不中止任何会话的流：并行任务各自在后台继续跑（真·多进程互不干扰）。
    // 消息数组保留所有会话的消息，hero/草稿视图按 sessionId 过滤隐藏，切回即可见。
    // 离开会话前恢复上一会话点编辑时已回滚的文件。
    void restorePendingEdit();
    requestSeqRef.current += 1;
    setMessages((current) => current.filter((m) => m.sessionId));
    setInput(firstMessage);
    setAttachments([]);
    setPendingRequests([]);
    setSessionId(undefined);
    sessionIdRef.current = undefined;
    setContextUsage(null); // 新会话不残留上一会话的上下文预算（B10）
    pendingProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
    setSelectedProjectId(projectId);
    const project = projects.find((p) => p.id === projectId);
    const resolvedAgent = project?.mode === 'single' ? 'default_agent' : (agentId ?? 'default_agent');
    setDraftAgentId(resolvedAgent);
    setActiveView('chat');
  };

  // 新对话：不再弹窗。在项目内新建则继承该项目 workspace；
  // 全局新建则进入空态，由 composer 顶部的 workspace 选择器指定。
  // 首启无任何真实项目时，全局「新对话」直接落到系统保留的聊天项目。
  const startNewChat = (projectId?: string, agentId?: string) => {
    const realProjects = projects.filter((p) => !p.is_chat);
    const chatProject = projects.find((p) => p.is_chat);
    const effectiveProjectId = projectId ?? (realProjects.length === 0 ? chatProject?.id : undefined);
    startProjectDraft(effectiveProjectId, '', agentId);
    setDraftMode(true);
  };

  // 草稿态下切换 workspace：仅改归属，不清空已输入内容
  const selectDraftWorkspace = (projectId: string) => {
    pendingProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
    setSelectedProjectId(projectId);
    setDraftMode(true);
    setActiveView('chat');
  };

  const openProject = (projectId: string) => {
    startProjectDraft(projectId);
    setDraftMode(false);
  };

  const openOrgSettings = (projectId: string) => {
    setOrgProjectId(projectId);
    setDraftMode(false);
    setActiveView('org');
  };

  const openDashboard = (projectId: string) => {
    setDashboardProjectId(projectId);
    setDraftMode(false);
    setActiveView('dashboard');
  };

  const selectProject = (projectId: string) => {
    setSelectedProjectId(projectId);
  };

  const pickWorkspaceDirectory = async () => {
    return chatService.openDirectoryPicker({ title: t('project_dialog.pick_workspace') });
  };

  const createProjectWithWorkspace = async (payload: CreateProjectRequest): Promise<ProjectEntry> => {
    const response = await chatService.createProject(payload);
    await refreshProjects();
    // 创建完成后直接进入该项目的新会话页（草稿态），而非停留在会话历史列表
    startNewChat(response.project.id);
    return response.project;
  };

  const restorePendingForSession = async (targetSessionId: string) => {
    try {
      const response = await chatService.listCommandApprovals();
      const restored: PendingRequest[] = [];
      for (const approval of response.approvals) {
        if (approval.status !== 'pending') continue;
        const context = approval.context;
        if (!context || context.session_id !== targetSessionId) continue;
        const kind = context.kind === 'question' ? 'question' : context.kind === 'plan' ? 'plan' : 'command';
        const base: PendingRequest = {
          approval_id: approval.id,
          kind,
          session_id: targetSessionId,
          approval_status: approval.status,
          messageId: '',
        };
        if (kind === 'question') {
          const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
          restored.push({
            ...base,
            ...(typeof args.question === 'string' ? { question: args.question } : {}),
            ...(typeof args.header === 'string' ? { header: args.header } : {}),
            ...(Array.isArray(args.options) ? { options: args.options as ApprovalOption[] } : {}),
            ...(typeof args.multiple === 'boolean' ? { multiple: args.multiple } : {}),
          });
        } else if (kind === 'plan') {
          const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
          restored.push({
            ...base,
            ...(typeof args.plan_text === 'string' ? { plan: args.plan_text } : {}),
          });
        } else {
          const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
          restored.push({
            ...base,
            command: Array.isArray(approval.command) ? approval.command : [],
            ...(approval.cwd ? { cwd: approval.cwd } : {}),
            ...(typeof context.tool_name === 'string' && context.tool_name ? { tool_name: context.tool_name } : {}),
            ...(Object.keys(args).length > 0 ? { tool_args: args } : {}),
          });
        }
      }
      if (restored.length === 0) return;
      setPendingRequests((current) => {
        const existing = current.filter((item) => item.session_id === targetSessionId);
        const existingIds = new Set(existing.map((item) => item.approval_id));
        const additions = restored.filter((item) => !existingIds.has(item.approval_id));
        if (additions.length === 0) return current;
        return [...current.filter((item) => item.session_id !== targetSessionId), ...existing, ...additions];
      });
    } catch (error) {
      console.error('Failed to restore pending approvals:', error);
    }
  };

  const openSession = async (sessionIdToOpen: string) => {
    // 同会话短路：已经在流该会话 → 只需切回 chat 视图
    if (sessionIdRef.current === sessionIdToOpen) {
      setActiveView('chat');
      return;
    }
    // 切走会话前，自动恢复上一会话点编辑时已回滚的文件，避免代码停留在已回滚态。
    void restorePendingEdit();
    // 不要中止正在运行的流：它属于另一个会话，让它在后台继续更新自己的消息，
    // 切回时仍能看到半截回复并原地续流（displayedMessages 会按 sessionId 过滤）。
    // 只切换当前视图，各会话的流由 per-session 的 controller 独立管理。
    setActiveView('chat');
    setDraftMode(false);
    // 已存在的会话自带 agent 归属，草稿 agent 选择器不再适用
    setDraftAgentId('');
    // 保存当前 sessionId，供 fetch 失败时回滚。
    const prevSessionId = sessionIdRef.current;
    // 切走会话 = 已查看其消息与错误状态 → 标记旧会话已读（未读 + 错误角标即时清除）。
    if (prevSessionId && prevSessionId !== sessionIdToOpen) {
      markSessionReadLocal(prevSessionId);
    }
    try {
      const response = await chatService.getSession(sessionIdToOpen);
      const records = response.session.messages ?? [];
      const loaded = records.map((record, index) =>
        createMessage(record.role as ChatMessage['role'], record.content, {
          id: record.id || `${record.role}-${index}-${record.id}`,
          status: 'done',
          // Loaded messages belong to this session — tag them so the
          // displayedMessages filter never treats them as ambient (which would
          // make them bleed into every other session's view).
          sessionId: sessionIdToOpen,
          ...(record.work_mode ? { work_mode: record.work_mode as WorkMode } : {}),
          ...(record.autonomy ? { autonomy: record.autonomy as Autonomy } : {}),
          ...(record.provider ? { provider: record.provider } : {}),
          ...(record.model ? { model: record.model } : {}),
          ...(record.attachments?.length ? { attachments: record.attachments } : {}),
          ...(record.parts?.length ? { parts: normalizeParts(record.parts as MessagePart[]) } : {}),
          ...(record.references?.length ? { references: record.references } : {}),
          ...(record.interject ? { interject: true } : {}),
          timestamp: new Date(record.created_at).getTime(),
        }),
      );
      setSessionId(sessionIdToOpen);
      sessionIdRef.current = sessionIdToOpen;
      // 打开会话 = 用户正在查看该会话 → 标记已读并清除错误标记（含同会话
      // 后端在后台产生新消息 / 新错误的情况）。
      markSessionReadLocal(sessionIdToOpen);
      // 打开会话时立即按「当前模型配置」拉取最新上下文预算，而非残留上一会话
      // 的旧值（否则旧会话会一直显示改动前的窗口，例如 252k 而非最新的 192k）。
      // 服务端按当前 providers 配置解析窗口，因此改过 context_window 后旧会话
      // 也会立刻反映新窗口；下一次真正 run 会用校准值覆盖这里的预览值。
      const selProvider = providers.find((p) => p.id === selectedModel);
      const selModel = selProvider?.model ?? runtimeConfig?.selected_model ?? '';
      void chatService
        .getContextUsage(sessionIdToOpen, selectedModel, selModel)
        .then((resp) => {
          if (sessionIdRef.current === sessionIdToOpen && resp?.context_usage) {
            setContextUsage(mapContextUsage(resp.context_usage));
          }
        })
        .catch(() => {
          if (sessionIdRef.current === sessionIdToOpen) setContextUsage(null);
        });
      pendingProjectIdRef.current = undefined;
      setPendingRequests((current) => current.filter((item) => item.session_id !== sessionIdToOpen));
      void restorePendingForSession(sessionIdToOpen);
      // 恢复该会话持久化的任务卡（切走再切回时卡片不消失）。
      const sessionRecord = response.session as SessionDetailResponse['session'] & { todos?: Todo[] };
      if (sessionRecord.todos && sessionRecord.todos.length > 0) {
        setSessionTodos(sessionIdToOpen, `persisted:${sessionIdToOpen}`, sessionRecord.todos);
      }
      // Goal 恢复（对标 codex restore_inherited_goal_runtime）：拉取该会话目标，
      // 若 active 则在 TodoBlock 渲染并自动重启续跑流（3.3.1），否则只渲染不重启。
      void chatService
        .getGoal(sessionIdToOpen)
        .then((resp) => {
          if (sessionIdRef.current !== sessionIdToOpen) return;
          setSessionGoal(sessionIdToOpen, resp?.goal ?? null);
          if (resp?.goal?.status === 'active') {
            void kickGoalContinuation(sessionIdToOpen);
          }
        })
        .catch(() => {
          if (sessionIdRef.current === sessionIdToOpen) setSessionGoal(sessionIdToOpen, null);
        });
      // 后台 resume 可能仍在运行：切回时首扫可能早于新审批创建，延迟重扫兜底。
      for (const delay of [5000, 15000]) {
        setTimeout(() => {
          if (sessionIdRef.current === sessionIdToOpen) {
            void restorePendingForSession(sessionIdToOpen);
          }
        }, delay);
      }
      setActiveProjectId(response.session.project_id || undefined);
      setSelectedProjectId(response.session.project_id || undefined);
      // 归而非覆盖：保留本地 status === 'running' 的消息 — 包括从其它会话切走后
      // 仍在后台续流的半截回复，以便切回时继续看到流式内容。
      // 注意：后端仅在流终结时（done/error/断开）才持久化 assistant 消息，因此
      // 切回时若目标会话仍在前台或后台流式中，loaded 不含该消息，running 会被保留；
      // 若后端已完成并持久化（同 id），则用持久化版本（内容完整），这是正确收尾。
      setMessages((current) => {
        // 归而非覆盖：只替换目标会话的消息，其余所有会话（含后台
        // streaming 的）原样保留，避免切换会话时抹掉其他会话正在进行的流。
        // 后端仅在流终结时才持久化 assistant 消息，切回时若目标会话仍
        // 在流式中，loaded 不含该消息，running 会被保留；若后端已完成
        // 并持久化（同 id），则用持久化版本（内容完整），这是正确收尾。
        const loadedIds = new Set(loaded.map((m) => m.id));
        // 所有非目标 session 的消息：ambient + 其他 session 的已发送
        // + 其他 session 的 running（后台续流）
        const others = current.filter((m) => !m.sessionId || m.sessionId !== sessionIdToOpen);
        // 目标 session 的 running 消息：若 loaded 已覆盖则丢弃（后
        // 端已完成），否则保留（后端未 commit，继续流式更新）
        const thisRunning = current.filter(
          (m) => m.sessionId === sessionIdToOpen && m.status === 'running' && !loadedIds.has(m.id),
        );
        return [...loaded, ...others, ...thisRunning];
      });
      setAttachments([]);
    } catch (error) {
      console.error('Failed to open session:', error);
      // 回滚已修改的 state，避免目标会话处于半加载状态。
      setSessionId(prevSessionId);
      sessionIdRef.current = prevSessionId;
    }
  };

  const deleteSession = async (sessionIdToDelete: string) => {
    try {
      // 先硬终止该会话正在进行的流/任务（SSE 断开会让后端取消生成），
      // 再删除，避免后端因会话仍在生成而拒绝（409）。
      abortStreamFor(sessionIdToDelete);
      await chatService.deleteSession(sessionIdToDelete);
      // 清理被删除会话所属项目的 activeProjectId（先于 sessionId 清理，避免中间状态导致 currentProjectId 指向已删除项目）
      const deletedSessionProject = sessions.find((s) => s.id === sessionIdToDelete)?.project_id;
      if (deletedSessionProject && activeProjectId === deletedSessionProject) {
        setActiveProjectId(undefined);
        pendingProjectIdRef.current = undefined;
      }
      if (sessionIdRef.current === sessionIdToDelete) {
        setSessionId(undefined);
        sessionIdRef.current = undefined;
        setContextUsage(null); // 删除当前会话后清掉残留预算（B10）
        setInput('');
        setAttachments([]);
        setDraftMode(true);
      }
      // 清理被删除会话在所有上下文中的消息
      setMessages((current) => current.filter((m) => m.sessionId && m.sessionId !== sessionIdToDelete));
      requestSeqRef.current += 1;
      setPendingRequests([]);
      await Promise.all([refreshSessions(), refreshProjects()]);
    } catch (error) {
      console.error('Failed to delete session:', error);
      window.alert(error instanceof Error ? error.message : 'Failed to delete session');
    }
  };

  const renameCurrentSession = async () => {
    if (!sessionIdRef.current) return;
    const currentTitle = currentSessionTitle(messages, sessions, sessionIdRef.current);
    const title = window.prompt(t('titlebar.rename_session'), currentTitle);
    if (!title || title.trim() === currentTitle) return;
    try {
      await chatService.renameSession(sessionIdRef.current, title.trim());
      await refreshSessions();
    } catch (error) {
      console.error('Failed to rename session:', error);
    }
  };

  const deleteCurrentSession = async () => {
    if (!sessionIdRef.current) return;
    const confirmed = window.confirm(t('titlebar.delete_session_confirm'));
    if (!confirmed) return;
    await deleteSession(sessionIdRef.current);
  };

  const createProject = () => setCreateProjectDialogOpen(true);

  const renameProject = async (project: ProjectEntry) => {
    // 系统聊天项目不可重命名（后端同样拒绝）。
    if (project.is_chat) return;
    try {
      const name = window.prompt(t('sidebar.project_rename'), project.name);
      if (!name || name.trim() === project.name) return;
      await chatService.renameProject(project.id, name.trim());
      await refreshProjects();
    } catch (error) {
      console.error('Failed to rename project:', error);
    }
  };

  const deleteProject = async (projectId: string) => {
    // 系统聊天项目不可删除（后端同样拒绝）。
    if (projects.find((p) => p.id === projectId)?.is_chat) return;
    const confirmed = window.confirm(t('sidebar.project_delete_confirm'));
    if (!confirmed) return;
    try {
      const sessionsInProject = sessions.filter((session) => session.project_id === projectId);
      await chatService.deleteProject(projectId);
      const deletedSessionIds = new Set(sessionsInProject.map(s => s.id));
      // 如果当前 activeProject 就是该项目，无条件清空
      if (activeProjectId === projectId) {
        setActiveProjectId(undefined);
        pendingProjectIdRef.current = undefined;
      }
      // 如果当前正在编辑该项目内的会话，清空 sessionId 和相关状态
      if (sessionsInProject.some(s => s.id === sessionIdRef.current)) {
        setSessionId(undefined);
        sessionIdRef.current = undefined;
        setContextUsage(null); // 删除项目后清掉残留预算（B10）
        setInput('');
        setAttachments([]);
      }
      // 清理所有属于该项目的会话的消息
      setMessages((current) => current.filter((m) => !m.sessionId || !deletedSessionIds.has(m.sessionId)));
      await Promise.all([refreshSessions(), refreshProjects()]);
    } catch (error) {
      console.error('Failed to delete project:', error);
      window.alert(error instanceof Error ? error.message : 'Failed to delete project');
    }
  };

  /** /goal 目标命令（严格会话隔离：只作用于当前会话）。 */
  const handleGoalSlash = async (sid: string, rest: string, meta?: GoalSetMeta | undefined) => {
    if (!goalEnabled) {
      setMessages((current) => [
        ...current,
        createMessage('assistant', tOrDefault('goal.disabled', '目標能力已關閉，請在設置頁開啟後再使用。'), { status: 'done' }),
      ]);
      return;
    }
    const sub = rest.split(/\s+/)[0] ?? '';
    const usage = () =>
      setMessages((current) => [
        ...current,
        createMessage('assistant', tOrDefault('goal.usage', '/goal <目标> ｜ /goal pause ｜ /goal resume ｜ /goal clear ｜ /goal edit <新目标>'), { status: 'done' }),
      ]);
    const fail = (error: unknown) => {
      console.error('goal command failed:', error);
      setMessages((current) => [
        ...current,
        createMessage('assistant', tOrDefault('goal.command_failed', '目标操作失败，请重试。'), { status: 'error' }),
      ]);
    };
    try {
      if (sub === 'pause') {
        const resp = await chatService.pauseGoal(sid);
        setSessionGoal(sid, resp?.goal ?? null);
        return;
      }
      if (sub === 'resume') {
        const resp = await chatService.resumeGoal(sid);
        setSessionGoal(sid, resp?.goal ?? null);
        if (resp?.goal?.status === 'active') void kickGoalContinuation(sid);
        return;
      }
      if (sub === 'clear') {
        await chatService.clearGoal(sid);
        setSessionGoal(sid, null);
        return;
      }
      if (sub === 'edit') {
        const objective = rest.slice('edit'.length).trim();
        if (!objective) return usage();
        const resp = await chatService.editGoal(sid, objective);
        setSessionGoal(sid, resp?.goal ?? null);
        return;
      }
      const objective = rest.trim();
      if (!objective) return usage();
      const resp = await chatService.setGoal(sid, objective, undefined, meta ?? null);
      setSessionGoal(sid, resp?.goal ?? null);
      // 空闲启动：设定 active 后立即触发续跑流（设计文档 §3.3.2）。
      if (resp?.goal?.status === 'active') void kickGoalContinuation(sid);
    } catch (error) {
      fail(error);
    }
  };

  const handleSlashCommand = (message: string, goalMeta?: GoalSetMeta | undefined) => {
    const [command] = message.split(/\s+/);
    setInput('');
    if (command === '/clear') {
      // 只清当前会话的消息，其它会话仍在后台运行的流不受影响。
      setMessages((current) => current.filter((m) => m.sessionId && m.sessionId !== sessionIdRef.current));
      setAttachments([]);
      return;
    }
    if (command === '/new') {
      startNewChat(activeProjectId);
      return;
    }
    if (command === '/providers') {
      setActiveView('providers');
      return;
    }
    if (command === '/settings') {
      setActiveView('settings');
      return;
    }
    if (command === '/skills') {
      setActiveView('skills');
      return;
    }
    if (command === '/memory') {
      setActiveView('memory');
      return;
    }
    if (command === '/skill') {
      // Backward-compatible "/skill <name> [task]" -> "/<name> [task]".
      const rest = message.slice('/skill'.length).trim();
      if (rest) {
        void handleSkillSlash(`/${rest}`);
      } else {
        setMessages((current) => [...current, createMessage('assistant', t('skills.slash_usage'), { status: 'done' })]);
      }
      return;
    }
    if (command === '/goal') {
      const rest = message.slice('/goal'.length).trim();
      void (async () => {
        let sid = sessionIdRef.current;
        // 新会话（尚无 session）：自动创建一个，让 /goal 在空会话里也能直接设定目标。
        if (!sid) {
          try {
            const requestProjectId = pendingProjectIdRef.current;
            if (!requestProjectId) {
              setMessages((current) => [
                ...current,
                createMessage('assistant', tOrDefault('goal.need_workspace', '请先选择工作空间，再设置目标。'), { status: 'done' }),
              ]);
              return;
            }
            const sessionResp = await chatService.createSession({ project_id: requestProjectId, agent_id: draftAgentId });
            const newSession = sessionResp.session;
            if (newSession) {
              sessionIdRef.current = newSession.id;
              setSessionId(newSession.id);
              // 立即加入本地会话列表，保证侧栏即时可见
              setSessions((current) => [newSession, ...current]);
              sid = newSession.id;
              // 把创建会话前渲染的 goal user 泡泡绑定到新会话（避免无 sessionId
              // 而显示在所有会话头部）。
              if (goalMeta?.userMessageId && sid) {
                setMessages((current) =>
                  current.map((m) =>
                    m.id === goalMeta.userMessageId && !m.sessionId ? { ...m, sessionId: sid as string } : m,
                  ),
                );
              }
            }
          } catch (error) {
            console.error('Failed to create session for /goal:', error);
          }
        }
        if (!sid) {
          setMessages((current) => [
            ...current,
            createMessage('assistant', tOrDefault('goal.need_session', '请先开始一个会话（发送一条消息），再设置目标。'), { status: 'done' }),
          ]);
          return;
        }
        void handleGoalSlash(sid, rest, goalMeta);
      })();
      return;
    }
    // Bare "/<cmd>": a known skill sub-command first, then a full skill name.
    const bareCmd = command ? (command.startsWith('/') ? command.slice(1) : command) : '';
    if (bareCmd && skillSubCommandIndex[bareCmd]) {
      void handleSubCommandSlash(skillSubCommandIndex[bareCmd], bareCmd, message);
      return;
    }
    if (bareCmd && skillNameIndex.has(bareCmd)) {
      void handleSkillSlash(message);
      return;
    }
    setMessages((current) => [...current, createMessage('assistant', t('chat.command_help_text'), { status: 'done' })]);
  };

  /** /<skill-name> [free-form prompt] -- validate the skill, then send a hidden
   *  activation tag so the backend injects the SKILL.md body into the system
   *  prompt (the body itself never appears in the conversation). */
  const handleSkillSlash = async (message: string) => {
    const rest = message.replace(/^\//, '').trim();
    const skillName = rest.split(/\s+/)[0] ?? '';
    const prompt = rest.slice(skillName.length).trim();
    if (!skillName) {
      setMessages((current) => [...current, createMessage('assistant', t('skills.slash_usage'), { status: 'done' })]);
      return;
    }
    try {
      const response = await chatService.getSkill(skillName);
      const skill = response.skill;
      if (!skill?.enabled) {
        setMessages((current) => [...current, createMessage('assistant', t('skills.slash_not_found').replace('{name}', skillName), { status: 'done' })]);
        return;
      }
      const injected = `[skill:${skillName}]${prompt ? `\n\n${prompt}` : ''}`;
      setInput('');
      void sendMessage({ message: injected });
    } catch (error) {
      setMessages((current) => [...current, createMessage('assistant', t('skills.slash_not_found').replace('{name}', skillName), { status: 'done' })]);
    }
  };

  /** /<command> [free-form prompt] -- load a skill sub-command body and send it. */
  const handleSubCommandSlash = async (pkg: string, cmd: string, message: string) => {
    const prompt = message.slice(`/${cmd}`.length).trim();
    try {
      const response = await chatService.getSkill(pkg, cmd);
      const skill = response.skill;
      if (!skill?.enabled) {
        setMessages((current) => [
          ...current,
          createMessage('assistant', t('skills.slash_not_found').replace('{name}', `${pkg} / ${cmd}`), { status: 'done' }),
        ]);
        return;
      }
      const injected = `[skill:${pkg}:${cmd}]${prompt ? `\n\n${prompt}` : ''}`;
      setInput('');
      void sendMessage({ message: injected });
    } catch (error) {
      setMessages((current) => [
        ...current,
        createMessage('assistant', t('skills.slash_not_found').replace('{name}', `${pkg} / ${cmd}`), { status: 'done' }),
      ]);
    }
  };

  /** 从存储的用户消息反解出命令 chip：`[skill:name]` -> {command:'/name'}，
   *  `[skill:pkg:cmd]` -> {command:'/cmd', packageName:'pkg'}。 */
  const parseSkillMarker = (content: string): { chip: CommandChip; text: string } | null => {
    const match = /^\[skill:([A-Za-z0-9][A-Za-z0-9_.-]*)(?::([A-Za-z0-9][A-Za-z0-9_.-]*))?\](?:\n\n|\n)?/.exec(content);
    if (!match) return null;
    const [, pkg, cmd] = match;
    const text = content.slice(match[0].length);
    if (cmd) {
      return { chip: { command: `/${cmd}`, type: 'skill', packageName: pkg as string }, text };
    }
    return { chip: { command: `/${pkg as string}`, type: 'skill' }, text };
  };

  const encodeSkillMarker = (chip: CommandChip): string =>
    chip.packageName ? `[skill:${chip.packageName}:${chip.command.slice(1)}]` : `[skill:${chip.command.slice(1)}]`;

  /** ChatInput 提交/移除命令 chip。skill 型在此异步验证（无效立即拒绝并提示），
   *  即时 sys 命令直接执行（不留 chip），skill 型保留 chip 等提示词。 */
  // 正在异步验证的 chip（防较慢的旧验证把更新的提交覆盖掉）
  const pendingCommandRef = useRef<CommandChip | null>(null);

  const handleCommandCommit = useCallback(
    (chip: CommandChip | null) => {
      pendingCommandRef.current = chip;
      // 同步鏡像先寫，避免 sendMessage 閉包在 state 落地前讀到舊值。
      commandChipRef.current = chip;
      if (!chip) {
        setCommandChip(null);
        return;
      }
      if (chip.type === 'sys') {
        if (chip.command === '/goal') {
          // /goal 需要 objective 参数：sys 命令不立即执行，落 chip 停留（胶囊仍标
          // 记 sys），用户在 chip 后输入目标文本，按发送时组合成 `/goal <objective>`。
          setCommandChip(chip);
          return;
        }
        setCommandChip(null);
        commandChipRef.current = null;
        handleSlashCommand(chip.command);
        return;
      }
      if (chip.type === 'skill') {
        const name = chip.packageName ? `${chip.packageName} / ${chip.command.slice(1)}` : chip.command.slice(1);
        void (async () => {
          try {
            const response = await chatService.getSkill(
              chip.packageName ?? chip.command.slice(1),
              chip.packageName ? chip.command.slice(1) : undefined,
            );
            if (pendingCommandRef.current !== chip) return;
            if (!response.skill?.enabled) {
              setMessages((current) => [
                ...current,
                createMessage('assistant', t('skills.slash_not_found').replace('{name}', name), { status: 'done' }),
              ]);
              setCommandChip(null);
              commandChipRef.current = null;
              return;
            }
            setCommandChip(chip);
          } catch (error) {
            if (pendingCommandRef.current !== chip) return;
            setMessages((current) => [
              ...current,
              createMessage('assistant', t('skills.slash_not_found').replace('{name}', name), { status: 'done' }),
            ]);
            setCommandChip(null);
            commandChipRef.current = null;
          }
        })();
        return;
      }
      setCommandChip(chip);
    },
    [handleSlashCommand],
  );

  const toggleTodo = (index: number) => {
    const todo = todos[index];
    if (!todo) return;
    const newStatus = todo.status === 'completed' ? 'pending' : 'completed';
    const updated = (list: Todo[]) =>
      list.map((t, i) => (i === index ? { ...t, status: newStatus as 'completed' | 'pending' } : t));
    // 同步 per-session 存储，切走再切回后勾选状态保留。
    setTodosBySession((current) => {
      const key = todosSessionKey(sessionId);
      const entry = current[key];
      if (!entry) return current;
      return { ...current, [key]: updated(entry) };
    });
  };

  const changeSelectedModel = async (providerId: string) => {
    const provider = providers.find((item) => item.id === providerId);
    if (!provider) return;
    const previous = selectedModel;
    setSelectedModel(provider.id);
    // 切换模型/服务商后，用新模型的「当前窗口」刷新预算显示，避免残留上一个模型的
    // 窗口（例如 DeepSeek 1000k）；若当前没有打开的会话则清空。
    const nextModel = provider.model ?? '';
    if (sessionIdRef.current) {
      void chatService
        .getContextUsage(sessionIdRef.current, provider.id, nextModel)
        .then((resp) => {
          if (sessionIdRef.current && resp?.context_usage) setContextUsage(mapContextUsage(resp.context_usage));
        })
        .catch(() => setContextUsage(null));
    } else {
      setContextUsage(null);
    }
    try {
      const config = await chatService.updateRuntimeConfig({
        selected_provider_id: provider.id,
        selected_model: provider.model,
      });
      setRuntimeConfig(config);
      await refreshProviders();
    } catch (error) {
      console.error('Failed to update runtime config:', error);
      setSelectedModel(previous);
    }
  };

  const modelOptions = providers.map((provider) => ({
    id: provider.id,
    label: provider.model,
    provider: provider.name,
    ...(provider.context_error ? { contextError: provider.context_error } : {}),
  }));
  const showRuntimeNotice = activeView === 'chat' && (runtimeStatus !== 'ready' || !runtimeConfig);
  const titlebarSessionTitle = currentSessionTitle(messages, sessions, sessionId);
  const currentProvider = providers.find((provider) => provider.id === selectedModel);
  const sessionProjectId = sessions.find((session) => session.id === sessionId)?.project_id;
  const currentProjectId = activeProjectId || sessionProjectId;
  const activeProject = projects.find((project) => project.id === currentProjectId);
  const titlebarProjectName = activeProject ? displayProjectName(activeProject) : t('sidebar.default_project');
  const activeProjectSessions = activeProject ? sessions.filter((session) => session.project_id === activeProject.id) : [];
  const showNewChatHero = activeView === 'chat' && !sessionId  && runtimeStatus === 'ready' && (!activeProject || draftMode);
  const showFirstRunStart = activeView === 'chat' && runtimeStatus === 'ready' && projects.filter((p) => !p.is_chat).length === 0 && sessions.length === 0 && !sessionId && !draftMode;
  const showProjectSessionList = activeView === 'chat' && activeProject && !sessionId && runtimeStatus === 'ready' && !draftMode;
  const workspaceOptions = projects.map((project) => ({
    id: project.id,
    name: displayProjectName(project),
    path: project.workspace_path,
  }));
  const agentOptions = activeProject?.mode === 'single' ? [{
    id: 'default_agent',
    name: 'default_agent',
    role: '',
    team: '',
    status: 'active',
  } satisfies OrgRosterEntry] : (activeProject?.roster ?? []);
  const currentProjectMode = activeProject?.mode ?? 'single';
  const orgProjectName = orgProjectId ? projects.find((p) => p.id === orgProjectId)?.name : undefined;
  const dashboardProjectName = dashboardProjectId ? projects.find((p) => p.id === dashboardProjectId)?.name : undefined;
  const currentSessionPending = sessionId
    ? pendingRequests.filter((item) => item.session_id === sessionId)
    : [];

  // ── Right-side panel (multi-tab) ──────────────────────────────────────
  const openRightPanel = () => setRightSidebarOpen(true);

  const addRightTab = (kind: RightPanelTabKind, data?: RightPanelTab['data']) => {
    openRightPanel();
    const id = `${kind}-${Date.now()}`;
    setRightTabs((prev) => {
      const tab: RightPanelTab = data ? { id, kind, data } : { id, kind };
      return [...prev, tab];
    });
    setActiveRightTabId(id);
  };

  const closeRightTab = (id: string) => {
    browserHandlesRef.current.delete(id);
    setRightTabs((prev) => {
      const next = prev.filter((tab) => tab.id !== id);
      if (next.length === 0) setRightSidebarOpen(false);
      return next;
    });
    setActiveRightTabId((current) => {
      if (current === id) {
        const remaining = rightTabs.filter((tab) => tab.id !== id);
        const last = remaining[remaining.length - 1];
        return last ? last.id : '';
      }
      return current;
    });
  };

  const navigateBrowserTab = (tabId: string, url: string) => {
    let attempts = 0;
    const tryNav = () => {
      const handle = browserHandlesRef.current.get(tabId);
      if (handle) {
        handle.navigate(url);
        return;
      }
      if (attempts++ < 20) setTimeout(tryNav, 50);
    };
    tryNav();
  };

  // Ensure a browser tab exists (kept alive even while the right panel is
  // collapsed). When a URL is given, navigate that tab to it. Does NOT auto-open
  // the panel — the titlebar indicator lights instead; the user opens the panel
  // and sees the already-running browser.
  const ensureBrowserTab = (url?: string) => {
    setRightTabs((prev) => {
      const existing = prev.find((tab) => tab.kind === 'browser');
      if (existing) {
        setActiveRightTabId(existing.id);
        if (url) navigateBrowserTab(existing.id, url);
        return prev;
      }
      const id = `browser-${Date.now()}`;
      setActiveRightTabId(id);
      const next: RightPanelTab[] = url
        ? [...prev, { id, kind: 'browser', data: { url } }]
        : [...prev, { id, kind: 'browser' }];
      if (url) navigateBrowserTab(id, url);
      return next;
    });
  };

  const handleBrowserHandle = (tabId: string, handle: BrowserViewHandle | null) => {
    if (handle) {
      browserHandlesRef.current.set(tabId, handle);
    } else {
      browserHandlesRef.current.delete(tabId);
    }
  };

  const handleBrowserTitle = (tabId: string, title: string) => {
    if (!title) return;
    setRightTabs((prev) =>
      prev.map((tab) => (tab.id === tabId ? { ...tab, data: { ...(tab.data || {}), title } } : tab)),
    );
  };

  // Drive the blue "agent is controlling the browser" overlay from the SSE
  // stream: any browser tool_start lights it up (and records click coords for a
  // brief target ring); the matching tool_end switches it off.
  const trackBrowserToolEvent = (event: StreamEvent) => {
    if (event.type === 'tool_start' && event.name === 'browser') {
      setBrowserAgentActive(true);
      try {
        const args = JSON.parse(event.input || '{}') as { action?: string; x?: number; y?: number };
        if (args?.action === 'click' && typeof args.x === 'number' && typeof args.y === 'number') {
          setBrowserAgentClick({ x: args.x, y: args.y, key: Date.now() });
        }
      } catch {
        // ignore malformed input
      }
    } else if (event.type === 'tool_end' && event.name === 'browser') {
      setBrowserAgentActive(false);
    }
  };

  const stopBrowserAgent = () => setBrowserAgentActive(false);

  // ── Right-panel width strategy ────────────────────────────────────────
  // The chat (main workspace) keeps a width floor; the right panel may grow
  // arbitrarily wide as long as it never crushes the chat below that floor.
  const CHAT_MIN_WIDTH = 420;
  // Measure the real workspace-frame width (tracks sidebar resize/collapse,
  // window resize, and the mobile drawer automatically).
  useEffect(() => {
    const node = workspaceFrameRef.current;
    if (!node) return;
    const update = () => setWorkspaceFrameWidth(node.getBoundingClientRect().width);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const rightPanelMax = Math.max(
    240,
    workspaceFrameWidth - CHAT_MIN_WIDTH - (changesPanelOpen ? changesPanelWidth : 0),
  );

  // Clamp the panel so the chat floor is always respected when the available
  // space shrinks (narrower window, opening the changes panel, etc.).
  useEffect(() => {
    if (!rightSidebarOpen) return;
    setInspectorWidth((current) => Math.min(Math.max(240, current), Math.max(240, rightPanelMax)));
  }, [rightPanelMax, rightSidebarOpen]);

  // Browser tabs open wider by default so the first view is comfortable.
  // Keyed on the active tab id only — a manual drag on an already-active
  // browser tab must never be overridden.
  const inspectorWidthRef = useRef(inspectorWidth);
  inspectorWidthRef.current = inspectorWidth;
  useEffect(() => {
    const activeTab = rightTabs.find((tab) => tab.id === activeRightTabId);
    if (activeTab?.kind === 'browser' && inspectorWidthRef.current < 560) {
      setInspectorWidth((current) => Math.min(Math.max(current, 560), Math.max(240, rightPanelMax)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRightTabId, rightTabs, rightPanelMax]);

  useEffect(() => {
    let cancelled = false;
    async function fetchBranch() {
      if (!currentProjectId) {
        setBranchStatus(null);
        return;
      }
      const projectExists = projects.some((p) => p.id === currentProjectId);
      if (!projectExists) {
        setBranchStatus(null);
        return;
      }
      try {
        const res = await chatService.getWorkspaceBranch(currentProjectId);
        if (!cancelled) setBranchStatus({ isRepo: res.is_repo, branch: res.branch });
      } catch {
        if (!cancelled) setBranchStatus({ isRepo: false, branch: null });
      }
    }
    void fetchBranch();
    const interval = setInterval(() => void fetchBranch(), 15000);
    const onFocus = () => void fetchBranch();
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, [currentProjectId, projects]);

  const changeThemeSettings = (nextSettings: ThemeSettings) => {
    setThemeSettingsState(nextSettings);
    setThemeSettings(nextSettings);
  };

  const changeMaxAttachmentMb = (value: number) => {
    const clamped = Math.max(
      MIN_MAX_ATTACHMENT_MB,
      Math.min(MAX_MAX_ATTACHMENT_MB, Number.isFinite(value) ? Math.round(value) : DEFAULT_MAX_ATTACHMENT_MB),
    );
    setMaxAttachmentMb(clamped);
    try {
      localStorage.setItem(MAX_ATTACHMENT_MB_STORAGE_KEY, String(clamped));
    } catch {
      /* localStorage unavailable — keep in-memory value */
    }
    chatService.saveSettings({ max_attachment_mb: clamped }).catch(() => { /* ignore */ });
  };

  const changeRevertCode = (value: boolean) => {
    setRevertCode(value);
    chatService.saveSettings({ revert_code: value }).catch(() => { /* ignore */ });
  };

  const changeGoalEnabled = (value: boolean) => {
    setGoalEnabled(value);
    chatService.saveSettings({ goal_enabled: value }).catch(() => { /* ignore */ });
  };

  const changeMemorySettings = (patch: MemorySettingsPatch) => {
    setMemorySettings((cur) => {
      const base: MemorySettings = cur ?? {
        enabled: true,
        auto_extract: true,
      };
      return { ...base, ...patch };
    });
    chatService.saveMemorySettings(patch).catch(() => { /* ignore */ });
  };

  const changeSkillReviewSettings = (patch: SkillReviewSettingsPatch) => {
    setSkillReviewSettings((current) => (current ? { ...current, ...patch } : current));
    chatService.saveSkillReviewSettings(patch).catch(() => { /* ignore */ });
  };

  // Auto-skills notification: refresh the pending-skill count (sidebar badge)
  // and, after a turn that produced NEW drafts, surface an inline "去審核" card.
  const refreshPendingSkillCount = useCallback(async (): Promise<number> => {
    try {
      const response = await chatService.listPendingSkills();
      const count = response.pending.length;
      setPendingSkillCount(count);
      return count;
    } catch {
      return 0;
    }
  }, []);

  // Real-time-ish badge: light periodic poll + refresh when the window regains
  // focus, so the sidebar "待審核 N" stays in sync even when a background skill
  // review stages a draft a few seconds after the turn ends.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshPendingSkillCount();
    }, 15000);
    const onFocus = () => {
      void refreshPendingSkillCount();
    };
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [refreshPendingSkillCount]);

  const maybeCheckSkillDrafts = useCallback(
    async (sessionId: string | undefined) => {
      const count = await refreshPendingSkillCount();
      if (count > skillCountRef.current) {
        setSkillDraftNote({ count: count - skillCountRef.current, sessionId: sessionId ?? '' });
      }
      skillCountRef.current = count;
    },
    [refreshPendingSkillCount],
  );

  const dismissSkillDraftNote = useCallback(() => setSkillDraftNote(null), []);

  const openSkillsForReview = useCallback(() => {
    setSkillDraftNote(null);
    setActiveView('skills');
    void refreshSkills();
  }, [refreshSkills]);

    return (
    <main
      className={`app-shell ${sidebarCollapsed ? 'app-shell--sidebar-collapsed' : ''} ${isNarrowViewport ? 'app-shell--narrow' : ''} ${mobileSidebarOpen ? 'app-shell--drawer-open' : ''} ${sidebarResizing || bottomPanelResizing || inspectorResizing || changesPanelResizing ? 'app-shell--resizing' : ''}`}
      style={{ '--sidebar-width': `${sidebarWidth}px`, '--bottom-panel-height': `${bottomPanelHeight}px`, '--inspector-width': `${inspectorWidth}px`, '--changes-width': `${changesPanelWidth}px` } as CSSProperties}
    >
      <WorkspaceTitlebar
        status={runtimeStatus}
        activeView={activeView}
        sessionTitle={titlebarSessionTitle}
        projectName={titlebarProjectName}
        sidebarCollapsed={isNarrowViewport ? !mobileSidebarOpen : sidebarCollapsed}
        rightSidebarOpen={rightSidebarOpen}
        browserActive={browserAgentActive}
        bottomPanelOpen={bottomPanelOpen}
        changesPanelOpen={changesPanelOpen}
        canEditSession={Boolean(sessionId)}
        pendingCount={currentSessionPending.length}
        contextUsage={contextUsage}
        onToggleSidebar={() => {
          if (isNarrowViewport) {
            setMobileSidebarOpen((value) => !value);
          } else {
            setSidebarCollapsed((value) => !value);
          }
        }}
        onToggleRightSidebar={() => setRightSidebarOpen((value) => !value)}
        onToggleBottomPanel={() => setBottomPanelOpen((value) => !value)}
        onToggleChangesPanel={() => setChangesPanelOpen((value) => !value)}
        onRenameSession={renameCurrentSession}
        onDeleteSession={deleteCurrentSession}
      />
      <div className="app-body">
        {isNarrowViewport ? (
          <div
            className={`sidebar-scrim ${mobileSidebarOpen ? 'sidebar-scrim--visible' : ''}`}
            role="presentation"
            aria-hidden={!mobileSidebarOpen}
            onClick={() => setMobileSidebarOpen(false)}
          />
        ) : null}
        <WorkspaceSidebar
          sessions={sessions}
          projects={projects}
          activeView={activeView}
          collapsed={isNarrowViewport ? false : sidebarCollapsed}
          {...(currentProjectId ? { activeProjectId: currentProjectId } : {})}
          onResizeStart={() => setSidebarResizing(true)}
          onResizeEnd={() => setSidebarResizing(false)}
          onResizeWidth={(width) => {
            setSidebarWidth(width);
          }}
          {...(sessionId ? { activeSessionId: sessionId } : {})}
          onViewChange={setActiveView}
          onNewChat={startNewChat}
          onOpenProject={openProject}
          onOpenDashboard={openDashboard}
          onSelectProject={selectProject}
          onOpenSession={openSession}
          onDeleteSession={deleteSession}
          onCreateProject={createProject}
          onRenameProject={renameProject}
          onDeleteProject={deleteProject}
          onOpenOrgSettings={openOrgSettings}
          sessionBadges={sessionBadges}
          pendingSkillCount={pendingSkillCount}
        />
        <section ref={workspaceFrameRef} className={`workspace-frame ${rightSidebarOpen ? 'workspace-frame--right-open' : ''} ${bottomPanelOpen ? 'workspace-frame--bottom-open' : ''}`}>
          <div className={`workspace-upper ${changesPanelOpen ? 'workspace-upper--changes-open' : ''}`}>
            <section className={`workspace-shell workspace-shell--${activeView}`}>
              {activeView === 'chat' ? (
                <>
                  {showRuntimeNotice && (
                    <section className={`runtime-notice runtime-notice--${runtimeStatus}`}>
                      <p className="runtime-notice__eyebrow">
                        {runtimeStatus === 'connecting' ? t('runtime.connecting_label') : t('runtime.error_label')}
                      </p>
                      <h2>{runtimeStatus === 'connecting' ? t('runtime.connecting_title') : t('runtime.error_title')}</h2>
                      {runtimeStatus === 'connecting' ? (
                        <p>{t('runtime.connecting_body')}</p>
                      ) : (
                        <>
                          <p>{t('runtime.error_body')}</p>
                          {runtimeError && <p className="runtime-notice__reason">{t('runtime.error_reason', { reason: runtimeError })}</p>}
                          <p className="runtime-notice__retry">{t('runtime.retrying')}</p>
                        </>
                      )}
                    </section>
                  )}
                  {showFirstRunStart ? (
                    <FirstRunStart onCreateProject={createProject} onNewSession={() => startNewChat()} />
                  ) : showNewChatHero ? (
                    <NewChatHero {...(activeProject ? { workspaceName: displayProjectName(activeProject) } : {})} />
                  ) : showProjectSessionList ? (
                    <ProjectSessionList
                      project={activeProject}
                      sessions={activeProjectSessions}
                      sessionBadges={sessionBadges}
                      onNewChat={startNewChat}
                      onOpenSession={openSession}
                      onDeleteSession={deleteSession}
                      onOpenOrgSettings={openOrgSettings}
                    />
                  ) : (
                    <>
                      <MessageList
                        messages={displayedMessages}
                        isThinking={isThinking}
                        onEditMessage={(messageId, content) => beginEditMessage(messageId, content)}
                        onRegenerateMessage={(messageId) => void handleRegenerateMessage(messageId)}
                        onRedoMessage={(messageId) => void handleRedoMessage(messageId)}
                        onSubscribeWorker={(messageId, part) => subscribeWorkerTranscript(messageId, part)}
                      />
                      {streamIdleWarning && (
                        <div className="stream-idle-warning">
                          <span className="stream-idle-warning__icon">⏱️</span>
                          <span className="stream-idle-warning__text">{streamIdleWarning}</span>
                        </div>
                      )}
                    </>
                  )}
                  {!showFirstRunStart && !showProjectSessionList && (
                    <div className="workspace-composer-slot">
                      {skillDraftNote && (
                        <div className="skill-draft-note">
                          <Sparkles size={15} className="skill-draft-note__icon" />
                          <span className="skill-draft-note__text">
                            {t('skills.draft_note', { count: skillDraftNote.count })}
                          </span>
                          <button type="button" className="skill-draft-note__action" onClick={openSkillsForReview}>
                            {t('skills.pending_review')}
                          </button>
                          <button
                            type="button"
                            className="skill-draft-note__close"
                            aria-label={t('common.close')}
                            title={t('common.close')}
                            onClick={dismissSkillDraftNote}
                          >
                            <X size={13} />
                          </button>
                        </div>
                      )}
                      {/* Task list (write_todos) — the agent's self-decomposed
                          checklist, shown in every mode above the composer.
                          Queued messages are listed in the same card so the user
                          sees each pending message and can remove it. */}
                      {(showTodoCard || queuedMessagesFor(sessionId).length > 0) && (
                        <TodoBlock
                          todos={todos}
                          onToggleTodo={toggleTodo}
                          queuedMessages={queuedMessagesFor(sessionId)}
                          onRemoveQueued={(id) => {
                            if (sessionId) removeQueuedMessage(sessionId, id);
                          }}
                          onEditQueued={(id, message) => {
                            if (sessionId) updateQueuedMessage(sessionId, id, message);
                          }}
                          onReorderQueued={(orderedIds) => {
                            if (sessionId) reorderQueuedMessage(sessionId, orderedIds);
                          }}
                          onInterjectQueued={(id) => {
                            if (sessionId) void interjectQueuedMessage(sessionId, id);
                          }}
                          streamActive={isThinking}
                          onClose={dismissCurrentTodos}
                        />
                      )}
                      {/* 独立 GoalCard：当前会话目标状态卡（对原版前端 UI） */}
                      {currentGoal != null && (
                        <GoalCard
                          goal={currentGoal}
                          onPause={() => {
                            if (sessionId) void chatService.pauseGoal(sessionId).then((resp) => setSessionGoal(sessionId, resp?.goal ?? null)).catch(() => undefined);
                          }}
                          onResume={() => {
                            if (sessionId) void chatService.resumeGoal(sessionId).then((resp) => {
                              setSessionGoal(sessionId, resp?.goal ?? null);
                              if (resp?.goal?.status === 'active') void kickGoalContinuation(sessionId);
                            }).catch(() => undefined);
                          }}
                          onDelete={() => {
                            if (sessionId) void chatService.clearGoal(sessionId).then(() => setSessionGoal(sessionId, null)).catch(() => undefined);
                          }}
                        />
                      )}
                      {webSetupHint && (
                        <WebSetupHintBar
                          status={webSetupHint}
                          onConfigure={() => openSettingsPage('web')}
                          onDismiss={() => setWebHintDismissed(true)}
                        />
                      )}
                      {currentSessionPending.length > 0 ? (
                        <div className="workspace-dock-area">
                          <PendingDocks
                            requests={currentSessionPending}
                            onResolve={async (request, decision) => {
                              await resolvePendingRequest(request, decision);
                            }}
                            onDismiss={(request) => {
                              void resolvePendingRequest(request, { type: 'reject' });
                            }}
                            onStop={() => stopMessage({ silent: true })}
                          />
                        </div>
                      ) : (
                        <ChatInput
                        value={editingMessage ? editDraft : input}
                        disabled={runtimeStatus === 'connecting'}
                        isThinking={isThinking}
                        onSendQueued={handleSendQueued}
                        workMode={workMode}
                        autonomy={autonomy}
                        selectedModel={selectedModel}
                        attachments={attachments}
                        maxAttachmentMb={maxAttachmentMb}
                        goalEnabled={goalEnabled}
                        references={references}
                        modelOptions={modelOptions}
                        editing={Boolean(editingMessage)}
                        commandChip={commandChip}
                        onCommandCommit={handleCommandCommit}
                        onChange={editingMessage ? setEditDraft : setInput}
                        onSend={editingMessage ? () => void commitEditMessage(editingMessage.id, editDraft) : sendMessage}
                        onStop={stopMessage}
                        onWorkModeChange={setWorkMode}
                        onAutonomyChange={setAutonomy}
                        onModelChange={(providerId) => void changeSelectedModel(providerId)}
                        onAttachmentsChange={setAttachments}
                        onReferencesChange={setReferences}
                        onResolveSession={resolveSessionReference}
                        apiRef={composerApiRef}
                        onCancelEdit={() => {
                          setEditingMessage(null);
                          setEditDraft('');
                          setCommandChip(null);
                          void restorePendingEdit();
                        }}
                        branchStatus={branchStatus}
                        showWorkspacePicker={showNewChatHero}
                        workspaceOptions={workspaceOptions}
                        {...(currentProjectId ? { activeWorkspaceId: currentProjectId } : {})}
                        onSelectWorkspace={selectDraftWorkspace}
                        onCreateWorkspace={createProject}
                        agentOptions={agentOptions}
                        {...(draftAgentId ? { activeAgentId: draftAgentId } : {})}
                        onSelectAgent={setDraftAgentId}
                        skills={skillEntries}
                        onOpenCommands={refreshSkills}
                      />
                      )}
                    </div>
                  )}
                </>
              ) : activeView === 'providers' ? (
                <ProvidersPanel onProviderChange={refreshProviders} />
              ) : activeView === 'mcp' ? (
                <MCPPanel servers={mcpServers} templates={mcpTemplates} setServers={setMcpServers} onMcpChange={refreshProviders} />
              ) : activeView === 'skills' ? (
                <SkillsPanel
                  skills={skillEntries}
                  diagnostics={skillDiagnostics}
                  setSkills={setSkillEntries}
                  setDiagnostics={setSkillDiagnostics}
                  onSkillsChange={refreshSkills}
                  onPendingCountChange={() => void refreshPendingSkillCount()}
                />
              ) : activeView === 'memory' ? (
                <MemoryPanel projectId={currentProjectId} />
              ) : activeView === 'org' && orgProjectId ? (
                <OrgSettingsPage
                  projectId={orgProjectId}
                  {...(orgProjectName ? { projectName: orgProjectName } : {})}
                  onBack={() => setActiveView('chat')}
                  onChanged={() => void refreshProjects()}
                />
              ) : activeView === 'dashboard' && dashboardProjectId ? (
                <ProjectDashboard
                  projectId={dashboardProjectId}
                  {...(dashboardProjectName ? { projectName: dashboardProjectName } : {})}
                  sessions={sessions}
                  sessionBadges={sessionBadges}
                  onBack={() => setActiveView('chat')}
                  onNewChat={startNewChat}
                  onOpenSession={openSession}
                  onDeleteSession={deleteSession}
                  onOpenOrgSettings={openOrgSettings}
                />
              ) : (
                <SettingsView
                  themeSettings={themeSettings}
                  autonomy={autonomy}
                  maxAttachmentMb={maxAttachmentMb}
                  onMaxAttachmentMbChange={changeMaxAttachmentMb}
                  revertCode={revertCode}
                  onRevertCodeChange={changeRevertCode}
                  goalEnabled={goalEnabled}
                  onGoalEnabledChange={changeGoalEnabled}
                  onThemeSettingsChange={changeThemeSettings}
                  onAutonomyChange={setAutonomy}
                  memorySettings={memorySettings}
                  onMemorySettingsChange={changeMemorySettings}
                  skillReviewSettings={skillReviewSettings}
                  onSkillReviewSettingsChange={changeSkillReviewSettings}
                  modelOptions={modelOptions}
                  updateCenter={updateCenter}
                  onClose={() => {
                    setSettingsPage('main');
                    setActiveView('chat');
                  }}
                  settingsPage={settingsPage}
                  onSettingsPageChange={setSettingsPage}
                  onOpenMemory={() => {
                    setSettingsPage('main');
                    setActiveView('memory');
                  }}
                />
              )}
            </section>
            <RightPanel
              hidden={!rightSidebarOpen}
              tabs={rightTabs}
              activeTabId={activeRightTabId}
              onSelect={(id) => setActiveRightTabId(id)}
              onClose={closeRightTab}
              onAdd={() => addRightTab('browser')}
              onBrowserHandle={handleBrowserHandle}
              onBrowserTitle={handleBrowserTitle}
              onOpenNewTab={(url) => {
                if (url) addRightTab('browser', { url });
              }}
              onAddCapture={(attachments) => {
                if (attachments.length) setAttachments((prev) => [...prev, ...attachments]);
              }}
              agentActive={browserAgentActive}
              agentClick={browserAgentClick}
              onResizeStart={() => setInspectorResizing(true)}
              onResizeEnd={() => setInspectorResizing(false)}
              onResizeWidth={(width) => setInspectorWidth(width)}
              maxWidth={rightPanelMax}
            />
            {changesPanelOpen && (
              <ChangesPanel
                open={changesPanelOpen}
                onClose={() => setChangesPanelOpen(false)}
                onRefreshKey={changesRefreshKey}
                onResizeStart={() => setChangesPanelResizing(true)}
                onResizeEnd={() => setChangesPanelResizing(false)}
                onResizeWidth={(width) => setChangesPanelWidth(width)}
                {...(sessionId ? { sessionId } : {})}
                {...(currentProjectId ? { projectId: currentProjectId } : {})}
              />
            )}
          </div>
          {bottomPanelOpen && (
            <WorkspaceBottomPanel
              view={bottomPanelView}
              runtimeStatus={runtimeStatus}
              runtimeConfig={runtimeConfig}
              sessionCount={sessions.length}
              projectCount={projects.length}
              {...(currentProjectId ? { projectId: currentProjectId } : {})}
              onViewChange={setBottomPanelView}
              onResizeStart={() => setBottomPanelResizing(true)}
              onResizeEnd={() => setBottomPanelResizing(false)}
              onResizeHeight={(height) => setBottomPanelHeight(height)}
            />
          )}
        </section>
      </div>
      <CreateProjectDialog
        open={createProjectDialogOpen}
        onClose={() => setCreateProjectDialogOpen(false)}
        onPickWorkspace={pickWorkspaceDirectory}
        onCreate={createProjectWithWorkspace}
        projects={projects}
      />
      <UpdateToastCard center={updateCenter} onOpenSettings={() => setActiveView('settings')} />
    </main>
  );
}

function currentSessionTitle(messages: ChatMessage[], sessions: SessionSummary[], sessionId?: string): string {
  if (!sessionId) return t('sidebar.new_chat');
  const saved = sessions.find((session) => session.id === sessionId)?.title;
  if (saved) return saved;
  // R1 keeps every session's messages in one array; fall back to the
  // current session's own first user message only.
  if (sessionId) {
    const firstUserMessage = messages.find((message) => message.role === 'user' && message.sessionId === sessionId);
    if (!firstUserMessage?.content.trim()) return t('sidebar.new_chat');
    return firstUserMessage.content.trim().slice(0, 64);
  }
  return t('sidebar.new_chat');
}

export default App;
