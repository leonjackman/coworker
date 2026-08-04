import { Bot, CheckIcon, FileText, Hammer, Loader2, ListChecks, Shield, ShieldCheck } from 'lucide-react';
import { lazy, Suspense, useEffect, useRef, type ReactNode } from 'react';
import { t } from '../lib/i18n';
import type { ChatMessage, MessagePart, PartFileChange } from '../types';
import { ScrollArea } from './ui/scroll-area';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanBlock } from './PlanBlock';
import { ToolCallCard } from './ToolCallCard';
import { FileChangesCard } from './FileChangesCard';
import { AgentActivity } from './AgentActivity';
import { MessageActions } from './MessageActions';
import { ToolGroup, ToolGroupTrigger, ToolGroupContent } from './assistant-ui/tool-group';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

const STICK_THRESHOLD = 80;

interface MessageListProps {
  messages: ChatMessage[];
  isThinking?: boolean;
  onEditMessage?: (messageId: string, content: string) => void;
  onRegenerateMessage?: (messageId: string) => void;
  onRollbackMessage?: (messageId: string) => void;
}

const CONTEXT_TOOLS = new Set(['read_file', 'search_files']);

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

interface ToolGroupNode {
  key: string;
  tools: Extract<MessagePart, { type: 'tool' }>[];
}

function buildToolGroups(toolParts: Extract<MessagePart, { type: 'tool' }>[]): ToolGroupNode[] {
  const groups: ToolGroupNode[] = [];
  let current: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flush = () => {
    if (current.length > 0) {
      groups.push({ key: current[0]!.id, tools: current });
      current = [];
    }
  };
  for (const part of toolParts) {
    if (CONTEXT_TOOLS.has(part.name)) {
      current.push(part);
    } else {
      flush();
      groups.push({ key: part.id, tools: [part] });
    }
  }
  flush();
  return groups;
}

function ToolChain({ toolParts }: { toolParts: Extract<MessagePart, { type: 'tool' }>[] }) {
  const groups = buildToolGroups(toolParts);
  return (
    <div className="tool-chain">
      {groups.map((group) => {
        if (group.tools.length === 1) {
          const single = group.tools[0]!;
          return <ToolCallCard key={single.id} tool={single} />;
        }
        const active = group.tools.some((part) => part.status === 'running');
        return (
          <ToolGroup.Root key={group.key} variant="ghost">
            <ToolGroupTrigger count={group.tools.length} active={active} />
            <ToolGroupContent>
              {group.tools.map((part) => (
                <ToolCallCard key={part.id} tool={part} />
              ))}
            </ToolGroupContent>
          </ToolGroup.Root>
        );
      })}
    </div>
  );
}

function collectFileChanges(toolParts: Extract<MessagePart, { type: 'tool' }>[]): PartFileChange[] {
  const files: PartFileChange[] = [];
  for (const part of toolParts) {
    for (const file of part.files ?? []) {
      files.push(file);
    }
  }
  return files;
}

function getTurnSummaryData(toolParts: Extract<MessagePart, { type: 'tool' }>[], fileChanges: PartFileChange[]) {
  const count = toolParts.length;
  if (count === 0 && fileChanges.length === 0) return null;

  const distinctTools = [...new Set(toolParts.map((p) => p.name))];
  const addedLines = fileChanges.reduce((s, f) => s + f.added, 0);
  const removedLines = fileChanges.reduce((s, f) => s + f.removed, 0);
  const durationMs = toolParts.reduce((s, p) => s + (p.duration_ms ?? 0), 0);
  const fileCount = fileChanges.length;

  return { count, distinctTools, fileCount, addedLines, removedLines, durationMs };
}

function formatDuration(ms: number): string {
  if (ms < 1000) return '<1s';
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 10) return `${(totalSeconds / 10).toFixed(1)}s`;
  if (totalSeconds < 60) return `${totalSeconds}s`;
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

function UserMessage({ message, onEdit, onRollback }: { message: ChatMessage; onEdit?: (content: string) => void; onRollback?: () => void }) {
  return (
    <div className="stream-row stream-row--user">
      <div className="stream-bubble-wrap">
        <div className="stream-bubble stream-bubble--user">{message.content}</div>
        {!message.content.includes(t('chat.waiting_resolution')) && (
          <MessageActions
            role="user"
            content={message.content}
            {...(onEdit ? { onEdit } : {})}
            {...(onRollback ? { onRollback } : {})}
          />
        )}
      </div>
    </div>
  );
}

function AssistantMessage({ message, onRegenerate }: { message: ChatMessage; onRegenerate?: () => void }) {
  const isError = message.status === 'error';
  const isStopped = message.status === 'stopped';
  const isRunning = message.status === 'running';
  const isRunningEmpty = isRunning && !message.content;
  const isWaiting = message.content.includes(t('chat.waiting_resolution'));
  const parts = message.parts ?? [];
  const { planParts, reasoningParts, toolParts } = groupParts(parts);
  const fileChanges = collectFileChanges(toolParts);
  const hasRunningTools = isRunning && toolParts.some((part) => part.status === 'running');
  const summaryData = getTurnSummaryData(toolParts, fileChanges);
  const hasToolsOrFiles = summaryData !== null;

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
        {toolParts.length > 0 && <ToolChain toolParts={toolParts} />}
        {!isRunning && fileChanges.length > 0 && <FileChangesCard files={fileChanges} />}

        {isRunning && hasRunningTools && <AgentActivity working={isRunning} />}

        {!isError && !isStopped && !isRunningEmpty && !isWaiting && (
          <Suspense fallback={<div className="markdown-body">{message.content}</div>}>
            <MarkdownContent content={message.content} />
          </Suspense>
        )}

        {!isRunning && hasToolsOrFiles && (
          <div className="turn-summary">
            <CheckIcon size={12} className="turn-summary__check" />
            <span className="turn-summary__text">
              {summaryData.count > 0 && (
                <>
                  {summaryData.count} tool{summaryData.count > 1 ? 's' : ''}
                  {summaryData.distinctTools.length > 0 && (
                    <> · <span className="turn-summary__tools">{summaryData.distinctTools.join(', ')}</span></>
                  )}
                </>
              )}
              {summaryData.fileCount > 0 && (
                <>
                  {summaryData.count > 0 && ' · '}
                  {summaryData.fileCount} file{summaryData.fileCount > 1 ? 's' : ''}
                  {summaryData.addedLines > 0 && (
                    <span className="text-success"> +{summaryData.addedLines}</span>
                  )}
                  {summaryData.removedLines > 0 && (
                    <span className="text-warning"> -{summaryData.removedLines}</span>
                  )}
                </>
              )}
              {summaryData.durationMs > 0 && (
                <> · {formatDuration(summaryData.durationMs)}</>
              )}
            </span>
          </div>
        )}

        {isError ? (
          <div className="stream-error">{message.content}</div>
        ) : isStopped ? (
          <div className="stream-stopped">{message.content}</div>
        ) : isRunningEmpty ? null : null}

        {!isError && !isStopped && !isRunning && !isRunningEmpty && !isWaiting && (
          <MessageActions role="assistant" content={message.content} {...(onRegenerate ? { onRegenerate } : {})} />
        )}
      </div>
    </div>
  );
}

function MessageListView({ messages, isThinking = false, onEditMessage, onRegenerateMessage, onRollbackMessage }: MessageListProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const lastUserMessageIdRef = useRef<string | undefined>(undefined);
  const lastCountRef = useRef(0);

  const scrollToBottom = (behavior: ScrollBehavior = 'auto') => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior });
  };

  const handleViewportScroll = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    stickToBottomRef.current = distanceFromBottom <= STICK_THRESHOLD;
  };

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const lastUserMessage = [...messages].reverse().find((message) => message.role === 'user');
    const lastUserMessageId = lastUserMessage?.id;
    const hasNewUserMessage = lastUserMessageId !== undefined && lastUserMessageId !== lastUserMessageIdRef.current;
    const isSessionOpen = messages.length > 0 && lastCountRef.current === 0;

    lastUserMessageIdRef.current = lastUserMessageId;
    lastCountRef.current = messages.length;

    if (hasNewUserMessage || isSessionOpen) {
      // A user just sent a new message (or a session just opened): always reveal
      // the latest user message regardless of the previous scroll position.
      stickToBottomRef.current = true;
      scrollToBottom('auto');
    } else if (isThinking) {
      // Streaming agent reply: follow only when the user hasn't scrolled away.
      if (stickToBottomRef.current) {
        scrollToBottom('auto');
      }
    }
  }, [messages, isThinking]);

  return (
    <ScrollArea
      className="messages"
      viewportRef={viewportRef}
      onViewportScroll={handleViewportScroll}
    >
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
            <UserMessage
              key={message.id}
              message={message}
              {...(onEditMessage ? { onEdit: (content) => onEditMessage(message.id, content) } : {})}
              {...(onRollbackMessage ? { onRollback: () => onRollbackMessage(message.id) } : {})}
            />
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              {...(onRegenerateMessage ? { onRegenerate: () => onRegenerateMessage(message.id) } : {})}
            />
          ),
        )}
      </section>
    </ScrollArea>
  );
}

export function MessageList(props: MessageListProps) {
  const { messages, isThinking, onEditMessage, onRegenerateMessage, onRollbackMessage } = props;
  return (
    <MessageListView
      messages={messages}
      isThinking={isThinking}
      {...(onEditMessage ? { onEditMessage } : {})}
      {...(onRegenerateMessage ? { onRegenerateMessage } : {})}
      {...(onRollbackMessage ? { onRollbackMessage } : {})}
    />
  );
}
