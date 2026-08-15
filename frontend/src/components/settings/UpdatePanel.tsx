import { CheckCircle2, Download, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { UpdateStateSnapshot } from '../../types';
import { t } from '../../lib/i18n';
import type { UpdateCenter } from '../../lib/useUpdateCenter';
import { Button } from '../ui/button';
import { MarkdownContent } from '../MarkdownContent';
import { cn } from '../../lib/utils';

interface UpdatePanelProps {
  center: UpdateCenter;
  className?: string;
}

function formatBytesPerSecond(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '';
  const mbps = bytesPerSecond / (1024 * 1024);
  return `${mbps >= 10 ? mbps.toFixed(1) : mbps.toFixed(2)} MB/s`;
}

function ReleaseNotes({ notes }: { notes: string | null }) {
  if (!notes) return null;
  return (
    <div className="update-panel__notes">
      <MarkdownContent content={notes} />
    </div>
  );
}

/**
 * Inline update-status panel rendered inside the Settings "About" group.
 * Mirrors the main-process update state and exposes the relevant actions
 * for each state (check / download / install / skip).
 */
export function UpdatePanel({ center, className }: UpdatePanelProps) {
  const { state } = center;
  const busy = state.state === 'checking' || state.state === 'downloading';
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Reset "Later" dismissal whenever the update state moves on.
    setDismissed(false);
  }, [state.state]);

  if (state.isDev) {
    return (
      <div className={cn('update-panel update-panel--muted', className)}>
        <p className="update-panel__hint">{t('update.dev_mode')}</p>
      </div>
    );
  }

  if (dismissed) return null;

  return (
    <div className={cn('update-panel', className)}>
      {state.state === 'idle' && (
        <p className="update-panel__hint">{t('update.idle_hint')}</p>
      )}

      {state.state === 'checking' && (
        <div className="update-panel__row">
          <RefreshCw size={15} className="update-panel__spin" />
          <span>{t('update.checking')}</span>
        </div>
      )}

      {state.state === 'up-to-date' && (
        <div className="update-panel__row">
          <CheckCircle2 size={15} className="update-panel__ok" />
          <span>{t('update.up_to_date', { version: state.currentVersion })}</span>
        </div>
      )}

      {state.state === 'available' && (
        <div className="update-panel__available">
          <div className="update-panel__row">
            <span className="update-panel__title">
              {state.skippedVersion === state.availableVersion
                ? t('update.available_skipped', { version: state.availableVersion ?? '' })
                : t('update.available', { version: state.availableVersion ?? '' })}
            </span>
          </div>
          <ReleaseNotes notes={state.releaseNotes} />
          <div className="update-panel__actions">
            <Button variant="primary" size="sm" onClick={() => void center.download()} disabled={busy}>
              <Download size={14} />
              {t('update.download')}
            </Button>
            {state.skippedVersion === state.availableVersion ? (
              <Button variant="ghost" size="sm" onClick={() => void center.clearSkip()}>
                {t('update.undo_skip')}
              </Button>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => void center.skip()}>
                {t('update.skip_version')}
              </Button>
            )}
          </div>
        </div>
      )}

      {state.state === 'downloading' && state.progress && (
        <div className="update-panel__downloading">
          <div className="update-panel__row">
            <RefreshCw size={15} className="update-panel__spin" />
            <span>{t('update.downloading', { percent: Math.round(state.progress.percent) })}</span>
            {state.progress.bytesPerSecond > 0 && (
              <span className="update-panel__speed">
                {formatBytesPerSecond(state.progress.bytesPerSecond)}
              </span>
            )}
          </div>
          <div className="update-panel__bar">
            <div
              className="update-panel__bar-fill"
              style={{ width: `${Math.max(2, Math.min(100, state.progress.percent))}%` }}
            />
          </div>
        </div>
      )}

      {state.state === 'downloaded' && (
        <div className="update-panel__downloaded">
          <div className="update-panel__row">
            <CheckCircle2 size={15} className="update-panel__ok" />
            <span>{t('update.downloaded', { version: state.availableVersion ?? '' })}</span>
          </div>
          <div className="update-panel__actions">
            <Button variant="primary" size="sm" onClick={() => void center.install()}>
              {t('update.restart_now')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDismissed(true)}>
              {t('update.later')}
            </Button>
          </div>
        </div>
      )}

      {state.state === 'error' && (
        <div className="update-panel__row update-panel__row--error">
          <XCircle size={15} />
          <span className="update-panel__error-text">{state.errorMessage || t('update.error_unknown')}</span>
          <Button variant="secondary" size="sm" onClick={() => void center.check()}>
            {t('update.retry')}
          </Button>
        </div>
      )}
    </div>
  );
}
