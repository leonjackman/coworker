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
 * Translate command argv into a human-readable "action target" shown before the
 * command line. Covers common scenarios; falls back to a generic description.
 */
function describeCommand(argv: string[] | undefined, _cwd?: string): string {
  if (!argv || argv.length === 0) return t('chat.describe_run_command');
  const bin = argv[0];
  if (!bin) return t('chat.describe_run_command');
  const rest = argv.slice(1);
  const isDev = rest.some((a) => a === '-D' || a === '--save-dev' || a === '--dev');
  const script = rest.find((a) => !a.startsWith('-') && a !== 'run' && a !== 'exec');

  if (/^(npm|pnpm|yarn|bun)$/i.test(bin)) {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'install' || sub === 'i' || sub === 'add') {
      const names = rest.slice(1).filter((a) => !a.startsWith('-'));
      if (names.length) return `${t('chat.describe_install_deps')}${names.join(', ')}${isDev ? ` ${t('chat.describe_dev_deps')}` : ''}`;
      return t('chat.describe_install_project_deps');
    }
    if (sub === 'run' || sub === 'exec') {
      if (script === 'build') return t('chat.describe_build_project');
      if (script === 'dev' || script === 'start') return t('chat.describe_start_dev_server');
      if (script === 'test') return t('chat.describe_run_tests');
      if (script) return `${t('chat.describe_run_script')}: ${script}`;
      return t('chat.describe_run_package_script');
    }
    if (sub === 'build') return t('chat.describe_build_project');
    if (sub === 'test') return t('chat.describe_run_tests');
    return `${t('chat.describe_run_cmd', { bin })}`;
  }
  if (bin === 'git') {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'commit') {
      const mIdx = rest.findIndex((a) => a === '-m' || a === '--message');
      const raw = rest[mIdx + 1];
      const msg = raw ? raw.replace(/^["']|["']$/g, '') : '';
      return msg ? `${t('chat.describe_commit_changes')}: ${msg}` : t('chat.describe_commit_changes');
    }
    if (sub === 'push') {
      const branch = rest.find((a) => !a.startsWith('-'));
      return branch ? `${t('chat.describe_push_branch')} ${branch} ${t('chat.describe_to_remote')}` : `${t('chat.describe_push_branch')} ${t('chat.describe_current_branch')} ${t('chat.describe_to_remote')}`;
    }
    if (sub === 'pull') return t('chat.describe_pull_remote');
    if (sub === 'clone') return t('chat.describe_clone_repo');
    if (sub === 'checkout' || sub === 'switch') {
      const hasNew = rest.includes('-b') || rest.includes('-c');
      const flag = rest.includes('-b') ? '-b' : rest.includes('-c') ? '-c' : '';
      const br = flag ? (rest[rest.indexOf(flag) + 1] ?? '') : (rest.find((a) => !a.startsWith('-') && a !== '-b' && a !== '-c') ?? '');
      if (br) return hasNew ? `${t('chat.describe_create_switch_branch')} ${br}` : `${t('chat.describe_switch_branch')} ${br}`;
      return t('chat.describe_switch_branch');
    }
    if (sub === 'merge') {
      const branch = rest.find((a) => !a.startsWith('-'));
      return branch ? `${t('chat.describe_merge_branch')} ${branch}` : t('chat.describe_merge_branch');
    }
    if (['status', 'diff', 'log', 'show'].includes(sub)) return `${t('chat.describe_view_git')} ${sub} ${t('chat.describe_info')}`;
    return t('chat.describe_run_git_cmd');
  }
  if (bin === 'pip' || bin === 'pip3') {
    if ((rest[0] || '').toLowerCase() === 'install') {
      const names = rest.slice(1).filter((a) => !a.startsWith('-'));
      return names.length ? `${t('chat.describe_install_python_deps')}: ${names.join(', ')}` : t('chat.describe_install_python_deps');
    }
    return t('chat.describe_run_pip_cmd');
  }
  if (bin === 'python' || bin === 'python3') {
    if (rest[0] === '-m' && rest[1] === 'venv') {
      const env = rest[2];
      return env ? `${t('chat.describe_create_venv')} ${env}` : t('chat.describe_create_venv');
    }
    const py = rest.find((a) => a.endsWith('.py'));
    return py ? `${t('chat.describe_run_python_script')} ${py}` : t('chat.describe_run_python_script');
  }
  if (bin === 'node') {
    const js = rest.find((a) => a.endsWith('.js') || a.endsWith('.mjs'));
    return js ? `${t('chat.describe_run_node_script')} ${js}` : t('chat.describe_run_node_script');
  }
  if (bin === 'rm' || bin === 'del') {
    const recursive = rest.some((a) => a.includes('r'));
    return recursive ? `${t('chat.describe_delete_file_dir')} ${t('chat.describe_recursive')}` : t('chat.describe_delete_file_dir');
  }
  if (bin === 'mv') return t('chat.describe_move_rename');
  if (bin === 'cp' || bin === 'copy') return t('chat.describe_copy_file');
  if (bin === 'mkdir') {
    const dir = rest.find((a) => !a.startsWith('-'));
    return dir ? `${t('chat.describe_create_dir')} ${dir}` : t('chat.describe_create_dir');
  }
  if (bin === 'touch') return t('chat.describe_create_empty_file');
  if (bin === 'curl' || bin === 'wget') return t('chat.describe_download_file');
  if (bin === 'chmod') return t('chat.describe_chmod');
  if (bin === 'docker') {
    const sub = (rest[0] || '').toLowerCase();
    if (sub === 'compose') {
      if (rest[1] === 'up') return t('chat.describe_start_docker');
      if (rest[1] === 'build') return t('chat.describe_build_docker_image');
      return t('chat.describe_run_docker_compose');
    }
    if (sub === 'build') return t('chat.describe_build_docker_image');
    if (sub === 'run') return t('chat.describe_run_docker_container');
    return t('chat.describe_run_docker_cmd');
  }
  if (['ls', 'cat', 'grep', 'find', 'head', 'tail', 'echo', 'pwd'].includes(bin)) return `${t('chat.describe_view_file_info')} (${bin})`;
  return `${t('chat.describe_run_cmd', { bin })}`;
}

/**
 * Human-readable intent for a NON-command tool approval (write_file, memory,
 * …). Backend classifies these as "command" with an empty argv, so without
 * this the card would show nothing actionable. Falls back to the raw tool name.
 */
function describeToolCall(name: string, args?: Record<string, unknown>): string {
  if (!name) return t('chat.describe_perform_action');
  const path = typeof args?.path === 'string' && args.path ? args.path : '';
  const label =
    name === 'write_file' ? t('chat.describe_write_file')
    : name === 'replace_in_file' || name === 'apply_text_edits' || name === 'edit_file' ? t('chat.describe_edit_file')
    : name === 'read_file' ? t('chat.describe_read_file')
    : name === 'memory' || name === 'remember' ? t('chat.describe_long_term_memory')
    : name === 'web_search' ? t('chat.describe_web_search')
    : name === 'fetch_web' ? t('chat.describe_fetch_web')
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
