import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ChatInput, extractSessionIds } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { PendingDocks } from './components/PendingDocks';
import { ProvidersPanel } from './components/ProvidersPanel';
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
import type { AccessMode, AppView, ApprovalDecisionPayload, ApprovalOption, ChatMessage, ComposerAttachment, CreateProjectRequest, MessagePart, PendingRequest, ProjectEntry, ProviderEntry, RuntimeConfig, SessionReference, SessionSummary, StreamEvent, WorkMode } from './types';
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
  const [draftMode, setDraftMode] = useState(false);
  const _language = useLanguage();
  const [themeSettings, setThemeSettingsState] = useState<ThemeSettings>(() => getThemeSettings());
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(276);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [bottomPanelOpen, setBottomPanelOpen] = useState(false);
  const [bottomPanelView, setBottomPanelView] = useState<BottomPanelView>('terminal');
  const [bottomPanelHeight, setBottomPanelHeight] = useState(190);
  const [bottomPanelResizing, setBottomPanelResizing] = useState(false);
  const [changesPanelOpen, setChangesPanelOpen] = useState(false);
  const [changesRefreshKey, setChangesRefreshKey] = useState(0);
  const [accessMode, setAccessMode] = useState<AccessMode>('default');
  const [selectedModel, setSelectedModel] = useState('');
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [references, setReferences] = useState<SessionReference[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const [pendingRequests, setPendingRequests] = useState<PendingRequest[]>([]);
  const [branchStatus, setBranchStatus] = useState<{ isRepo: boolean; branch: string | null } | null>(null);
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
  }, []);

  useEffect(() => {
    if (sessionId || messages.length > 0 || projects.length === 0 || sessions.length > 0) return;
    if (activeProjectId && projects.some((project) => project.id === activeProjectId)) return;
    const firstProject = projects[0];
    if (!firstProject) return;
    pendingProjectIdRef.current = firstProject.id;
    setActiveProjectId(firstProject.id);
  }, [activeProjectId, messages.length, projects, sessionId, sessions.length]);

  const resolveSessionReference = async (sessionId: string): Promise<SessionReference | null> => {
    try {
      const response = await chatService.getSession(sessionId);
      return { id: response.session.id, title: response.session.title };
    } catch {
      return null;
    }
  };

  const sendMessage = async (override?: { message: string; projectId?: string }) => {
    const typedMessage = (override?.message ?? input).trim();
    if (isThinking) return;

    if (!typedMessage && attachments.length === 0) return;

    if (typedMessage.startsWith('/')) {
      handleSlashCommand(typedMessage);
      return;
    }

    const requestProjectId = override?.projectId || pendingProjectIdRef.current;

    // 未选择 workspace 时不发送，停留在草稿态提示用户先选工作空间
    if (!sessionIdRef.current && !requestProjectId) {
      setDraftMode(true);
      return;
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

    setMessages((current) => [
      ...current,
      createMessage('user', message, {
        status: 'done',
        access_mode: accessMode,
        provider: requestProvider,
        model: requestModel,
        attachments: requestAttachments,
        ...(requestReferences.length > 0 ? { references: requestReferences } : {}),
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
          ...(requestReferences.length > 0 ? { referenced_sessions: requestReferences.map((reference) => reference.id) } : {}),
          ...(requestSessionId ? { session_id: requestSessionId } : {}),
          ...(requestProjectId ? { project_id: requestProjectId } : {}),
        },
        handleEvent,
        controller.signal,
      );
      if (requestId !== requestSeqRef.current) return;
      setAttachments([]);
      setReferences([]);
      setRuntimeStatus('ready');
      await refreshSessions();
      await refreshProjects();
      setChangesRefreshKey((value) => value + 1);
      _generateSessionTitleIfNeeded(message, streamedContent, sessionIdRef.current);
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
        // Safety net: if the stream ended without a terminal event reaching us
        // (e.g. the backend dropped the `done` frame), force the assistant
        // message out of the "running" state so the blue bar always clears.
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantMessageId && item.status === 'running'
              ? { ...item, content: streamedContent, status: 'done', streamEndAt: Date.now() }
              : item,
          ),
        );
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
    const requestId = requestSeqRef.current;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫
      if (requestId !== requestSeqRef.current) return;
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
        localParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
    const requestId = requestSeqRef.current;
    const handleEvent = (event: StreamEvent) => {
      // P1 陈旧守卫
      if (requestId !== requestSeqRef.current) return;
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
        localParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
    // 记录当前 requestId，使 handleEvent 中的陈旧检查生效（P1 守卫）
    const requestId = requestSeqRef.current;
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
        ...chained.map((event): PendingRequest => pendingFromEvent(event, request.session_id, targetMessageId)),
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
    // 将 resume 流的 controller 写入 abortRef，使会话切换能正确中断它（幽灵流修复）
    const resumeController = new AbortController();
    abortRef.current = resumeController;
    const resumeSessionId = request.session_id || sessionIdRef.current || '';
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
          // P1 陈旧请求守卫（P1 修复）
          if (requestId !== requestSeqRef.current) return;
          if (event.type === 'done') {
            resumeContent = event.content || resumeContent;
            if (event.parts && event.parts.length > 0) {
              resumeParts = event.parts;
            }
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
            resumeParts.push({ type: 'tool', id: event.id, name: event.name, status: 'running', input: event.input || '' });
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
              return [...current, pendingFromEvent(event, resumeSessionId, targetMessageId)];
            });
          } else if (event.type === 'error') {
            setMessages((current) =>
              current.map((item) =>
                item.id === targetMessageId
                  ? { ...item, content: event.error || t('chat.backend_unreachable'), status: 'error', streamEndAt: Date.now() }
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
      // P0 并发双流修复：resume 流结束后清除 isThinking 和 abortRef
      setIsThinking(false);
      if (abortRef.current === resumeController) {
        abortRef.current = null;
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

  const startProjectDraft = (projectId?: string, firstMessage = '') => {
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

  // 新对话：不再弹窗。在项目内新建则继承该项目 workspace；
  // 全局新建则进入空态，由 composer 顶部的 workspace 选择器指定。
  const startNewChat = (projectId?: string) => {
    startProjectDraft(projectId);
    setDraftMode(true);
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

  const pickWorkspaceDirectory = async () => {
    return chatService.openDirectoryPicker({ title: t('project_dialog.pick_workspace') });
  };

  const createProjectWithWorkspace = async (payload: CreateProjectRequest): Promise<ProjectEntry> => {
    const response = await chatService.createProject(payload);
    await refreshProjects();
    setActiveProjectId(response.project.id);
    if (!sessionIdRef.current) {
      pendingProjectIdRef.current = response.project.id;
    }
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
    // 若已有正在运行的流，先正常中断（保留 AbortError 兜底让 finally 将已收到的内容落盘）
    abortRef.current?.abort();
    requestSeqRef.current += 1;
    activeAssistantMessageIdRef.current = undefined;
    setIsThinking(false);
    setActiveView('chat');
    setDraftMode(false);
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
          ...(record.references?.length ? { references: record.references } : {}),
          timestamp: new Date(record.created_at).getTime(),
        }),
      );
      setSessionId(sessionIdToOpen);
      sessionIdRef.current = sessionIdToOpen;
      pendingProjectIdRef.current = undefined;
      setPendingRequests((current) => current.filter((item) => item.session_id !== sessionIdToOpen));
      void restorePendingForSession(sessionIdToOpen);
      // 后台 resume 可能仍在运行：切回时首扫可能早于新审批创建，延迟重扫兜底。
      for (const delay of [5000, 15000]) {
        setTimeout(() => {
          if (sessionIdRef.current === sessionIdToOpen) {
            void restorePendingForSession(sessionIdToOpen);
          }
        }, delay);
      }
      setActiveProjectId(response.session.project_id || undefined);
      // 归而非覆盖：保留本地 status === 'running' 的消息（半截回复可能由 AbortError 兜底已写入）
      setMessages((current) => {
        const running = current.filter((m) => m.status === 'running');
        if (running.length === 0) return loaded;
        const loadedIds = new Set(loaded.map((m) => m.id));
        const merged = running.filter((m) => !loadedIds.has(m.id));
        return [...loaded, ...merged];
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

    return (
    <main
      className={`app-shell ${sidebarCollapsed ? 'app-shell--sidebar-collapsed' : ''} ${sidebarResizing || bottomPanelResizing ? 'app-shell--resizing' : ''}`}
      style={{ '--sidebar-width': `${sidebarWidth}px`, '--bottom-panel-height': `${bottomPanelHeight}px` } as CSSProperties}
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
          onNewChat={startNewChat}
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
                        references={references}
                        modelOptions={modelOptions}
                        editing={Boolean(editingMessage)}
                        onChange={editingMessage ? setEditDraft : setInput}
                        onSend={editingMessage ? () => void commitEditMessage(editingMessage.id, editDraft) : sendMessage}
                        onStop={stopMessage}
                        onAccessModeChange={setAccessMode}
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
