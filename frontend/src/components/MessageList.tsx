import type { ChatMessage } from '../types';
import { t } from '../lib/i18n';

interface MessageListProps {
  messages: ChatMessage[];
  isThinking: boolean;
}

export function MessageList({ messages, isThinking }: MessageListProps) {
  return (
    <section className="messages" aria-live="polite">
      {messages.length === 0 && (
        <div className="empty-state">
          <p className="empty-state__title">{t('app.title')}</p>
          <p className="empty-state__body">{t('app.subtitle')}</p>
        </div>
      )}

      {messages.map((message) => (
        <article className={`message message--${message.role}`} key={message.id}>
          <div className="message__role">{message.role === 'user' ? t('common.you') : t('common.coworker')}</div>
          <pre className="message__content">{message.content}</pre>
        </article>
      ))}

      {isThinking && (
        <article className="message message--assistant">
          <div className="message__role">{t('common.coworker')}</div>
          <div className="thinking">{t('agent.thinking')}</div>
        </article>
      )}
    </section>
  );
}
