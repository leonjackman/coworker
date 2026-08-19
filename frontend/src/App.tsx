import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ChatInput, extractSessionIds, type CommandChip } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { GoalCard } from './components/GoalCard';
import { TodoBlock } from './components/TodoBlock';
import { ProvidersPanel } from './components/ProvidersPanel';
import { MCPPanel } from './components/MCPPanel';
import { SkillsPanel } from './components/SkillsPanel';
import { MemoryPanel } from './components/MemoryPanel';
import { CreateProjectDialog } from './components/CreateProjectDialog';
import { ProjectSessionList } from './components/ProjectSessionList';
import { FirstRunStart } from './components/FirstRunStart';
import { NewChatHero } from './components/NewChatHero';
import { SettingsView } from './components/settings/SettingsView';
import { OrgSettingsPage } from './components/settings/OrgSettingsPage';
import { WorkspaceTitlebar } from './components/WorkspaceTitlebar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { WorkspaceBottomPanel, type BottomPanelView } from './components/WorkspaceBottomPanel';
import { WorkspaceInspector } from './components/WorkspaceInspector';
import { ChangesPanel } from './components/ChangesPanel';
import { UpdateToastCard } from './components/UpdateToastCard';
import { getLanguage, initLanguage, t, translateError, useLanguage } from './lib/i18n';
import { useUpdateCenter } from './lib/useUpdateCenter';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { chatService } from './services/chatService';
import type { AppView, ApprovalDecisionPayload, ApprovalOption, Autonomy, ChatMessage, ComposerAttachment, ContextUsage, CreateProjectRequest, GoalState, GoalTodo, McpServerEntry, McpTemplateEntry, MemorySettings, MemorySettingsPatch, MessagePart, OrgRosterEntry, PartDelegate, PendingRequest, ProjectEntry, ProviderEntry, RuntimeConfig, SessionDetailResponse, SessionReference, SessionSummary, SkillDiagnostic, SkillEntry, StreamEvent, WorkMode } from './types';
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
    } else if (part.type === 'text') {
      // 各次 resume 会重放同一轮执行（工具按 id 去重），文本同样按内容去重，
      // 避免重放时 text part 重复叠加；goal 多轮文本内容各不相同，天然追加。
      const exists = merged.some((p) => p.type === 'text' && p.content === part.content);
      if (!exists && part.content) {
        merged.push(part);
      }
    } else {
      merged.push(part);
    }
  }
  return merged;
}

function settleRunningTools(parts: MessagePart[]): MessagePart[] {
  // A tool that is still 'running' when the turn reaches a terminal state was
  // interrupted (awaiting approval) or aborted — never finish with a live
  // spinner. Demote it to 'pending' (static, non-spinning).
  return parts.map((part) =>
    part.type === 'tool' && part.status === 'running' ? { ...part, status: 'pending' as const } : part,
  );
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

type ApprovalStreamEvent = Extract<
  StreamEvent,
  { type: 'approval_required' } | { type: 'question_required' }
>;

/**
 * Single mapping from an interrupt stream event to a `PendingRequest`.
 *
 * All four call paths (stream, non-stream, goal loop and resume) funnel through
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
  return { ...base, command: event.command, cwd: event.cwd };
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

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [goal, setGoal] = useState<GoalState>({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '' });
  // Global task-list (write_todos) shown by the TodoBlock card above the
  // composer, in every mode — the agent's self-decomposed checklist.
  const [todos, setTodos] = useState<GoalTodo[]>([]);
  const [editingGoalDraft, setEditingGoalDraft] = useState(false);
  const goalDraftRef = useRef('');
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
  // useLanguage() 订阅语言变化以触发重渲染（返回值不直接使用）。
  useLanguage();
  const updateCenter = useUpdateCenter();
  const [themeSettings, setThemeSettingsState] = useState<ThemeSettings>(() => getThemeSettings());
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(276);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [isNarrowViewport, setIsNarrowViewport] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 860px)').matches,
  );
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false);
  const [bottomPanelView, setBottomPanelView] = useState<BottomPanelView>('terminal');
  const [bottomPanelHeight, setBottomPanelHeight] = useState(190);
  const [bottomPanelResizing, setBottomPanelResizing] = useState(false);
  const [changesPanelOpen, setChangesPanelOpen] = useState(false);
  const [changesRefreshKey, setChangesRefreshKey] = useState(0);
  const [inspectorWidth, setInspectorWidth] = useState(300);
  const [inspectorResizing, setInspectorResizing] = useState(false);
  const [changesPanelWidth, setChangesPanelWidth] = useState(380);
  const [changesPanelResizing, setChangesPanelResizing] = useState(false);
  const [autonomy, setAutonomy] = useState<Autonomy>(() => {
    const stored = localStorage.getItem('cw.autonomy') as Autonomy | null;
    return stored === 'supervised' || stored === 'guarded' || stored === 'autonomous' ? stored : 'guarded';
  });
  const [goalMaxRounds, setGoalMaxRounds] = useState<number>(50);
  const [memorySettings, setMemorySettings] = useState<MemorySettings | null>(null);
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
  const [references, setReferences] = useState<SessionReference[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerEntry[]>([]);
  const [mcpTemplates, setMcpTemplates] = useState<McpTemplateEntry[]>([]);
  const [skillEntries, setSkillEntries] = useState<SkillEntry[]>([]);
  const [skillDiagnostics, setSkillDiagnostics] = useState<SkillDiagnostic[]>([]);

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
    if (!sessionId) return messages.filter((m) => !m.sessionId);
    return messages.filter((m) => !m.sessionId || m.sessionId === sessionId);
  }, [messages, sessionId]);

  const [pendingRequests, setPendingRequests] = useState<PendingRequest[]>([]);
  const [branchStatus, setBranchStatus] = useState<{ isRepo: boolean; branch: string | null } | null>(null);
  const requestSeqRef = useRef(0);
  const goalSessionIdRef = useRef<string | undefined>(undefined);
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
  const abortStreamFor = (sessionId?: string | null) => {
    const key = streamKey(sessionId);
    streamControllersRef.current[key]?.abort();
    delete streamControllersRef.current[key];
    delete activeAssistantMessageIdsRef.current[key];
    delete streamStartAtsRef.current[key];
  };
  const sessionIdRef = useRef<string | undefined>(undefined);
  const pendingProjectIdRef = useRef<string | undefined>(undefined);
  const goalStreamIdRef = useRef<string | undefined>(undefined);

  // Whether the CURRENT session is busy. Derived (not a hand-maintained flag)
  // so that a stream left running in another session (we keep it alive across
  // session switches instead of aborting it) never locks the composer here,
  // and a background stream completing never spuriously unlocks this session.
  const isThinking = useMemo(
    () =>
      (goal.running && goalSessionIdRef.current === sessionId) ||
      messages.some(
        (m) => (m.status === 'running' || m.status === 'waiting') && (!m.sessionId || m.sessionId === sessionId),
      ),
    [goal.running, goalSessionIdRef.current, messages, sessionId],
  );

  // 后端轮询到的活跃会话 id 集合（Phase 2 兜底：前端刷新/重启后不知道哪些
  // 会话仍在后台运行）。只作为 runningSessionIds 的补充来源。
  const [backendActiveSessionIds, setBackendActiveSessionIds] = useState<Set<string>>(new Set());

  // 侧栏会话"运行中"指示器：来自前台消息流状态（实时）、当前 goal 会话
  // （goal.running）与后端轮询结果（兜底）的并集。
  const runningSessionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of messages) {
      if ((m.status === 'running' || m.status === 'waiting') && m.sessionId) ids.add(m.sessionId);
    }
    if (goal.running && goalSessionIdRef.current) ids.add(goalSessionIdRef.current);
    for (const id of backendActiveSessionIds) ids.add(id);
    return ids;
  }, [messages, goal.running, goalSessionIdRef.current, backendActiveSessionIds]);

  // 轮询后端活跃会话（仅当前端判断可能遗漏时才持续；集合为空即停止以省资源）。
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const active = await chatService.listActiveSessions();
        if (cancelled) return;
        setBackendActiveSessionIds(new Set(active));
        if (active.length > 0) {
          timer = setTimeout(poll, 5000);
        }
      } catch {
        if (!cancelled) {
          timer = setTimeout(poll, 5000);
        }
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
          if (typeof settings.goal_max_rounds === 'number') {
            setGoalMaxRounds(settings.goal_max_rounds);
          }
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
        } catch { /* ignore */ }
        try {
          const memSettings = await chatService.getMemorySettings();
          if (mounted) setMemorySettings(memSettings);
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

  useEffect(() => {
    document.title = t('app.title');
  }, []);

  useEffect(() => {
    if (sessionId || messages.length > 0 || projects.length === 0 || sessions.length > 0) return;
    if (activeProjectId && projects.some((project) => project.id === activeProjectId)) return;
    const firstProject = projects[0];
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

  const sendMessage = async (override?: { message: string; projectId?: string; goalMode?: boolean; goalText?: string }) => {
    // 目标编辑模式：composer 已是 contentEditable（无 textarea），草稿经
    // ChatInput 的 onChange 同步进 input state，这里直接读它更新目标卡。
    if (editingGoalDraft) {
      const newGoal = (override?.message ?? input).trim();
      if (newGoal && !override) {
        setInput(newGoal);
        goalDraftRef.current = newGoal;
      }
      if (!newGoal) {
        setInput('');
        setEditingGoalDraft(false);
        setGoal((prev) => ({ ...prev, editingDraft: false }));
        return;
      }
      const sid = sessionIdRef.current;
      if (!sid) {
        setInput(newGoal);
        return;
      }
      setInput('');
      try {
        await chatService.editGoal(sid, newGoal);
        await new Promise((r) => setTimeout(r, 300));
        setEditingGoalDraft(false);
        // Only auto-resume if the goal was paused
        const wasPaused = goal.paused;
        setGoal({ goalText: newGoal, done: false, paused: false, todos: [], running: true, round: 0, progress: '', editingDraft: false });
        setMessages((current) => [
          ...current,
          createMessage('assistant', `目标已更新为：${newGoal}`, { status: 'done' }),
        ]);
        if (wasPaused) {
          void resumeGoal();
        }
      } catch (error) {
        console.error('Failed to edit goal:', error);
      }
      return;
    }

    const typedMessage = (override?.message ?? input).trim();
    if (isThinking) return;

    // 命令 chip 路径：skill/子命令/goal 已作为真实 chip 提交，raw 文字不再携带
    // 命令 token，这里把 chip + 提示词组合回注入标记并走既有 handler（含气泡逻辑）。
    const chip = commandChip;
    if (chip && !override) {
      const prompt = input.trim();
      setCommandChip(null);
      if (chip.type === 'skill') {
        setInput('');
        if (chip.packageName) {
          void handleSubCommandSlash(chip.packageName, chip.command.slice(1), `${chip.command}${prompt ? ` ${prompt}` : ''}`);
        } else {
          void handleSkillSlash(`${chip.command}${prompt ? ` ${prompt}` : ''}`);
        }
        return;
      }
      if (chip.command === '/goal') {
        setInput('');
        if (!prompt) {
          setMessages((current) => [...current, createMessage('assistant', t('chat.goal_help_text'), { status: 'done' })]);
          return;
        }
        setGoal({ goalText: prompt, done: false, paused: false, todos: [], running: true, round: 0, progress: "", editingDraft: false });
        void sendMessage({ message: prompt, goalMode: true, goalText: prompt });
        return;
      }
      return;
    }

    if (!typedMessage && attachments.length === 0) return;

    if (typedMessage.startsWith('/')) {
      // 消息墙也要显示命令令牌（/goal、/new、/help 等系统命令显示原始令牌）。
      // 技能命令不显示原始令牌——由 handler 以「已加载技能」的干净标签气泡展示。
      const command = typedMessage.split(/\s+/)[0] ?? '';
      const bareCmd = command.startsWith('/') ? command.slice(1) : '';
      const isSkillCommand =
        command === '/skill' || Boolean(skillSubCommandIndex[bareCmd]) || skillNameIndex.has(bareCmd);
      if (!isSkillCommand) {
        const provider = providers.find((p) => p.id === selectedModel);
        const model = provider?.model ?? runtimeConfig?.selected_model ?? '';
        const providerName = provider?.name ?? runtimeConfig?.agent_provider ?? '';
        setMessages((current) => [
          ...current,
          createMessage('user', typedMessage, { status: 'done', autonomy, provider: providerName, model }),
        ]);
      }
      handleSlashCommand(typedMessage);
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
    const requestAttachments = attachments;
    const requestModel = selectedProvider?.model ?? runtimeConfig?.selected_model ?? '';
    const requestProvider = selectedProvider?.name ?? runtimeConfig?.agent_provider ?? '';
    const requestSessionId = sessionIdRef.current;
    // New generation for this session: any older stream of the SAME session is
    // now stale, but streams of OTHER sessions (kept alive after a switch)
    // stay valid and keep updating their own message in the background.
    bumpSessionSeq(requestSessionId);
    const myRequestSeq = getSessionSeq(requestSessionId);

    // 引用会话：先采用 composer 中已确认的 chips，再兜底扫描消息文本里出现的会话 id
    const requestReferences = [...references];
    const referencedIds = new Set(requestReferences.map((reference) => reference.id));
    for (const sessionIdInText of extractSessionIds(message)) {
      if (referencedIds.has(sessionIdInText)) continue;
      const resolved = await resolveSessionReference(sessionIdInText);
      if (resolved) {
        requestReferences.push(resolved);
        referencedIds.add(resolved.id);
      }
    }

    // 在消息墙展示用户输入（含 /goal 等命令令牌）
    // 前端生成稳定的消息 id，回传后端以统一前后端 id（修复按 id 回退/重生成时 404）
    const userMessageId = `user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    setInput('');
    // 发送即清空输入框里的附件与引用：它们已被捕获进用户消息气泡（上方
    // setMessages 的 attachments/references）和即将发出的请求体，无需再停留
    // 在 composer。放在此处（而非等流式结束后）可彻底避免两类问题：
    // 1) 长任务的整段流式期间附件 chip 一直挂在输入框，看起来像「没发出去」；
    // 2) 流式出错/被中止时 catch/finally 不会清附件，导致 chip 永久残留，
    //    且残留的附件会在下一次发送时被 requestAttachments 误带重发。
    setAttachments([]);
    setReferences([]);

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
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(requestSessionId)] = streamStartAt;
    let inGoal = false;

    const handleEvent = (event: StreamEvent) => {
      // Stale guard: only superseded streams of the SAME session are ignored.
      // Events from a stream belonging to another session MUST be processed —
      // they update that session's own message by id (kept alive across a
      // switch), so the background reply streams to completion instead of
      // freezing at status "running" forever.
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      // Goal-state events (goal_*, todos) must only drive the goal card of the
      // currently-viewed session; a background session's goal must not clobber
      // it (including while on the hero/draft, where no goal card should exist).
      // Message events (delta/tool/plan/done) are always processed.
      if (event.type === 'context_usage') {
        setContextUsage({ usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, windowSource: event.window_source });
        return;
      }
      const goalMatchesView = !event.session_id || event.session_id === sessionIdRef.current;
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
        // 文本增量追加到最后一个 text part；若最后不是 text part（例如工具/推理
        // 之后重新开始说话），则新建 text part。这样 text 与 tool 在 parts 数组
        // 中按流式到达顺序交错，渲染时按数组顺序即可还原 LLM 的真实输出顺序。
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'text') {
          last.content += event.content;
        } else {
          localParts.push({ type: 'text', content: event.content });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'reasoning_delta') {
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'reasoning') {
          last.content = event.content;
        } else {
          localParts.push({ type: 'reasoning', content: event.content });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_start') {
        localParts = upsertToolPart(localParts, event.id, event.name, event.input || '');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_delta') {
        const toolPart = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (toolPart) {
          toolPart.input = (toolPart.input || '') + (event.input || '');
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'plan_start') {
        localParts.push({ type: 'plan', content: '' });
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'plan_delta') {
        const planPart = localParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
        if (planPart) {
          planPart.content += event.content;
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'plan_end') {
        const planPart = localParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
        if (planPart && event.content) {
          planPart.content = event.content;
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'delegate_start') {
        const delegatePart: PartDelegate = {
          type: 'delegate',
          from: event.from || '',
          to: event.to || '',
          task: event.task,
          status: 'running',
          parallel: event.parallel,
        };
        localParts.push(delegatePart);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'delegate_progress') {
        const delegatePart = localParts.find((p): p is Extract<MessagePart, { type: 'delegate' }> => p.type === 'delegate');
        if (delegatePart) {
          delegatePart.status = event.status === 'error' ? 'error' : delegatePart.status;
          if (event.error) delegatePart.error = event.error;
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'delegate_end') {
        const delegatePart = localParts.find((p): p is Extract<MessagePart, { type: 'delegate' }> => p.type === 'delegate');
        if (delegatePart) {
          delegatePart.status = event.error ? 'error' : 'done';
          delegatePart.chars = typeof event.chars === 'number' ? event.chars : delegatePart.chars;
          delegatePart.failed = event.failed;
          delegatePart.error = event.error;
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const sessionIdValue = event.session_id ?? sessionIdRef.current ?? '';
        const pending = pendingRequestFromEvent(event, sessionIdValue, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: t('chat.waiting_resolution'), status: 'waiting', parts: [...localParts] }
              : item,
          ),
        );
      } else if (event.type === 'done') {
        receivedDone = true;
        // Only confirm the session for the currently-viewed session's stream;
        // a background stream from another session must never hijack the view.
        if (event.session_id && event.session_id === sessionIdRef.current) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
        if (inGoal) {
          // Per-round done: accumulate parts, keep the message running.
          if (event.parts && event.parts.length > 0) {
            localParts = mergeMessageParts(localParts, event.parts);
          }
          // Track recent tool names for goal card display
          const toolNames = event.parts
            ?.filter((p) => p.type === 'tool')
            .map((p) => (p as Extract<MessagePart, { type: 'tool' }>).name)
            .filter(Boolean);
          if (toolNames && toolNames.length > 0) {
            setGoal((current) => ({ ...current, recentToolNames: toolNames }));
          }
          setMessages((current) =>
            current.map((item) =>
              item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
            ),
          );
          return;
        }
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) {
          localParts = mergeMessageParts(localParts, event.parts);
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
        setTodos([]);
      } else if (event.type === 'goal_start') {
        inGoal = true;
        if (goalMatchesView) {
          if (event.session_id) goalSessionIdRef.current = event.session_id;
          setGoal({ goalText: event.goal, done: false, paused: false, todos: [], running: true, round: 0, progress: "", editingDraft: false });
        }
      } else if (event.type === 'goal_round') {
        if (goalMatchesView) setGoal((current) => ({ ...current, round: event.round, running: true, paused: false }));
      } else if (event.type === 'goal_edited') {
        if (goalMatchesView) setGoal((current) => ({ ...current, goalText: event.goal || current.goalText }));
      } else if (event.type === 'goal_checkpoint') {
        if (goalMatchesView) {
          setGoal((current) => ({
            ...current,
            progress: event.progress || current.progress,
            ...(event.achieved ? { done: true } : {}),
          }));
        }
      } else if (event.type === 'todos') {
        // Task list is global across modes: the TodoBlock card above the composer
        // shows it in build / plan / goal alike; keep goal.todos in sync too.
        if (goalMatchesView) {
          setTodos(event.todos);
          setGoal((current) => ({ ...current, todos: event.todos }));
        }
      } else if (event.type === 'goal_force') {
        // Force-loop nudge: agent didn't use tools, system will retry.
        if (goalMatchesView) setGoal((current) => ({ ...current, progress: `Force retry ${event.count}/3` }));
      } else if (event.type === 'goal_done') {
        receivedDone = true;
        const failed =
          Boolean(event.stalled) ||
          ['timeout', 'stopped', 'interrupted', 'max_rounds_exceeded'].includes(event.reason || '');
        if (goalMatchesView) {
          setGoal((current) => {
            const next: GoalState = {
              ...current,
              done: true,
              running: false,
              stalled: failed,
              progress: event.content || current.progress,
            };
            if (event.reason) next.reason = event.reason;
            if (event.verification) next.verification = event.verification;
            return next;
          });
          setTodos([]);
        }
        if (event.content) streamedContent = event.content;
        localParts = settleRunningTools(localParts);
        const msgStatus = failed ? 'error' : 'done';
        const failedReasonLabel: Record<string, string> = {
          timeout: 'Agent timed out',
          stopped: 'Goal stopped',
          interrupted: 'Goal interrupted',
          max_rounds_exceeded: 'Max rounds exceeded',
          stalled: 'Agent stalled',
        };
        const msgContent = failed
          ? event.content || failedReasonLabel[event.reason || ''] || 'Goal failed'
          : streamedContent || '✓ 目标已完成';
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: msgContent, status: msgStatus, parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      } else if (event.type === 'goal_paused') {
        if (goalMatchesView) {
          setGoal((current) => ({ ...current, paused: true, running: false }));
          setTodos([]);
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent || t('chat.goal_paused_message'), status: 'done', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      } else if (event.type === 'error') {
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      } else if (event.type === 'goal_stream_id') {
        goalStreamIdRef.current = event.stream_id;
      } else if (event.type === 'goal_system') {
        setMessages((current) => [
          ...current,
          createMessage('assistant', event.content, {
            status: 'done',
            ...(event.session_id ? { sessionId: event.session_id } : {}),
          }),
        ]);
      } else if (event.type === 'goal_attached') {
        goalStreamIdRef.current = event.stream_id;
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
          ...(override?.goalMode ? { goal_mode: true, goal_text: override.goalText || message } : {}),
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
      // For goal mode: check if the goal stream ended naturally and query final status
      if (inGoal && override?.goalMode) {
        void (async () => {
          try {
            const status = await chatService.getGoalStatus(sessionIdRef.current || '');
            if (status.status === 'done') {
              // Don't clobber a correctly-set stalled/error state with a plain
              // "done" — the SSE goal_done event already carried the truth.
              setGoal((current) =>
                current.stalled
                  ? current
                  : {
                      ...current,
                      done: true,
                      running: false,
                      progress: status.goal?.progress || current.progress,
                      ...(status.goal?.reason ? { reason: status.goal.reason } : {}),
                    },
              );
            } else if (status.status === 'paused') {
              setGoal((current) => ({ ...current, paused: true, running: false }));
            }
          } catch { /* ignore */ }
        })();
      }
      _generateSessionTitleIfNeeded(message, streamedContent, requestSessionId);
    } catch (error) {
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      console.error('Failed to stream message:', error);
      if ((error as Error).name === 'AbortError') {
        // For goal mode, abort on disconnect — check session status to let backend finalize
        if (inGoal) {
          void (async () => {
            try {
              if (!sessionIdRef.current) throw new Error('no session');
              const status = await chatService.getGoalStatus(sessionIdRef.current);
              if (status.status === 'done') {
                setGoal((current) =>
                  current.stalled
                    ? current
                    : {
                        ...current,
                        done: true,
                        running: false,
                        progress: status.goal?.progress || current.progress,
                        ...(status.goal?.reason ? { reason: status.goal.reason } : {}),
                      },
                );
              } else if (status.status === 'paused') {
                setGoal((current) => ({ ...current, paused: true, running: false }));
              } else {
                setGoal((current) => ({ ...current, running: false }));
              }
            } catch { /* ignore */ }
          })();
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent || t('chat.stopped'), status: 'stopped', streamEndAt: Date.now() }
              : item,
          ),
        );
      } else {
        setRuntimeStatus('error');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: translateError(error) || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
              : item,
          ),
        );
      }
    } finally {
      // 安全网：强制把这条流命中的 assistant 消息退出 running，避免「蓝条一直挂起不结束」。
      // 按消息 id 收尾（每个流持有独立 id），因此切走会话后后台流结束时也会被正确收尾，
      // 侧栏 running 指示随之清除；不会误伤其它流的消息。
      // 若流结束却从未收到 done/goal_done（断线、后端重启），标记为 interrupted
      // 而非 done —— 半截回复不能被伪装成「已完成」。
      // Guard against batched updates: only transition if the message is still
      // running (not already marked done/error by event handlers above).
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? {
                ...item,
                content: streamedContent || (receivedDone ? item.content : t('chat.stream_interrupted')),
                status: receivedDone ? 'done' : 'interrupted',
                streamEndAt: Date.now(),
              }
            : item,
        ),
      );
      if (streamControllersRef.current[streamKey(requestSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(requestSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(requestSessionId)];
        delete streamStartAtsRef.current[streamKey(requestSessionId)];
      }
    }
  };

  const stopMessage = () => {
    const key = streamKey(sessionIdRef.current);
    const assistantMessageId = activeAssistantMessageIdsRef.current[key];
    const streamStartAt = streamStartAtsRef.current[key];
    abortStreamFor(sessionIdRef.current);
    if (assistantMessageId) {
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: item.content || t('chat.stopped'), status: 'stopped', streamStartAt: streamStartAt ?? Date.now(), streamEndAt: Date.now() }
            : item,
        ),
      );
    }
    requestSeqRef.current += 1;
    bumpSessionSeq(sessionIdRef.current);
  };

  const [editingMessage, setEditingMessage] = useState<{ id: string; content: string } | null>(null);
  const [editDraft, setEditDraft] = useState('');
  // 已提交到 composer 的命令 chip（skill/子命令/goal）。真实 DOM 元素渲染在
  // ChatInput 里，这里持有权威状态供 sendMessage / 编辑流程使用。
  const [commandChip, setCommandChip] = useState<CommandChip | null>(null);
  // 点编辑键即回滚（edit-begin）后，记下待恢复的 (session, message)，取消编辑 /
  // 内容未变退出 / 切换会话时自动调用 edit-cancel 恢复文件。
  const pendingEditRevertRef = useRef<{ sessionId: string; messageId: string } | null>(null);

  const beginEditMessage = (messageId: string, content: string) => {
    setEditingMessage({ id: messageId, content });
    // skill 命令消息存储为注入标记：编辑时反解成 chip + 剩余提示词，与 composer 一致。
    const parsed = parseSkillMarker(content);
    setEditDraft(parsed?.text ?? content);
    setCommandChip(parsed?.chip ?? null);
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
    // 内容未变化：退出编辑态并恢复点编辑时已回滚的文件。
    const original = messages.find((m) => m.id === messageId)?.content;
    if (original !== undefined && trimmed === (original ?? '').trim()) {
      setEditingMessage(null);
      setEditDraft('');
      void restorePendingEdit();
      return;
    }
    setEditingMessage(null);
    setEditDraft('');
    // 已进入发送流程：清掉待恢复标记，避免切换会话时重复恢复。
    pendingEditRevertRef.current = null;

    // 编辑会重跑该会话，任何同会话仍在跑的流都会被本次编辑取代。先中止旧流，
    // 否则后端 _guard_session_not_streaming 会拒绝这次 /edit（409），且旧流的
    // 事件会被陈旧守卫丢弃，气泡悬在 running 计秒。
    abortStreamFor(currentSessionId);

    // 编辑模式下，如果内容包含 /goal 等斜杠命令，走 sendMessage 路径
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
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let receivedDone = false;
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      if (event.type === 'context_usage') {
        setContextUsage({ usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, windowSource: event.window_source });
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'reasoning_delta') {
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'reasoning') {
          last.content = event.content;
        } else {
          localParts.push({ type: 'reasoning', content: event.content });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_start') {
        // 编辑/重生成路径支持 tool_delta（P1 修复）
        localParts = upsertToolPart(localParts, event.id, event.name, event.input || '');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_delta') {
        const td = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (td) td.input = (td.input || '') + (event.input || '');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'waiting', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        receivedDone = true;
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
        setTodos([]);
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
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      }
    };
    try {
      await chatService.streamEditMessage(currentSessionId, messageId, trimmed, handleEvent, {
        signal: controller.signal,
        workMode,
        autonomy,
        revertCode,
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
      }
    } finally {
      if (streamControllersRef.current[streamKey(currentSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(currentSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(currentSessionId)];
        delete streamStartAtsRef.current[streamKey(currentSessionId)];
      }
      // Safety net: ensure the assistant message leaves the "running" state
      // even if the terminal event was dropped by the backend. A stream that
      // ended without a done event is interrupted, not "done" (half a reply
      // must not masquerade as complete).
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: streamedContent || (receivedDone ? item.content : t('chat.stream_interrupted')), status: receivedDone ? 'done' : 'interrupted', streamEndAt: Date.now() }
            : item,
        ),
      );
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
    const triggerUserMessageId = idxNow > 0 ? thisSessionNow[idxNow - 1]?.id : undefined;
    bumpSessionSeq(currentSessionId);
    const myRequestSeq = getSessionSeq(currentSessionId);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    abortStreamFor(currentSessionId);
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
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let receivedDone = false;
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      if (event.type === 'context_usage') {
        setContextUsage({ usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, windowSource: event.window_source });
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'reasoning_delta') {
        const last = localParts[localParts.length - 1];
        if (last && last.type === 'reasoning') {
          last.content = event.content;
        } else {
          localParts.push({ type: 'reasoning', content: event.content });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_start') {
        // 编辑/重生成路径支持 tool_delta（P1 修复）
        localParts = upsertToolPart(localParts, event.id, event.name, event.input || '');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'tool_delta') {
        const td = localParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
        if (td) td.input = (td.input || '') + (event.input || '');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
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
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'waiting', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        receivedDone = true;
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
        setTodos([]);
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
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      }
    };
    try {
      await chatService.streamRegenerateMessage(currentSessionId, messageId, handleEvent, controller.signal);
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
      }
    } finally {
      if (streamControllersRef.current[streamKey(currentSessionId)] === controller) {
        delete streamControllersRef.current[streamKey(currentSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(currentSessionId)];
        delete streamStartAtsRef.current[streamKey(currentSessionId)];
      }
      // Safety net: ensure the assistant message leaves the "running" state
      // even if the terminal event was dropped by the backend. A stream that
      // ended without a done event is interrupted, not "done" (half a reply
      // must not masquerade as complete).
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: streamedContent || (receivedDone ? item.content : t('chat.stream_interrupted')), status: receivedDone ? 'done' : 'interrupted', streamEndAt: Date.now() }
            : item,
        ),
      );
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
    resolvingRef.current = true;
    setPendingRequests((current) =>
      current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: true } : item)),
    );
    let resumeId: string | undefined;
    try {
      const response = await chatService.resolveCommandApproval(request.approval_id, decision);
      resumeId = response.resume_id;
      const chained = (response.events ?? []).filter(
        (event): event is Extract<StreamEvent, { type: 'approval_required' } | { type: 'question_required' }> =>
          event.type === 'approval_required' || event.type === 'question_required',
      );
      setPendingRequests((current) => [
        ...current.filter((item) => item.approval_id !== request.approval_id),
        ...chained.map((event): PendingRequest => pendingRequestFromEvent(event, request.session_id, targetMessageId)),
      ]);
      if (response.resumed === false && !response.resume_id) {
        return;
      }
    } catch (error) {
      console.error('Failed to resolve approval:', error);
      setPendingRequests((current) =>
        current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: false } : item)),
      );
      return;
    } finally {
      resolvingRef.current = false;
    }

    if (!resumeId) return;
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
    try {
      await chatService.subscribeApprovalEvents(
        resumeId,
        (event) => {
          // P1 陈旧请求守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
          if (isStreamStale(resumeSessionId, resumeRequestSeq)) return;
          if (event.type === 'context_usage') {
            setContextUsage({ usedChars: event.used_chars, budgetChars: event.budget_chars, compressed: event.compressed, usedTokens: event.used_tokens, budgetTokens: event.budget_tokens, windowTokens: event.window_tokens, compacted: event.compacted, windowSource: event.window_source });
            return;
          }
          if (event.type === 'done') {
            resumeDone = true;
            resumeContent = event.content || resumeContent;
            if (event.parts && event.parts.length > 0) {
              resumeParts = event.parts;
            }
            resumeParts = settleRunningTools(resumeParts);
            applyResume('done');
          } else if (event.type === 'delta') {
            resumeContent += event.content;
            const last = resumeParts[resumeParts.length - 1];
            if (last && last.type === 'text') {
              last.content += event.content;
            } else {
              resumeParts.push({ type: 'text', content: event.content });
            }
            applyResume('running');
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
            }
            // 装完即见：agent 安装技能后立刻刷新技能列表。
            if (tp?.name === 'install_skill') void refreshSkills();
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
            resumeParts = settleRunningTools(resumeParts);
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: t('chat.waiting_resolution'), status: 'waiting', parts: mergeMessageParts(item.parts || [], resumeParts) }
                  : item,
              ),
            );
          } else if (event.type === 'error') {
            resumeParts = settleRunningTools(resumeParts);
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', parts: mergeMessageParts(item.parts || [], resumeParts), streamEndAt: Date.now() }
                  : item,
              ),
            );
          }
        },
        resumeController.signal,
      );
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        console.error('Approval event stream failed:', error);
      }
    } finally {
      // P0 并发双流修复：resume 流结束后清除该会话的 controller
      if (streamControllersRef.current[streamKey(resumeSessionId)] === resumeController) {
        delete streamControllersRef.current[streamKey(resumeSessionId)];
        delete activeAssistantMessageIdsRef.current[streamKey(resumeSessionId)];
        delete streamStartAtsRef.current[streamKey(resumeSessionId)];
      }
      // 断线兜底：resume 流结束却没收到 done，把目标消息从 running 收尾为
      // interrupted，避免 spinner 永久挂起。
      if (!resumeDone) {
        setMessages((current) =>
          current.map((item) =>
            item.id === targetMessageId && item.status === 'running'
              ? { ...item, content: resumeContent || t('chat.stream_interrupted'), status: 'interrupted' as const, parts: mergeMessageParts(item.parts || [], resumeParts), streamEndAt: Date.now() }
              : item,
          ),
        );
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
    const project = projects.find((p) => p.id === projectId);
    const resolvedAgent = project?.mode === 'single' ? 'default_agent' : (agentId ?? 'default_agent');
    setDraftAgentId(resolvedAgent);
    // 新开对话属于新的空会话：清掉上一会话残留的 goal 卡片，避免它串到新会话显示。
    setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
    goalSessionIdRef.current = undefined;
    setActiveView('chat');
  };

  // 新对话：不再弹窗。在项目内新建则继承该项目 workspace；
  // 全局新建则进入空态，由 composer 顶部的 workspace 选择器指定。
  const startNewChat = (projectId?: string, agentId?: string) => {
    startProjectDraft(projectId, '', agentId);
    setDraftMode(true);
    setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
  };

  // 草稿态下切换 workspace：仅改归属，不清空已输入内容
  const selectDraftWorkspace = (projectId: string) => {
    pendingProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
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
          restored.push({
            ...base,
            command: Array.isArray(approval.command) ? approval.command : [],
            ...(approval.cwd ? { cwd: approval.cwd } : {}),
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
          ...(record.parts?.length ? { parts: record.parts as MessagePart[] } : {}),
          ...(record.references?.length ? { references: record.references } : {}),
          timestamp: new Date(record.created_at).getTime(),
        }),
      );
      setSessionId(sessionIdToOpen);
      sessionIdRef.current = sessionIdToOpen;
      setContextUsage(null); // 打开会话时不残留上一会话的上下文预算（B10）
      pendingProjectIdRef.current = undefined;
      setPendingRequests((current) => current.filter((item) => item.session_id !== sessionIdToOpen));
      void restorePendingForSession(sessionIdToOpen);
      // 恢复持久化的目标状态（goal 卡片）
      const sessionRecord = response.session as SessionDetailResponse['session'] & {
        goal_text?: string;
        goal_done?: boolean;
        goal_paused?: boolean;
        goal_todos?: GoalTodo[];
        goal_stopped?: boolean;
        goal_interrupted?: boolean;
      };
      if (sessionRecord.goal_text && !sessionRecord.goal_stopped) {
        // An interrupted goal (e.g. a crash) still has goal_text but no
        // goal_paused flag — treat it as resumable so the user can restart it
        // from the checkpoint instead of being stuck with no controls.
        const recoverable = Boolean(sessionRecord.goal_paused || sessionRecord.goal_interrupted);
        setGoal({
          goalText: sessionRecord.goal_text,
          done: Boolean(sessionRecord.goal_done),
          paused: recoverable,
          todos: sessionRecord.goal_todos || [],
          running: false,
          round: 0,
          progress: '',
          stalled: false,
          editingDraft: false,
        });
        goalSessionIdRef.current = sessionIdToOpen;
      } else {
        setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
        setTodos([]);
      }
      // 后台 resume 可能仍在运行：切回时首扫可能早于新审批创建，延迟重扫兜底。
      for (const delay of [5000, 15000]) {
        setTimeout(() => {
          if (sessionIdRef.current === sessionIdToOpen) {
            void restorePendingForSession(sessionIdToOpen);
          }
        }, delay);
      }
      setActiveProjectId(response.session.project_id || undefined);
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
        setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
        setDraftMode(true);
      }
      // 清理被删除会话在所有上下文中的消息
      setMessages((current) => current.filter((m) => m.sessionId && m.sessionId !== sessionIdToDelete));
      abortStreamFor(sessionIdToDelete);
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
        setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
      }
      // 清理所有属于该项目的会话的消息
      setMessages((current) => current.filter((m) => !m.sessionId || !deletedSessionIds.has(m.sessionId)));
      await Promise.all([refreshSessions(), refreshProjects()]);
    } catch (error) {
      console.error('Failed to delete project:', error);
      window.alert(error instanceof Error ? error.message : 'Failed to delete project');
    }
  };

  const handleSlashCommand = (message: string) => {
    const [command] = message.split(/\s+/);
    setInput('');
    if (command === '/goal') {
      const goalText = message.slice('/goal'.length).trim();
      if (!goalText) {
        setMessages((current) => [...current, createMessage('assistant', t('chat.goal_help_text'), { status: 'done' })]);
        return;
      }
      setGoal({ goalText, done: false, paused: false, todos: [], running: true, round: 0, progress: "", editingDraft: false });
      void sendMessage({ message: goalText, goalMode: true, goalText });
      return;
    }
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
   *  即时 sys 命令直接执行（不留 chip），/goal 与 skill 型保留 chip 等提示词。 */
  // 正在异步验证的 chip（防较慢的旧验证把更新的提交覆盖掉）
  const pendingCommandRef = useRef<CommandChip | null>(null);

  const handleCommandCommit = useCallback(
    (chip: CommandChip | null) => {
      pendingCommandRef.current = chip;
      if (!chip) {
        setCommandChip(null);
        return;
      }
      if (chip.type === 'sys' && chip.command !== '/goal') {
        setCommandChip(null);
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
          }
        })();
        return;
      }
      setCommandChip(chip);
    },
    [handleSlashCommand],
  );

  const pauseGoal = async () => {
    if (!sessionIdRef.current) return;
    try {
      await chatService.pauseGoal(sessionIdRef.current);
      setGoal((current) => ({ ...current, paused: true, running: false }));
    } catch (error) {
      console.error('Failed to pause goal:', error);
    }
  };

  const resumeGoal = async () => {
    const targetSessionId = sessionIdRef.current;
    if (!targetSessionId) return;
    // Lock the composer for the duration of the resumed loop so the user
    // cannot spin up a concurrent stream that would race the running goal.
    // isThinking is derived from goal.running scoped to this session.
    goalSessionIdRef.current = targetSessionId;
    setGoal((current) => ({ ...current, running: true, paused: false }));
    // Register an abort controller so the Stop button can cancel the resume
    // stream (previously stopMessage was a no-op during goal resume).
    const controller = new AbortController();
    const key = streamKey(targetSessionId);
    streamControllersRef.current[key] = controller;
    try {
      await chatService.resumeGoal(targetSessionId, handleGoalResumeEventWithChat, controller.signal);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        console.error('Failed to resume goal:', error);
      }
      setGoal((current) => ({ ...current, running: false }));
    } finally {
      if (streamControllersRef.current[key] === controller) {
        delete streamControllersRef.current[key];
        delete activeAssistantMessageIdsRef.current[key];
        delete streamStartAtsRef.current[key];
      }
      // Reconcile with the backend: if the stream ended WITHOUT a terminal
      // goal_done/goal_paused (e.g. the backend restarted mid-loop), the goal
      // card would stay "running" forever and lock the composer. Ask the
      // backend for the real status and settle it.
      void (async () => {
        try {
          const status = await chatService.getGoalStatus(targetSessionId);
          if (status.status === 'done' && status.goal?.progress) {
            setGoal((current) => (current.running ? { ...current, done: true, running: false, progress: status.goal?.progress || current.progress } : current));
          } else if (status.status === 'paused') {
            setGoal((current) => (current.running ? { ...current, paused: true, running: false } : current));
          } else if (!status.status || status.status === 'none') {
            setGoal((current) => ({ ...current, running: false }));
          }
        } catch {
          // keep whatever the card shows; polling covers it later
        }
      })();
    }
  };

  const toggleTodo = (index: number) => {
    const todo = todos[index];
    if (!todo) return;
    const newStatus = todo.status === 'completed' ? 'pending' : 'completed';
    setTodos((current) => current.map((t, i) => (i === index ? { ...t, status: newStatus as 'completed' | 'pending' } : t)));
    // Keep the goal card's todo list in sync for goal-mode runs.
    setGoal((current) => {
      if (!current.goalText) return current;
      return { ...current, todos: current.todos.map((t, i) => (i === index ? { ...t, status: newStatus as 'completed' | 'pending' } : t)) };
    });
  };

  const draftEditGoal = () => {
    if (!sessionIdRef.current || !goal.goalText) return;
    // composer 已是 contentEditable（无 textarea），草稿即 input state。
    const currentComposerVal = input.trim() || goal.goalText;
    setInput(currentComposerVal);
    goalDraftRef.current = currentComposerVal;
    setEditingGoalDraft(true);
    setGoal((prev) => ({ ...prev, editingDraft: true }));
  };

  const saveGoalEdit = async (goalText: string) => {
    if (!sessionIdRef.current) return;
    try {
      await chatService.editGoal(sessionIdRef.current, goalText);
      // Only auto-resume if the goal was paused
      const wasPaused = goal.paused;
      setGoal((current) => ({ ...current, goalText, editingDraft: false }));
      if (wasPaused) {
        void resumeGoal();
      }
    } catch (error) {
      console.error('Failed to edit goal:', error);
    }
  };

  const deleteGoal = async () => {
    if (!sessionIdRef.current) return;
    try {
      await chatService.deleteGoal(sessionIdRef.current);
      setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
    } catch (error) {
      console.error('Failed to delete goal:', error);
    }
  };

  const cancelGoalEdit = () => {
    setEditingGoalDraft(false);
    setGoal((current) => ({ ...current, editingDraft: false }));
  };

  const handleGoalResumeEvent = (event: StreamEvent) => {
    if (event.session_id && event.session_id !== sessionIdRef.current) return;
    if (event.type === 'goal_start' || event.type === 'goal_round') {
      setGoal((current) => ({ ...current, running: true, paused: false, round: event.type === 'goal_round' ? event.round : current.round }));
    } else if (event.type === 'goal_checkpoint') {
      setGoal((current) => ({ ...current, progress: event.progress || current.progress, ...(event.achieved ? { done: true } : {}) }));
    } else if (event.type === 'todos') {
      setTodos(event.todos);
      setGoal((current) => ({ ...current, todos: event.todos }));
    } else if (event.type === 'goal_done') {
      const failed =
        Boolean(event.stalled) ||
        ['timeout', 'stopped', 'interrupted', 'max_rounds_exceeded'].includes(event.reason || '');
      setGoal((current) => {
        const next: GoalState = { ...current, done: true, running: false, stalled: failed, progress: event.content || current.progress };
        if (event.reason) next.reason = event.reason;
        return next;
      });
      setTodos([]);
    } else if (event.type === 'goal_paused') {
      setGoal((current) => ({ ...current, paused: true, running: false }));
      setTodos([]);
    } else if (event.type === 'goal_force') {
      setGoal((current) => ({ ...current, progress: `Force retry ${event.count}/3` }));
    }
  };

  const handleGoalResumeEventWithChat = (event: StreamEvent) => {
    if (event.session_id && event.session_id !== sessionIdRef.current) return;
    if (event.type === 'goal_start' || event.type === 'goal_round') {
      if (event.session_id) goalSessionIdRef.current = event.session_id;
      setGoal((current) => ({ ...current, running: true, paused: false, round: event.type === 'goal_round' ? event.round : current.round }));
    } else if (event.type === 'goal_checkpoint') {
      setGoal((current) => ({ ...current, progress: event.progress || current.progress, ...(event.achieved ? { done: true } : {}) }));
    } else if (event.type === 'todos') {
      setTodos(event.todos);
      setGoal((current) => ({ ...current, todos: event.todos }));
    } else if (event.type === 'goal_system') {
      // Display system messages in chat
      setMessages((current) => [
        ...current,
        createMessage('assistant', event.content, { status: 'done' }),
      ]);
    } else if (event.type === 'goal_stream_id') {
      goalStreamIdRef.current = event.stream_id;
    } else if (event.type === 'goal_attached') {
      goalStreamIdRef.current = event.stream_id;
    } else if (event.type === 'goal_done') {
      setGoal((current) => {
        const next: GoalState = { ...current, done: true, running: false, stalled: event.stalled || false, progress: event.content || current.progress };
        if (event.reason) next.reason = event.reason;
        return next;
      });
      setTodos([]);
      const content = event.content;
      if (content) {
        setMessages((current) => [
          ...current,
          createMessage('assistant', content, { status: event.stalled ? 'error' : 'done' }),
        ]);
      }
    } else if (event.type === 'goal_paused') {
      setGoal((current) => ({ ...current, paused: true, running: false }));
      setTodos([]);
    } else if (event.type === 'goal_force') {
      setGoal((current) => ({ ...current, progress: `Force retry ${event.count}/3` }));
    } else if (event.type === 'delta') {
      // Append streaming deltas to the last assistant message in goal context.
      // Only search within the current session's messages — a background session's
      // streaming message must not be clobbered with this goal's delta.
      setMessages((current) => {
        const last = [...current].reverse().find(
          (m) =>
            m.role === 'assistant' &&
            !m.content.includes('目标已更新') &&
            (!m.sessionId || m.sessionId === sessionIdRef.current),
        );
        if (last) {
          return current.map((m) => m.id === last.id ? { ...m, content: m.content + event.content } : m);
        }
        return current;
      });
    }
  };

  const changeSelectedModel = async (providerId: string) => {
    const provider = providers.find((item) => item.id === providerId);
    if (!provider) return;
    const previous = selectedModel;
    setSelectedModel(provider.id);
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
  const titlebarProjectName = activeProject?.name ?? t('sidebar.default_project');
  const activeProjectSessions = activeProject ? sessions.filter((session) => session.project_id === activeProject.id) : [];
  const showNewChatHero = activeView === 'chat' && !sessionId  && runtimeStatus === 'ready' && (!activeProject || draftMode);
  const showFirstRunStart = activeView === 'chat' && runtimeStatus === 'ready' && projects.length === 0 && sessions.length === 0 && !sessionId && !draftMode;
  const showProjectSessionList = activeView === 'chat' && activeProject && !sessionId && runtimeStatus === 'ready' && !draftMode;
  const workspaceOptions = projects.map((project) => ({
    id: project.id,
    name: project.name,
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
  const currentSessionPending = sessionId
    ? pendingRequests.filter((item) => item.session_id === sessionId)
    : [];

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

  const changeGoalMaxRounds = (value: number) => {
    setGoalMaxRounds(value);
    chatService.saveSettings({ goal_max_rounds: value }).catch(() => { /* ignore */ });
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
          onOpenSession={openSession}
          onDeleteSession={deleteSession}
          onCreateProject={createProject}
          onRenameProject={renameProject}
          onDeleteProject={deleteProject}
          onOpenOrgSettings={openOrgSettings}
          {...(goal.goalText && !goal.done && sessionId ? { goalIndicatorSessionId: sessionId } : {})}
          runningSessionIds={runningSessionIds}
        />
        <section className={`workspace-frame ${rightSidebarOpen ? 'workspace-frame--right-open' : ''} ${bottomPanelOpen ? 'workspace-frame--bottom-open' : ''}`}>
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
                    <NewChatHero {...(activeProject?.name ? { workspaceName: activeProject.name } : {})} />
                  ) : showProjectSessionList ? (
                    <ProjectSessionList
                      project={activeProject}
                      sessions={activeProjectSessions}
                      runningSessionIds={runningSessionIds}
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
                      {/* Task list (write_todos) — the agent's self-decomposed
                          checklist, shown in every mode above the composer. */}
                      <TodoBlock todos={todos} onToggleTodo={toggleTodo} />
                      {goal.goalText && !editingGoalDraft && (
                        <GoalCard goal={goal} onPause={pauseGoal} onResume={resumeGoal} onDelete={deleteGoal} onDraftEdit={draftEditGoal} recentToolNames={goal.recentToolNames ?? undefined} />
                      )}
                      {editingGoalDraft && (
                        <div className="goal-edit-banner">
                          <p className="goal-edit-banner__text">编辑目标：在下方输入框中输入新目标，按回车确认</p>
                          <button type="button" className="goal-edit-banner__cancel" onClick={cancelGoalEdit} aria-label="取消编辑">
                            取消
                          </button>
                        </div>
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
                            onStop={stopMessage}
                          />
                        </div>
                      ) : (
                        <ChatInput
                        value={editingMessage ? editDraft : (editingGoalDraft ? input : input)}
                        disabled={isThinking || runtimeStatus === 'connecting'}
                        isThinking={isThinking}
                        workMode={workMode}
                        autonomy={autonomy}
                        selectedModel={selectedModel}
                        attachments={attachments}
                        maxAttachmentMb={maxAttachmentMb}
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
              ) : (
                <SettingsView
                  themeSettings={themeSettings}
                  autonomy={autonomy}
                  goalMaxRounds={goalMaxRounds}
                  onGoalMaxRoundsChange={changeGoalMaxRounds}
                  maxAttachmentMb={maxAttachmentMb}
                  onMaxAttachmentMbChange={changeMaxAttachmentMb}
                  revertCode={revertCode}
                  onRevertCodeChange={changeRevertCode}
                  onThemeSettingsChange={changeThemeSettings}
                  onAutonomyChange={setAutonomy}
                  memorySettings={memorySettings}
                  onMemorySettingsChange={changeMemorySettings}
                  modelOptions={modelOptions}
                  updateCenter={updateCenter}
                  onClose={() => setActiveView('chat')}
                />
              )}
            </section>
            {rightSidebarOpen && (
              <WorkspaceInspector
                sessionTitle={titlebarSessionTitle}
                projectName={activeProject?.name ?? t('sidebar.default_project')}
                modelName={currentProvider?.model ?? runtimeConfig?.selected_model ?? t('chat.model_unselected')}
                providerName={currentProvider?.name ?? runtimeConfig?.agent_provider ?? t('chat.model_unselected')}
                autonomy={autonomy}
                attachmentCount={attachments.length}
                messageCount={messages.length}
                onResizeStart={() => setInspectorResizing(true)}
                onResizeEnd={() => setInspectorResizing(false)}
                onResizeWidth={(width) => setInspectorWidth(width)}
              />
            )}
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
