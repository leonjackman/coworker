import { Bot, CheckCircle2, Clock3, FileText, Hammer, ListChecks, Shield, ShieldCheck, Square, UserRound } from 'lucide-react';
import type { ReactNode } from 'react';
import { t } from '../lib/i18n';
import type { ChatMessage } from '../types';
import { Message, MessageAvatar, MessageContent, MessageHeader } from './ui/message';
import { ScrollArea } from './ui/scroll-area';

interface MessageListProps {
  messages: ChatMessage[];
  isThinking: boolean;
}

function messageTime(timestamp: number) {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(timestamp);
}

function statusLabel(status: ChatMessage['status']) {
  if (status === 'running') return t('chat.status_running');
  if (status === 'stopped') return t('chat.status_stopped');
  if (status === 'error') return t('chat.status_error');
  if (status === 'queued') return t('chat.status_queued');
  return t('chat.status_done');
}

function statusIcon(status: ChatMessage['status']) {
  if (status === 'running') return <Clock3 size={13} />;
  if (status === 'stopped') return <Square size={13} />;
  if (status === 'error') return <Square size={13} />;
  if (status === 'queued') return <Clock3 size={13} />;
  return <CheckCircle2 size={13} />;
}

function renderContext(message: ChatMessage) {
  const chips: Array<{ key: string; icon: ReactNode; label: string }> = [];
  if (message.work_mode) {
    chips.push({
      key: 'mode',
      icon: message.work_mode === 'plan' ? <ListChecks size={13} /> : <Hammer size={13} />,
      label: message.work_mode === 'plan' ? t('chat.mode_plan') : t('chat.mode_build'),
    });
  }
  if (message.access_mode) {
    chips.push({
      key: 'access',
      icon: message.access_mode === 'full' ? <ShieldCheck size={13} /> : <Shield size={13} />,
      label: message.access_mode === 'full' ? t('chat.access_full') : t('chat.access_default'),
    });
  }
  if (message.model) {
    chips.push({
      key: 'model',
      icon: <Bot size={13} />,
      label: message.provider ? `${message.provider} · ${message.model}` : message.model,
    });
  }

  if (chips.length === 0 && !message.attachments?.length) return null;

  return (
    <div className="message-context">
      {chips.map((chip) => (
        <span className="message-chip" key={chip.key}>
          {chip.icon}
          {chip.label}
        </span>
      ))}
      {message.attachments?.map((attachment) => (
        <span className="message-chip message-chip--attachment" key={attachment.id}>
          <FileText size={13} />
          {attachment.name}
        </span>
      ))}
    </div>
  );
}

function TimelineMessage({ message, index }: { message: ChatMessage; index: number }) {
  const isUser = message.role === 'user';
  return (
    <Message align={isUser ? 'end' : 'start'} className={`timeline-item timeline-item--${message.role} timeline-item--${message.status ?? 'done'}`}>
      <div className="timeline-item__rail">
        <span className="timeline-item__index">{String(index + 1).padStart(2, '0')}</span>
        <MessageAvatar className="timeline-item__node">{isUser ? <UserRound size={15} /> : <Bot size={15} />}</MessageAvatar>
      </div>
      <MessageContent className="timeline-card">
        <MessageHeader className="timeline-card__header">
          <div>
            <span className="timeline-card__role">{isUser ? t('common.you') : t('common.coworker')}</span>
            <time>{messageTime(message.timestamp)}</time>
          </div>
          <span className="timeline-status">
            {statusIcon(message.status)}
            {statusLabel(message.status)}
          </span>
        </MessageHeader>
        {renderContext(message)}
        <pre className="timeline-card__content">{message.content}</pre>
      </MessageContent>
    </Message>
  );
}

export function MessageList({ messages, isThinking }: MessageListProps) {
  return (
    <ScrollArea className="messages">
      <section className="messages__inner" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-state">
            <p className="empty-state__eyebrow">{t('app.eyebrow')}</p>
            <p className="empty-state__title">{t('app.title')}</p>
            <p className="empty-state__body">{t('app.subtitle')}</p>
            <div className="empty-state__hints">
              <span>{t('chat.empty_hint_plan')}</span>
              <span>{t('chat.empty_hint_build')}</span>
              <span>{t('chat.empty_hint_slash')}</span>
            </div>
          </div>
        )}

        <div className="timeline-wall">
          {messages.map((message, index) => (
            <TimelineMessage index={index} key={message.id} message={message} />
          ))}

          {isThinking && (
            <TimelineMessage
              index={messages.length}
              message={{
                id: 'assistant-running',
                role: 'assistant',
                content: t('agent.thinking'),
                timestamp: Date.now(),
                status: 'running',
              }}
            />
          )}
        </div>
      </section>
    </ScrollArea>
  );
}
