import { AlertTriangle, ChevronDown, HelpCircle, ShieldCheck, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { t } from '../lib/i18n';
import type { ApprovalDecisionPayload, ApprovalOption, PendingRequest } from '../types';
import { Button } from './ui/button';

function DockShell({
  icon,
  title,
  actions,
  children,
  onDismiss,
}: {
  icon: ReactNode;
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  onDismiss?: () => void;
}) {
  const [minimized, setMinimized] = useState(false);
  return (
    <div className={`pending-dock ${minimized ? 'pending-dock--minimized' : ''}`} data-component="pending-dock">
      <div className="pending-dock__header">
        <div className="pending-dock__title">
          <span className="pending-dock__icon">{icon}</span>
          <span className="pending-dock__title-text">{title}</span>
        </div>
        <div className="pending-dock__header-actions">
          {actions}
          <Button variant="ghost" size="icon-sm" onClick={() => setMinimized((value) => !value)} aria-label={minimized ? 'expand' : 'minimize'}>
            <ChevronDown size={15} style={{ transform: minimized ? 'rotate(180deg)' : 'none' }} />
          </Button>
          {onDismiss && (
            <Button variant="ghost" size="icon-sm" onClick={onDismiss} aria-label={t('chat.question_dismiss')}>
              <X size={15} />
            </Button>
          )}
        </div>
      </div>
      {!minimized && <div className="pending-dock__body">{children}</div>}
    </div>
  );
}

function CommandDetails({ command, cwd }: { command: string[]; cwd?: string | undefined }) {
  return (
    <div className="pending-dock__command">
      <code className="pending-dock__command-line">{command.join(' ')}</code>
      {cwd && <code className="pending-dock__command-cwd">{cwd}</code>}
    </div>
  );
}

function QuestionBody({
  request,
  disabled,
  onSubmit,
  onDismiss,
}: {
  request: PendingRequest;
  disabled: boolean;
  onSubmit: (message: string) => void;
  onDismiss: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [custom, setCustom] = useState('');
  const [customOn, setCustomOn] = useState(false);
  const multiple = request.multiple === true;
  const options = request.options ?? [];

  const toggleOption = (label: string) => {
    if (multiple) {
      setPicked((current) => (current.includes(label) ? current.filter((item) => item !== label) : [...current, label]));
    } else {
      setPicked([label]);
      setCustomOn(false);
    }
  };

  const toggleCustom = () => {
    if (!multiple) {
      setCustomOn(true);
      setPicked([]);
    } else {
      setCustomOn((value) => !value);
    }
  };

  const submit = () => {
    const answers = [...picked];
    if (customOn && custom.trim()) answers.push(custom.trim());
    const message = answers.join(', ');
    if (!message) return;
    onSubmit(message);
  };

  return (
    <div className="pending-dock__question" data-slot="question-options">
      {request.question && <p className="pending-dock__question-text">{request.question}</p>}
      <p className="pending-dock__question-hint">
        {multiple ? t('chat.question_multi_hint') : t('chat.question_single_hint')}
      </p>
      <div className="pending-dock__options">
        {options.map((option: ApprovalOption) => {
          const active = picked.includes(option.label) || (!multiple && customOn === false && picked[0] === option.label);
          return (
            <button
              type="button"
              key={option.label}
              data-slot="question-option"
              data-picked={active}
              onClick={() => toggleOption(option.label)}
              disabled={disabled}
            >
              <span className="pending-dock__option-box" data-type={multiple ? 'checkbox' : 'radio'} data-picked={active}>
                {active && <span className="pending-dock__option-check" />}
              </span>
              <span className="pending-dock__option-main">
                <span className="pending-dock__option-label">{option.label}</span>
                {option.description && <span className="pending-dock__option-description">{option.description}</span>}
              </span>
            </button>
          );
        })}
        <button
          type="button"
          data-slot="question-option"
          data-custom="true"
          data-picked={customOn}
          onClick={toggleCustom}
          disabled={disabled}
        >
          <span className="pending-dock__option-box" data-type={multiple ? 'checkbox' : 'radio'} data-picked={customOn}>
            {customOn && <span className="pending-dock__option-check" />}
          </span>
          <span className="pending-dock__option-main">
            <span className="pending-dock__option-label">{t('chat.question_type_own_answer')}</span>
            {customOn ? (
              <input
                className="pending-dock__option-input"
                value={custom}
                placeholder={t('chat.question_type_own_answer')}
                onChange={(event) => setCustom(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                disabled={disabled}
              />
            ) : (
              <span className="pending-dock__option-description">{t('chat.question_type_own_answer')}</span>
            )}
          </span>
        </button>
      </div>
      <div className="pending-dock__footer">
        <Button variant="ghost" size="sm" onClick={onDismiss} disabled={disabled}>
          {t('chat.question_dismiss')}
        </Button>
        <Button variant="primary" size="sm" onClick={submit} disabled={disabled || (picked.length === 0 && !(customOn && custom.trim()))}>
          {t('chat.question_submit')}
        </Button>
      </div>
    </div>
  );
}

export function PendingDocks({
  requests,
  onResolve,
  onDismiss,
}: {
  requests: PendingRequest[];
  onResolve: (request: PendingRequest, decision: ApprovalDecisionPayload) => void;
  onDismiss: (request: PendingRequest) => void;
}) {
  if (requests.length === 0) return null;

  return (
    <div className="pending-docks">
      {requests.map((request) => {
        const disabled = request.resolving === true;
        if (request.kind === 'question') {
          return (
            <DockShell
              key={request.approval_id}
              icon={<HelpCircle size={16} />}
              title={request.header || t('chat.question_title')}
              onDismiss={() => onDismiss(request)}
            >
              <QuestionBody
                request={request}
                disabled={disabled}
                onSubmit={(message) => onResolve(request, { type: 'respond', message })}
                onDismiss={() => onDismiss(request)}
              />
            </DockShell>
          );
        }
        return (
          <DockShell
            key={request.approval_id}
            icon={<AlertTriangle size={16} />}
            title={t('chat.approval_title')}
            onDismiss={() => onDismiss(request)}
          >
            <CommandDetails command={request.command ?? []} cwd={request.cwd} />
            <div className="pending-dock__footer">
              <Button variant="ghost" size="sm" onClick={() => onResolve(request, { type: 'reject' })} disabled={disabled}>
                {t('chat.approval_deny')}
              </Button>
              <Button variant="secondary" size="sm" onClick={() => onResolve(request, { type: 'always' })} disabled={disabled}>
                <ShieldCheck size={14} />
                {t('chat.approval_always')}
              </Button>
              <Button variant="primary" size="sm" onClick={() => onResolve(request, { type: 'approve' })} disabled={disabled}>
                {t('chat.approval_once')}
              </Button>
            </div>
          </DockShell>
        );
      })}
    </div>
  );
}
