import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ChatInput, extractSessionIds } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { GoalCard } from './components/GoalCard';
import { ProvidersPanel } from './components/ProvidersPanel';
import { MCPPanel } from './components/MCPPanel';
import { SkillsPanel } from './components/SkillsPanel';
import { CreateProjectDialog } from './components/CreateProjectDialog';
import { ProjectSessionList } from './components/ProjectSessionList';
import { FirstRunStart } from './components/FirstRunStart';
import { NewChatHero } from './components/NewChatHero';
import { SettingsView } from './components/settings/SettingsView';
import { WorkspaceTitlebar } from './components/WorkspaceTitlebar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { WorkspaceBottomPanel, type BottomPanelView } from './components/WorkspaceBottomPanel';
import { WorkspaceInspector } from './components/WorkspaceInspector';
import { ChangesPanel } from './components/ChangesPanel';
import { RollbackDialog } from './components/RollbackDialog';
import { getLanguage, initLanguage, t, translateError, useLanguage } from './lib/i18n';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { chatService } from './services/chatService';
import type { AppView, ApprovalDecisionPayload, ApprovalOption, Autonomy, ChatMessage, ComposerAttachment, CreateProjectRequest, GoalState, GoalTodo, McpServerEntry, McpTemplateEntry, MessagePart, PendingRequest, ProjectEntry, ProviderEntry, RuntimeConfig, SessionDetailResponse, SessionReference, SessionSummary, SkillDiagnostic, SkillEntry, StreamEvent, WorkMode } from './types';
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
  { type: 'approval_required' } | { type: 'question_required' } | { type: 'plan_required' }
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
  if (event.type === 'plan_required') return { ...base, plan: event.plan };
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
  const [editingGoalDraft, setEditingGoalDraft] = useState(false);
  const goalDraftRef = useRef('');
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<'connecting' | 'ready' | 'error'>('connecting');
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | undefined>();
  const [createProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
  const [draftMode, setDraftMode] = useState(false);
  const _language = useLanguage();
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
  const [autonomy, setAutonomy] = useState<Autonomy>('guarded');
  const [goalMaxRounds, setGoalMaxRounds] = useState<number>(50);
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
  const [workMode, setWorkMode] = useState<WorkMode>('build');
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
  const skillCommandIndex = useMemo<Record<string, string>>(() => {
    const index: Record<string, string> = {};
    for (const skill of skillEntries) {
      for (const cmd of skill.commands ?? []) {
        if (!index[cmd.name]) index[cmd.name] = skill.name;
      }
    }
    return index;
  }, [skillEntries]);

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
        (m) => m.status === 'running' && (!m.sessionId || m.sessionId === sessionId),
      ),
    [goal.running, goalSessionIdRef.current, messages, sessionId],
  );

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
        } catch { /* ignore */ }
      } catch (error) {
        console.error('Failed to load runtime config:', error);
        if (!mounted) return;
        setRuntimeStatus('error');
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
    // 目标编辑模式：从 DOM 读取 textarea 值，更新目标卡
    if (editingGoalDraft) {
      const textarea = document.querySelector('textarea');
      let newGoal: string;
      if (override?.message) {
        newGoal = override.message;
      } else if (textarea && textarea.value.trim()) {
        newGoal = textarea.value.trim();
        setInput(newGoal);
        goalDraftRef.current = newGoal;
      } else {
        newGoal = input.trim();
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

    if (!typedMessage && attachments.length === 0) return;

    if (typedMessage.startsWith('/')) {
      // 消息墙也要显示命令令牌
      const provider = providers.find((p) => p.id === selectedModel);
      const model = provider?.model ?? runtimeConfig?.selected_model ?? '';
      const providerName = provider?.name ?? runtimeConfig?.agent_provider ?? '';
      setMessages((current) => [
        ...current,
        createMessage('user', typedMessage, { status: 'done', autonomy, provider: providerName, model }),
      ]);
      handleSlashCommand(typedMessage);
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
        const sessionResp = await chatService.createSession({ project_id: requestProjectId });
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

    const controller = new AbortController();
    streamControllersRef.current[streamKey(requestSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(requestSessionId)] = assistantMessageId;
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(requestSessionId)] = streamStartAt;
    let inGoal = false;

    const handleEvent = (event: StreamEvent) => {
      // Stale guard: only superseded streams of the SAME session are ignored.
      // A stream belonging to another session (kept alive across a switch)
      // continues updating its own message in the background.
      if (isStreamStale(requestSessionId, myRequestSeq)) return;
      if (event.session_id && sessionIdRef.current && event.session_id !== sessionIdRef.current) {
        if (event.type !== 'start') return;
      }
      if (event.type === 'start') {
        streamStartAt = Date.now();
        if (event.session_id && !sessionIdRef.current) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
      } else if (event.type === 'delta') {
        streamedContent += event.content;
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
          if (event.output) toolPart.output = event.output;
          if (event.duration_ms !== undefined) toolPart.duration_ms = event.duration_ms;
          if (event.files !== undefined) toolPart.files = event.files;
        }
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
      } else if (event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required') {
        const sessionIdValue = event.session_id ?? sessionIdRef.current ?? '';
        const pending = pendingRequestFromEvent(event, sessionIdValue, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] }
              : item,
          ),
        );
      } else if (event.type === 'done') {
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
          localParts = event.parts;
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      } else if (event.type === 'goal_start') {
        inGoal = true;
        if (event.session_id) goalSessionIdRef.current = event.session_id;
        setGoal({ goalText: event.goal, done: false, paused: false, todos: [], running: true, round: 0, progress: "", editingDraft: false });
      } else if (event.type === 'goal_round') {
        setGoal((current) => ({ ...current, round: event.round, running: true, paused: false }));
      } else if (event.type === 'goal_checkpoint') {
        setGoal((current) => ({
          ...current,
          progress: event.progress || current.progress,
          ...(event.achieved ? { done: true } : {}),
        }));
      } else if (event.type === 'todos') {
        setGoal((current) => ({ ...current, todos: event.todos }));
      } else if (event.type === 'goal_force') {
        // Force-loop nudge: agent didn't use tools, system will retry.
        setGoal((current) => ({ ...current, progress: `Force retry ${event.count}/3` }));
      } else if (event.type === 'goal_done') {
        const failed =
          Boolean(event.stalled) ||
          ['timeout', 'stopped', 'interrupted', 'max_rounds_exceeded'].includes(event.reason || '');
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
        setGoal((current) => ({ ...current, paused: true, running: false }));
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
          createMessage('assistant', event.content, { status: 'done' }),
        ]);
      } else if (event.type === 'goal_attached') {
        goalStreamIdRef.current = event.stream_id;
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
      // 仅当该流仍属当前请求 且 消息属于当前会话时才收尾。切走会话时不再中止流，
      // 该流在后台继续更新自己的消息（displayedMessages 会按 sessionId 过滤），
      // 切回时仍能看到半截回复并在原地续流，直到收到 done 自然收尾。
      const belongsToCurrent = requestId === requestSeqRef.current && sessionIdRef.current === requestSessionId;
      if (belongsToCurrent) {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId && item.status === 'running'
              ? { ...item, content: streamedContent, status: 'done', streamEndAt: Date.now() }
              : item,
          ),
        );
      }
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

  const beginEditMessage = (messageId: string, content: string) => {
    setEditingMessage({ id: messageId, content });
    setEditDraft(content);
  };

  const commitEditMessage = async (messageId: string, content: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;
    const trimmed = content.trim();
    if (!trimmed) return;
    setEditingMessage(null);
    setEditDraft('');

    // 编辑模式下，如果内容包含 /goal 等斜杠命令，走 sendMessage 路径
    if (trimmed.startsWith('/')) {
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
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      if (event.type === 'delta') {
        streamedContent += event.content;
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
          if (event.output) toolPart.output = event.output;
          if (event.duration_ms !== undefined) toolPart.duration_ms = event.duration_ms;
          if (event.files !== undefined) toolPart.files = event.files;
        }
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
      } else if (event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
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
      // even if the terminal event was dropped by the backend.
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: streamedContent, status: 'done', streamEndAt: Date.now() }
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
    bumpSessionSeq(currentSessionId);
    const myRequestSeq = getSessionSeq(currentSessionId);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    let streamStartAt = Date.now();
    streamStartAtsRef.current[streamKey(currentSessionId)] = streamStartAt;
    const controller = new AbortController();
    streamControllersRef.current[streamKey(currentSessionId)] = controller;
    activeAssistantMessageIdsRef.current[streamKey(currentSessionId)] = assistantMessageId;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫：仅同会话内被更新的流视为陈旧；其它会话的后台流继续更新自己的消息
      if (isStreamStale(currentSessionId, myRequestSeq)) return;
      if (event.type === 'delta') {
        streamedContent += event.content;
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
          if (event.output) toolPart.output = event.output;
          if (event.duration_ms !== undefined) toolPart.duration_ms = event.duration_ms;
          if (event.files !== undefined) toolPart.files = event.files;
        }
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
      } else if (event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required') {
        const pending = pendingRequestFromEvent(event, currentSessionId, assistantMessageId);
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        localParts = settleRunningTools(localParts);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
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
      // even if the terminal event was dropped by the backend.
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: streamedContent, status: 'done', streamEndAt: Date.now() }
            : item,
        ),
      );
      requestSeqRef.current += 1;
      bumpSessionSeq(currentSessionId);
    }
  };

  const [rollbackTarget, setRollbackTarget] = useState<{ sessionId: string; messageId: string } | null>(null);

  const handleRollbackMessage = (messageId: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;
    setRollbackTarget({ sessionId: currentSessionId, messageId });
  };

  const performRollback = async (withCode: boolean) => {
    if (!rollbackTarget) return;
    try {
      const response = await chatService.rollbackMessage(rollbackTarget.sessionId, rollbackTarget.messageId, withCode);
      const remaining = response.messages.map((m) =>
        createMessage(m.role as 'user' | 'assistant', m.content, {
          id: m.id,
          status: 'done',
          sessionId: rollbackTarget.sessionId,
          parts: (m.parts as MessagePart[]) ?? [],
        }),
      );
      setMessages(remaining);
      await refreshSessions();
      await refreshProjects();
      setChangesRefreshKey((value) => value + 1);
      if (response.revert && response.revert.conflict_count > 0) {
        window.alert(
          t('rollback.result_with_conflicts', {
            reverted: response.revert.reverted_count,
            conflicts: response.revert.conflict_count,
          }),
        );
      }
    } catch (error) {
      console.error('Failed to rollback message:', error);
      window.alert(translateError(error) || t('rollback.failed'));
    }
  };

  const pendingRequestsRef = useRef<PendingRequest[]>([]);
  useEffect(() => {
    pendingRequestsRef.current = pendingRequests;
  }, [pendingRequests]);
  const resolvingRef = useRef(false);

  const _generateSessionTitleIfNeeded = (firstMessageContent: string, assistantResponse: string, sessionSessionId?: string) => {
    if (!sessionSessionId) return;
    if (messages.length > 0) return;
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
        (event): event is Extract<StreamEvent, { type: 'approval_required' } | { type: 'question_required' } | { type: 'plan_required' }> =>
          event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required',
      );
      setPendingRequests((current) => [
        ...current.filter((item) => item.approval_id !== request.approval_id),
        ...chained.map((event): PendingRequest => pendingRequestFromEvent(event, request.session_id, targetMessageId)),
      ]);
      if (response.resumed === false && !response.resume_id) {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== targetMessageId) return item;
            return item;
          }),
        );
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
          if (event.type === 'done') {
            resumeContent = event.content || resumeContent;
            if (event.parts && event.parts.length > 0) {
              resumeParts = event.parts;
            }
            resumeParts = settleRunningTools(resumeParts);
            applyResume('done');
          } else if (event.type === 'delta') {
            resumeContent += event.content;
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
              if (event.output) tp.output = event.output;
            }
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
          } else if (event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required') {
            setPendingRequests((current) => {
              if (current.some((item) => item.approval_id === event.approval_id)) return current;
              return [...current, pendingRequestFromEvent(event, resumeSessionId, targetMessageId)];
            });
            resumeParts = settleRunningTools(resumeParts);
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: t('chat.waiting_resolution'), status: 'done' as const, parts: mergeMessageParts(item.parts || [], resumeParts) }
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
    }
  };

  const dismissPendingRequest = (request: PendingRequest) => {
    void resolvePendingRequest(request, { type: 'reject' });
  };

  const isResolving = () => resolvingRef.current;

  const startProjectDraft = (projectId?: string, firstMessage = '') => {
    // 新开对话不中止任何会话的流：并行任务各自在后台继续跑（真·多进程互不干扰）。
    // 消息数组保留所有会话的消息，hero/草稿视图按 sessionId 过滤隐藏，切回即可见。
    requestSeqRef.current += 1;
    setMessages((current) => current.filter((m) => m.sessionId));
    setInput(firstMessage);
    setAttachments([]);
    setPendingRequests([]);
    setSessionId(undefined);
    sessionIdRef.current = undefined;
    pendingProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
    // 新开对话属于新的空会话：清掉上一会话残留的 goal 卡片，避免它串到新会话显示。
    setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
    goalSessionIdRef.current = undefined;
    setActiveView('chat');
  };

  // 新对话：不再弹窗。在项目内新建则继承该项目 workspace；
  // 全局新建则进入空态，由 composer 顶部的 workspace 选择器指定。
  const startNewChat = (projectId?: string) => {
    startProjectDraft(projectId);
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
    // 不要中止正在运行的流：它属于另一个会话，让它在后台继续更新自己的消息，
    // 切回时仍能看到半截回复并原地续流（displayedMessages 会按 sessionId 过滤）。
    // 只切换当前视图，各会话的流由 per-session 的 controller 独立管理。
    setActiveView('chat');
    setDraftMode(false);
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
        const running = current.filter((m) => m.status === 'running');
        if (running.length === 0) return loaded;
        const loadedIds = new Set(loaded.map((m) => m.id));
        const preserved = running.filter((m) => !loadedIds.has(m.id));
        return [...loaded, ...preserved];
      });
      setAttachments([]);
    } catch (error) {
      console.error('Failed to open session:', error);
    }
  };

  const deleteSession = async (sessionIdToDelete: string) => {
    try {
      await chatService.deleteSession(sessionIdToDelete);
      if (sessionIdRef.current === sessionIdToDelete) {
        setSessionId(undefined);
        sessionIdRef.current = undefined;
        // 保留其它会话仍在后台运行的消息
        setMessages((current) => current.filter((m) => m.sessionId && m.sessionId !== sessionIdToDelete));
        setInput('');
        setAttachments([]);
        setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
        setDraftMode(true);
      }
      abortStreamFor(sessionIdToDelete);
      requestSeqRef.current += 1;
      setPendingRequests([]);
      await refreshSessions();
      await refreshProjects();
    } catch (error) {
      console.error('Failed to delete session:', error);
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
      const sessionInProject = sessions.find((session) => session.project_id === projectId);
      await chatService.deleteProject(projectId);
      if (sessionInProject && sessionIdRef.current === sessionInProject.id) {
        setSessionId(undefined);
        sessionIdRef.current = undefined;
        // 保留其它会话仍在后台运行的消息
        setMessages((current) => current.filter((m) => m.sessionId && m.sessionId !== sessionInProject.id));
        setInput('');
        setAttachments([]);
        setGoal({ goalText: '', done: false, paused: false, todos: [], running: false, round: 0, progress: '', editingDraft: false });
      }
      if (activeProjectId === projectId) {
        setActiveProjectId(undefined);
        pendingProjectIdRef.current = undefined;
      }
      await refreshSessions();
      await refreshProjects();
    } catch (error) {
      console.error('Failed to delete project:', error);
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
      setMessages([]);
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
    if (command === '/skill') {
      void handleSkillSlash(message);
      return;
    }
    // Bare sub-command: "/<cmd>" where <cmd> is a known skill sub-command.
    const bareCmd = command ? (command.startsWith('/') ? command.slice(1) : command) : '';
    if (bareCmd && skillCommandIndex[bareCmd]) {
      void handleSubCommandSlash(skillCommandIndex[bareCmd], bareCmd, message);
      return;
    }
    setMessages((current) => [...current, createMessage('assistant', t('chat.command_help_text'), { status: 'done' })]);
  };

  /** /skill <name> [free-form prompt] -- load the skill body and send it with the prompt. */
  const handleSkillSlash = async (message: string) => {
    const rest = message.slice('/skill'.length).trim();
    const skillName = rest.split(/\s+/)[0] ?? '';
    const prompt = rest.slice(skillName.length).trim();
    if (!skillName) {
      setMessages((current) => [...current, createMessage('assistant', t('skills.slash_usage'), { status: 'done' })]);
      return;
    }
    try {
      const response = await chatService.getSkill(skillName);
      const skill = response.skill;
      if (!skill?.body || !skill.enabled) {
        setMessages((current) => [...current, createMessage('assistant', t('skills.slash_not_found').replace('{name}', skillName), { status: 'done' })]);
        return;
      }
      const injected = `${t('skills.slash_loaded').replace('{name}', skillName)}\n\n${skill.body}\n\n---\n${prompt}`;
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
      if (!skill?.body || !skill.enabled) {
        setMessages((current) => [
          ...current,
          createMessage('assistant', t('skills.slash_not_found').replace('{name}', `${pkg} / ${cmd}`), { status: 'done' }),
        ]);
        return;
      }
      const injected = `${t('skills.slash_loaded').replace('{name}', `${pkg} / ${cmd}`)}\n\n${skill.body}\n\n---\n${prompt}`;
      setInput('');
      void sendMessage({ message: injected });
    } catch (error) {
      setMessages((current) => [
        ...current,
        createMessage('assistant', t('skills.slash_not_found').replace('{name}', `${pkg} / ${cmd}`), { status: 'done' }),
      ]);
    }
  };

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
    try {
      await chatService.resumeGoal(targetSessionId, handleGoalResumeEventWithChat);
    } catch (error) {
      console.error('Failed to resume goal:', error);
      setGoal((current) => ({ ...current, running: false }));
    }
  };

  const toggleTodo = (index: number) => {
    const todo = goal.todos[index];
    if (!todo) return;
    const newStatus = todo.status === 'completed' ? 'pending' : 'completed';
    setGoal((current) => {
      const newTodos = current.todos.map((t, i) => (i === index ? { ...t, status: newStatus as 'completed' | 'pending' } : t));
      return { ...current, todos: newTodos };
    });
  };

  const draftEditGoal = () => {
    if (!sessionIdRef.current || !goal.goalText) return;
    const textarea = document.querySelector('textarea');
    const currentComposerVal = textarea?.value || goal.goalText;
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
    } else if (event.type === 'goal_paused') {
      setGoal((current) => ({ ...current, paused: true, running: false }));
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
      const content = event.content;
      if (content) {
        setMessages((current) => [
          ...current,
          createMessage('assistant', content, { status: event.stalled ? 'error' : 'done' }),
        ]);
      }
    } else if (event.type === 'goal_paused') {
      setGoal((current) => ({ ...current, paused: true, running: false }));
    } else if (event.type === 'goal_force') {
      setGoal((current) => ({ ...current, progress: `Force retry ${event.count}/3` }));
    } else if (event.type === 'delta') {
      // Append streaming deltas to the last assistant message in goal context
      setMessages((current) => {
        const last = [...current].reverse().find((m) => m.role === 'assistant' && !m.content.includes('目标已更新'));
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
  }));
  const showRuntimeNotice = activeView === 'chat' && (runtimeStatus !== 'ready' || !runtimeConfig);
  const titlebarSessionTitle = currentSessionTitle(messages, sessions, sessionId);
  const currentProvider = providers.find((provider) => provider.id === selectedModel);
  const sessionProjectId = sessions.find((session) => session.id === sessionId)?.project_id;
  const currentProjectId = activeProjectId || sessionProjectId;
  const activeProject = projects.find((project) => project.id === currentProjectId);
  const titlebarProjectName = activeProject?.name ?? t('sidebar.default_project');
  const activeProjectSessions = activeProject ? sessions.filter((session) => session.project_id === activeProject.id) : [];
  const showNewChatHero = activeView === 'chat' && !sessionId && messages.length === 0 && runtimeStatus === 'ready' && (!activeProject || draftMode);
  const showFirstRunStart = activeView === 'chat' && runtimeStatus === 'ready' && projects.length === 0 && sessions.length === 0 && !sessionId && messages.length === 0 && !draftMode;
  const showProjectSessionList = activeView === 'chat' && activeProject && !sessionId && messages.length === 0 && runtimeStatus === 'ready' && !draftMode;
  const workspaceOptions = projects.map((project) => ({
    id: project.id,
    name: project.name,
    path: project.workspace_path,
  }));
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
  }, [currentProjectId]);

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
          config={runtimeConfig}
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
          {...(goal.goalText && !goal.done ? { goalIndicatorSessionId: sessionId } : {})}
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
                      <p>{runtimeStatus === 'connecting' ? t('runtime.connecting_body') : t('runtime.error_body')}</p>
                      {runtimeStatus === 'error' && <p className="runtime-notice__retry">{t('runtime.retrying')}</p>}
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
                      onNewChat={startNewChat}
                      onOpenSession={openSession}
                      onDeleteSession={deleteSession}
                    />
                  ) : (
                    <>
                      <MessageList
                        messages={displayedMessages}
                        isThinking={isThinking}
                        onEditMessage={(messageId, content) => beginEditMessage(messageId, content)}
                        onRegenerateMessage={(messageId) => void handleRegenerateMessage(messageId)}
                        onRollbackMessage={(messageId) => void handleRollbackMessage(messageId)}
                      />
                    </>
                  )}
                  {!showFirstRunStart && !showProjectSessionList && (
                    <div className="workspace-composer-slot">
                      {goal.goalText && !editingGoalDraft && (
                        <GoalCard goal={goal} onPause={pauseGoal} onResume={resumeGoal} onDelete={deleteGoal} onDraftEdit={draftEditGoal} onToggleTodo={toggleTodo} recentToolNames={goal.recentToolNames ?? undefined} />
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
                        }}
                        branchStatus={branchStatus}
                        {...(activeProject?.workspace_path ? { workspaceLabel: activeProject.workspace_path } : {})}
                        showWorkspacePicker={showNewChatHero}
                        workspaceOptions={workspaceOptions}
                        {...(currentProjectId ? { activeWorkspaceId: currentProjectId } : {})}
                        onSelectWorkspace={selectDraftWorkspace}
                        onCreateWorkspace={createProject}
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
              ) : (
                <SettingsView
                  themeSettings={themeSettings}
                  autonomy={autonomy}
                  goalMaxRounds={goalMaxRounds}
                  onGoalMaxRoundsChange={changeGoalMaxRounds}
                  maxAttachmentMb={maxAttachmentMb}
                  onMaxAttachmentMbChange={changeMaxAttachmentMb}
                  onThemeSettingsChange={changeThemeSettings}
                  onAutonomyChange={setAutonomy}
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
              messageCount={messages.length}
              {...(currentProjectId ? { projectId: currentProjectId } : {})}
              {...(activeProject?.workspace_path ? { workspaceLabel: activeProject.workspace_path } : {})}
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
      />
      {rollbackTarget && (
        <RollbackDialog
          sessionId={rollbackTarget.sessionId}
          messageId={rollbackTarget.messageId}
          onClose={() => setRollbackTarget(null)}
          onConfirm={(withCode) => performRollback(withCode)}
        />
      )}
    </main>
  );
}

function currentSessionTitle(messages: ChatMessage[], sessions: SessionSummary[], sessionId?: string): string {
  const saved = sessionId ? sessions.find((session) => session.id === sessionId)?.title : undefined;
  if (saved) return saved;
  const firstUserMessage = messages.find((message) => message.role === 'user');
  if (!firstUserMessage?.content.trim()) return t('sidebar.new_chat');
  return firstUserMessage.content.trim().slice(0, 64);
}

export default App;
