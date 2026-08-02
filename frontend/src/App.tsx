import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ChatInput } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { ProvidersPanel } from './components/ProvidersPanel';
import { CreateProjectDialog } from './components/CreateProjectDialog';
import { EmptyProjectStart } from './components/EmptyProjectStart';
import { FirstRunStart } from './components/FirstRunStart';
import { NewSessionDialog } from './components/NewSessionDialog';
import { SettingsView } from './components/settings/SettingsView';
import { WorkspaceTitlebar } from './components/WorkspaceTitlebar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { WorkspaceBottomPanel, type BottomPanelView } from './components/WorkspaceBottomPanel';
import { WorkspaceInspector } from './components/WorkspaceInspector';
import { getLanguage, initLanguage, t, translateError } from './lib/i18n';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { chatService } from './services/chatService';
import type { AccessMode, AppView, ApprovalDecisionPayload, ChatMessage, ComposerAttachment, CreateProjectRequest, PendingRequest, ProjectEntry, ProviderEntry, RuntimeConfig, SessionSummary, StreamEvent, WorkMode } from './types';
import './App.css';

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
  const [isThinking, setIsThinking] = useState(false);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<'connecting' | 'ready' | 'error'>('connecting');
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | undefined>();
  const [createProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
  const [newSessionDialogOpen, setNewSessionDialogOpen] = useState(false);
  const [newSessionInitialProjectId, setNewSessionInitialProjectId] = useState<string | undefined>();
  const [newSessionInitialMessage, setNewSessionInitialMessage] = useState('');
  const [languageVersion, setLanguageVersion] = useState(0);
  const [themeSettings, setThemeSettingsState] = useState<ThemeSettings>(() => getThemeSettings());
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => Number(localStorage.getItem('cw.sidebarWidth')) || 276);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false);
  const [bottomPanelView, setBottomPanelView] = useState<BottomPanelView>('terminal');
  const [workMode, setWorkMode] = useState<WorkMode>('build');
  const [accessMode, setAccessMode] = useState<AccessMode>('default');
  const [selectedModel, setSelectedModel] = useState('');
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [pendingRequests, setPendingRequests] = useState<PendingRequest[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const pendingProjectIdRef = useRef<string | undefined>(undefined);
  const activeAssistantMessageIdRef = useRef<string | undefined>(undefined);
  const resumeAttemptedRef = useRef(false);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      try {
        localStorage.setItem('cw.last-session', sessionId);
      } catch {
        // ignore
      }
    } else {
      try {
        localStorage.removeItem('cw.last-session');
      } catch {
        // ignore
      }
    }
  }, [sessionId]);

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
      setLanguageVersion((value) => value + 1);

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
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  useEffect(() => {
    document.title = t('app.title');
  }, [languageVersion]);

  useEffect(() => {
    if (sessionId || messages.length > 0 || projects.length === 0 || sessions.length > 0) return;
    if (activeProjectId && projects.some((project) => project.id === activeProjectId)) return;
    const firstProject = projects[0];
    if (!firstProject) return;
    pendingProjectIdRef.current = firstProject.id;
    setActiveProjectId(firstProject.id);
  }, [activeProjectId, messages.length, projects, sessionId, sessions.length]);

  useEffect(() => {
    if (resumeAttemptedRef.current) return;
    if (sessionId && messages.length > 0) {
      resumeAttemptedRef.current = true;
      return;
    }
    if (sessions.length === 0) return;
    let lastId = '';
    try {
      lastId = localStorage.getItem('cw.last-session') || '';
    } catch {
      return;
    }
    if (!lastId || sessions.every((s) => s.id !== lastId)) {
      const latest = sessions[0];
      if (latest) {
        openSession(latest.id);
      }
    } else {
      openSession(lastId);
    }
    resumeAttemptedRef.current = true;
  }, [sessionId, sessions.length, activeProjectId]);

  const sendMessage = async (override?: { message: string; projectId?: string }) => {
    const typedMessage = (override?.message ?? input).trim();
    if (isThinking) return;

    if (!typedMessage && attachments.length === 0) return;

    if (typedMessage.startsWith('/')) {
      handleSlashCommand(typedMessage);
      return;
    }

    const requestProjectId = override?.projectId || pendingProjectIdRef.current;

    if (!sessionIdRef.current && !requestProjectId) {
      openNewSessionDialog(undefined, typedMessage);
      setInput('');
      return;
    }

    const message = typedMessage || t('chat.attachment_only_message');
    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    const selectedProvider = providers.find((provider) => provider.id === selectedModel);
    const requestAttachments = attachments;
    const requestModel = selectedProvider?.model ?? runtimeConfig?.selected_model ?? '';
    const requestProvider = selectedProvider?.name ?? runtimeConfig?.agent_provider ?? '';
    const requestSessionId = sessionIdRef.current;

    setMessages((current) => [
      ...current,
      createMessage('user', message, {
        status: 'done',
        work_mode: workMode,
        access_mode: accessMode,
        provider: requestProvider,
        model: requestModel,
        attachments: requestAttachments,
      }),
    ]);
    setInput('');
    setIsThinking(true);

    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setMessages((current) => [
      ...current,
      createMessage('assistant', '', {
        id: assistantMessageId,
        status: 'running',
        work_mode: workMode,
        access_mode: accessMode,
        provider: requestProvider,
        model: requestModel,
      }),
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    activeAssistantMessageIdRef.current = assistantMessageId;
    let streamedContent = '';

    const handleEvent = (event: StreamEvent) => {
      if (requestId !== requestSeqRef.current) return;
      if (event.type === 'start') {
        if (event.session_id && !sessionIdRef.current) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
      } else if (event.type === 'delta') {
        streamedContent += event.content;
        setMessages((current) =>
          current.map((item) => (item.id === assistantMessageId ? { ...item, content: streamedContent } : item)),
        );
      } else if (event.type === 'approval_required' || event.type === 'question_required') {
        const sessionIdValue = event.session_id ?? sessionIdRef.current ?? '';
        const base: PendingRequest = {
          approval_id: event.approval_id,
          kind: event.type === 'approval_required' ? 'command' : 'question',
          session_id: sessionIdValue,
          approval_status: event.approval_status,
          messageId: assistantMessageId,
        };
        const pending: PendingRequest =
          event.type === 'approval_required'
            ? { ...base, command: event.command, cwd: event.cwd }
            : {
                ...base,
                ...(event.question !== undefined ? { question: event.question } : {}),
                ...(event.header !== undefined ? { header: event.header } : {}),
                ...(event.options !== undefined ? { options: event.options } : {}),
                ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
              };
        setPendingRequests((current) => [...current, pending]);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: t('chat.waiting_resolution'), status: 'done' }
              : item,
          ),
        );
      } else if (event.type === 'done') {
        if (event.session_id) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
        streamedContent = event.content || streamedContent;
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent, status: 'done' }
              : item,
          ),
        );
      } else if (event.type === 'error') {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error' }
              : item,
          ),
        );
      }
    };

    try {
      await chatService.sendMessageStream(
        {
          message,
          mode: runtimeConfig?.default_mode ?? 'single',
          language: getLanguage(),
          work_mode: workMode,
          access_mode: accessMode,
          ...(selectedProvider
            ? {
                provider_id: selectedProvider.id,
                model: selectedProvider.model,
              }
            : {}),
          ...(requestAttachments.length > 0 ? { attachments: requestAttachments } : {}),
          ...(requestSessionId ? { session_id: requestSessionId } : {}),
          ...(requestProjectId ? { project_id: requestProjectId } : {}),
        },
        handleEvent,
        controller.signal,
      );
      if (requestId !== requestSeqRef.current) return;
      setAttachments([]);
      setRuntimeStatus('ready');
      await refreshSessions();
      await refreshProjects();
    } catch (error) {
      if (requestId !== requestSeqRef.current) return;
      console.error('Failed to stream message:', error);
      if ((error as Error).name === 'AbortError') {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent || t('chat.stopped'), status: 'stopped' }
              : item,
          ),
        );
      } else {
        setRuntimeStatus('error');
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: translateError(error) || t('chat.backend_unreachable'), status: 'error' }
              : item,
          ),
        );
      }
    } finally {
      if (requestId === requestSeqRef.current) {
        setIsThinking(false);
        abortRef.current = null;
        activeAssistantMessageIdRef.current = undefined;
      }
    }
  };

  const stopMessage = () => {
    const assistantMessageId = activeAssistantMessageIdRef.current;
    abortRef.current?.abort();
    setIsThinking(false);
    if (assistantMessageId) {
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId && item.status === 'running'
            ? { ...item, content: item.content || t('chat.stopped'), status: 'stopped' }
            : item,
        ),
      );
    }
    requestSeqRef.current += 1;
    abortRef.current = null;
    activeAssistantMessageIdRef.current = undefined;
  };

  const resolvePendingRequest = async (request: PendingRequest, decision: ApprovalDecisionPayload) => {
    setPendingRequests((current) =>
      current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: true } : item)),
    );
    try {
      const response = await chatService.resolveCommandApproval(request.approval_id, decision);
      const done = response.events?.findLast((event) => event.type === 'done');
      const chained = (response.events ?? []).filter(
        (event): event is Extract<StreamEvent, { type: 'approval_required' } | { type: 'question_required' }> =>
          event.type === 'approval_required' || event.type === 'question_required',
      );
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== request.messageId) return item;
          if (done?.type === 'done') return { ...item, content: done.content || item.content, status: 'done' };
          if (response.resumed === false) return item;
          return item;
        }),
      );
      setPendingRequests((current) => [
        ...current.filter((item) => item.approval_id !== request.approval_id),
        ...chained.map((event): PendingRequest => {
          const base: PendingRequest = {
            approval_id: event.approval_id,
            kind: event.type === 'approval_required' ? 'command' : 'question',
            session_id: event.session_id ?? request.session_id,
            approval_status: event.approval_status,
            messageId: request.messageId,
          };
          return event.type === 'approval_required'
            ? { ...base, command: event.command, cwd: event.cwd }
            : {
                ...base,
                ...(event.question !== undefined ? { question: event.question } : {}),
                ...(event.header !== undefined ? { header: event.header } : {}),
                ...(event.options !== undefined ? { options: event.options } : {}),
                ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
              };
        }),
      ]);
    } catch (error) {
      console.error('Failed to resolve approval:', error);
      setPendingRequests((current) =>
        current.map((item) => (item.approval_id === request.approval_id ? { ...item, resolving: false } : item)),
      );
    }
  };

  const dismissPendingRequest = (request: PendingRequest) => {
    setPendingRequests((current) => current.filter((item) => item.approval_id !== request.approval_id));
  };

  const startProjectDraft = (projectId: string, firstMessage = '') => {
    abortRef.current?.abort();
    requestSeqRef.current += 1;
    activeAssistantMessageIdRef.current = undefined;
    setMessages([]);
    setInput(firstMessage);
    setAttachments([]);
    setPendingRequests([]);
    setSessionId(undefined);
    sessionIdRef.current = undefined;
    pendingProjectIdRef.current = projectId;
    setActiveProjectId(projectId);
    setIsThinking(false);
    setActiveView('chat');
  };

  const openNewSessionDialog = (projectId?: string, initialMessage = '') => {
    setNewSessionInitialProjectId(projectId || activeProjectId);
    setNewSessionInitialMessage(initialMessage);
    setNewSessionDialogOpen(true);
  };

  const openProject = (projectId: string) => {
    startProjectDraft(projectId);
  };

  const startProjectSession = (projectId: string, firstMessage: string) => {
    startProjectDraft(projectId);
    if (firstMessage.trim()) {
      void sendMessage({ message: firstMessage, projectId });
    }
  };

  const pickWorkspaceDirectory = async () => {
    return chatService.openDirectoryPicker({ title: t('project_dialog.pick_workspace') });
  };

  const createProjectWithWorkspace = async (payload: CreateProjectRequest): Promise<ProjectEntry> => {
    const response = await chatService.createProject(payload);
    await refreshProjects();
    setActiveProjectId(response.project.id);
    return response.project;
  };

  const openSession = async (sessionIdToOpen: string) => {
    abortRef.current?.abort();
    requestSeqRef.current += 1;
    activeAssistantMessageIdRef.current = undefined;
    setIsThinking(false);
    setActiveView('chat');
    try {
      const response = await chatService.getSession(sessionIdToOpen);
      const records = response.session.messages ?? [];
      const loaded = records.map((record, index) =>
        createMessage(record.role as ChatMessage['role'], record.content, {
          id: record.id || `${record.role}-${index}-${record.id}`,
          status: 'done',
          ...(record.mode ? { work_mode: record.mode as WorkMode } : {}),
          ...(record.provider ? { provider: record.provider } : {}),
          ...(record.model ? { model: record.model } : {}),
          ...(record.attachments?.length ? { attachments: record.attachments } : {}),
          timestamp: new Date(record.created_at).getTime(),
        }),
      );
      setSessionId(sessionIdToOpen);
      sessionIdRef.current = sessionIdToOpen;
      pendingProjectIdRef.current = undefined;
      setPendingRequests([]);
      setActiveProjectId(response.session.project_id || undefined);
      setMessages(loaded);
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
        setMessages([]);
        setInput('');
        setAttachments([]);
        pendingProjectIdRef.current = activeProjectId;
        try {
          localStorage.removeItem('cw.last-session');
        } catch {
          // ignore
        }
      }
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
        setMessages([]);
        setInput('');
        setAttachments([]);
        try {
          localStorage.removeItem('cw.last-session');
        } catch {
          // ignore
        }
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
    if (command === '/clear') {
      setMessages([]);
      setAttachments([]);
      return;
    }
    if (command === '/new') {
      openNewSessionDialog();
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
    if (command === '/plan') {
      setWorkMode('plan');
      return;
    }
    if (command === '/build') {
      setWorkMode('build');
      return;
    }
    setMessages((current) => [...current, createMessage('assistant', t('chat.command_help_text'), { status: 'done' })]);
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
  const activeProjectSessions = activeProject ? sessions.filter((session) => session.project_id === activeProject.id) : [];
  const showFirstRunStart = activeView === 'chat' && projects.length === 0 && sessions.length === 0 && !sessionId && messages.length === 0;
  const showEmptyProjectStart = activeView === 'chat' && activeProject && !sessionId && messages.length === 0 && activeProjectSessions.length === 0;

  const changeThemeSettings = (nextSettings: ThemeSettings) => {
    setThemeSettingsState(nextSettings);
    setThemeSettings(nextSettings);
  };

  return (
    <main
      className={`app-shell ${sidebarCollapsed ? 'app-shell--sidebar-collapsed' : ''} ${sidebarResizing ? 'app-shell--resizing' : ''}`}
      key={languageVersion}
      style={{ '--sidebar-width': `${sidebarWidth}px` } as CSSProperties}
    >
      <WorkspaceTitlebar
        status={runtimeStatus}
        activeView={activeView}
        sessionTitle={titlebarSessionTitle}
        sidebarCollapsed={sidebarCollapsed}
        rightSidebarOpen={rightSidebarOpen}
        bottomPanelOpen={bottomPanelOpen}
        canEditSession={Boolean(sessionId)}
        onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
        onToggleRightSidebar={() => setRightSidebarOpen((value) => !value)}
        onToggleBottomPanel={() => setBottomPanelOpen((value) => !value)}
        onRenameSession={renameCurrentSession}
        onDeleteSession={deleteCurrentSession}
      />
      <div className="app-body">
        <WorkspaceSidebar
          config={runtimeConfig}
          sessions={sessions}
          projects={projects}
          activeView={activeView}
          collapsed={sidebarCollapsed}
          {...(currentProjectId ? { activeProjectId: currentProjectId } : {})}
          onResizeStart={() => setSidebarResizing(true)}
          onResizeEnd={() => setSidebarResizing(false)}
          onResizeWidth={(width) => {
            setSidebarWidth(width);
            localStorage.setItem('cw.sidebarWidth', String(width));
          }}
          {...(sessionId ? { activeSessionId: sessionId } : {})}
          onViewChange={setActiveView}
          onNewChat={openNewSessionDialog}
          onOpenProject={openProject}
          onOpenSession={openSession}
          onDeleteSession={deleteSession}
          onCreateProject={createProject}
          onRenameProject={renameProject}
          onDeleteProject={deleteProject}
        />
        <section className={`workspace-frame ${rightSidebarOpen ? 'workspace-frame--right-open' : ''} ${bottomPanelOpen ? 'workspace-frame--bottom-open' : ''}`}>
          <div className="workspace-upper">
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
                    <FirstRunStart onCreateProject={createProject} onNewSession={() => openNewSessionDialog()} />
                  ) : showEmptyProjectStart ? (
                    <EmptyProjectStart project={activeProject} onStart={() => openNewSessionDialog(activeProject.id)} />
                  ) : (
                    <>
                      <MessageList messages={messages} />
                      <div ref={bottomRef} />
                    </>
                  )}
                  {!showFirstRunStart && !showEmptyProjectStart && (
                    <div className="workspace-dock-area">
                      <PendingDocks
                        requests={pendingRequests}
                        onResolve={(request, decision) => void resolvePendingRequest(request, decision)}
                        onDismiss={dismissPendingRequest}
                      />
                      <ChatInput
                        value={input}
                        disabled={isThinking || runtimeStatus === 'connecting' || pendingRequests.length > 0}
                        isThinking={isThinking}
                        workMode={workMode}
                        accessMode={accessMode}
                        selectedModel={selectedModel}
                        attachments={attachments}
                        modelOptions={modelOptions}
                        onChange={setInput}
                        onSend={sendMessage}
                        onStop={stopMessage}
                        onWorkModeChange={setWorkMode}
                        onAccessModeChange={setAccessMode}
                        onModelChange={(providerId) => void changeSelectedModel(providerId)}
                        onAttachmentsChange={setAttachments}
                      />
                    </div>
                  )}
                </>
              ) : activeView === 'providers' ? (
                <ProvidersPanel onProviderChange={refreshProviders} />
              ) : (
                <SettingsView
                  themeSettings={themeSettings}
                  workMode={workMode}
                  accessMode={accessMode}
                  onThemeSettingsChange={changeThemeSettings}
                  onWorkModeChange={setWorkMode}
                  onAccessModeChange={setAccessMode}
                  onLanguageChange={() => setLanguageVersion((value) => value + 1)}
                  onClose={() => setActiveView('chat')}
                  supportsVibrancy={window.electronAPI?.platform === 'darwin'}
                />
              )}
            </section>
            {rightSidebarOpen && (
              <WorkspaceInspector
                sessionTitle={titlebarSessionTitle}
                projectName={activeProject?.name ?? t('sidebar.default_project')}
                modelName={currentProvider?.model ?? runtimeConfig?.selected_model ?? t('chat.model_unselected')}
                providerName={currentProvider?.name ?? runtimeConfig?.agent_provider ?? t('chat.model_unselected')}
                workMode={workMode}
                accessMode={accessMode}
                attachmentCount={attachments.length}
                messageCount={messages.length}
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
      <NewSessionDialog
        open={newSessionDialogOpen}
        projects={projects}
        {...(newSessionInitialProjectId ? { initialProjectId: newSessionInitialProjectId } : {})}
        {...(newSessionInitialMessage ? { initialMessage: newSessionInitialMessage } : {})}
        onClose={() => setNewSessionDialogOpen(false)}
        onPickWorkspace={pickWorkspaceDirectory}
        onCreateProject={createProjectWithWorkspace}
        onStart={startProjectSession}
      />
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
