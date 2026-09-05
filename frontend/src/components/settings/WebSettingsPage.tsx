import { ArrowLeft } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { WebSettings } from '../../types';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { WebSettingsPanel } from './WebSettingsPanel';

interface WebSettingsPageProps {
  settings: WebSettings;
  onChange: (next: WebSettings) => void;
  onBack: () => void;
  onSearchBrowserOpen?: (() => void) | undefined;
  onSearchBrowserClose?: (() => void) | undefined;
}

export function WebSettingsPage({
  settings,
  onChange,
  onBack,
  onSearchBrowserOpen,
  onSearchBrowserClose,
}: WebSettingsPageProps) {
  return (
    <WorkspacePage
      eyebrow={t('settings.title')}
      title={t('settings.web_title')}
      description={t('settings.web_description')}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <WebSettingsPanel
        settings={settings}
        onChange={onChange}
        onSearchBrowserOpen={onSearchBrowserOpen}
        onSearchBrowserClose={onSearchBrowserClose}
      />
    </WorkspacePage>
  );
}
