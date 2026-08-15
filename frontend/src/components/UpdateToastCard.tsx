import { Download, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { t } from '../lib/i18n';
import type { UpdateCenter } from '../lib/useUpdateCenter';
import { Button } from './ui/button';

interface UpdateToastCardProps {
  center: UpdateCenter;
  onOpenSettings: () => void;
}

const AUTO_DISMISS_MS = 30 * 1000;

/**
 * Non-blocking update-ready toast in the bottom-right corner.
 * Shows only when an update has finished downloading. Auto-dismisses after
 * 30s or on explicit close — never steals focus or reflows the layout.
 */
export function UpdateToastCard({ center, onOpenSettings }: UpdateToastCardProps) {
  const { state } = center;
  const [visible, setVisible] = useState(false);
  // Initialise to null so that mounting into an already-downloaded state
  // (update finished on a previous run but the app closed before installing)
  // is treated as a transition into 'downloaded' and shows the toast.
  const prevState = useRef<string | null>(null);

  useEffect(() => {
    if (state.state === 'downloaded' && prevState.current !== 'downloaded') {
      setVisible(true);
    }
    prevState.current = state.state;
  }, [state.state]);

  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => setVisible(false), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [visible]);

  if (!visible || state.state !== 'downloaded') return null;

  return (
    <div className="update-toast" role="status">
      <div
        className="update-toast__card"
        onClick={onOpenSettings}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onOpenSettings();
          }
        }}
        aria-label={t('update.toast_details')}
      >
        <div className="update-toast__icon">
          <Download size={16} />
        </div>
        <div className="update-toast__body">
          <div className="update-toast__title">{t('update.toast_title')}</div>
          <div className="update-toast__subtitle">
            {t('update.toast_subtitle', { version: state.availableVersion ?? '' })}
          </div>
          <div className="update-toast__actions">
            <Button
              variant="primary"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                void center.install();
              }}
            >
              {t('update.restart_now')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setVisible(false);
              }}
            >
              {t('update.later')}
            </Button>
          </div>
        </div>
      </div>
      <button
        type="button"
        className="update-toast__close"
        onClick={(e) => {
          e.stopPropagation();
          setVisible(false);
        }}
        aria-label={t('update.close')}
      >
        <X size={14} />
      </button>
    </div>
  );
}
