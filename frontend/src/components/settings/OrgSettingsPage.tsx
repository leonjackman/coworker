import { ArrowLeft } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { t } from '../../lib/i18n';
import type { ProjectEntry } from '../../types';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { usePageNavPublish } from '../../nav/PageNav';
import { OrgSettingsPanel } from './OrgSettingsPanel';

interface OrgSettingsPageProps {
  projectId: string;
  projectName?: string;
  onBack: () => void;
  onChanged?: () => void;
}

export function OrgSettingsPage({ projectId, projectName, onBack, onChanged }: OrgSettingsPageProps) {
  const title = useMemo(() => (projectName ? `${projectName} · ${t('settings.org_group')}` : t('settings.org_group')), [projectName]);
  const publishNav = usePageNavPublish();
  useEffect(() => {
    publishNav({ viewLabel: t('settings.org_group') });
    return () => publishNav(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishNav]);
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
      <OrgSettingsPanel projectId={projectId} {...(onChanged ? { onChanged } : {})} />
    </WorkspacePage>
  );
}
