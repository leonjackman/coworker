import { Bot, FileText, Hammer, ListChecks, Loader2, Shield, ShieldCheck } from 'lucide-react';
import { lazy, Suspense, type ReactNode } from 'react';
import { t } from '../lib/i18n';
import type { ChatMessage, MessagePart } from '../types';
import { ScrollArea } from './ui/scroll-area';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanBlock } from './PlanBlock';
import { ToolCallCard } from './ToolCallCard';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

interface MessageListProps {
  messages: ChatMessage[];
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
    <div className="message-meta">
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

function groupParts(parts: MessagePart[]) {
  const planParts = parts.filter((p) => p.type === 'plan');
  const reasoningParts = parts.filter((p) => p.type === 'reasoning');
  const toolParts = parts.filter((p) => p.type === 'tool');
  return { planParts, reasoningParts, toolParts };
}

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="stream-row stream-row--user">
      <div className="stream-bubble stream-bubble--user">{message.content}</div>
    </div>
  );
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  const isError = message.status === 'error';
  const isStopped = message.status === 'stopped';
  const isRunning = message.status === 'running';
  const isRunningEmpty = isRunning && !message.content;
  const parts = message.parts ?? [];
  const { planParts, reasoningParts, toolParts } = groupParts(parts);

  return (
    <div className="stream-row stream-row--assistant">
      <div className="stream-avatar" aria-hidden="true">
        <Bot size={15} />
      </div>
      <div className={`stream-content stream-content--${message.status ?? 'done'}`}>
        <div className="stream-role">
          <span>{t('common.coworker')}</span>
        </div>
        {renderContext(message)}

        {planParts.length > 0 && <PlanBlock planParts={planParts} working={isRunning} />}
        {reasoningParts.length > 0 && <ThinkingBlock reasoningParts={reasoningParts} working={isRunning} />}

        {toolParts.length > 0 && (
          <div className="tool-chain">
            {toolParts.map((part) => {
              if (part.type !== 'tool') return null;
              const key = `${part.id}-${part.status}`;
              return <ToolCallCard key={key} tool={part} />;
            })}
          </div>
        )}

        {isError ? (
          <div className="stream-error">{message.content}</div>
        ) : isStopped ? (
          <div className="stream-stopped">{message.content}</div>
        ) : isRunningEmpty ? (
          <div className="stream-thinking">
            <Loader2 className="stream-thinking__spinner" size={15} />
            {t('agent.thinking')}
          </div>
        ) : (
          <Suspense fallback={<div className="markdown-body">{message.content}</div>}>
            <MarkdownContent content={message.content} />
          </Suspense>
        )}
      </div>
    </div>
  );
}

export function MessageList({ messages }: MessageListProps) {
  return (
    <ScrollArea className="messages">
      <section className="stream-wall" aria-live="polite">
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

        {messages.map((message) =>
          message.role === 'user' ? (
            <UserMessage key={message.id} message={message} />
          ) : (
            <AssistantMessage key={message.id} message={message} />
          ),
        )}
      </section>
    </ScrollArea>
  );
}
