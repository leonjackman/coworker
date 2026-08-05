import { useState, useCallback, useEffect, useRef } from 'react';
import { Command, HelpCircle, ListChecks } from 'lucide-react';
import { Tooltip } from '@/components/ui/tooltip';
import { t } from '@/lib/i18n';
import type { ApprovalDecisionPayload, ApprovalOption, PendingRequest } from '@/types';

interface PendingDocksProps {
  requests: PendingRequest[];
  onResolve: (request: PendingRequest, decision: ApprovalDecisionPayload) => Promise<void>;
  onDismiss?: (request: PendingRequest) => void;
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

function ApprovalDock({ request, onResolve }: { request: PendingRequest & { kind: 'command' }; onResolve: (req: PendingRequest & { kind: 'command' }, decision: ApprovalDecisionPayload) => Promise<void> }) {
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

  const intent = describeCommand(request.command, request.cwd);

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
      ) : null}
      <div className="pending-dock__footer" data-slot="approval-actions">
        <button
          type="button"
          className="button-ghost"
          onClick={() => dispatch({ type: 'reject' })}
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
          {t('chat.approval_always', { command: request.command?.[0] || '' })}
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

function QuestionDock({ request, index, total, onResolve }: { request: PendingRequest & { kind: 'question' }; index?: number; total?: number; onResolve: (req: PendingRequest & { kind: 'question' }, decision: ApprovalDecisionPayload) => Promise<void> }) {
  const [resolving, setResolving] = useState(false);
  // Use refs to preserve form state across view switches (component unmount/remount)
  const pickedRef = useRef<number[]>([]);
  const customAnswerRef = useRef('');
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

  const hasAnswer = request.options
    ? (request.multiple
        ? picked.length > 0
        : picked.length > 0 || customAnswer.trim() !== '')
    : true;

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
      if (request.multiple && picked.length > 0) {
        await dispatch({ type: 'respond', message: picked
          .map((i) => request.options?.[i]?.label)
          .filter(Boolean)
          .join(', ') });
      } else if (customAnswer.trim()) {
        await dispatch({ type: 'respond', message: customAnswer.trim() });
      } else {
        await dispatch({ type: 'respond', message: '' });
      }
    } else {
      await dispatch({ type: 'respond', message: customAnswer.trim() || '' });
    }
  }, [resolving, hasAnswer, request.multiple, request.options, request.question, picked, customAnswer, dispatch]);

  const showProgress = typeof total === 'number' && total > 1;

  return (
    <div className="pending-dock__body">
      {showProgress ? (
        <p className="pending-dock__question-hint">
          {t('chat.question_progress', { total, index: index ?? 1 })}
        </p>
      ) : null}
      <div className="pending-dock__question" data-slot="question-content">
        {request.header ? <h4 className="pending-dock__question-hint">{request.header}</h4> : null}
        <p className="pending-dock__question-text">{request.question}</p>
      </div>
      {request.options && request.options.length > 0 ? (
        <fieldset className="pending-dock__options" data-slot="question-options">
          {request.options.map((option, optionIndex) => {
            const isPicked = picked.includes(optionIndex);
            return (
              <label
                key={`${option.label}-${optionIndex}`}
                className="pending-dock__option-box"
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
                />
                <div className="pending-dock__option-main">
                  <span className="pending-dock__option-label">{option.label}</span>
                  {option.description ? <span className="pending-dock__option-description">{option.description}</span> : null}
                </div>
              </label>
            );
          })}
        </fieldset>
      ) : null}
      <div className="pending-dock__option-input">
        <input
          placeholder={t('chat.question_custom_input') || t('chat.question_input_placeholder')}
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
        />
      </div>
      <div className="pending-dock__footer">
        <button
          type="button"
          className="button-ghost"
          onClick={() => dispatch({ type: 'reject' })}
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

function PlanDock({ request, onResolve }: { request: PendingRequest & { kind: 'plan' }; onResolve: (req: PendingRequest & { kind: 'plan' }, decision: ApprovalDecisionPayload) => Promise<void> }) {
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

  return (
    <div className="pending-dock__body">
      <div className="pending-dock__question" data-slot="plan-content">
        <p className="pending-dock__plan-text">{request.plan || ''}</p>
      </div>
      <div className="pending-dock__footer" data-slot="plan-actions">
        <button
          type="button"
          className="button-ghost"
          onClick={() => dispatch({ type: 'reject' })}
          disabled={resolving}
        >
          {t('chat.plan_reject')}
        </button>
        <button
          type="button"
          className="button-secondary"
          onClick={() => dispatch({ type: 'regenerate' })}
          disabled={resolving}
        >
          {t('chat.plan_regenerate')}
        </button>
        <button
          type="button"
          className="button-primary"
          onClick={() => dispatch({ type: 'approve' })}
          disabled={resolving}
        >
          {t('chat.plan_approve')}
        </button>
      </div>
    </div>
  );
}

export function PendingDocks({ requests, onResolve, onDismiss }: PendingDocksProps) {
  // 计算问题类请求在全部 pending 中的序号，用于展示 (i/N) 进度
  const questionOrder = new Map<string, number>();
  let questionSeq = 0;
  for (const r of requests) {
    if (r.kind === 'question') {
      questionSeq += 1;
      questionOrder.set(r.approval_id, questionSeq);
    }
  }
  const questionTotal = questionSeq;

  return (
    <div className="pending-docks" data-slot="pending-docks">
      {requests.map((request) => {
        const isResolving = request.resolving;
        const kindLabel = request.kind === 'command'
          ? t('chat.kind_command')
          : request.kind === 'question'
            ? t('chat.kind_question')
            : t('chat.kind_plan');
        const isQuestion = request.kind === 'question';
        const qIndex = isQuestion ? questionOrder.get(request.approval_id) : undefined;
        const qTotal = isQuestion ? questionTotal : undefined;
        const showProgress = isQuestion && (qTotal ?? 0) > 1;
        const progressPct = showProgress && qTotal ? Math.round(((qIndex ?? 1) / qTotal) * 100) : 0;

        return (
          <div
            key={request.approval_id}
            className={`pending-dock ${isResolving ? 'pending-dock--resolving' : ''}`}
            data-slot="pending-dock"
            data-approval-id={request.approval_id}
          >
            <div className="pending-dock__header">
              <div className="pending-dock__title">
                <span className="pending-dock__icon">
                  {request.kind === 'command' ? <Command size={14} /> : request.kind === 'question' ? <HelpCircle size={14} /> : <ListChecks size={14} />}
                </span>
                <span className="pending-dock__title-text">
                  {request.kind === 'command' ? t('chat.command_pending') : request.kind === 'question' ? t('chat.question_pending') : t('chat.plan_pending')}
                </span>
                <span className="pending-dock__kind">{kindLabel}</span>
              </div>
              <div className="pending-dock__header-actions">
                {showProgress ? (
                  <span className="pending-dock__counter">{qIndex} / {qTotal}</span>
                ) : null}
                <Tooltip content={t('chat.approval_reject_close')}>
                  <button
                    type="button"
                    className="pending-action-button button-ghost"
                    onClick={() => onDismiss?.(request)}
                    disabled={isResolving}
                    aria-label={t('chat.approval_reject_close')}
                  >
                    ✕
                  </button>
                </Tooltip>
              </div>
            </div>
            {showProgress ? (
              <div className="pending-dock__progress">
                <i style={{ width: `${progressPct}%` }} />
              </div>
            ) : null}
            {request.kind === 'command' ? (
              <ApprovalDock request={request as PendingRequest & { kind: 'command' }} onResolve={onResolve} />
            ) : request.kind === 'question' ? (
              <QuestionDock request={request as PendingRequest & { kind: 'question' }} index={qIndex ?? 1} total={qTotal ?? 1} onResolve={onResolve} />
            ) : (
              <PlanDock request={request as PendingRequest & { kind: 'plan' }} onResolve={onResolve} />
            )}
          </div>
        );
      })}
    </div>
  );
}
