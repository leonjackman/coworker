import { t } from '../lib/i18n';
import coworkerLogoBlack from '../assets/brand/coworker-logo-black.png';
import coworkerLogoWhite from '../assets/brand/coworker-logo-white.png';

interface NewChatHeroProps {
  workspaceName?: string;
}

export function NewChatHero({ workspaceName }: NewChatHeroProps) {
  return (
    <section className="new-chat-hero" aria-labelledby="new-chat-hero-title">
      <div className="new-chat-hero__inner">
        <span className="new-chat-hero__brand" aria-label="CoWorker">
          <img className="new-chat-hero__logo new-chat-hero__logo--dark" src={coworkerLogoBlack} alt="CoWorker" />
          <img className="new-chat-hero__logo new-chat-hero__logo--light" src={coworkerLogoWhite} alt="" aria-hidden="true" />
        </span>
        <h2 id="new-chat-hero-title">{t('new_chat.title')}</h2>
        <p className="new-chat-hero__subtitle">
          {workspaceName ? t('new_chat.subtitle_workspace', { name: workspaceName }) : t('new_chat.subtitle_empty')}
        </p>
      </div>
    </section>
  );
}
