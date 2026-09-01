import { useState, useCallback, useEffect, useRef } from 'react';
import { Command, HelpCircle, Plug } from 'lucide-react';
import { Tooltip } from '@/components/ui/tooltip';
import { CardSlot } from '@/components/ui/card-slot';
import { t } from '@/lib/i18n';
import type { ApprovalDecisionPayload, ApprovalOption, PendingRequest } from '@/types';

interface PendingDocksProps {
  requests: PendingRequest[];
  onResolve: (request: PendingRequest, decision: ApprovalDecisionPayload) => Promise<void>;
  onDismiss?: (request: PendingRequest) => void;
  onStop?: () => void;
}

/**
 * 将命令 argv 转成一句话「操作目标」，先于命令行展示，做到产品化表达。
 * 仅覆盖常见场景，未命中则回退到通用描述。文案为中文（命令本身语言无关）。
 */
function describeCommand(argv: string[] | undefined, _cwd?: string): string {
  if (!argv || argv.length === 0) return '执行命令';
  const bin = argv[0];
  if (!bin) return '执行命令';
  const rest = argv.slice(1);
  const isDev = rest.some((a) => a === '-D' || a === '--save-dev' || a === '--dev');
  const script = rest.find((a) => !a.startsWith('-') && a !== 'run' && a !== 'exec');

  if (/^(npm|pnpm|yarn|bun)$/i.test(bin)) {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'install' || sub === 'i' || sub === 'add') {
      const names = rest.slice(1).filter((a) => !a.startsWith('-'));
      if (names.length) return `安装依赖：${names.join('、')}${isDev ? '（开发依赖）' : ''}`;
      return '安装项目依赖（按 package.json）';
    }
    if (sub === 'run' || sub === 'exec') {
      if (script === 'build') return '构建项目（运行 build 脚本）';
      if (script === 'dev' || script === 'start') return '启动开发服务器';
      if (script === 'test') return '运行测试';
      if (script) return `运行脚本：${script}`;
      return '运行 package.json 脚本';
    }
    if (sub === 'build') return '构建项目';
    if (sub === 'test') return '运行测试';
    return `执行 ${bin} 命令`;
  }
  if (bin === 'git') {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'commit') {
      const mIdx = rest.findIndex((a) => a === '-m' || a === '--message');
      const raw = rest[mIdx + 1];
      const msg = raw ? raw.replace(/^["']|["']$/g, '') : '';
      return msg ? `提交代码改动：${msg}` : '提交代码改动';
    }
    if (sub === 'push') {
      const branch = rest.find((a) => !a.startsWith('-'));
      return `推送分支${branch ? ` ${branch}` : ' 当前分支'}到远程`;
    }
    if (sub === 'pull') return '拉取并合并远程改动';
    if (sub === 'clone') return '克隆仓库';
    if (sub === 'checkout' || sub === 'switch') {
      const hasNew = rest.includes('-b') || rest.includes('-c');
      const flag = rest.includes('-b') ? '-b' : rest.includes('-c') ? '-c' : '';
      const br = flag ? (rest[rest.indexOf(flag) + 1] ?? '') : (rest.find((a) => !a.startsWith('-') && a !== '-b' && a !== '-c') ?? '');
      if (br) return hasNew ? `创建并切换到分支 ${br}` : `切换到分支 ${br}`;
      return '切换分支';
    }
    if (sub === 'merge') return `合并分支 ${rest.find((a) => !a.startsWith('-')) || ''}`.trim();
    if (['status', 'diff', 'log', 'show'].includes(sub)) return `查看 git ${sub} 信息`;
    return '执行 git 命令';
  }
  if (bin === 'pip' || bin === 'pip3') {
    if ((rest[0] || '').toLowerCase() === 'install') {
      const names = rest.slice(1).filter((a) => !a.startsWith('-'));
      return names.length ? `安装 Python 依赖：${names.join('、')}` : '安装 Python 依赖';
    }
    return '执行 pip 命令';
  }
  if (bin === 'python' || bin === 'python3') {
    if (rest[0] === '-m' && rest[1] === 'venv') return `创建 Python 虚拟环境 ${rest[2] || ''}`.trim();
    const py = rest.find((a) => a.endsWith('.py'));
    return py ? `运行 Python 脚本 ${py}` : '运行 Python 脚本';
  }
  if (bin === 'node') {
    const js = rest.find((a) => a.endsWith('.js') || a.endsWith('.mjs'));
    return js ? `运行 Node 脚本 ${js}` : '运行 Node 脚本';
  }
  if (bin === 'rm' || bin === 'del') return `删除文件/目录${rest.some((a) => a.includes('r')) ? '（递归）' : ''}`;
  if (bin === 'mv') return '移动或重命名文件';
  if (bin === 'cp' || bin === 'copy') return '复制文件';
  if (bin === 'mkdir') return `创建目录 ${rest.find((a) => !a.startsWith('-')) || ''}`.trim();
  if (bin === 'touch') return '创建空文件';
  if (bin === 'curl' || bin === 'wget') return '下载文件';
  if (bin === 'chmod') return '修改文件权限';
  if (bin === 'docker') {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'compose') return rest[1] === 'up' ? '启动 Docker 服务' : rest[1] === 'build' ? '构建 Docker 镜像' : '执行 docker compose 命令';
    if (sub === 'build') return '构建 Docker 镜像';
    if (sub === 'run') return '运行 Docker 容器';
    return '执行 docker 命令';
  }
  if (['ls', 'cat', 'grep', 'find', 'head', 'tail', 'echo', 'pwd'].includes(bin)) return `查看文件信息（${bin}）`;
  return `执行命令：${bin}`;
}

/**
 * Human-readable intent for a NON-command tool approval (write_file, memory,
 * …). Backend classifies these as "command" with an empty argv, so without
 * this the card would show nothing actionable. Falls back to the raw tool name.
 */
function describeToolCall(name: string, args?: Record<string, unknown>): string {
  if (!name) return '执行操作';
  const path = typeof args?.path === 'string' && args.path ? args.path : '';
  const label =
    name === 'write_file' ? '写入文件'
    : name === 'replace_in_file' || name === 'apply_text_edits' || name === 'edit_file' ? '修改文件'
    : name === 'read_file' ? '读取文件'
    : name === 'memory' || name === 'remember' ? '读写长期记忆'
    : name === 'web_search' ? '网页搜索'
    : name === 'fetch_web' ? '抓取网页内容'
    : name;
  return path ? `${label} ${path}` : label;
}

/** Pretty-print tool args; truncates large payloads (e.g. write_file content). */
function formatToolArgs(args: Record<string, unknown> | undefined): string {
  if (!args || Object.keys(args).length === 0) return '';
  try {
    const text = JSON.stringify(args, null, 2);
    return text.length > 600 ? `${text.slice(0, 600)}\n…` : text;
  } catch {
    return String(args);
  }
}

function ApprovalDock({ request, onResolve, onStop }: { request: PendingRequest & { kind: 'command' }; onResolve: (req: PendingRequest & { kind: 'command' }, decision: ApprovalDecisionPayload) => Promise<void>; onStop?: () => void }) {
  const [resolving, setResolving] = useState(false);

  const dispatch = useCallback(async (decision: ApprovalDecisionPayload) => {
    if (resolving) return;
    setResolving(true);
    try {
      await onResolve(request, decision);
    } finally {
      setResolving(false);
    }
  }, [resolving, request, onResolve]);

  const intent = request.command && request.command.length > 0
    ? describeCommand(request.command, request.cwd)
    : describeToolCall(request.tool_name ?? '', request.tool_args);
  const toolArgsText = formatToolArgs(request.tool_args);

  return (
    <div className="pending-dock__body">
      <div className="pending-dock__intent">
        <span className="pending-dock__intent-icon" aria-hidden>▶</span>
        <span className="pending-dock__intent-text">
          <span className="pending-dock__intent-eyebrow">{t('chat.approval_intent')}</span>
          <span className="pending-dock__intent-desc">{intent}</span>
        </span>
      </div>
      {request.command && request.command.length > 0 ? (
        <div className="pending-dock__command">
          <span className="pending-dock__command-line">
            {request.command.join(' ')}
          </span>
          {request.cwd ? (
            <span className="pending-dock__command-cwd">
              cd {request.cwd}
            </span>
          ) : null}
        </div>
      ) : request.tool_name ? (
        <div className="pending-dock__command">
          <span className="pending-dock__command-line">{request.tool_name}</span>
          {toolArgsText ? <pre className="pending-dock__command-args">{toolArgsText}</pre> : null}
        </div>
      ) : null}
      <div className="pending-dock__footer" data-slot="approval-actions">
        <button
          type="button"
          className="button-ghost"
          onClick={() => {
            onStop?.();
            dispatch({ type: 'reject' });
          }}
          disabled={resolving}
        >
          {t('chat.approval_disagree')}
        </button>
        <button
          type="button"
          className="button-secondary"
          onClick={() => dispatch({ type: 'always' })}
          disabled={resolving}
        >
          {t('chat.approval_always', { command: request.command?.[0] || request.tool_name || '' })}
        </button>
        <button
          type="button"
          className="button-primary"
          onClick={() => dispatch({ type: 'approve' })}
          disabled={resolving}
        >
          {t('chat.approval_once')}
        </button>
      </div>
    </div>
  );
}

/**
 * Approval card for an external MCP tool call.
 *
 * MCP calls leave the workspace sandbox, so the card names the server and the
 * remote tool explicitly instead of showing an argv line, and "always allow"
 * is scoped to that one server+tool pair.
 */
function McpApprovalDock({ request, onResolve, onStop }: { request: PendingRequest & { kind: 'mcp' }; onResolve: (req: PendingRequest & { kind: 'mcp' }, decision: ApprovalDecisionPayload) => Promise<void>; onStop?: () => void }) {
  const [resolving, setResolving] = useState(false);

  const dispatch = useCallback(async (decision: ApprovalDecisionPayload) => {
    if (resolving) return;
    setResolving(true);
    try {
      await onResolve(request, decision);
    } finally {
      setResolving(false);
    }
  }, [resolving, request, onResolve]);

  const toolLabel = request.remote_name || request.tool_name || '';
  const serverLabel = request.server_name || request.server_id || '';
  const argsText = formatToolArgs(request.tool_args);

  return (
    <div className="pending-dock__body">
      <div className="pending-dock__intent">
        <span className="pending-dock__intent-icon" aria-hidden>▶</span>
        <span className="pending-dock__intent-text">
          <span className="pending-dock__intent-eyebrow">{t('chat.approval_intent')}</span>
          <span className="pending-dock__intent-desc">
            {t('chat.mcp_approval_intent', { tool: toolLabel, server: serverLabel })}
          </span>
        </span>
      </div>
      {request.destructive ? (
        <div className="pending-dock__warning" data-slot="mcp-destructive">
          {t('chat.mcp_approval_destructive')}
        </div>
      ) : null}
      <div className="pending-dock__command">
        <span className="pending-dock__command-line">{request.tool_name || toolLabel}</span>
        {argsText ? <pre className="pending-dock__command-args">{argsText}</pre> : null}
      </div>
      <div className="pending-dock__footer" data-slot="approval-actions">
        <button
          type="button"
          className="button-ghost"
          onClick={() => {
            onStop?.();
            dispatch({ type: 'reject' });
          }}
          disabled={resolving}
        >
          {t('chat.approval_disagree')}
        </button>
        <button
          type="button"
          className="button-secondary"
          onClick={() => dispatch({ type: 'always' })}
          disabled={resolving}
        >
          {t('chat.approval_always', { command: toolLabel })}
        </button>
        <button
          type="button"
          className="button-primary"
          onClick={() => dispatch({ type: 'approve' })}
          disabled={resolving}
        >
          {t('chat.approval_once')}
        </button>
      </div>
    </div>
  );
}

function QuestionDock({ request, total, onResolve, onStop }: { request: PendingRequest & { kind: 'question' }; total?: number; onResolve: (req: PendingRequest & { kind: 'question' }, decision: ApprovalDecisionPayload) => Promise<void>; onStop?: () => void }) {
  const [resolving, setResolving] = useState(false);
  // Use refs to preserve form state across view switches (component unmount/remount)
  const pickedRef = useRef<number[]>([]);
  const customAnswerRef = useRef('');
  const isComposingRef = useRef(false);
  // Sync refs to state for rendering, but only once on mount
  const [picked, setPicked] = useState(() => { pickedRef.current = request._savedPicked || []; return pickedRef.current; });
  const [customAnswer, setCustomAnswer] = useState(() => { customAnswerRef.current = request._savedAnswer || ''; return customAnswerRef.current; });

  // Save state before unmount
  useEffect(() => {
    return () => {
      request._savedPicked = [...pickedRef.current];
      request._savedAnswer = customAnswerRef.current;
    };
  }, []);

  // Sync state → ref on change
  useEffect(() => { pickedRef.current = picked; }, [picked]);
  useEffect(() => { customAnswerRef.current = customAnswer; }, [customAnswer]);

  // Auto-grow answer textarea to fit content (composer style)
  const answerInputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = answerInputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [customAnswer]);

  // 默认允许"其他（自由文本）"作为最后一个显式选项，
  // 但必须先选中该选项才能填写，杜绝"不选任何选项只填空"绕过选择。
  const allowCustom = request.allowCustom !== false;
  const otherIndex = request.options?.length ?? 0;
  const pickedOther = picked.includes(otherIndex);

  const hasAnswer = request.options
    ? picked.length > 0 && (!pickedOther || customAnswer.trim() !== '')
    : customAnswer.trim() !== '';

  const dispatch = useCallback(async (decision: ApprovalDecisionPayload) => {
    if (resolving) return;
    setResolving(true);
    try {
      await onResolve(request, decision);
    } finally {
      setResolving(false);
    }
  }, [resolving, request, onResolve]);

  const handleSubmit = useCallback(async () => {
    if (resolving || !hasAnswer) return;
    if (request.options) {
      if (request.multiple) {
        const labels = picked
          .filter((i) => i !== otherIndex)
          .map((i) => request.options?.[i]?.label)
          .filter(Boolean) as string[];
        const message = pickedOther && customAnswer.trim()
          ? [...labels, customAnswer.trim()].join(', ')
          : labels.join(', ');
        await dispatch({ type: 'respond', message });
      } else if (pickedOther) {
        await dispatch({ type: 'respond', message: customAnswer.trim() });
      } else if (picked.length > 0) {
        await dispatch({ type: 'respond', message: request.options[picked[0]!]?.label ?? '' });
      } else {
        await dispatch({ type: 'respond', message: '' });
      }
    } else {
      await dispatch({ type: 'respond', message: customAnswer.trim() || '' });
    }
  }, [resolving, hasAnswer, request.multiple, request.options, request.question, otherIndex, pickedOther, picked, customAnswer, dispatch]);

  const showProgress = typeof total === 'number' && total > 1;

  return (
    <div className="pending-dock__body">
      {showProgress ? (
        <p className="pending-dock__question-hint">
          {t('chat.question_progress', { total })}
        </p>
      ) : null}
      <div className="pending-dock__question" data-slot="question-content">
        {request.header ? <h4 className="pending-dock__question-hint">{request.header}</h4> : null}
        <p className="pending-dock__question-text">{request.question || t('chat.question_empty_fallback')}</p>
      </div>
      {request.options && request.options.length > 0 ? (
        <fieldset className="pending-dock__options" data-slot="question-options">
          {request.options.map((option, optionIndex) => {
            const isPicked = picked.includes(optionIndex);
            return (
              <label
                key={`${option.label}-${optionIndex}`}
                className="pending-dock__option"
                data-slot="question-option"
                data-type={request.multiple ? 'checkbox' : 'radio'}
                data-picked={isPicked}
              >
                <input
                  type={request.multiple ? 'checkbox' : 'radio'}
                  name={request.approval_id}
                  checked={isPicked}
                  onChange={() => {
                    if (request.multiple) {
                      setPicked((prev) =>
                        prev.includes(optionIndex)
                          ? prev.filter((i) => i !== optionIndex)
                          : [...prev, optionIndex],
                      );
                    } else {
                      setPicked((prev) =>
                        prev.includes(optionIndex) ? [] : [optionIndex],
                      );
                    }
                  }}
                  className="pending-dock__option-native"
                />
                <span
                  className="pending-dock__option-box"
                  data-type={request.multiple ? 'checkbox' : 'radio'}
                  data-picked={isPicked}
                  aria-hidden="true"
                >
                  <span className="pending-dock__option-check" />
                </span>
                <span className="pending-dock__option-main">
                  <span className="pending-dock__option-label">{option.label}</span>
                  {option.description ? <span className="pending-dock__option-description">{option.description}</span> : null}
                </span>
              </label>
            );
          })}
          {allowCustom ? (
            <label
              key="__other__"
              className="pending-dock__option"
              data-slot="question-option"
              data-type={request.multiple ? 'checkbox' : 'radio'}
              data-picked={pickedOther}
            >
              <input
                type={request.multiple ? 'checkbox' : 'radio'}
                name={request.approval_id}
                checked={pickedOther}
                onChange={() => {
                  if (request.multiple) {
                    setPicked((prev) =>
                      prev.includes(otherIndex)
                        ? prev.filter((i) => i !== otherIndex)
                        : [...prev, otherIndex],
                    );
                  } else {
                    setPicked((prev) => (prev.includes(otherIndex) ? [] : [otherIndex]));
                  }
                }}
                className="pending-dock__option-native"
              />
              <span
                className="pending-dock__option-box"
                data-type={request.multiple ? 'checkbox' : 'radio'}
                data-picked={pickedOther}
                aria-hidden="true"
              >
                <span className="pending-dock__option-check" />
              </span>
              <span className="pending-dock__option-main">
                <span className="pending-dock__option-label">{t('chat.question_other_option')}</span>
              </span>
            </label>
          ) : null}
        </fieldset>
      ) : null}
      {(!request.options || (allowCustom && pickedOther)) ? (
        <textarea
          ref={answerInputRef}
          className="pending-dock__option-input"
          rows={2}
          placeholder={request.options ? t('chat.question_other_placeholder') : (t('chat.question_custom_input') || t('chat.question_input_placeholder'))}
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
              e.preventDefault();
              handleSubmit();
            }
          }}
        />
      ) : null}
      <div className="pending-dock__footer">
        <button
          type="button"
          className="button-ghost"
          onClick={() => {
            onStop?.();
            dispatch({ type: 'reject' });
          }}
          disabled={resolving}
        >
          {t('chat.approval_disagree')}
        </button>
        <button
          type="button"
          className="button-primary"
          onClick={handleSubmit}
          disabled={!hasAnswer || resolving}
        >
          {t('chat.question_submit')}
        </button>
      </div>
    </div>
  );
}

export function PendingDocks({ requests, onResolve, onDismiss, onStop }: PendingDocksProps) {
  const front = requests[0];
  if (!front) return null;

  const isResolving = front.resolving;
  const kindLabel = front.kind === 'command'
    ? t('chat.kind_command')
    : front.kind === 'mcp'
      ? t('chat.kind_mcp')
      : front.kind === 'question'
        ? t('chat.kind_question')
        : t('chat.kind_plan');
  const titleText = front.kind === 'command'
    ? t('chat.command_pending')
    : front.kind === 'mcp'
      ? t('chat.mcp_pending')
      : front.kind === 'question'
        ? t('chat.question_pending')
        : t('chat.plan_pending');
  const questionTotal = requests.filter((r) => r.kind === 'question').length;

  return (
    <div className="pending-docks" data-slot="pending-docks">
      <CardSlot
        key={front.approval_id}
        className={`pending-dock ${isResolving ? 'pending-dock--resolving' : ''}`}
        data-slot="pending-dock"
        data-approval-id={front.approval_id}
      >
        <div className="pending-dock__header">
          <div className="pending-dock__title">
            <span className="pending-dock__icon">
              {front.kind === 'command' ? <Command size={14} /> : front.kind === 'mcp' ? <Plug size={14} /> : <HelpCircle size={14} />}
            </span>
            <span className="pending-dock__title-text">{titleText}</span>
            <span className="pending-dock__kind">{kindLabel}</span>
          </div>
          <div className="pending-dock__header-actions">
            <Tooltip content={t('chat.approval_reject_close')}>
              <button
                type="button"
                className="pending-action-button button-ghost"
                onClick={() => {
                  onStop?.();
                  onDismiss?.(front);
                }}
                disabled={isResolving}
                aria-label={t('chat.approval_reject_close')}
              >
                ✕
              </button>
            </Tooltip>
          </div>
        </div>
        {front.kind === 'command' ? (
          <ApprovalDock request={front as PendingRequest & { kind: 'command' }} onResolve={onResolve} {...(onStop ? { onStop } : {})} />
        ) : front.kind === 'mcp' ? (
          <McpApprovalDock request={front as PendingRequest & { kind: 'mcp' }} onResolve={onResolve} {...(onStop ? { onStop } : {})} />
        ) : front.kind === 'question' ? (
          <QuestionDock request={front as PendingRequest & { kind: 'question' }} total={questionTotal} onResolve={onResolve} {...(onStop ? { onStop } : {})} />
        ) : null}
      </CardSlot>
    </div>
  );
}
