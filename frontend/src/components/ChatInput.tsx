import { Check, Hammer, ListChecks, Pencil, Plus, SendHorizontal, Shield, ShieldCheck, Slash, Square, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n';
import type { AccessMode, ComposerAttachment, WorkMode } from '../types';
import { Button } from './ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Textarea } from './ui/textarea';
import { Tooltip } from './ui/tooltip';

export interface ModelOption {
  id: string;
  label: string;
  provider?: string;
}

interface ChatInputProps {
  value: string;
  disabled: boolean;
  isThinking: boolean;
  workMode: WorkMode;
  accessMode: AccessMode;
  selectedModel: string;
  attachments: ComposerAttachment[];
  modelOptions: ModelOption[];
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onWorkModeChange: (value: WorkMode) => void;
  onAccessModeChange: (value: AccessMode) => void;
  onModelChange: (value: string) => void;
  onAttachmentsChange: (attachments: ComposerAttachment[]) => void;
  editing?: boolean;
  onCancelEdit?: () => void;
}

const SLASH_COMMANDS = ['/help', '/new', '/clear', '/providers', '/settings', '/plan', '/build'];
const MAX_ATTACHMENT_CHARS = 120_000;

function isTextAttachment(file: File) {
  if (file.type.startsWith('text/')) return true;
  return /\.(c|cc|cpp|cs|css|csv|go|h|hpp|html|java|js|json|jsx|kt|log|md|mdx|php|py|rb|rs|sh|sql|swift|toml|ts|tsx|txt|vue|xml|yaml|yml)$/i.test(file.name);
}

async function buildAttachment(file: File): Promise<ComposerAttachment> {
  const base = {
    id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: file.name,
    size: file.size,
    type: file.type || 'file',
  };
  if (!isTextAttachment(file)) {
    return { ...base, binary: true };
  }
  try {
    const content = await file.text();
    const truncated = content.length > MAX_ATTACHMENT_CHARS;
    return {
      ...base,
      content: truncated ? content.slice(0, MAX_ATTACHMENT_CHARS) : content,
      truncated,
      binary: false,
    };
  } catch (error) {
    return { ...base, binary: true, error: error instanceof Error ? error.message : 'Unable to read attachment' };
  }
}

export function ChatInput({
  value,
  disabled,
  isThinking,
  workMode,
  accessMode,
  selectedModel,
  attachments,
  modelOptions,
  onChange,
  onSend,
  onStop,
  onWorkModeChange,
  onAccessModeChange,
  onModelChange,
  onAttachmentsChange,
  editing = false,
  onCancelEdit,
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showCommands, setShowCommands] = useState(false);
  const canSend = Boolean(value.trim() || attachments.length > 0);

  useEffect(() => {
    setShowCommands(value.trim().startsWith('/'));
  }, [value]);

  async function addFiles(files: FileList | null) {
    if (!files) return;
    const nextAttachments = await Promise.all(Array.from(files).map((file) => buildAttachment(file)));
    onAttachmentsChange([...attachments, ...nextAttachments]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function removeAttachment(id: string) {
    onAttachmentsChange(attachments.filter((attachment) => attachment.id !== id));
  }

  function insertCommand(command: string) {
    onChange(`${command} `);
    setShowCommands(false);
  }

  const nextWorkMode = workMode === 'plan' ? 'build' : 'plan';
  const nextAccessMode = accessMode === 'default' ? 'full' : 'default';
  const effectiveAccessMode: AccessMode = workMode === 'build' ? accessMode : 'default';

  return (
    <footer className="composer">
      <div className="composer__surface">
        {editing && (
          <div className="composer__edit-bar">
            <span className="composer__edit-label">
              <Pencil size={13} />
              {t('message.edit')}
            </span>
            <Button variant="ghost" size="xs" onClick={onCancelEdit} aria-label={t('message.edit_cancel')}>
              <X size={13} />
              {t('message.edit_cancel')}
            </Button>
          </div>
        )}
        <div className="composer__input-box">
          <div className="composer__editor">
            <Textarea
              className="composer__input"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  onSend();
                }
                if (event.key === 'Escape') {
                  setShowCommands(false);
                }
              }}
              placeholder={t('chat.placeholder')}
              disabled={disabled}
            />

            {showCommands && (
              <div className="slash-menu">
                {SLASH_COMMANDS.map((command) => (
                  <button type="button" key={command} onClick={() => insertCommand(command)}>
                    <Slash size={13} />
                    <span>{command}</span>
                    <small>{t(`chat.command_${command.slice(1)}`)}</small>
                  </button>
                ))}
              </div>
            )}
          </div>

          {attachments.length > 0 && (
            <div className="composer__attachments">
              {attachments.map((attachment) => (
                <span className="attachment-chip" key={attachment.id}>
                  {attachment.name}
                  <button type="button" onClick={() => removeAttachment(attachment.id)} aria-label={t('chat.remove_attachment')}>
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="composer__input-actions">
            <div className="composer__tools">
              <input ref={fileInputRef} type="file" multiple className="composer__file-input" onChange={(event) => addFiles(event.target.files)} />
              <Tooltip content={t('chat.attach_tooltip')}>
                <Button variant="icon" onClick={() => fileInputRef.current?.click()} aria-label={t('chat.attach_tooltip')}>
                  <Plus size={19} />
                </Button>
              </Tooltip>
              <Tooltip content={t('chat.slash_tooltip')}>
                <Button variant="icon" onClick={() => onChange(value.trim().startsWith('/') ? value : `${value}/`)} aria-label={t('chat.slash_tooltip')}>
                  <Slash size={17} />
                </Button>
              </Tooltip>
            </div>

            {isThinking ? (
              <Button
                variant="secondary"
                className="composer__send-button composer__send-button--stop"
                onClick={onStop}
                aria-label={t('chat.stop')}
              >
                <Square size={15} fill="currentColor" />
              </Button>
            ) : (
              <Button variant="primary" className="composer__send-button" onClick={onSend} disabled={disabled || !canSend} aria-label={editing ? t('message.edit_save') : t('common.send')}>
                {editing ? <Check size={17} /> : <SendHorizontal size={17} />}
              </Button>
            )}
          </div>
        </div>

        <div className="composer__toolbar">
          <div className="composer__meta">
            <Tooltip content={t(workMode === 'plan' ? 'chat.mode_plan_tip' : 'chat.mode_build_tip')}>
              <button
                type="button"
                className="composer-toggle-button"
                onClick={() => onWorkModeChange(nextWorkMode)}
                aria-label={t('chat.toggle_work_mode')}
              >
                {workMode === 'plan' ? <ListChecks size={14} /> : <Hammer size={14} />}
                <span>{t(workMode === 'plan' ? 'chat.mode_plan' : 'chat.mode_build')}</span>
              </button>
            </Tooltip>

            <div className="composer__select">
              <span>{t('chat.model')}</span>
              <Select value={selectedModel} onValueChange={onModelChange} disabled={modelOptions.length === 0}>
                <SelectTrigger className="composer__model-trigger" size="sm">
                  <SelectValue placeholder={t('chat.model_unselected')} />
                </SelectTrigger>
                <SelectContent position="popper" align="start">
                  {modelOptions.map((model) => (
                    <SelectItem key={model.id} value={model.id}>
                      {model.provider ? `${model.provider} · ${model.label}` : model.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Tooltip content={t(effectiveAccessMode === 'default' ? 'chat.access_default_tip' : 'chat.access_full_tip')}>
              <button
                type="button"
                className="composer-toggle-button"
                onClick={() => onAccessModeChange(nextAccessMode)}
                disabled={workMode === 'plan'}
                aria-label={t('chat.toggle_access_mode')}
              >
                {effectiveAccessMode === 'default' ? <Shield size={14} /> : <ShieldCheck size={14} />}
                <span>{t(effectiveAccessMode === 'default' ? 'chat.access_default' : 'chat.access_full')}</span>
              </button>
            </Tooltip>
          </div>
        </div>
      </div>
    </footer>
  );
}
