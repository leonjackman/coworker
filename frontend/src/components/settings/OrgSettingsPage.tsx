import { ArrowLeft } from 'lucide-react';
import { useMemo } from 'react';
import { t } from '../../lib/i18n';
import type { ProjectEntry } from '../../types';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { OrgSettingsPanel } from './OrgSettingsPanel';

interface OrgSettingsPageProps {
  projectId: string;
  projectName?: string;
  onBack: () => void;
}

export function OrgSettingsPage({ projectId, projectName, onBack }: OrgSettingsPageProps) {
  const title = useMemo(() => (projectName ? `${projectName} · ${t('settings.org_group')}` : t('settings.org_group')), [projectName]);
  return (
    <WorkspacePage
      eyebrow={t('settings.title')}
      title={title}
      description={t('settings.org_group_desc')}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <OrgSettingsPanel projectId={projectId} />
    </WorkspacePage>
  );
}
