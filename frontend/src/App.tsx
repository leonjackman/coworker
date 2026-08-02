import { useEffect, useRef, useState } from 'react';
import { ChatInput } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { ProvidersPanel } from './components/ProvidersPanel';
import { SettingsView } from './components/settings/SettingsView';
import { StatusBar } from './components/StatusBar';
import { WorkspaceSidebar } from './components/WorkspaceSidebar';
import { getLanguage, initLanguage, t, translateError } from './lib/i18n';
import { applyTheme, getThemeSettings, setThemeSettings, type ThemeSettings } from './lib/theme';
import { chatService } from './services/chatService';
import type { AccessMode, AppView, ChatMessage, ComposerAttachment, ProviderEntry, RuntimeConfig, WorkMode } from './types';
import './App.css';

function createMessage(
  role: ChatMessage['role'],
  content: string,
  metadata: Omit<Partial<ChatMessage>, 'id' | 'role' | 'content' | 'timestamp'> = {},
): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    timestamp: Date.now(),
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
  const [languageVersion, setLanguageVersion] = useState(0);
  const [themeSettings, setThemeSettingsState] = useState<ThemeSettings>(() => getThemeSettings());
  const [activeView, setActiveView] = useState<AppView>('chat');
  const [workMode, setWorkMode] = useState<WorkMode>('build');
  const [accessMode, setAccessMode] = useState<AccessMode>('default');
  const [selectedModel, setSelectedModel] = useState('auto');
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [providers, setProviders] = useState<ProviderEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const requestSeqRef = useRef(0);

  const refreshProviders = async () => {
    try {
      const response = await chatService.listProviders();
      setProviders(response.providers.filter((provider) => provider.enabled));
      setRuntimeStatus('ready');
    } catch (error) {
      console.error('Failed to load providers:', error);
    }
  };

  useEffect(() => {
    let mounted = true;
    async function bootstrap() {
      applyTheme(themeSettings);
      await initLanguage();
      if (!mounted) return;
      setLanguageVersion((value) => value + 1);

      try {
        const config = await chatService.getRuntimeConfig();
        if (!mounted) return;
        setRuntimeConfig(config);
        setRuntimeStatus('ready');
        await refreshProviders();
      } catch (error) {
        console.error('Failed to load runtime config:', error);
        if (mounted) setRuntimeStatus('error');
      }
    }

    bootstrap();

    return () => {
      mounted = false;
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

  const sendMessage = async () => {
    const message = input.trim();
    if (!message || isThinking) return;

    if (message.startsWith('/')) {
      handleSlashCommand(message);
      return;
    }

    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    const selectedProvider = providers.find((provider) => provider.id === selectedModel);
    const requestAttachments = attachments;
    const requestModel = selectedProvider?.model ?? t('chat.model_auto');
    const requestProvider = selectedProvider?.name ?? t('chat.model_auto');
    setMessages((current) => [
      ...current,
      createMessage('user', message, {
        status: 'queued',
        work_mode: workMode,
        access_mode: accessMode,
        provider: requestProvider,
        model: requestModel,
        attachments: requestAttachments,
      }),
    ]);
    setInput('');
    setIsThinking(true);

    try {
      const response = await chatService.sendMessage({
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
        ...(sessionId ? { session_id: sessionId } : {}),
      });
      if (requestId !== requestSeqRef.current) return;
      setSessionId(response.session_id);
      setMessages((current) => [
        ...current,
        createMessage('assistant', response.response, {
          status: 'done',
          work_mode: workMode,
          access_mode: accessMode,
          provider: response.provider,
          model: requestModel,
        }),
      ]);
      setAttachments([]);
      setRuntimeStatus('ready');
    } catch (error) {
      if (requestId !== requestSeqRef.current) return;
      console.error('Failed to send message:', error);
      setRuntimeStatus('error');
      setMessages((current) => [
        ...current,
        createMessage('assistant', translateError(error) || t('chat.backend_unreachable'), {
          status: 'error',
          work_mode: workMode,
          access_mode: accessMode,
          provider: requestProvider,
          model: requestModel,
        }),
      ]);
    } finally {
      if (requestId === requestSeqRef.current) setIsThinking(false);
    }
  };

  const stopMessage = () => {
    requestSeqRef.current += 1;
    setIsThinking(false);
    setMessages((current) => [...current, createMessage('assistant', t('chat.stopped'), { status: 'stopped' })]);
  };

  const startNewChat = () => {
    requestSeqRef.current += 1;
    setMessages([]);
    setInput('');
    setAttachments([]);
    setSessionId(undefined);
    setIsThinking(false);
    setActiveView('chat');
  };

  const handleSlashCommand = (message: string) => {
    const [command] = message.split(/\s+/);
    setInput('');
    if (command === '/clear') {
      setMessages([]);
      setAttachments([]);
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

  const modelOptions = [
    { id: 'auto', label: t('chat.model_auto') },
    ...providers.map((provider) => ({
      id: provider.id,
      label: provider.model,
      provider: provider.name,
    })),
  ];
  const showRuntimeNotice = activeView === 'chat' && (runtimeStatus !== 'ready' || !runtimeConfig);

  const changeThemeSettings = (nextSettings: ThemeSettings) => {
    setThemeSettingsState(nextSettings);
    setThemeSettings(nextSettings);
  };

  return (
    <main className="app-shell" key={languageVersion}>
      <WorkspaceSidebar
        config={runtimeConfig}
        messages={messages}
        activeView={activeView}
        onViewChange={setActiveView}
        onNewChat={startNewChat}
      />
      <section className="workspace-shell">
        <StatusBar status={runtimeStatus} />
        {activeView === 'chat' ? (
          <>
            {showRuntimeNotice && (
              <section className={`runtime-notice runtime-notice--${runtimeStatus}`}>
                <p className="runtime-notice__eyebrow">
                  {runtimeStatus === 'connecting' ? t('runtime.connecting_label') : t('runtime.error_label')}
                </p>
                <h2>{runtimeStatus === 'connecting' ? t('runtime.connecting_title') : t('runtime.error_title')}</h2>
                <p>{runtimeStatus === 'connecting' ? t('runtime.connecting_body') : t('runtime.error_body')}</p>
              </section>
            )}
            <MessageList messages={messages} isThinking={isThinking} />
            <div ref={bottomRef} />
            <ChatInput
              value={input}
              disabled={isThinking || runtimeStatus === 'connecting'}
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
              onModelChange={setSelectedModel}
              onAttachmentsChange={setAttachments}
            />
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
          />
        )}
      </section>
    </main>
  );
}

export default App;
