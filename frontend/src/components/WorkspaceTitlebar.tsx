import {
  AlertTriangle,
  Diff,
  Layers,
  MoreHorizontal,
  PanelBottom,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Trash2,
} from 'lucide-react';
import type { AppView, ContextUsage } from '../types';
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
  projectName: string;
  sidebarCollapsed: boolean;
  rightSidebarOpen: boolean;
  bottomPanelOpen: boolean;
  changesPanelOpen: boolean;
  canEditSession: boolean;
  pendingCount: number;
  contextUsage?: ContextUsage | null;
  onToggleSidebar: () => void;
  onToggleRightSidebar: () => void;
  onToggleBottomPanel: () => void;
  onToggleChangesPanel: () => void;
  onRenameSession: () => void;
  onDeleteSession: () => void;
}

const STATUS_COLORS = {
  green: '#2fa06d',
  amber: '#c79a2f',
  red: '#c95343',
} as const;

function ContextBudgetIndicator({ usage }: { usage: ContextUsage }) {
  const CHARS_PER_TOKEN = 3.5;
  // Prefer the backend's token estimates (they count tool I/O and are CJK-aware);
  // fall back to char-based values for older backends — B3/B4.
  const used = usage.usedTokens ?? Math.round(usage.usedChars / CHARS_PER_TOKEN);
  // When compaction has been triggered, the active budget may have been halved.
  // Use activeBudgetTokens (if provided by the backend) so the bar reflects the
  // real budget ceiling; otherwise fall back to the model's full context window.
  const rawActive: number | undefined = usage.activeBudgetTokens;
  const activeTok = (rawActive != null && rawActive > 0) ? rawActive : undefined;
  const windowTok = activeTok ?? usage.windowTokens ?? usage.budgetTokens ?? Math.round(usage.budgetChars / CHARS_PER_TOKEN);
  // Bar is measured against the model's REAL window so the meter reflects true
  // context fullness; trimming kicks in at 75% (the safety budget) — see B2.
  const pct = windowTok > 0 ? Math.min(100, Math.round((used / windowTok) * 100)) : 0;
  const color = pct < 70 ? STATUS_COLORS.green : pct < 90 ? STATUS_COLORS.amber : STATUS_COLORS.red;
  const formatK = (n: number) => (n >= 1_000 ? `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k` : `${n}`);
  const tooltip = [
    t('titlebar.context_usage', { used: formatK(used), window: formatK(windowTok), pct }),
    t('titlebar.context_trim_hint'),
    ...(usage.compressed ? [t('titlebar.context_compressing')] : []),
    ...(usage.compactCount > 0 || usage.compacted ? [t('titlebar.context_compressed')] : []),
    ...(usage.windowWarning ? [usage.windowWarning] : []),
  ].join(' · ');

  return (
    <Tooltip content={tooltip}>
      <div className="workspace-titlebar__context-budget">
        <Layers size={12} className="context-budget-icon" />
        <div className="context-budget-bar" style={{ '--ctx-color': color } as React.CSSProperties}>
          <div className="context-budget-bar__fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="context-budget-text">{formatK(used)} / {formatK(windowTok)}</span>
        {usage.windowWarning && (
          <span className="context-budget-badge context-budget-badge--warn" title={usage.windowWarning}>
            ⚠
          </span>
        )}
        {usage.compressed && (
          <span className="context-budget-badge context-budget-badge--compressing" title={t('titlebar.context_compressing')}>
            {t('titlebar.context_compressing')}
          </span>
        )}
        {(usage.compactCount > 0 || usage.compacted) && (
          <span className="context-budget-badge" title={t('titlebar.context_compressed')}>
            {t('titlebar.context_badge')}
          </span>
        )}
      </div>
    </Tooltip>
  );
}

const isMacInset = typeof window !== 'undefined' && window.electronAPI?.platform === 'darwin';

export function WorkspaceTitlebar({
  status,
  activeView,
  sessionTitle,
  projectName,
  sidebarCollapsed,
  rightSidebarOpen,
  bottomPanelOpen,
  changesPanelOpen,
  canEditSession,
  pendingCount,
  contextUsage,
  onToggleSidebar,
  onToggleRightSidebar,
  onToggleBottomPanel,
  onToggleChangesPanel,
  onRenameSession,
  onDeleteSession,
}: WorkspaceTitlebarProps) {
  const statusText = status === 'ready' ? t('common.ready') : status === 'connecting' ? t('common.connecting') : t('common.offline');
  const title = titleForView(activeView, sessionTitle);
  const displayProject = truncate(projectName, 12);
  const displayTitle = truncate(title, 15);

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
          <span title={projectName}>{displayProject}</span>
          <span className="workspace-titlebar__sep" aria-hidden="true">/</span>
          <strong title={title}>{displayTitle}</strong>
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
        {contextUsage && <ContextBudgetIndicator usage={contextUsage} />}
        <div className="workspace-titlebar__status" aria-label={statusText}>
          <span className={`status-dot status-dot--${status}`} />
          <span>{statusText}</span>
        </div>
        {pendingCount > 0 && (
          <Tooltip content={t('chat.pending_badge', { count: pendingCount })}>
            <div className="titlebar-pending" role="status">
              <AlertTriangle size={13} />
              <span>{pendingCount}</span>
            </div>
          </Tooltip>
        )}
        <Tooltip content={changesPanelOpen ? t('titlebar.changes_hide') : t('titlebar.changes_show')}>
          <Button
            type="button"
            variant="icon"
            size="icon-sm"
            className={`workspace-titlebar__button ${changesPanelOpen ? 'workspace-titlebar__button--active' : ''}`}
            onClick={onToggleChangesPanel}
            aria-label={changesPanelOpen ? t('titlebar.changes_hide') : t('titlebar.changes_show')}
            aria-pressed={changesPanelOpen}
          >
            <Diff size={16} />
          </Button>
        </Tooltip>
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
  if (activeView === 'mcp') return t('mcp.title');
  if (activeView === 'skills') return t('skills.title');
  if (activeView === 'memory') return t('memory.title');
  if (activeView === 'settings') return t('settings.title');
  if (activeView === 'org') return t('settings.org_group');
  return sessionTitle.trim() || t('sidebar.new_chat');
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
