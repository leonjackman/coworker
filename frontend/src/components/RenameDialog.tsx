import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import { Input } from './ui/input';

interface RenameDialogProps {
  open: boolean;
  title: string;
  initialValue: string;
  maxLength: number;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (value: string) => void;
}

/**
 * In-app rename dialog. Replaces `window.prompt`, which is unreliable inside
 * the Electron renderer (no native prompt is shown), so renaming a project
 * or session silently did nothing on desktop.
 */
export function RenameDialog({ open, title, initialValue, maxLength, confirmLabel, busy = false, error = null, onClose, onConfirm }: RenameDialogProps) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setValue(initialValue);
  }, [open, initialValue]);

  useEffect(() => {
    if (open) inputRef.current?.select();
  }, [open]);

  if (!open) return null;

  const trimmed = value.trim();
  const canSubmit = !busy && trimmed.length > 0 && trimmed.length <= maxLength && trimmed !== initialValue.trim();

  const submit = () => {
    if (!canSubmit) return;
    onConfirm(trimmed);
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="workspace-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <button className="workspace-dialog__close" type="button" onClick={onClose} aria-label={t('dialog.close')} disabled={busy}>
          <X size={18} />
        </button>
        <div className="workspace-dialog__header">
          <h2>{title}</h2>
        </div>

        <div className="workspace-dialog__field">
          <Input
            ref={inputRef}
            value={value}
            maxLength={maxLength}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submit();
              else if (event.key === 'Escape') onClose();
            }}
            autoFocus
          />
          <p className="workspace-dialog__hint">
            {value.trim().length}/{maxLength}
          </p>
        </div>

        {error && <p className="workspace-dialog__error">{error}</p>}

        <div className="workspace-dialog__footer">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            {t('dialog.cancel')}
          </Button>
          <Button type="button" variant="primary" onClick={submit} disabled={!canSubmit}>
            {confirmLabel}
          </Button>
        </div>
      </section>
    </div>
  );
}
