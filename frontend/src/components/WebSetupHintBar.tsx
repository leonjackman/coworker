import { Settings2, X } from 'lucide-react';
import { t } from '../lib/i18n';
import { Button } from './ui/button';

interface WebSetupHintBarProps {
  status: 'disabled' | 'no_key' | 'browser_unavailable';
  onConfigure: () => void;
  onDismiss: () => void;
}

export function WebSetupHintBar({ status, onConfigure, onDismiss }: WebSetupHintBarProps) {
  const message =
    status === 'disabled'
      ? t('chat.web_setup_hint_disabled')
      : status === 'browser_unavailable'
        ? t('chat.web_setup_hint_browser')
        : t('chat.web_setup_hint_no_key');
  return (
    <div className="web-setup-hint" role="status">
      <Settings2 size={14} className="web-setup-hint__icon" />
      <span className="web-setup-hint__text">{message}</span>
      <Button variant="secondary" size="sm" onClick={onConfigure}>
        {t('chat.web_setup_go')}
      </Button>
      <button type="button" className="web-setup-hint__dismiss" onClick={onDismiss} aria-label={t('chat.web_setup_dismiss')}>
        <X size={14} />
      </button>
    </div>
  );
}
