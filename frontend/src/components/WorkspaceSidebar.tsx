import { ChevronDown, ChevronRight, Folder, FolderOpen, MessageSquarePlus, MoreHorizontal, Pencil, Plus, Settings2, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
  collapsed: boolean;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
  onViewChange: (view: AppView) => void;
  onNewChat: (projectId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onCreateProject: () => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
  onMoveSession: (sessionId: string, projectId: string) => void;
}

interface SessionRowProps {
  session: SessionSummary;
  active: boolean;
  isStandalone: boolean;
  projects: ProjectEntry[];
  onOpen: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  onMove: (sessionId: string, projectId: string) => void;
}

function SessionRow({ session, active, isStandalone, projects, onOpen, onDelete, onMove }: SessionRowProps) {
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
          {isStandalone ? (
            projects.map((project) => (
              <DropdownMenuItem key={project.id} onClick={() => onMove(session.id, project.id)}>
                <Folder size={14} />
                {t('sidebar.session_move_to')}「{project.name}」
              </DropdownMenuItem>
            ))
          ) : (
            <DropdownMenuItem onClick={() => onMove(session.id, '')}>
              <Folder size={14} />
              {t('sidebar.session_move_out')}
            </DropdownMenuItem>
          )}
          {!isStandalone && projects.length > 0 && <DropdownMenuSeparator />}
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
  defaultExpanded?: boolean;
  onNewChat: (projectId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
  onMoveSession: (sessionId: string, projectId: string) => void;
}

function ProjectRow({ project, sessions, activeSessionId, defaultExpanded, onNewChat, onOpenSession, onDeleteSession, onRenameProject, onDeleteProject, onMoveSession }: ProjectRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false);

  return (
    <div className="sidebar-project">
      <div className={`sidebar-project__title-row ${activeSessionId && sessions.some((s) => s.id === activeSessionId) ? 'sidebar-project__title-row--active' : ''}`}>
        <button
          type="button"
          className="sidebar-project__title"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
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
              isStandalone={false}
              projects={[]}
              onOpen={onOpenSession}
              onDelete={onDeleteSession}
              onMove={onMoveSession}
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
  collapsed,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
  onViewChange,
  onNewChat,
  onOpenSession,
  onDeleteSession,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
  onMoveSession,
}: WorkspaceSidebarProps) {
  const workspaceName = workspaceProjectName(config?.workspace);
  const standaloneSessions = sessions.filter((session) => !session.project_id);
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
        <button className={`sidebar-nav-item sidebar-nav-item--strong ${activeView === 'chat' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onNewChat()}>
          <MessageSquarePlus size={17} />
          {!collapsed && <span>{t('sidebar.new_chat')}</span>}
          {!collapsed && <Plus className="sidebar-nav-item__trail" size={15} />}
        </button>
        <button className={`sidebar-nav-item ${activeView === 'providers' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('providers')}>
          <Settings2 size={17} />
          {!collapsed && <span>{t('nav.providers')}</span>}
        </button>
      </nav>

      <Separator />

      {!collapsed && (
        <div className="sidebar__scroll">
        <section className="sidebar-group" aria-labelledby="sidebar-sessions-title">
          <div className="sidebar-group__header">
            <h2 id="sidebar-sessions-title">{t('sidebar.sessions')}</h2>
            <Tooltip content={t('sidebar.new_chat')}>
              <Button variant="icon" className="h-7 w-7" onClick={() => onNewChat()}>
                <Plus size={15} />
              </Button>
            </Tooltip>
          </div>

          {standaloneSessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              active={session.id === activeSessionId}
              isStandalone
              projects={projects}
              onOpen={onOpenSession}
              onDelete={onDeleteSession}
              onMove={onMoveSession}
            />
          ))}
          {standaloneSessions.length === 0 && <p className="sidebar-group__empty">{t('sidebar.sessions_empty')}</p>}
        </section>

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
            <div className="sidebar-project sidebar-project--empty">
              <div className="sidebar-project__title sidebar-project__title--static">
                <Folder size={16} />
                <span>{workspaceName}</span>
              </div>
              <p className="sidebar-project__hint">{t('sidebar.projects_empty')}</p>
            </div>
          )}

          {projects.map((project) => (
            <ProjectRow
              key={project.id}
              project={project}
              sessions={sessions.filter((session) => session.project_id === project.id)}
              {...(activeSessionId ? { activeSessionId } : {})}
              defaultExpanded={expandedProjectIds.has(project.id)}
              onNewChat={onNewChat}
              onOpenSession={onOpenSession}
              onDeleteSession={onDeleteSession}
              onRenameProject={onRenameProject}
              onDeleteProject={onDeleteProject}
              onMoveSession={onMoveSession}
            />
          ))}
        </section>
      </div>
      )}

      <div className="sidebar__settings">
        <Separator />
        <button className={`sidebar-nav-item ${activeView === 'settings' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('settings')}>
          <Settings2 size={17} />
          {!collapsed && <span>{t('nav.settings')}</span>}
        </button>
      </div>
    </aside>
  );
}

function workspaceProjectName(workspace?: string): string {
  if (!workspace) return t('sidebar.default_project');
  const normalized = workspace.replace(/\/+$/, '');
  const [, name] = normalized.match(/([^/]+)$/) ?? [];
  return name || t('sidebar.default_project');
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
