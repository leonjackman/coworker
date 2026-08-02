import { t } from '../lib/i18n';

interface StatusBarProps {
  status: 'connecting' | 'ready' | 'error';
}

export function StatusBar({ status }: StatusBarProps) {
  const statusText = status === 'ready' ? t('common.ready') : status === 'connecting' ? t('common.connecting') : t('common.offline');

  return (
    <header className="status-bar">
      <div className="status-bar__workspace">
        <span className={`status-dot status-dot--${status}`} />
        <span>{statusText}</span>
      </div>
    </header>
  );
}
