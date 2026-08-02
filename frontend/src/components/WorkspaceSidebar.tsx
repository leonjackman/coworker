import { ChevronDown, ChevronLeft, Folder, Link2, MessageSquarePlus, MoreHorizontal, Plus, Settings2 } from 'lucide-react';
import type { AppView, ChatMessage, RuntimeConfig } from '../types';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { Tooltip } from './ui/tooltip';
import coworkerLogoBlack from '../assets/brand/coworker-logo-black.png';
import coworkerLogoWhite from '../assets/brand/coworker-logo-white.png';

interface WorkspaceSidebarProps {
  config: RuntimeConfig | null;
  messages: ChatMessage[];
  activeView: AppView;
  onViewChange: (view: AppView) => void;
  onNewChat: () => void;
}

export function WorkspaceSidebar({ config, messages, activeView, onViewChange, onNewChat }: WorkspaceSidebarProps) {
  const currentSessionTitle = sessionTitle(messages);
  const projectName = workspaceProjectName(config?.workspace);

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__brand-lockup" aria-label="CoWorker">
          <img className="sidebar__brand-logo sidebar__brand-logo--dark" src={coworkerLogoBlack} alt="CoWorker" />
          <img className="sidebar__brand-logo sidebar__brand-logo--light" src={coworkerLogoWhite} alt="" aria-hidden="true" />
        </span>
        <Tooltip content={t('sidebar.collapse')}>
          <Button variant="icon" className="h-8 w-8">
            <ChevronLeft size={17} />
          </Button>
        </Tooltip>
      </div>

      <nav className="sidebar__primary" aria-label={t('sidebar.primary_nav')}>
        <button className={`sidebar-nav-item sidebar-nav-item--strong ${activeView === 'chat' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={onNewChat}>
          <MessageSquarePlus size={17} />
          <span>{t('sidebar.new_chat')}</span>
          <Plus className="sidebar-nav-item__trail" size={15} />
        </button>
        <button className={`sidebar-nav-item ${activeView === 'providers' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('providers')}>
          <Link2 size={17} />
          <span>{t('nav.providers')}</span>
        </button>
      </nav>

      <Separator />

      <div className="sidebar__scroll">
        <section className="sidebar-group" aria-labelledby="sidebar-sessions-title">
          <div className="sidebar-group__header">
            <h2 id="sidebar-sessions-title">{t('sidebar.sessions')}</h2>
          </div>
          <button className={`sidebar-session ${activeView === 'chat' ? 'sidebar-session--active' : ''}`} type="button" onClick={() => onViewChange('chat')}>
            <span className="sidebar-session__marker">›</span>
            <span className="sidebar-session__title">{currentSessionTitle}</span>
            <MoreHorizontal className="sidebar-session__more" size={15} />
          </button>
        </section>

        <section className="sidebar-group" aria-labelledby="sidebar-projects-title">
          <div className="sidebar-group__header">
            <h2 id="sidebar-projects-title">{t('sidebar.projects')}</h2>
            <Tooltip content={t('sidebar.new_project')}>
              <Button variant="icon" className="h-7 w-7">
                <Plus size={15} />
              </Button>
            </Tooltip>
          </div>
          <div className="sidebar-project">
            <button className="sidebar-project__title" type="button">
              <ChevronDown size={15} />
              <Folder size={16} />
              <span>{projectName}</span>
            </button>
          </div>
        </section>
      </div>

      <div className="sidebar__settings">
        <Separator />
        <button className={`sidebar-nav-item ${activeView === 'settings' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('settings')}>
          <Settings2 size={17} />
          <span>{t('nav.settings')}</span>
        </button>
      </div>
    </aside>
  );
}

function sessionTitle(messages: ChatMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === 'user');
  if (!firstUserMessage?.content.trim()) return t('sidebar.new_chat');
  const compact = firstUserMessage.content.replace(/\s+/g, ' ').trim();
  return compact.length > 18 ? `${compact.slice(0, 18)}...` : compact;
}

function workspaceProjectName(workspace?: string): string {
  if (!workspace) return t('sidebar.default_project');
  const normalized = workspace.replace(/\/+$/, '');
  const [, name] = normalized.match(/([^/]+)$/) ?? [];
  return name || t('sidebar.default_project');
}
