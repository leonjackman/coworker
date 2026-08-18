import { Check, Copy, Pencil, RefreshCw, Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Tooltip } from './ui/tooltip';
import { t } from '../lib/i18n';

export interface MessageActionsProps {
  role: 'user' | 'assistant';
  content: string;
  onEdit?: (content: string) => void;
  onRegenerate?: () => void;
  onRedo?: () => void;
  revertedFiles?: number;
  disabled?: boolean;
}

export function MessageActions({ role, content, onEdit, onRegenerate, onRedo, revertedFiles = 0, disabled = false }: MessageActionsProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
    } catch {
      // clipboard unavailable
    }
  };

  return (
    <div className="message-actions" data-role={role}>
      <Tooltip content={copied ? t('message.copied') : t('message.copy')}>
        <Button
          variant="ghost"
          size="icon-xs"
          className="message-actions__btn"
          onClick={handleCopy}
          aria-label={copied ? t('message.copied') : t('message.copy')}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </Button>
      </Tooltip>

      {role === 'user' && onEdit && (
        <Tooltip content={t('message.edit')}>
          <Button
            variant="ghost"
            size="icon-xs"
            className="message-actions__btn"
            onClick={() => onEdit(content)}
            aria-label={t('message.edit')}
          >
            <Pencil size={13} />
          </Button>
        </Tooltip>
      )}

      {role === 'assistant' && onRegenerate && (
        <Tooltip content={t('message.regenerate')}>
          <Button
            variant="ghost"
            size="icon-xs"
            className="message-actions__btn"
            onClick={() => onRegenerate()}
            disabled={disabled}
            aria-label={t('message.regenerate')}
          >
            <RefreshCw size={13} />
          </Button>
        </Tooltip>
      )}

      {role === 'user' && onRedo && revertedFiles > 0 && (
        <Tooltip content={t('message.redo', { count: revertedFiles })}>
          <Button
            variant="ghost"
            size="icon-xs"
            className="message-actions__btn"
            onClick={() => onRedo()}
            aria-label={t('message.redo', { count: revertedFiles })}
          >
            <Undo2 size={13} />
          </Button>
        </Tooltip>
      )}
    </div>
  );
}
