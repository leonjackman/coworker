import { FolderPlus, MessageSquarePlus } from 'lucide-react';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import coworkerLogoBlack from '../assets/brand/coworker-logo-black.png';
import coworkerLogoWhite from '../assets/brand/coworker-logo-white.png';

interface FirstRunStartProps {
  onCreateProject: () => void;
  onNewSession: () => void;
}

export function FirstRunStart({ onCreateProject, onNewSession }: FirstRunStartProps) {
  return (
    <section className="first-run-start" aria-labelledby="first-run-title">
      <div className="first-run-start__card">
        <span className="first-run-start__brand" aria-label="CoWorker">
          <img className="first-run-start__logo first-run-start__logo--dark" src={coworkerLogoBlack} alt="CoWorker" />
          <img className="first-run-start__logo first-run-start__logo--light" src={coworkerLogoWhite} alt="" aria-hidden="true" />
        </span>
        <p className="first-run-start__eyebrow">{t('first_run.eyebrow')}</p>
        <h1 id="first-run-title">{t('first_run.title')}</h1>
        <p className="first-run-start__description">{t('first_run.description')}</p>
        <div className="first-run-start__actions">
          <Button variant="primary" size="lg" onClick={onCreateProject}>
            <FolderPlus size={17} />
            {t('first_run.create_project')}
          </Button>
          <Button variant="secondary" size="lg" onClick={onNewSession}>
            <MessageSquarePlus size={17} />
            {t('first_run.new_session')}
          </Button>
        </div>
      </div>
    </section>
  );
}
