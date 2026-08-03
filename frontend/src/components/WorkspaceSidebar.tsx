import { ChevronDown, ChevronRight, Folder, FolderOpen, MessageSquarePlus, MoreHorizontal, Pencil, Plus, Settings2, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import type { AppView, ProjectEntry, RuntimeConfig, SessionSummary } from '../types';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { Tooltip } from './ui/tooltip';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import coworkerLogoBlack from '../assets/brand/coworker-logo-black.png';
import coworkerLogoWhite from '../assets/brand/coworker-logo-white.png';

interface WorkspaceSidebarProps {
  config: RuntimeConfig | null;
  sessions: SessionSummary[];
  projects: ProjectEntry[];
  activeView: AppView;
  activeSessionId?: string;
  activeProjectId?: string;
  collapsed: boolean;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
  onViewChange: (view: AppView) => void;
  onNewChat: (projectId?: string) => void;
  onOpenProject: (projectId: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onCreateProject: () => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
}

interface SessionRowProps {
  session: SessionSummary;
  active: boolean;
  onOpen: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}

function SessionRow({ session, active, onOpen, onDelete }: SessionRowProps) {
  return (
    <div className={`sidebar-session ${active ? 'sidebar-session--active' : ''}`}>
      <button type="button" className="sidebar-session__inner" onClick={() => onOpen(session.id)}>
        <span className="sidebar-session__title">{session.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger className="sidebar-session__more-trigger" aria-label="Session actions">
          <MoreHorizontal size={15} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" alignOffset={-8}>
          <DropdownMenuItem onClick={() => onOpen(session.id)}>
            <FolderOpen size={14} />
            {t('sidebar.session_open')}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={() => onDelete(session.id)}>
            <Trash2 size={14} />
            {t('common.delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

interface ProjectRowProps {
  project: ProjectEntry;
  sessions: SessionSummary[];
  activeSessionId?: string;
  activeProjectId?: string;
  defaultExpanded?: boolean;
  onNewChat: (projectId?: string) => void;
  onOpenProject: (projectId: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
}

function ProjectRow({ project, sessions, activeSessionId, activeProjectId, defaultExpanded, onNewChat, onOpenProject, onOpenSession, onDeleteSession, onRenameProject, onDeleteProject }: ProjectRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false);
  const active = Boolean(activeSessionId && sessions.some((s) => s.id === activeSessionId)) || activeProjectId === project.id;

  const handleTitleClick = () => {
    setExpanded(true);
    onOpenProject(project.id);
  };

  return (
    <div className="sidebar-project">
      <div className={`sidebar-project__title-row ${active ? 'sidebar-project__title-row--active' : ''}`}>
        <button
          type="button"
          className="sidebar-project__title"
          onClick={handleTitleClick}
          aria-expanded={expanded}
        >
          <span
            className="sidebar-project__chevron-icon"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
          >
            {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </span>
          {expanded ? <FolderOpen size={16} /> : <Folder size={16} />}
          <span>{project.name}</span>
          {project.session_count > 0 && <small className="sidebar-project__count">{project.session_count}</small>}
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger className="sidebar-project__more-trigger" aria-label="Project actions">
            <MoreHorizontal size={15} />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" alignOffset={-8}>
            <DropdownMenuItem onClick={() => onNewChat(project.id)}>
              <MessageSquarePlus size={14} />
              {t('sidebar.new_chat')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onRenameProject(project)}>
              <Pencil size={14} />
              {t('sidebar.project_rename')}
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onClick={() => onDeleteProject(project.id)}>
              <Trash2 size={14} />
              {t('sidebar.project_delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {expanded && (
        <div className="sidebar-project__sessions">
          <button type="button" className="sidebar-project__new-session" onClick={() => onNewChat(project.id)}>
            <Plus size={13} />
            {t('sidebar.new_chat')}
          </button>
          {sessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              active={session.id === activeSessionId}
              onOpen={onOpenSession}
              onDelete={onDeleteSession}
            />
          ))}
          {sessions.length === 0 && <p className="sidebar-project__empty">{t('sidebar.project_empty')}</p>}
        </div>
      )}
    </div>
  );
}

export function WorkspaceSidebar({
  config,
  sessions,
  projects,
  activeView,
  activeSessionId,
  activeProjectId,
  collapsed,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
  onViewChange,
  onNewChat,
  onOpenProject,
  onOpenSession,
  onDeleteSession,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
}: WorkspaceSidebarProps) {
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(new Set());
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const handleResizePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (collapsed) return;
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: document.querySelector('.sidebar')?.getBoundingClientRect().width ?? 276 };
    document.body.classList.add('sidebar-resizing');
    onResizeStart();
    window.addEventListener('pointermove', handleResizePointerMove);
    window.addEventListener('pointerup', handleResizePointerUp);
  };

  const handleResizePointerMove = (event: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const next = clamp(drag.startWidth + (event.clientX - drag.startX), 200, 480);
    onResizeWidth(next);
  };

  const handleResizePointerUp = () => {
    dragRef.current = null;
    document.body.classList.remove('sidebar-resizing');
    onResizeEnd();
    window.removeEventListener('pointermove', handleResizePointerMove);
    window.removeEventListener('pointerup', handleResizePointerUp);
  };

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handleResizePointerMove);
      window.removeEventListener('pointerup', handleResizePointerUp);
      document.body.classList.remove('sidebar-resizing');
    };
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    const session = sessions.find((item) => item.id === activeSessionId);
    if (!session?.project_id) return;
    setExpandedProjectIds((current) => {
      if (current.has(session.project_id)) return current;
      const next = new Set(current);
      next.add(session.project_id);
      return next;
    });
  }, [activeSessionId, sessions]);

  useEffect(() => {
    if (!activeProjectId) return;
    setExpandedProjectIds((current) => {
      if (current.has(activeProjectId)) return current;
      const next = new Set(current);
      next.add(activeProjectId);
      return next;
    });
  }, [activeProjectId]);

  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div
        className="sidebar__resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('sidebar.resize')}
        onPointerDown={handleResizePointerDown}
      />
      <div className="sidebar__brand">
        {!collapsed && (
          <span className="sidebar__brand-lockup" aria-label="CoWorker">
            <img className="sidebar__brand-logo sidebar__brand-logo--dark" src={coworkerLogoBlack} alt="CoWorker" />
            <img className="sidebar__brand-logo sidebar__brand-logo--light" src={coworkerLogoWhite} alt="" aria-hidden="true" />
          </span>
        )}
      </div>

      <nav className="sidebar__primary" aria-label={t('sidebar.primary_nav')}>
        <button className="sidebar-nav-item sidebar-nav-item--strong" type="button" onClick={() => onNewChat()}>
          <MessageSquarePlus size={17} />
          {!collapsed && <span>{t('sidebar.new_chat')}</span>}
        </button>
        <button className={`sidebar-nav-item ${activeView === 'providers' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('providers')}>
          <Settings2 size={17} />
          {!collapsed && <span>{t('nav.providers')}</span>}
        </button>
      </nav>

      <Separator />

      {!collapsed && (
        <div className="sidebar__scroll">
          <section className="sidebar-group" aria-labelledby="sidebar-projects-title">
            <div className="sidebar-group__header">
              <h2 id="sidebar-projects-title">{t('sidebar.projects')}</h2>
              <Tooltip content={t('sidebar.new_project')}>
                <Button variant="icon" className="h-7 w-7" onClick={onCreateProject}>
                  <Plus size={15} />
                </Button>
              </Tooltip>
            </div>

            {projects.length === 0 && (
              <p className="sidebar-group__empty">{t('sidebar.projects_empty')}</p>
            )}

            {projects.map((project) => (
              <ProjectRow
                key={project.id}
                project={project}
                sessions={sessions.filter((session) => session.project_id === project.id)}
                {...(activeSessionId ? { activeSessionId } : {})}
                {...(activeProjectId ? { activeProjectId } : {})}
                defaultExpanded={expandedProjectIds.has(project.id)}
                onNewChat={onNewChat}
                onOpenProject={onOpenProject}
                onOpenSession={onOpenSession}
                onDeleteSession={onDeleteSession}
                onRenameProject={onRenameProject}
                onDeleteProject={onDeleteProject}
              />
            ))}
          </section>
        </div>
      )}

      <div className="sidebar__settings">
        <Separator />
        <button
          className={`sidebar-nav-item ${activeView === 'settings' ? 'sidebar-nav-item--active' : ''}`}
          type="button"
          onClick={() => onViewChange(activeView === 'settings' ? 'chat' : 'settings')}
        >
          <Settings2 size={17} />
          {!collapsed && <span>{t('nav.settings')}</span>}
        </button>
      </div>
    </aside>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
