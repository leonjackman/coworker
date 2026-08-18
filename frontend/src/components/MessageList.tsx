import { Bot, CheckIcon, Hammer, ListChecks, Paperclip, Shield, ShieldCheck, Users } from 'lucide-react';
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { t } from '../lib/i18n';
import type { ChatMessage, MessagePart, PartDelegate, PartFileChange } from '../types';
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
const IGNORED_TOOLS = new Set(['ask_user']);

interface MessageListProps {
  messages: ChatMessage[];
  isThinking?: boolean;
  onEditMessage?: (messageId: string, content: string) => void;
  onRegenerateMessage?: (messageId: string) => void;
  onRollbackMessage?: (messageId: string) => void;
}

const CONTEXT_TOOLS = new Set(['read_file', 'search_files']);

function formatTime(timestamp: number): string {
  if (!timestamp) return '';
  const d = new Date(timestamp);
  const h = d.getHours().toString().padStart(2, '0');
  const m = d.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function groupParts(parts: MessagePart[]) {
  const planParts = parts.filter((p) => p.type === 'plan');
  const reasoningParts = parts.filter((p) => p.type === 'reasoning');
  const toolParts = parts.filter((p) => p.type === 'tool');
  const delegateParts = parts.filter((p) => p.type === 'delegate');
  return { planParts, reasoningParts, toolParts, delegateParts };
}

function DelegateBlock({ delegate }: { delegate: PartDelegate }) {
  const { from, to, task, status, parallel, chars, failed } = delegate;
  const targets = Array.isArray(to) ? to : [to];
  const label = parallel
    ? `${from || ''} → ${targets.join(', ')}`
    : `${from || ''} → ${targets[0] || ''}`;
  return (
    <div className={`delegate-block delegate-block--${status}`}>
      <span className="delegate-block-icon"><Users size={14} /></span>
      <div className="delegate-block-body">
        <div className="delegate-block-title">
          {label}
          {status === 'running' && <span className="delegate-block-status">{t('chat.delegate_running')}</span>}
          {status === 'done' && chars !== undefined && <span className="delegate-block-status">· {chars} chars</span>}
          {status === 'error' && delegate.error && <span className="delegate-block-status">{delegate.error}</span>}
        </div>
        {task && <div className="delegate-block-task">{task}</div>}
        {failed && failed.length > 0 && (
          <div className="delegate-block-failed">{t('chat.delegate_failed')}: {failed.join(', ')}</div>
        )}
      </div>
    </div>
  );
}

interface ToolGroupNode {
  key: string;
  tools: Extract<MessagePart, { type: 'tool' }>[];
}

function buildToolGroups(toolParts: Extract<MessagePart, { type: 'tool' }>[]): { groups: ToolGroupNode[]; ignored: Extract<MessagePart, { type: 'tool' }>[] } {
  const groups: ToolGroupNode[] = [];
  const ignored: Extract<MessagePart, { type: 'tool' }>[] = [];
  let current: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flush = () => {
    if (current.length > 0) {
      groups.push({ key: current[0]!.id, tools: current });
      current = [];
    }
  };
  for (const part of toolParts) {
    if (IGNORED_TOOLS.has(part.name)) {
      ignored.push(part);
    } else if (CONTEXT_TOOLS.has(part.name)) {
      current.push(part);
    } else {
      flush();
      groups.push({ key: part.id, tools: [part] });
    }
  }
  flush();
  return { groups, ignored };
}

function ToolChain({ toolParts, running }: { toolParts: Extract<MessagePart, { type: 'tool' }>[]; running?: boolean }) {
  // After the turn completes, fold EVERY tool into a single collapsible group
  // ("big fold containing small folds"): each tool keeps its own inner card.
  // While streaming, render individual live cards so the user can follow which
  // tool is executing right now.
  if (!running) {
    const visibleTools = toolParts.filter((t) => !IGNORED_TOOLS.has(t.name));
    const active = toolParts.some((part) => part.status === 'running');
    return (
      <div className="tool-chain">
        {toolParts.filter((part) => IGNORED_TOOLS.has(part.name)).map((part) => (
          <ToolCallCard key={part.id} tool={part} />
        ))}
        {visibleTools.length > 0 && (
          <ToolGroup.Root variant="ghost">
            <ToolGroupTrigger count={visibleTools.length} active={active} />
            <ToolGroupContent>
              {visibleTools.map((part) => (
                <ToolCallCard key={part.id} tool={part} />
              ))}
            </ToolGroupContent>
          </ToolGroup.Root>
        )}
      </div>
    );
  }
  const { groups, ignored } = buildToolGroups(toolParts);
  return (
    <div className="tool-chain">
      {ignored.map((part) => (
        <ToolCallCard key={part.id} tool={part} />
      ))}
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

/** 把有序的 MessagePart[] 渲染成一组 JSX 节点，text/tool/reasoning/plan 按数组顺序交错。 */
function OrderedParts({ parts, running, isError, isStopped }: { parts: MessagePart[]; running: boolean; isError: boolean; isStopped: boolean }) {
  const nodes: ReactNode[] = [];
  let toolRun: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flushTools = (key: string) => {
    if (toolRun.length > 0) {
      nodes.push(<ToolChain key={key} toolParts={toolRun} running={running} />);
      toolRun = [];
    }
  };
  parts.forEach((part, index) => {
    if (part.type === 'tool') {
      toolRun.push(part);
      return;
    }
    flushTools(`tools-${index}`);
    if (part.type === 'text') {
      if (isError || isStopped) return;
      nodes.push(
        <Suspense key={`text-${index}`} fallback={<div className="markdown-body">{part.content}</div>}>
          <MarkdownContent content={part.content} />
        </Suspense>,
      );
    } else if (part.type === 'reasoning') {
      nodes.push(<ThinkingBlock key={`reasoning-${index}`} reasoningParts={[part]} working={running} />);
    } else if (part.type === 'plan') {
      nodes.push(<PlanBlock key={`plan-${index}`} planParts={[part]} working={running} />);
    } else if (part.type === 'delegate') {
      nodes.push(<DelegateBlock key={`delegate-${index}`} delegate={part} />);
    }
  });
  flushTools(`tools-end`);
  if (nodes.length === 0) return null;
  return <>{nodes}</>;
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
  if (totalSeconds < 10) return `${(ms / 1000).toFixed(1)}s`;
  if (totalSeconds < 60) return `${totalSeconds}s`;
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

function UserMessage({ message, onEdit, onRollback }: { message: ChatMessage; onEdit?: (content: string) => void; onRollback?: () => void }) {
  return (
    <div className="stream-row stream-row--user">
      <div className="stream-bubble-wrap">
        {message.references && message.references.length > 0 && (
          <div className="user-bubble-references">
            {message.references.map((reference) => (
              <span className="reference-chip" key={reference.id} title={reference.id}>
                <span className="reference-chip__title">{reference.title}</span>
                <span className="reference-chip__id">{reference.id.slice(0, 8)}</span>
              </span>
            ))}
          </div>
        )}
        <div className="stream-bubble stream-bubble--user">{message.content}</div>
        {message.attachments && message.attachments.length > 0 && (
          <div className="user-bubble-attachments">
            {message.attachments.map((attachment) => (
              <span
                className={`attachment-chip${attachment.tooLarge ? ' attachment-chip--warn' : ''}`}
                key={attachment.id}
                title={`${attachment.name} · ${formatBytes(attachment.size)}${attachment.tooLarge ? '（过大未内联字节）' : ''}`}
              >
                <Paperclip size={12} />
                <span className="attachment-chip__name">{attachment.name}</span>
                <span className="attachment-chip__size">{formatBytes(attachment.size)}</span>
              </span>
            ))}
          </div>
        )}
        <div className="user-bubble-meta">
          <span className="user-bubble-meta__time">{formatTime(message.timestamp)}</span>
        </div>
        {(message.status !== 'waiting') && (
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

function AssistantMessage({ message, onRegenerate, actionsDisabled = false }: { message: ChatMessage; onRegenerate?: () => void; actionsDisabled?: boolean }) {
  const isError = message.status === 'error';
  const isStopped = message.status === 'stopped' || message.status === 'interrupted';
  const isInterrupted = message.status === 'interrupted';
  const isRunning = message.status === 'running';
  const isRunningEmpty = isRunning && !message.content;
  const isWaiting = message.status === 'waiting';

  // 流式进行中，让计时每秒跳动（否则只在收到 token 时刷新，思考阶段会"卡住"）
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (isRunning && message.streamStartAt && !message.streamEndAt) {
      const id = setInterval(() => forceTick((n) => n + 1), 1000);
      return () => clearInterval(id);
    }
  }, [isRunning, message.streamStartAt, message.streamEndAt]);
  const msgParts = message.parts ?? [];
  // 新格式：parts 内包含 text part（text 与 tool 交错）。旧会话没有 text part，
  // 走分组渲染 + content 兜底。
  const hasTextParts = msgParts.some((p) => p.type === 'text');
  const { planParts, reasoningParts, toolParts } = groupParts(msgParts);
  const fileChanges = collectFileChanges(toolParts);
  const hasRunningTools = isRunning && toolParts.some((part) => part.status === 'running');
  const summaryData = getTurnSummaryData(toolParts, fileChanges);
  const hasToolsOrFiles = summaryData !== null;

  // Build the meta text (Plan/Build · autonomy · model · duration)
  const metaParts: string[] = [];
  if (message.work_mode) {
    metaParts.push(message.work_mode === 'plan' ? 'Plan' : 'Build');
  }
  if (message.autonomy) {
    metaParts.push(t(`chat.autonomy_${message.autonomy}`));
  }
  if (message.model) {
    metaParts.push(message.provider ? `${message.provider} · ${message.model}` : message.model);
  }
  // 任务总计时：收到任务开始计时，done/error/stopped 结束
  if (message.streamStartAt) {
    if (message.streamEndAt) {
      const durationMs = message.streamEndAt - message.streamStartAt;
      if (durationMs >= 0) {
        const s = Math.round(durationMs / 1000);
        if (s < 60) metaParts.push(`${s}s`);
        else metaParts.push(`${Math.floor(s / 60)}m ${s % 60}s`);
      }
    } else if (isRunning) {
      const s = Math.round((Date.now() - message.streamStartAt) / 1000);
      metaParts.push(`${s}s`);
    }
  }
  const metaText = metaParts.length > 0 ? metaParts.join(' · ') : null;

  return (
    <div className="stream-row stream-row--assistant">
      <div className="stream-avatar" aria-hidden="true">
        <Bot size={15} />
      </div>
      <div className={`stream-content stream-content--${message.status ?? 'done'}`}>
        <div className="stream-role">
          <span>{t('common.coworker')}</span>
          <span className="stream-role__time">{formatTime(message.timestamp)}</span>
        </div>
        {metaText !== null && (
          <div className="assistant-meta">
            <span>{metaText}</span>
          </div>
        )}

        {hasTextParts ? (
          <OrderedParts parts={msgParts} running={isRunning} isError={isError} isStopped={isStopped} />
        ) : (
          <>
            {planParts.length > 0 && <PlanBlock planParts={planParts} working={isRunning} />}
            {reasoningParts.length > 0 && <ThinkingBlock reasoningParts={reasoningParts} working={isRunning} />}
            {toolParts.length > 0 && <ToolChain toolParts={toolParts} running={isRunning} />}
          </>
        )}

        {isRunning && hasRunningTools && <AgentActivity working={isRunning} />}

        {!hasTextParts && !isError && !isStopped && !isRunningEmpty && !isWaiting && (
          <Suspense fallback={<div className="markdown-body">{message.content}</div>}>
            <MarkdownContent content={message.content} />
          </Suspense>
        )}

        {fileChanges.length > 0 && <FileChangesCard files={fileChanges} />}

        {hasToolsOrFiles && (
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
          <div>
            <div className="stream-error">{message.content}</div>
            {onRegenerate && (
              <button
                className="stream-retry-btn"
                onClick={onRegenerate}
                disabled={actionsDisabled}
              >
                {t('common.retry')}
              </button>
            )}
          </div>
        ) : isStopped ? (
          <div className="stream-stopped">{message.content}</div>
        ) : isWaiting ? (
          <div className="stream-waiting">
            <span className="stream-waiting__dot" aria-hidden="true" />
            <span className="stream-waiting__text">{message.content || t('chat.waiting_resolution')}</span>
          </div>
        ) : null}

        {/* interrupted messages keep the regenerate action so the user can act on
            the "connection interrupted; you can regenerate" hint. */}
        {!isError && !isStopped && !isRunning && !isRunningEmpty && !isWaiting && (
          <MessageActions role="assistant" content={message.content} disabled={actionsDisabled} {...(onRegenerate ? { onRegenerate } : {})} />
        )}
        {isInterrupted && (
          <MessageActions role="assistant" content={message.content} disabled={actionsDisabled} {...(onRegenerate ? { onRegenerate } : {})} />
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
  // 整个会话只有 1 条用户消息时，撤回会清空会话，隐藏撤销按钮。
  const userMessageCount = messages.filter((m) => m.role === 'user').length;

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

    // Only auto-scroll when: (1) new user message sent, (2) session just opened, or (3) agent is thinking and user was at bottom
    if (hasNewUserMessage || isSessionOpen) {
      stickToBottomRef.current = true;
      scrollToBottom('auto');
    } else if (isThinking && stickToBottomRef.current) {
      // During streaming: only scroll if user hasn't scrolled up
      scrollToBottom('smooth');
    }
    // If user scrolled up and agent continues, preserve their position
  }, [messages, isThinking]);

  return (
    <ScrollArea
      className="messages"
      viewportRef={viewportRef}
      onViewportScroll={handleViewportScroll}
    >
      <section className="stream-wall" aria-live="polite">
        {messages.map((message, index) =>
          message.role === 'user' ? (
            <UserMessage
              key={message.id}
              message={message}
              {...(onEditMessage ? { onEdit: (content) => onEditMessage(message.id, content) } : {})}
              {...(onRollbackMessage && index > 0 && userMessageCount > 1 ? { onRollback: () => onRollbackMessage(message.id) } : {})}
            />
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              {...(onRegenerateMessage ? { onRegenerate: () => onRegenerateMessage(message.id) } : {})}
              actionsDisabled={isThinking}
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
      isThinking={isThinking ?? false}
      {...(onEditMessage ? { onEditMessage } : {})}
      {...(onRegenerateMessage ? { onRegenerateMessage } : {})}
      {...(onRollbackMessage ? { onRollbackMessage } : {})}
    />
  );
}
