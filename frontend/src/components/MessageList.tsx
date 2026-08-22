import { Bot, CheckIcon, ChevronDown, Hammer, ListChecks, Paperclip, Shield, ShieldCheck } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n';
import type { ChatMessage, MessagePart, PartFileChange, PartAgent } from '../types';
import { ScrollArea } from './ui/scroll-area';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanBlock } from './PlanBlock';
import { FileChangesCard } from './FileChangesCard';
import { AgentActivity } from './AgentActivity';
import { MessageActions } from './MessageActions';
import { OrderedParts, ToolChain } from './part-renderers';
import { AgentBlock } from './AgentBlock';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

const STICK_THRESHOLD = 80;

interface MessageListProps {
  messages: ChatMessage[];
  isThinking?: boolean;
  onEditMessage?: (messageId: string, content: string) => void;
  onRegenerateMessage?: (messageId: string) => void;
  onRedoMessage?: (messageId: string) => void;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
}

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
  const agentParts = parts.filter((p) => p.type === 'agent');
  return { planParts, reasoningParts, toolParts, agentParts };
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

function UserMessage({ message, onEdit, onRedo }: { message: ChatMessage; onEdit?: (content: string) => void; onRedo?: () => void }) {
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
            {...(onRedo ? { onRedo, revertedFiles: message.revertedFiles ?? 0 } : {})}
          />
        )}
      </div>
    </div>
  );
}

function AssistantMessage({ message, onRegenerate, actionsDisabled = false, onSubscribeWorker }: { message: ChatMessage; onRegenerate?: () => void; actionsDisabled?: boolean; onSubscribeWorker?: (messageId: string, part: PartAgent) => void }) {
  const isError = message.status === 'error';
  const isRunning = message.status === 'running';
  const isRunningEmpty = isRunning && !message.content;
  const isWaiting = message.status === 'waiting';
  const isInterrupted = message.status === 'interrupted';

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
  const { planParts, reasoningParts, toolParts, agentParts } = groupParts(msgParts);
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
  // 停止/打断与成功态同构渲染，仅用 meta 小标签提示回复不完整。
  const statusLabel = message.status === 'stopped' ? t('chat.meta_stopped') : isInterrupted ? t('chat.meta_interrupted') : null;
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
            {statusLabel !== null && <span className="assistant-meta__status">{statusLabel}</span>}
            <span>{metaText}</span>
          </div>
        )}

        {hasTextParts ? (
          <OrderedParts
            parts={msgParts}
            running={isRunning}
            isError={isError}
            messageId={message.id}
            {...(onSubscribeWorker ? { onSubscribeWorker } : {})}
            renderAgentBlock={AgentBlock}
          />
        ) : (
          <>
            {planParts.length > 0 && <PlanBlock planParts={planParts} working={isRunning} />}
            {reasoningParts.length > 0 && <ThinkingBlock reasoningParts={reasoningParts} working={isRunning} />}
            {toolParts.length > 0 && <ToolChain toolParts={toolParts} />}
            {agentParts.map((part) => (
              <AgentBlock
                key={`agent-${part.workerRunId}`}
                part={part}
                messageId={message.id}
                {...(onSubscribeWorker ? { onSubscribeWorker } : {})}
              />
            ))}
          </>
        )}

        {isRunning && hasRunningTools && <AgentActivity working={isRunning} />}

        {!hasTextParts && !isError && !isRunningEmpty && !isWaiting && (
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
                    <span className="file-counts__add"> +{summaryData.addedLines}</span>
                  )}
                  {summaryData.removedLines > 0 && (
                    <span className="file-counts__del"> -{summaryData.removedLines}</span>
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
        ) : isWaiting ? (
          <div className="stream-waiting">
            <span className="stream-waiting__dot" aria-hidden="true" />
            <span className="stream-waiting__text">{message.content || t('chat.waiting_resolution')}</span>
          </div>
        ) : null}

        {!isError && !isRunning && !isRunningEmpty && !isWaiting && (
          <MessageActions role="assistant" content={message.content} disabled={actionsDisabled} {...(onRegenerate ? { onRegenerate } : {})} />
        )}
      </div>
    </div>
  );
}

function MessageListView({ messages, isThinking = false, onEditMessage, onRegenerateMessage, onRedoMessage, onSubscribeWorker }: MessageListProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const lastUserMessageIdRef = useRef<string | undefined>(undefined);
  const lastCountRef = useRef(0);
  const [isNearBottom, setIsNearBottom] = useState(true);

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
    setIsNearBottom(distanceFromBottom <= STICK_THRESHOLD);
  };

  const handleScrollToBottom = useCallback(() => {
    scrollToBottom('smooth');
    setIsNearBottom(false);
  }, [scrollToBottom]);

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
              {...(onRedoMessage && message.revertedFiles ? { onRedo: () => onRedoMessage(message.id) } : {})}
            />
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              {...(onRegenerateMessage ? { onRegenerate: () => onRegenerateMessage(message.id) } : {})}
              actionsDisabled={isThinking}
              {...(onSubscribeWorker ? { onSubscribeWorker } : {})}
            />
          ),
        )}
      </section>

      {!isNearBottom && (
        <button
          className="scroll-to-bottom-btn"
          onClick={handleScrollToBottom}
          aria-label={t('message.scrollToBottom')}
        >
          <ChevronDown size={20} />
        </button>
      )}
    </ScrollArea>
  );
}

export function MessageList(props: MessageListProps) {
  const { messages, isThinking, onEditMessage, onRegenerateMessage, onRedoMessage, onSubscribeWorker } = props;
  return (
    <MessageListView
      messages={messages}
      isThinking={isThinking ?? false}
      {...(onEditMessage ? { onEditMessage } : {})}
      {...(onRegenerateMessage ? { onRegenerateMessage } : {})}
      {...(onRedoMessage ? { onRedoMessage } : {})}
      {...(onSubscribeWorker ? { onSubscribeWorker } : {})}
    />
  );
}
