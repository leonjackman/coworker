import { useEffect, useRef, useState } from 'react';
import { ChatInput } from './components/ChatInput';
import { MessageList } from './components/MessageList';
import { StatusBar } from './components/StatusBar';
import { getLanguage, initLanguage, t, translateError } from './lib/i18n';
import { chatService } from './services/chatService';
import type { ChatMessage, RuntimeConfig } from './types';
import './App.css';

function createMessage(role: ChatMessage['role'], content: string): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    timestamp: Date.now(),
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
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mounted = true;
    async function bootstrap() {
      await initLanguage();
      if (!mounted) return;
      setLanguageVersion((value) => value + 1);

      try {
        const config = await chatService.getRuntimeConfig();
        if (!mounted) return;
        setRuntimeConfig(config);
        setRuntimeStatus('ready');
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
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  useEffect(() => {
    document.title = t('app.title');
  }, [languageVersion]);

  const sendMessage = async () => {
    const message = input.trim();
    if (!message || isThinking) return;

    setMessages((current) => [...current, createMessage('user', message)]);
    setInput('');
    setIsThinking(true);

    try {
      const response = await chatService.sendMessage({
        message,
        mode: runtimeConfig?.default_mode ?? 'single',
        language: getLanguage(),
        ...(sessionId ? { session_id: sessionId } : {}),
      });
      setSessionId(response.session_id);
      setMessages((current) => [...current, createMessage('assistant', response.response)]);
      setRuntimeStatus('ready');
    } catch (error) {
      console.error('Failed to send message:', error);
      setRuntimeStatus('error');
      setMessages((current) => [
        ...current,
        createMessage('assistant', translateError(error) || t('chat.backend_unreachable')),
      ]);
    } finally {
      setIsThinking(false);
    }
  };

  return (
    <main className="app-shell" key={languageVersion}>
      <StatusBar config={runtimeConfig} status={runtimeStatus} onLanguageChange={() => setLanguageVersion((value) => value + 1)} />
      <MessageList messages={messages} isThinking={isThinking} />
      <div ref={bottomRef} />
      <ChatInput value={input} disabled={isThinking || runtimeStatus === 'connecting'} onChange={setInput} onSend={sendMessage} />
    </main>
  );
}

export default App;
