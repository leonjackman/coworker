import type { ChatMessage, SessionSummary } from '../types';
import { t } from './i18n';

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

export { currentSessionTitle };
