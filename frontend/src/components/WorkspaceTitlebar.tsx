import {
  MoreHorizontal,
  PanelBottom,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Trash2,
} from 'lucide-react';
import type { AppView } from '../types';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { Tooltip } from './ui/tooltip';

interface WorkspaceTitlebarProps {
  status: 'connecting' | 'ready' | 'error';
  activeView: AppView;
  sessionTitle: string;
  sidebarCollapsed: boolean;
  rightSidebarOpen: boolean;
  bottomPanelOpen: boolean;
  canEditSession: boolean;
  onToggleSidebar: () => void;
  onToggleRightSidebar: () => void;
  onToggleBottomPanel: () => void;
  onRenameSession: () => void;
  onDeleteSession: () => void;
}

const isMacInset = typeof window !== 'undefined' && window.electronAPI?.platform === 'darwin';

export function WorkspaceTitlebar({
  status,
  activeView,
  sessionTitle,
  sidebarCollapsed,
  rightSidebarOpen,
  bottomPanelOpen,
  canEditSession,
  onToggleSidebar,
  onToggleRightSidebar,
  onToggleBottomPanel,
  onRenameSession,
  onDeleteSession,
}: WorkspaceTitlebarProps) {
  const statusText = status === 'ready' ? t('common.ready') : status === 'connecting' ? t('common.connecting') : t('common.offline');
  const title = titleForView(activeView, sessionTitle);

  return (
    <header className={`workspace-titlebar ${isMacInset ? 'workspace-titlebar--mac-inset' : ''}`}>
      <div className="workspace-titlebar__left">
        <Tooltip content={sidebarCollapsed ? t('titlebar.sidebar_show') : t('titlebar.sidebar_hide')}>
          <Button
            type="button"
            variant="icon"
            size="icon-sm"
            className="workspace-titlebar__button"
            onClick={onToggleSidebar}
            aria-label={sidebarCollapsed ? t('titlebar.sidebar_show') : t('titlebar.sidebar_hide')}
            aria-pressed={!sidebarCollapsed}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </Button>
        </Tooltip>
      </div>

      <div className="workspace-titlebar__center">
        <div className="workspace-titlebar__title">
          <span>Coworker</span>
          <span aria-hidden="true">/</span>
          <strong>{title}</strong>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="icon"
              size="icon-sm"
              className="workspace-titlebar__button"
              aria-label={t('titlebar.session_actions')}
            >
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="titlebar-menu">
            <DropdownMenuItem onClick={onRenameSession} disabled={!canEditSession}>
              <Pencil size={14} />
              {t('titlebar.rename_session')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onDeleteSession} disabled={!canEditSession}>
              <Trash2 size={14} />
              {t('titlebar.delete_session')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="workspace-titlebar__right">
        <div className="workspace-titlebar__status" aria-label={statusText}>
          <span className={`status-dot status-dot--${status}`} />
          <span>{statusText}</span>
        </div>
        <Tooltip content={bottomPanelOpen ? t('titlebar.bottom_hide') : t('titlebar.bottom_show')}>
          <Button
            type="button"
            variant="icon"
            size="icon-sm"
            className={`workspace-titlebar__button ${bottomPanelOpen ? 'workspace-titlebar__button--active' : ''}`}
            onClick={onToggleBottomPanel}
            aria-label={bottomPanelOpen ? t('titlebar.bottom_hide') : t('titlebar.bottom_show')}
            aria-pressed={bottomPanelOpen}
          >
            <PanelBottom size={16} />
          </Button>
        </Tooltip>
        <Tooltip content={rightSidebarOpen ? t('titlebar.right_sidebar_hide') : t('titlebar.right_sidebar_show')}>
          <Button
            type="button"
            variant="icon"
            size="icon-sm"
            className={`workspace-titlebar__button ${rightSidebarOpen ? 'workspace-titlebar__button--active' : ''}`}
            onClick={onToggleRightSidebar}
            aria-label={rightSidebarOpen ? t('titlebar.right_sidebar_hide') : t('titlebar.right_sidebar_show')}
            aria-pressed={rightSidebarOpen}
          >
            {rightSidebarOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}

function titleForView(activeView: AppView, sessionTitle: string): string {
  if (activeView === 'providers') return t('providers.title');
  if (activeView === 'settings') return t('settings.title');
  return sessionTitle.trim() || t('sidebar.new_chat');
}
