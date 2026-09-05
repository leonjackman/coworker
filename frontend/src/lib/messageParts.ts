import type { ChatMessage, ContextUsage, MessagePart, PartAgent, PendingRequest, StreamEvent } from '../types';
import { chatService } from '../services/chatService';
import { t } from './i18n';

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
    } else if (part.type === 'agent') {
      // The backend persists agent parts with `worker_run_id` (snake_case), but
      // the frontend PartAgent uses `workerRunId`. Normalize so the coalescing
      // key matches the streaming part — otherwise `done.parts` stacks a second
      // block and the reloaded block cannot subscribe to the worker stream.
      const authoritative = normalizeAgentPart(part) as Extract<MessagePart, { type: 'agent' }>;
      const index = merged.findIndex(
        (p) => p.type === 'agent' && (p as Extract<MessagePart, { type: 'agent' }>).workerRunId === authoritative.workerRunId,
      );
      if (index >= 0) {
        const prev = merged[index] as Extract<MessagePart, { type: 'agent' }>;
        // Keep the live-built nested transcript; adopt the authoritative summary.
        merged[index] = {
          ...prev,
          ...authoritative,
          parts: prev.parts ?? authoritative.parts,
          ...((prev.transcriptLoaded || authoritative.transcriptLoaded) ? { transcriptLoaded: true } : {}),
        };
      } else {
        merged.push(authoritative);
      }
    } else if (part.type === 'text') {
      // 各次 resume 会重放同一轮执行（工具按 id 去重），文本同样按内容去重，
      // 避免重放时 text part 重复叠加；多轮文本内容各不相同，天然追加。
      const exists = merged.some((p) => p.type === 'text' && p.content === part.content);
      if (!exists && part.content) {
        merged.push(part);
      }
    } else if (part.type === 'steer') {
      // steer 通知既通过 steer_injected 事件 live 加入，也会随 done.parts 回传，
      // 按 steer_id（无 id 时退化为内容）去重，避免重复叠加。
      const exists = merged.some(
        (p) =>
          p.type === 'steer' &&
          ((p.steer_id !== undefined && p.steer_id === part.steer_id) ||
            (p.steer_id === undefined && p.content === part.content)),
      );
      if (!exists) {
        merged.push(part);
      }
    } else {
      merged.push(part);
    }
  }
  return merged;
}

/**
 * Map a backend agent part's snake_case `worker_run_id` to the frontend
 * `workerRunId`. Backend `done.parts` / persisted session parts use the raw
 * JSON shape; without this the coalescing key mismatches the streaming part
 * (duplicate blocks) and the reloaded block cannot subscribe to its stream.
 */
function normalizeAgentPart(part: MessagePart): MessagePart {
  if (part.type !== 'agent') return part;
  const raw = part as Extract<MessagePart, { type: 'agent' }> & { worker_run_id?: string };
  const { worker_run_id, ...rest } = raw;
  return { ...rest, workerRunId: rest.workerRunId || worker_run_id || '', parts: rest.parts ?? [] };
}

/** Normalize a whole parts array (backend JSON → frontend MessagePart shapes). */
function normalizeParts(parts: MessagePart[]): MessagePart[] {
  return parts.map(normalizeAgentPart);
}

/**
 * Shared SSE→parts reducer for BOTH the main agent stream and worker sub-agent
 * streams, so a worker transcript renders with the exact same blocks
 * (text/reasoning/tool/plan) as the main message. Terminal/status handling
 * (done/error/approvals/todos) stays in each caller.
 */
function applyStreamEventToParts(parts: MessagePart[], event: StreamEvent): MessagePart[] {
  switch (event.type) {
    case 'delta': {
      const last = parts[parts.length - 1];
      if (last && last.type === 'text') {
        const next = [...parts];
        next[next.length - 1] = { ...last, content: last.content + event.content };
        return next;
      }
      return [...parts, { type: 'text', content: event.content }];
    }
    case 'reasoning_delta': {
      const last = parts[parts.length - 1];
      if (last && last.type === 'reasoning') {
        const next = [...parts];
        next[next.length - 1] = { ...last, content: event.content };
        return next;
      }
      return [...parts, { type: 'reasoning', content: event.content }];
    }
    case 'tool_start':
      return upsertToolPart(parts, event.id, event.name, event.input || '');
    case 'tool_delta':
      return parts.map((p) =>
        p.type === 'tool' && p.id === event.id ? { ...p, input: (p.input || '') + (event.input || '') } : p,
      );
    case 'tool_end':
      return parts.map((p) => {
        if (p.type !== 'tool' || p.id !== event.id) return p;
        return {
          ...p,
          status: event.status === 'success' ? 'success' : 'error',
          ...(event.input !== undefined ? { input: event.input } : {}),
          ...(event.output ? { output: event.output } : {}),
          ...(event.duration_ms !== undefined ? { duration_ms: event.duration_ms } : {}),
          ...(event.files !== undefined ? { files: event.files } : {}),
        };
      });
    case 'plan_start':
      return [...parts, { type: 'plan', content: '' }];
    case 'plan_delta':
      return parts.map((p) => (p.type === 'plan' ? { ...p, content: p.content + event.content } : p));
    case 'plan_end':
      return parts.map((p) => (p.type === 'plan' ? { ...p, content: event.content || p.content } : p));
    case 'steer_injected': {
      const steerPart: MessagePart = {
        type: 'steer',
        content: event.content || '',
        ...(event.steer_id ? { steer_id: event.steer_id } : {}),
      };
      return [...parts, steerPart];
    }
    default:
      return parts;
  }
}

function settleRunningTools(parts: MessagePart[]): MessagePart[] {
  // A tool that is still 'running' when the turn reaches a terminal state was
  // interrupted (awaiting approval) or aborted — never finish with a live
  // spinner. Demote it to 'pending' (static, non-spinning).
  return parts.map((part) => {
    if (part.type === 'tool' && part.status === 'running') {
      return { ...part, status: 'pending' as const };
    }
    // A worker (agent part) still 'running' when the turn terminates means the
    // turn was stopped/interrupted before delegate_end arrived — the main
    // stream is aborted client-side, so the backend's delegate_end never
    // reaches us. Settle it to a stopped/error state so the "Delegating…"
    // spinner stops instead of spinning forever.
    if (part.type === 'agent' && part.status === 'running') {
      return { ...part, status: 'error' as const, error: t('chat.meta_stopped'), done: true };
    }
    return part;
  });
}

/**
 * 流式文本渲染节流。每 token 都触发 setMessages 会让 React 对整段累积文本全量
 * 重解析 markdown（长回复/代码块尤其重），主线程被占满 → 文本「卡住不动、
 * 稍后一次性补齐」。这里数据仍实时累积（streamedContent/localParts），仅把
 * UI 推送限频；终态时 flushNow() 一次补齐。
 */
function createStreamThrottle(flush: () => void, intervalMs = 60) {
  let lastFlush = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const update = () => {
    const now = Date.now();
    const elapsed = now - lastFlush;
    if (elapsed >= intervalMs) {
      lastFlush = now;
      flush();
    } else if (timer === undefined) {
      timer = setTimeout(() => {
        timer = undefined;
        lastFlush = Date.now();
        flush();
      }, intervalMs - elapsed);
    }
  };
  const flushNow = () => {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
    flush();
  };
  return { update, flushNow };
}

/**
 * Apply a delegation frame (delegate_start / delegate_progress / delegate_end)
 * to a parts array: creates the worker PartAgent block on start, updates its
 * status/error/chars on progress/end. Shared by the main stream, edit/rerun and
 * resume paths so the worker block renders in every turn-start mode.
 */
function applyDelegateEventToParts(parts: MessagePart[], event: StreamEvent): MessagePart[] {
  if (event.type === 'delegate_start') {
    const runId =
      event.worker_run_id ||
      `delegate-${Date.now()}-${parts.length}-${Math.random().toString(16).slice(2)}`;
    const existingIdx = parts.findIndex((p) => p.type === 'agent' && p.workerRunId === runId);
    const delegatePart: PartAgent = {
      type: 'agent',
      workerRunId: runId,
      from: event.from || '',
      to: event.to || '',
      task: event.task,
      status: 'running',
      parallel: event.parallel,
      parts: [],
    };
    if (existingIdx >= 0) {
      return parts.map((p, i) =>
        i === existingIdx ? { ...delegatePart, parts: (p as PartAgent).parts } : p,
      );
    }
    return [...parts, delegatePart];
  }
  if (event.type !== 'delegate_progress' && event.type !== 'delegate_end') return parts;
  const runId = event.worker_run_id || '';
  // A delegation frame without a worker_run_id cannot be attributed to any
  // specific block — never apply it to *every* agent part (an empty runId used
  // to match all of them, corrupting sibling worker blocks in the same message).
  if (!runId) return parts;
  return parts.map((p) => {
    if (p.type !== 'agent') return p;
    if (p.workerRunId !== runId) return p;
    if (event.type === 'delegate_progress') {
      return {
        ...p,
        status: event.status === 'error' || event.error ? ('error' as const) : p.status,
        ...(event.error ? { error: event.error } : {}),
      };
    }
    if (event.type === 'delegate_end') {
      return {
        ...p,
        status: event.error ? ('error' as const) : ('done' as const),
        ...(typeof event.chars === 'number' ? { chars: event.chars } : {}),
        ...(event.failed !== undefined ? { failed: event.failed } : {}),
        ...(event.error ? { error: event.error } : {}),
      };
    }
    return p;
  });
}

function upsertToolPart(parts: MessagePart[], id: string, name: string, input: string): MessagePart[] {
  // Dedupe by tool call id: a resumed graph re-emits the same tool, so update
  // the existing part instead of stacking a duplicate card.
  const next = [...parts];
  const index = next.findIndex((p): p is Extract<MessagePart, { type: 'tool' }> => p.type === 'tool' && p.id === id);
  const part: MessagePart = { type: 'tool', id, name, status: 'running' as const, input };
  if (index >= 0) {
    next[index] = part;
  } else {
    next.push(part);
  }
  return next;
}

/**
 * Merge the live worker transcript back into the main stream's parts write.
 *
 * The main stream accumulates a separate `localParts` closure (the delegate
 * *summary frame* only: `{workerRunId, status, chars, parts: []}`), while the
 * worker's internal transcript (nested `parts` + `transcriptLoaded`) is written
 * into React state by `subscribeWorkerTranscript`. A plain `parts: [...localParts]`
 * write therefore overwrites the worker transcript every time the main agent
 * streams — resetting `transcriptLoaded`, so an open worker block keeps falling
 * back to the "正在加载 worker 流…" placeholder. This merge makes the main
 * stream's write non-destructive: summary fields come from `nextParts`, the live
 * transcript state comes from the current message.
 */
function mergeLiveAgentTranscript(nextParts: MessagePart[], currentParts: MessagePart[] | undefined): MessagePart[] {
  if (!currentParts) return nextParts;
  return nextParts.map((np) => {
    if (np.type !== 'agent') return np;
    const cur = currentParts.find(
      (cp): cp is Extract<MessagePart, { type: 'agent' }> =>
        cp.type === 'agent' && cp.workerRunId === np.workerRunId,
    );
    if (!cur) return np;
    return {
      ...np,
      parts: cur.parts ?? np.parts,
      ...(cur.transcriptLoaded !== undefined ? { transcriptLoaded: cur.transcriptLoaded } : {}),
      ...(cur.done !== undefined ? { done: cur.done } : {}),
      ...(cur.error ? { error: cur.error } : {}),
    };
  });
}

/**
 * Reconcile a stream's terminal state against the backend's committed truth.
 *
 * The backend persists the assistant message (with the client-supplied
 * `assistant_message_id`) in its `done` handler BEFORE writing the `done` SSE
 * frame. That means by the time a stream settles, a completed reply is already
 * durable server-side regardless of whether the client ever received `done`.
 *
 * If the `done` frame is dropped somewhere in the untracked
 * SSE → Electron IPC → renderer hop, the old fallback marked the still-running
 * bubble `interrupted` (orange) even though the backend fully replied. This
 * helper re-fetches the session and, when the message id is present, adopts the
 * backend's committed content — turning the dropped-terminal case into a
 * successful commit (blue → done) instead of a false orange bar.
 *
 * Returns null when the backend has no record for this message id (genuinely
 * interrupted / aborted before commit / backend unreachable).
 */
async function findCommittedAssistantMessage(
  sessionId: string | undefined,
  assistantMessageId: string,
  timeoutMs = 3000,
): Promise<{ content: string; parts: MessagePart[] } | null> {
  if (!sessionId) return null;
  try {
    const response = await Promise.race([
      chatService.getSession(sessionId),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('reconcile timeout')), timeoutMs),
      ),
    ]);
    const committed = (response.session.messages ?? []).find(
      (record) => record.role === 'assistant' && record.id === assistantMessageId,
    );
    if (committed) {
      return {
        content: committed.content ?? '',
        parts: normalizeParts((committed.parts as MessagePart[] | undefined) ?? []),
      };
    }
  } catch {
    // Backend unreachable / reconcile timeout — caller falls back to "interrupted".
  }
  return null;
}

type ApprovalStreamEvent = Extract<
  StreamEvent,
  { type: 'approval_required' } | { type: 'question_required' }
>;

/**
 * Single mapping from an interrupt stream event to a `PendingRequest`.
 *
 * All call paths (stream, non-stream and resume) funnel through
 * here so a new interrupt field only has to be handled once.
 */
function pendingRequestFromEvent(
  event: ApprovalStreamEvent,
  sessionId: string,
  messageId: string,
): PendingRequest {
  const base: PendingRequest = {
    approval_id: event.approval_id,
    kind:
      event.type === 'approval_required'
        ? event.kind === 'mcp'
          ? 'mcp'
          : 'command'
        : event.type === 'question_required'
          ? 'question'
          : 'plan',
    session_id: event.session_id ?? sessionId,
    approval_status: event.approval_status,
    messageId,
  };
  if (event.type === 'question_required') {
    return {
      ...base,
      ...(event.question !== undefined ? { question: event.question } : {}),
      ...(event.header !== undefined ? { header: event.header } : {}),
      ...(event.options !== undefined ? { options: event.options } : {}),
      ...(event.multiple !== undefined ? { multiple: event.multiple } : {}),
      ...(event.allowCustom !== undefined ? { allowCustom: event.allowCustom } : {}),
    };
  }
  if (event.kind === 'mcp') {
    return {
      ...base,
      tool_name: event.tool_name ?? '',
      tool_args: event.tool_args ?? {},
      server_name: event.server_name ?? '',
      server_id: event.server_id ?? '',
      remote_name: event.remote_name ?? '',
      read_only: Boolean(event.read_only),
      destructive: Boolean(event.destructive),
    };
  }
  return {
    ...base,
    command: event.command,
    cwd: event.cwd,
    ...(event.tool_name ? { tool_name: event.tool_name } : {}),
    ...(event.tool_args && Object.keys(event.tool_args).length > 0 ? { tool_args: event.tool_args } : {}),
  };
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

/**
 * True when any modal / menu / overlay that should consume Esc is currently
 * open. All of the app's popups (DetailModal, SideDrawer, CreateProjectDialog,
 * context menu, radix menus) are only mounted while open, so their DOM markers
 * are a reliable "popup open" signal.
 */
function hasOpenOverlay(): boolean {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"], [role="menu"], [role="listbox"]'));
}

/**
 * True when the Esc key originated inside an editable control (input/textarea/
 * contenteditable). On non-chat pages a first Esc then only blurs the control
 * so the inner field's own cancel logic isn't shadowed by page navigation.
 */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target === document.body) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable;
}

// Map a raw `context_usage` event (snake_case) into the frontend ContextUsage
// shape. Shared by the streaming event handler AND the session-open preview
// fetch so both paths render an identical indicator.
function mapContextUsage(e: any): ContextUsage {
  return {
    usedChars: e.used_chars,
    budgetChars: e.budget_chars,
    compressed: e.compressed,
    usedTokens: e.used_tokens,
    budgetTokens: e.budget_tokens,
    windowTokens: e.window_tokens,
    compacted: e.compacted,
    compactCount: e.compact_count,
    windowSource: e.window_source,
    ...(e.active_budget_tokens != null ? { activeBudgetTokens: e.active_budget_tokens } : {}),
    ...(e.window_warning ? { windowWarning: e.window_warning } : {}),
    ...(e.used_tokens_calibrated != null ? { usedTokensCalibrated: e.used_tokens_calibrated } : {}),
    ...(e.calibration_factor != null ? { calibrationFactor: e.calibration_factor } : {}),
    ...(e.effective_window_tokens != null ? { effectiveWindowTokens: e.effective_window_tokens } : {}),
    ...(e.max_output_tokens != null ? { maxOutputTokens: e.max_output_tokens } : {}),
  };
}


export {
  normalizeAgentPart,
  normalizeParts,
  mergeMessageParts,
  applyStreamEventToParts,
  settleRunningTools,
  createStreamThrottle,
  applyDelegateEventToParts,
  upsertToolPart,
  mergeLiveAgentTranscript,
  findCommittedAssistantMessage,
  pendingRequestFromEvent,
  createMessage,
  mapContextUsage,
};
