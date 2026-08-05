import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ChatInput } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { ProvidersPanel } from './components/ProvidersPanel';
import { CreateProjectDialog } from './components/CreateProjectDialog';
import { ProjectSessionList } from './components/ProjectSessionList';
import { FirstRunStart } from './components/FirstRunStart';
import { NewSessionDialog } from './components/NewSessionDialog';
import { SettingsView } from './components/settings/SettingsView';
import { WorkspaceTitlebar } from './components/WorkspaceTitlebar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { WorkspaceBottomPanel, type BottomPanelView } from './components/WorkspaceBottomPanel';
import { WorkspaceInspector } from './components/WorkspaceInspector';
import { ChangesPanel } from './components/ChangesPanel';
import { RollbackDialog } from './components/RollbackDialog';
import { getLanguage, initLanguage, t, translateError } from './lib/i18n';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { chatService } from './services/chatService';
import type { AccessMode, AppView, ApprovalDecisionPayload, ApprovalOption, ChatMessage, ComposerAttachment, CreateProjectRequest, MessagePart, PendingRequest, ProjectEntry, ProviderEntry, RuntimeConfig, SessionSummary, StreamEvent, WorkMode } from './types';
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
  const [sidebarWidth, setSidebarWidth] = useState(276);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false);
  const [bottomPanelView, setBottomPanelView] = useState<BottomPanelView>('terminal');
  const [changesPanelOpen, setChangesPanelOpen] = useState(false);
  const [changesRefreshKey, setChangesRefreshKey] = useState(0);
  const [accessMode, setAccessMode] = useState<AccessMode>('default');
  const [selectedModel, setSelectedModel] = useState('');
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [pendingRequests, setPendingRequests] = useState<PendingRequest[]>([]);
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const pendingProjectIdRef = useRef<string | undefined>(undefined);
  const activeAssistantMessageIdRef = useRef<string | undefined>(undefined);
  const streamStartAtRef = useRef<number | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
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
        streamStartAt: Date.now(),
        id: assistantMessageId,
        status: 'running',
        access_mode: accessMode,
        provider: requestProvider,
        model: requestModel,
      }),
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    activeAssistantMessageIdRef.current = assistantMessageId;
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let streamStartAt = Date.now();
    streamStartAtRef.current = streamStartAt;

    const handleEvent = (event: StreamEvent) => {
      if (requestId !== requestSeqRef.current) return;
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
        localParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
        const base: PendingRequest = {
          approval_id: event.approval_id,
          kind: event.type === 'approval_required' ? 'command' : event.type === 'question_required' ? 'question' : 'plan',
          session_id: sessionIdValue,
          approval_status: event.approval_status,
          messageId: assistantMessageId,
        };
        const pending: PendingRequest =
          event.type === 'approval_required'
            ? { ...base, command: event.command, cwd: event.cwd }
            : event.type === 'question_required'
              ? {
                  ...base,
                  ...(event.question !== undefined ? { question: event.question } : {}),
                  ...(event.header !== undefined ? { header: event.header } : {}),
                  ...(event.options !== undefined ? { options: event.options } : {}),
                  ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
                }
              : { ...base, plan: event.plan };
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] }
              : item,
          ),
        );
      } else if (event.type === 'done') {
        if (event.session_id) {
          setSessionId(event.session_id);
          sessionIdRef.current = event.session_id;
        }
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) {
          localParts = event.parts;
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() }
              : item,
          ),
        );
      } else if (event.type === 'error') {
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
      await chatService.sendMessageStream(
        {
          message,
          mode: runtimeConfig?.default_mode ?? 'single',
          language: getLanguage(),
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
      setChangesRefreshKey((value) => value + 1);
      _generateSessionTitleIfNeeded(message, sessionIdRef.current);
    } catch (error) {
      if (requestId !== requestSeqRef.current) return;
      console.error('Failed to stream message:', error);
      if ((error as Error).name === 'AbortError') {
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
            ? { ...item, content: item.content || t('chat.stopped'), status: 'stopped', streamStartAt: streamStartAtRef.current ?? Date.now(), streamEndAt: Date.now() }
            : item,
        ),
      );
    }
    requestSeqRef.current += 1;
    abortRef.current = null;
    activeAssistantMessageIdRef.current = undefined;
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
    setIsThinking(true);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setMessages((current) => {
      const index = current.findIndex((m) => m.id === messageId);
      if (index < 0) return current;
      const truncated = current.slice(0, index + 1);
      return [
        ...truncated.map((m) => (m.id === messageId ? { ...m, content: trimmed, status: 'done' as const } : m)),
        createMessage('assistant', '', {
        streamStartAt: Date.now(),
          id: assistantMessageId,
          status: 'running',
            access_mode: accessMode,
        }),
      ];
    });
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let streamStartAt = Date.now();
    streamStartAtRef.current = streamStartAt;
    const controller = new AbortController();
    abortRef.current = controller;
    const handleEvent = (event: StreamEvent) => {
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
        localParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
        const pending: PendingRequest = {
          approval_id: event.approval_id,
          kind: event.type === 'approval_required' ? 'command' : event.type === 'question_required' ? 'question' : 'plan',
          session_id: currentSessionId,
          approval_status: event.approval_status,
          messageId: assistantMessageId,
          ...(event.type === 'approval_required' ? { command: event.command, cwd: event.cwd } : {}),
          ...(event.type === 'question_required' && event.question !== undefined ? { question: event.question } : {}),
          ...(event.type === 'question_required' && event.header !== undefined ? { header: event.header } : {}),
          ...(event.type === 'question_required' && event.options !== undefined ? { options: event.options } : {}),
          ...(event.type === 'question_required' && event.multiple !== undefined ? { multiple: event.multiple } : {}),
          ...(event.type === 'plan_required' ? { plan: event.plan } : {}),
        };
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
      } else if (event.type === 'error') {
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
        accessMode,
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
      setIsThinking(false);
      abortRef.current = null;
      requestSeqRef.current += 1;
    }
  };

  const handleRegenerateMessage = async (messageId: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || isThinking) return;
    setIsThinking(true);
    const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setMessages((current) => {
      const index = current.findIndex((m) => m.id === messageId);
      if (index < 0) return current;
      const truncated = current.slice(0, index);
      return [
        ...truncated,
        createMessage('assistant', '', {
        streamStartAt: Date.now(),
          id: assistantMessageId,
          status: 'running',
            access_mode: accessMode,
        }),
      ];
    });
    let streamedContent = '';
    let localParts: MessagePart[] = [];
    let streamStartAt = Date.now();
    streamStartAtRef.current = streamStartAt;
    const controller = new AbortController();
    abortRef.current = controller;
    const handleEvent = (event: StreamEvent) => {
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
        localParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
        const pending: PendingRequest = {
          approval_id: event.approval_id,
          kind: event.type === 'approval_required' ? 'command' : event.type === 'question_required' ? 'question' : 'plan',
          session_id: currentSessionId,
          approval_status: event.approval_status,
          messageId: assistantMessageId,
          ...(event.type === 'approval_required' ? { command: event.command, cwd: event.cwd } : {}),
          ...(event.type === 'question_required' && event.question !== undefined ? { question: event.question } : {}),
          ...(event.type === 'question_required' && event.header !== undefined ? { header: event.header } : {}),
          ...(event.type === 'question_required' && event.options !== undefined ? { options: event.options } : {}),
          ...(event.type === 'question_required' && event.multiple !== undefined ? { multiple: event.multiple } : {}),
          ...(event.type === 'plan_required' ? { plan: event.plan } : {}),
        };
        setPendingRequests((current) => [...current, pending]);
        if (event.type === 'plan_required') {
          localParts.push({ type: 'plan', content: event.plan });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: t('chat.waiting_resolution'), status: 'done', parts: [...localParts] } : item,
          ),
        );
      } else if (event.type === 'done') {
        streamedContent = event.content || streamedContent;
        if (event.parts && event.parts.length > 0) localParts = event.parts;
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId ? { ...item, content: streamedContent, status: 'done', parts: [...localParts], streamEndAt: Date.now() } : item,
          ),
        );
      } else if (event.type === 'error') {
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
      setIsThinking(false);
      abortRef.current = null;
      requestSeqRef.current += 1;
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

  const _generateSessionTitleIfNeeded = (firstMessageContent: string, sessionSessionId?: string) => {
    if (!sessionSessionId) return;
    const userMessages = messages.filter((m) => m.role === 'user');
    const currentSession = sessions.find((s) => s.id === sessionSessionId);
    if (!currentSession || currentSession.title !== '新会话') return;
    if (userMessages.length > 1) return;
    chatService.generateTitle(sessionSessionId, firstMessageContent).then(
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
        ...chained.map((event): PendingRequest => pendingFromEvent(event, request.session_id, request.messageId)),
      ]);
      if (response.resumed === false && !response.resume_id) {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== request.messageId) return item;
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
    const controller = new AbortController();
    const resumeSessionId = request.session_id || sessionIdRef.current || '';
    let resumeContent = '';
    let resumeParts: MessagePart[] = [];
    try {
      await chatService.subscribeApprovalEvents(
        resumeId,
        (event) => {
          if (event.type === 'done') {
            resumeContent = event.content || resumeContent;
            if (event.parts && event.parts.length > 0) {
              resumeParts = event.parts;
            }
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'done' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'delta') {
            resumeContent += event.content;
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'reasoning_delta') {
            const last = resumeParts[resumeParts.length - 1];
            if (last && last.type === 'reasoning') {
              last.content = event.content;
            } else {
              resumeParts.push({ type: 'reasoning', content: event.content });
            }
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'tool_start') {
            resumeParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'tool_delta') {
            const tp = resumeParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
            if (tp) tp.input = (tp.input || '') + (event.input || '');
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'tool_end') {
            const tp = resumeParts.find((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === event.id);
            if (tp) {
              tp.status = event.status === 'success' ? 'success' : 'error';
              if (event.output) tp.output = event.output;
            }
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'plan_start') {
            resumeParts.push({ type: 'plan', content: '' });
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'plan_delta') {
            const pp = resumeParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
            if (pp) pp.content += event.content;
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'plan_end') {
            const pp = resumeParts.find((p): p is Extract<MessagePart, { type: 'plan' }> => p.type === 'plan');
            if (pp && event.content) pp.content = event.content;
            setMessages((current) =>
              current.map((item) =>
                item.id !== request.messageId ? item : { ...item, content: resumeContent, status: 'running' as const, parts: [...resumeParts] },
              ),
            );
          } else if (event.type === 'stage') {
            setMessages((current) =>
              current.map((item) =>
                item.id === request.messageId
                  ? { ...item, content: `${t('chat.waiting_resolution')} · ${event.name}`, status: 'running' as const }
                  : item,
              ),
            );
          } else if (event.type === 'approval_required' || event.type === 'question_required' || event.type === 'plan_required') {
            setPendingRequests((current) => {
              if (current.some((item) => item.approval_id === event.approval_id)) return current;
              return [...current, pendingFromEvent(event, resumeSessionId, request.messageId)];
            });
          } else if (event.type === 'error') {
            setMessages((current) =>
              current.map((item) =>
                item.id === request.messageId
                  ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
                  : item,
              ),
            );
          }
        },
        controller.signal,
      );
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        console.error('Approval event stream failed:', error);
      }
    }
  };

  const pendingFromEvent = (
    event: Extract<StreamEvent, { type: 'approval_required' } | { type: 'question_required' } | { type: 'plan_required' }>,
    sessionId: string,
    messageId: string,
  ): PendingRequest => {
    const base: PendingRequest = {
      approval_id: event.approval_id,
      kind: event.type === 'approval_required' ? 'command' : event.type === 'question_required' ? 'question' : 'plan',
      session_id: event.session_id ?? sessionId,
      approval_status: event.approval_status,
      messageId,
    };
    return event.type === 'approval_required'
      ? { ...base, command: event.command, cwd: event.cwd }
      : event.type === 'question_required'
        ? {
            ...base,
            ...(event.question !== undefined ? { question: event.question } : {}),
            ...(event.header !== undefined ? { header: event.header } : {}),
            ...(event.options !== undefined ? { options: event.options } : {}),
            ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
          }
        : { ...base, plan: event.plan };
  };

  const dismissPendingRequest = (request: PendingRequest) => {
    void resolvePendingRequest(request, { type: 'reject' });
  };

  const isResolving = () => resolvingRef.current;

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

  const restorePendingForSession = async (targetSessionId: string) => {
    try {
      const response = await chatService.listCommandApprovals();
      const restored: PendingRequest[] = [];
      for (const approval of response.approvals) {
        if (approval.status !== 'pending') continue;
        const context = approval.context;
        if (!context || context.session_id !== targetSessionId) continue;
        const kind = context.kind === 'question' ? 'question' : 'command';
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
        } else {
          restored.push({
            ...base,
            command: Array.isArray(approval.command) ? approval.command : [],
            ...(approval.cwd ? { cwd: approval.cwd } : {}),
          });
        }
      }
      if (restored.length === 0) return;
      setPendingRequests((current) => [
        ...current.filter((item) => item.session_id !== targetSessionId),
        ...restored,
      ]);
    } catch (error) {
      console.error('Failed to restore pending approvals:', error);
    }
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
          ...(record.parts?.length ? { parts: record.parts as MessagePart[] } : {}),
          timestamp: new Date(record.created_at).getTime(),
        }),
      );
      setSessionId(sessionIdToOpen);
      sessionIdRef.current = sessionIdToOpen;
      pendingProjectIdRef.current = undefined;
      setPendingRequests((current) => current.filter((item) => item.session_id !== sessionIdToOpen));
      void restorePendingForSession(sessionIdToOpen);
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
  const titlebarProjectName = activeProject?.name ?? t('sidebar.default_project');
  const activeProjectSessions = activeProject ? sessions.filter((session) => session.project_id === activeProject.id) : [];
  const showFirstRunStart = activeView === 'chat' && runtimeStatus === 'ready' && projects.length === 0 && sessions.length === 0 && !sessionId && messages.length === 0;
  const showProjectSessionList = activeView === 'chat' && activeProject && !sessionId && messages.length === 0 && runtimeStatus === 'ready';
  const currentSessionPending = sessionId
    ? pendingRequests.filter((item) => item.session_id === sessionId)
    : [];

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
        projectName={titlebarProjectName}
        sidebarCollapsed={sidebarCollapsed}
        rightSidebarOpen={rightSidebarOpen}
        bottomPanelOpen={bottomPanelOpen}
        changesPanelOpen={changesPanelOpen}
        canEditSession={Boolean(sessionId)}
        pendingCount={currentSessionPending.length}
        onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
        onToggleRightSidebar={() => setRightSidebarOpen((value) => !value)}
        onToggleBottomPanel={() => setBottomPanelOpen((value) => !value)}
        onToggleChangesPanel={() => setChangesPanelOpen((value) => !value)}
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
                    <FirstRunStart onCreateProject={createProject} onNewSession={() => openNewSessionDialog()} />
                  ) : showProjectSessionList ? (
                    <ProjectSessionList
                      project={activeProject}
                      sessions={activeProjectSessions}
                      onNewChat={openNewSessionDialog}
                      onOpenSession={openSession}
                      onDeleteSession={deleteSession}
                    />
                  ) : (
                    <>
                      <MessageList
                        messages={messages}
                        isThinking={isThinking}
                        onEditMessage={(messageId, content) => beginEditMessage(messageId, content)}
                        onRegenerateMessage={(messageId) => void handleRegenerateMessage(messageId)}
                        onRollbackMessage={(messageId) => void handleRollbackMessage(messageId)}
                      />
                    </>
                  )}
                  {!showFirstRunStart && !showProjectSessionList && (
                    currentSessionPending.length > 0 ? (
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
                        value={editingMessage ? editDraft : input}
                        disabled={isThinking || runtimeStatus === 'connecting'}
                        isThinking={isThinking}
                        accessMode={accessMode}
                        selectedModel={selectedModel}
                        attachments={attachments}
                        modelOptions={modelOptions}
                        editing={Boolean(editingMessage)}
                        onChange={editingMessage ? setEditDraft : setInput}
                        onSend={editingMessage ? () => void commitEditMessage(editingMessage.id, editDraft) : sendMessage}
                        onStop={stopMessage}
                        onAccessModeChange={setAccessMode}
                        onModelChange={(providerId) => void changeSelectedModel(providerId)}
                        onAttachmentsChange={setAttachments}
                        onCancelEdit={() => {
                          setEditingMessage(null);
                          setEditDraft('');
                        }}
                      />
                    )
                  )}
                </>
              ) : activeView === 'providers' ? (
                <ProvidersPanel onProviderChange={refreshProviders} />
              ) : (
                <SettingsView
                  themeSettings={themeSettings}
                  accessMode={accessMode}
                  onThemeSettingsChange={changeThemeSettings}
                  onAccessModeChange={setAccessMode}
                  onLanguageChange={() => setLanguageVersion((value) => value + 1)}
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
                accessMode={accessMode}
                attachmentCount={attachments.length}
                messageCount={messages.length}
              />
            )}
            {changesPanelOpen && (
              <ChangesPanel
                open={changesPanelOpen}
                onClose={() => setChangesPanelOpen(false)}
                onRefreshKey={changesRefreshKey}
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
