import { useState, useCallback } from 'react';
import { Command, HelpCircle, Loader2 } from 'lucide-react';
import { Tooltip } from '@/components/ui/tooltip';
import { t } from '@/lib/i18n';
import type { ApprovalDecisionPayload, ApprovalOption, PendingRequest } from '@/types';

interface PendingDocksProps {
  requests: PendingRequest[];
  onResolve: (request: PendingRequest, decision: ApprovalDecisionPayload) => Promise<void>;
  onDismiss?: (request: PendingRequest) => void;
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

  return (
    <div className="pending-dock__body">
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

function QuestionDock({ request, onResolve }: { request: PendingRequest & { kind: 'question' }; onResolve: (req: PendingRequest & { kind: 'question' }, decision: ApprovalDecisionPayload) => Promise<void> }) {
  const [resolving, setResolving] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [customAnswer, setCustomAnswer] = useState('');

  const hasAnswer = request.options
    ? (request.multiple
        ? picked.length > 0
        : picked.length > 0 || customAnswer.trim() !== '')
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

  return (
    <div className="pending-dock__body">
      <div className="pending-dock__question" data-slot="question-content">
        {request.header ? <h4 className="pending-dock__question-hint">{request.header}</h4> : null}
        <p className="pending-dock__question-text">{request.question}</p>
      </div>
      {request.options && request.options.length > 0 ? (
        <fieldset className="pending-dock__options" data-slot="question-options">
          {request.options.map((option, index) => {
            const isPicked = picked.includes(index);
            return (
              <label
                key={`${option.label}-${index}`}
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
                        prev.includes(index)
                          ? prev.filter((i) => i !== index)
                          : [...prev, index],
                      );
                    } else {
                      setPicked((prev) =>
                        prev.includes(index) ? [] : [index],
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
        <Tooltip content={t('chat.question_disagree_tooltip')}>
          <button
            type="button"
            className="button-ghost"
            onClick={() => dispatch({ type: 'reject' })}
            disabled={resolving}
          >
            {t('chat.approval_disagree')}
          </button>
        </Tooltip>
        {request.options ? (
          <button
            type="button"
            className="button-secondary"
            onClick={handleSubmit}
            disabled={!hasAnswer || resolving}
          >
            {t('chat.approval_once')}
          </button>
        ) : null}
        <button
          type="button"
          className="button-primary"
          onClick={async () => {
            if (resolving) return;
            setResolving(true);
            try {
              await onResolve(request, { type: 'respond', message: '' });
            } finally { setResolving(false); }
          }}
          disabled={resolving}
        >
          {t('chat.approval_once')}
        </button>
      </div>
    </div>
  );
}

export function PendingDocks({ requests, onResolve, onDismiss }: PendingDocksProps) {
  return (
    <div className="pending-docks" data-slot="pending-docks">
      {requests.map((request) => {
        const isResolving = request.resolving;
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
                  {request.kind === 'command' ? <Command size={14} /> : <HelpCircle size={14} />}
                </span>
                <span className="pending-dock__title-text">
                  {request.kind === 'command' ? t('chat.command_pending') : t('chat.question_pending')}
                </span>
                {isResolving && <Loader2 className="pending-dock__spinner" size={14} />}
              </div>
              <div className="pending-dock__header-actions">
                <Tooltip content={t('chat.approval_ignore_tooltip')}>
                  <button
                    type="button"
                    className="pending-action-button button-ghost"
                    onClick={() => onDismiss?.(request)}
                    disabled={isResolving}
                    aria-label={t('chat.approval_disagree')}
                  >
                    ✕
                  </button>
                </Tooltip>
              </div>
            </div>
            {request.kind === 'command' ? (
              <ApprovalDock request={request as PendingRequest & { kind: 'command' }} onResolve={onResolve} />
            ) : (
              <QuestionDock request={request as PendingRequest & { kind: 'question' }} onResolve={onResolve} />
            )}
          </div>
        );
      })}
    </div>
  );
}
