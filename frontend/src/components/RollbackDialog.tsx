import { AlertTriangle, RotateCcw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { RevertPreviewResponse } from '../types';
import { Button } from './ui/button';
import { Switch } from './ui/switch';
import { FileDiffViewer } from './FileDiffViewer';

interface RollbackDialogProps {
  sessionId: string;
  messageId: string;
  onClose: () => void;
  onConfirm: (withCode: boolean) => Promise<void>;
}

export function RollbackDialog({ sessionId, messageId, onClose, onConfirm }: RollbackDialogProps) {
  const [preview, setPreview] = useState<RevertPreviewResponse | null>(null);
  const [withCode, setWithCode] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    chatService
      .getRevertPreview(sessionId, messageId)
      .then((response) => {
        if (!cancelled) setPreview(response);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load preview');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, messageId]);

  const hasCodeChanges = Boolean(preview && preview.count > 0);

  const confirm = async () => {
    setSubmitting(true);
    try {
      await onConfirm(withCode && hasCodeChanges);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rollback');
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="workspace-dialog workspace-dialog--wide" role="dialog" aria-modal="true" aria-labelledby="rollback-title">
        <button className="workspace-dialog__close" type="button" onClick={onClose} aria-label={t('dialog.close')}>
          ×
        </button>
        <div className="workspace-dialog__header">
          <p className="workspace-dialog__eyebrow">{t('rollback.eyebrow')}</p>
          <h2 id="rollback-title">{t('rollback.title')}</h2>
          <p>{t('rollback.description')}</p>
        </div>

        <div className="workspace-dialog__body">
          {loading && <p className="rollback-dialog__status">{t('rollback.loading')}</p>}
          {error && <p className="workspace-dialog__error">{error}</p>}

          {!loading && !error && preview && (
            <>
              <div className="rollback-dialog__switch">
                <Switch
                  id="rollback-with-code"
                  label={t('rollback.with_code')}
                  checked={withCode && hasCodeChanges}
                  disabled={!hasCodeChanges}
                  onChange={(event) => setWithCode(event.target.checked)}
                />
                {!hasCodeChanges && <p className="rollback-dialog__hint">{t('rollback.no_code_changes')}</p>}
              </div>

              {hasCodeChanges && withCode && (
                <div className="rollback-dialog__preview">
                  <div className="rollback-dialog__preview-title">{t('rollback.preview_title')}</div>
                  <div className="rollback-dialog__preview-list">
                    {preview.changes.map((change) => (
                      <FileDiffViewer
                        key={change.id}
                        path={change.file_path}
                        {...(change.hunks && change.hunks.length > 0 ? { hunks: change.hunks } : {})}
                        kind={change.kind as 'write' | 'edit'}
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="workspace-dialog__footer">
          <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
            {t('dialog.cancel')}
          </Button>
          <Button type="button" onClick={() => void confirm()} disabled={submitting || loading}>
            <RotateCcw size={14} />
            {t('rollback.confirm')}
          </Button>
        </div>
      </section>
    </div>
  );
}
