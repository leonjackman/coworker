import { Ban, CornerDownLeft, Paperclip, Plus, Slash, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n';
import type { AccessMode, ComposerAttachment, WorkMode } from '../types';
import { accessModeOptions, workModeOptions } from './settings/preference-options';
import { Button } from './ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Textarea } from './ui/textarea';
import { ToggleGroup } from './ui/toggle-group';
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
}

const SLASH_COMMANDS = ['/help', '/clear', '/providers', '/settings', '/plan', '/build'];

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
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showCommands, setShowCommands] = useState(false);

  useEffect(() => {
    setShowCommands(value.trim().startsWith('/'));
  }, [value]);

  function addFiles(files: FileList | null) {
    if (!files) return;
    const nextAttachments = Array.from(files).map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}`,
      name: file.name,
      size: file.size,
      type: file.type || 'file',
    }));
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

  return (
    <footer className="composer">
      <div className="composer__surface">
        <div className="composer__topbar">
          <ToggleGroup
            value={workMode}
            onValueChange={onWorkModeChange}
            items={workModeOptions()}
          />

          <div className="composer__select">
            <span>{t('chat.model')}</span>
            <Select value={selectedModel} onValueChange={onModelChange}>
              <SelectTrigger className="composer__model-trigger" size="sm">
                <SelectValue />
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

          <ToggleGroup
            value={accessMode}
            onValueChange={onAccessModeChange}
            className="composer__access"
            items={accessModeOptions()}
          />
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

        <div className="composer__toolbar">
          <div className="composer__tools">
            <input ref={fileInputRef} type="file" multiple className="composer__file-input" onChange={(event) => addFiles(event.target.files)} />
            <Tooltip content={t('chat.attach_tooltip')}>
              <Button variant="icon" onClick={() => fileInputRef.current?.click()}>
                <Paperclip size={16} />
              </Button>
            </Tooltip>
            <Tooltip content={t('chat.slash_tooltip')}>
              <Button variant="icon" onClick={() => onChange(value.trim().startsWith('/') ? value : `${value}/`)}>
                <Slash size={16} />
              </Button>
            </Tooltip>
            <Tooltip content={t('chat.new_task_tooltip')}>
              <Button variant="icon" disabled>
                <Plus size={16} />
              </Button>
            </Tooltip>
          </div>

          {isThinking ? (
            <Button variant="secondary" onClick={onStop}>
              <Ban size={16} />
              {t('chat.stop')}
            </Button>
          ) : (
            <Button variant="primary" onClick={onSend} disabled={disabled || !value.trim()}>
              <CornerDownLeft size={16} />
              {t('common.send')}
            </Button>
          )}
        </div>
      </div>
    </footer>
  );
}
