import type { RuntimeConfig } from '../types';
import { t } from '../lib/i18n';
import { LanguageSwitch } from './LanguageSwitch';

interface StatusBarProps {
  config: RuntimeConfig | null;
  status: 'connecting' | 'ready' | 'error';
  onLanguageChange: () => void;
}

export function StatusBar({ config, status, onLanguageChange }: StatusBarProps) {
  const statusText = status === 'ready' ? t('common.ready') : status === 'connecting' ? t('common.connecting') : t('common.offline');

  return (
    <header className="status-bar">
      <div className="status-bar__workspace">
        <span className={`status-dot status-dot--${status}`} />
        <span>{statusText}</span>
        <span className="status-bar__divider" />
        <span>{config?.workspace ?? t('workspace.unavailable')}</span>
      </div>
      <div className="status-bar__mode">
        <span>{t('agent.mode.single')}</span>
        <span className="status-bar__provider">{config?.agent_provider ?? t('agent.provider.unknown')}</span>
        <LanguageSwitch onLanguageChange={onLanguageChange} />
      </div>
    </header>
  );
}
