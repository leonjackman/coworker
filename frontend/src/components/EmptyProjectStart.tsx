import { Folder, MessageSquarePlus } from 'lucide-react';
import { t } from '../lib/i18n';
import type { ProjectEntry } from '../types';
import { Button } from './ui/button';

interface EmptyProjectStartProps {
  project: ProjectEntry;
  onStart: () => void;
}

export function EmptyProjectStart({ project, onStart }: EmptyProjectStartProps) {
  return (
    <section className="empty-project-start">
      <div className="empty-project-start__icon">
        <Folder size={30} />
      </div>
      <p className="empty-project-start__eyebrow">{t('empty_project.eyebrow')}</p>
      <h2>{t('empty_project.title', { name: project.name })}</h2>
      <p>{t('empty_project.description')}</p>
      <code>{project.workspace_path || t('project.workspace_missing')}</code>
      <Button type="button" variant="primary" onClick={onStart}>
        <MessageSquarePlus size={16} />
        {t('empty_project.start')}
      </Button>
    </section>
  );
}
