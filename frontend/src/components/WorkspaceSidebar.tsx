import { Check, ChevronDown, ChevronRight, ChevronUp, Copy, FileText, Folder, FolderOpen, LayoutDashboard, Loader2, MessageCircle, MessageSquare, MessageSquarePlus, MoreHorizontal, Network, Pencil, Plus, Settings2, Trash2, Users, Briefcase, Folders, FoldersIcon, CirclePile, FolderTree } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import type { AppView, OrgRosterEntry, ProjectEntry, SessionBadgeMap, SessionSummary } from '../types';
import { t } from '../lib/i18n';
import { displayProjectName } from '../lib/projectName';
import { formatTimeAgo } from '../lib/utils';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { Tooltip } from './ui/tooltip';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import { ContextMenu, type ContextMenuItem } from './ui/context-menu';
import { SidebarScrollbar } from './ui/sidebar-scrollbar';
import { SessionStatusBadge } from './SessionStatusBadge';
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import cwIconWhite from '../../../assets/brand/png/cw-icon-white.png';
import cwIconBlack from '../../../assets/brand/png/cw-icon-black.png';
import coworkerLogoBlack from '../../../assets/brand/png/coworker-logo-black.png';
import coworkerLogoWhite from '../../../assets/brand/png/coworker-logo-white.png';

interface WorkspaceSidebarProps {
  sessions: SessionSummary[];
  projects: ProjectEntry[];
  activeView: AppView;
  activeSessionId?: string;
  activeProjectId?: string;
  sessionBadges: SessionBadgeMap;
  collapsed: boolean;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
  onViewChange: (view: AppView) => void;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenProject: (projectId: string) => void;
  onOpenDashboard: (projectId: string) => void;
  onSelectProject: (projectId: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onCreateProject: () => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
}

interface SessionRowProps {
  session: SessionSummary;
  active: boolean;
  badges?: SessionBadgeMap;
  onOpen: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}

const DEFAULT_AGENT_ID = 'default_agent';

const SIDEBAR_PROJECT_ORDER_KEY = 'coworker.sidebar.projectOrder';

function loadProjectOrder(): string[] {
  try {
    const raw = localStorage.getItem(SIDEBAR_PROJECT_ORDER_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === 'string');
  } catch {
    return [];
  }
}

function saveProjectOrder(ids: string[]): void {
  try {
    localStorage.setItem(SIDEBAR_PROJECT_ORDER_KEY, JSON.stringify(ids));
  } catch {
    /* localStorage may be unavailable in restricted renderer contexts */
  }
}

/**
 * Reorders `projects` according to a previously persisted id order.
 * Ids no longer present are skipped; brand-new projects are appended
 * at the end so the manual order stays stable across reloads.
 */
function orderProjects(projects: ProjectEntry[], preferred: string[]): ProjectEntry[] {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const seen = new Set<string>();
  const result: ProjectEntry[] = [];
  for (const id of preferred) {
    const project = byId.get(id);
    if (project && !seen.has(id)) {
      result.push(project);
      seen.add(id);
    }
  }
  for (const project of projects) {
    if (!seen.has(project.id)) {
      result.push(project);
      seen.add(project.id);
    }
  }
  // 系统保留的聊天项目始终置顶固定。
  return result.sort((a, b) => Number(b.is_chat) - Number(a.is_chat));
}

function SessionRow({ session, active, badges, onOpen, onDelete }: SessionRowProps) {
  const [copied, setCopied] = useState(false);
  const [sessionMenu, setSessionMenu] = useState<{ x: number; y: number } | null>(null);

  const handleCopyId = async () => {
    try {
      await navigator.clipboard.writeText(session.id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };

  const sessionMenuItems: ContextMenuItem[] = [
    { id: 'open', label: t('sidebar.session_open'), icon: <FolderOpen size={14} />, onSelect: () => onOpen(session.id) },
    { id: 'copy-id', label: copied ? t('sidebar.session_id_copied') : t('sidebar.session_copy_id'), icon: copied ? <Check size={14} /> : <Copy size={14} />, onSelect: () => void handleCopyId() },
    { id: 'delete', label: t('common.delete'), icon: <Trash2 size={14} />, danger: true, onSelect: () => onDelete(session.id) },
  ];

  return (
    <>
    <div className="sidebar-session__container">
      {badges && <SessionStatusBadge badges={badges.get(session.id)} />}
      {badges && (badges.get(session.id)?.unread ?? 0) > 0 && (
        <span className="sidebar-session__unread-pill">{badges.get(session.id)?.unread}</span>
      )}
      <div className={`sidebar-session ${active ? 'sidebar-session--active' : ''}`} onContextMenu={(e) => { e.preventDefault(); setSessionMenu({ x: e.clientX, y: e.clientY }); }}>
        <button type="button" className="sidebar-session__inner" onClick={() => onOpen(session.id)}>
          <span className="sidebar-session__title">{session.title}</span>
          <DropdownMenu>
            <DropdownMenuTrigger className="sidebar-session__more-trigger" aria-label="Session actions">
              <span className="sidebar-session__more-time">{formatTimeAgo(session.updated_at || session.created_at)}</span>
              <MoreHorizontal size={15} className="sidebar-session__more-icon" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" alignOffset={-8}>
              <DropdownMenuItem onClick={() => onOpen(session.id)}>
                <FolderOpen size={14} />
                {t('sidebar.session_open')}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void handleCopyId()}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? t('sidebar.session_id_copied') : t('sidebar.session_copy_id')}
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => onDelete(session.id)}>
                <Trash2 size={14} />
                {t('common.delete')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </button>
      </div>
      <ContextMenu open={sessionMenu !== null} x={sessionMenu?.x ?? 0} y={sessionMenu?.y ?? 0} onClose={() => setSessionMenu(null)} items={sessionMenuItems} />
    </div>
    </>
  );
}

interface ProjectRowProps {
  project: ProjectEntry;
  sessions: SessionSummary[];
  activeSessionId?: string;
  activeProjectId?: string;
  sessionBadges: SessionBadgeMap;
  defaultExpanded?: boolean;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenProject: (projectId: string) => void;
  onOpenDashboard: (projectId: string) => void;
  onSelectProject: (projectId: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onRenameProject: (project: ProjectEntry) => void;
  onDeleteProject: (projectId: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
}

interface AgentGroupData {
  agentId: string;
  name: string;
  role: string;
  team: string;
  disabled: boolean;
  sessions: SessionSummary[];
}

const AGENT_PAGE_SIZE = 10;

function AgentGroup({ group, projectId, activeSessionId, sessionBadges, onNewChat, onOpenSession, onDeleteSession }: {
  group: AgentGroupData;
  projectId: string;
  activeSessionId?: string;
  sessionBadges: SessionBadgeMap;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [listExpanded, setListExpanded] = useState(false);
  const sorted = useMemo(
    () =>
      [...group.sessions].sort((a, b) => {
        const ta = new Date(a.updated_at || a.created_at).getTime();
        const tb = new Date(b.updated_at || b.created_at).getTime();
        return tb - ta;
      }),
    [group.sessions],
  );
  const hasMore = sorted.length > AGENT_PAGE_SIZE;
  const shownCount = listExpanded ? sorted.length : AGENT_PAGE_SIZE;
  const displaySessions = sorted.slice(0, shownCount);
  const countLabel = t('sidebar.agent_sessions_count', { count: sorted.length });
  const heading = [group.name, group.role && group.role].filter(Boolean).join(' · ');

  return (
    <div className={`sidebar-agent ${group.disabled ? 'sidebar-agent--disabled' : ''}`}>
      <div
        className="sidebar-agent__title"
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded((v) => !v); } }}
        role="button"
        tabIndex={0}
      >
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Users size={13} className="sidebar-agent__icon" />
        <span className="sidebar-agent__name">{heading}</span>
        <span className="sidebar-agent__meta">{countLabel}</span>
        {group.team && <span className="sidebar-agent__team">{group.team}</span>}
        <button
          type="button"
          className="sidebar-agent__new-trigger"
          onClick={(e) => { e.stopPropagation(); onNewChat(projectId, group.agentId); }}
          title={t('sidebar.new_chat')}
          aria-label={t('sidebar.new_chat')}
        >
          <MessageSquarePlus size={13} />
        </button>
      </div>
      {expanded && (
        <div className="sidebar-agent__sessions">
          {sorted.length === 0 ? (
            <p className="sidebar-agent__empty">{t('sidebar.agent_empty')}</p>
          ) : (
            <>
              {displaySessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  active={session.id === activeSessionId}
                  badges={sessionBadges}
                  onOpen={onOpenSession}
                  onDelete={onDeleteSession}
                />
              ))}
              {hasMore && (
                <>
                  <div className="sidebar-project__footer-meta">
                    {listExpanded
                      ? t('sidebar.sessions_shown', { shown: shownCount, total: sorted.length })
                      : t('sidebar.sessions_recent', { shown: shownCount, total: sorted.length })}
                  </div>
                  <div className="sidebar-project__footer">
                    {listExpanded ? (
                      <button type="button" className="pg-btn" onClick={() => setListExpanded(false)}>
                        <ChevronUp size={14} />
                        <span>{t('sidebar.collapse_list')}</span>
                      </button>
                    ) : (
                      <button type="button" className="pg-btn pg-btn--accent" onClick={() => setListExpanded(true)}>
                        <ChevronDown size={14} />
                        <span>{t('sidebar.expand_more')}</span>
                      </button>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ProjectRow({ project, sessions, activeSessionId, activeProjectId, sessionBadges, defaultExpanded, onNewChat, onOpenProject, onOpenDashboard, onSelectProject, onOpenSession, onDeleteSession, onRenameProject, onDeleteProject, onOpenOrgSettings }: ProjectRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false);
  const [projectMenu, setProjectMenu] = useState<{ x: number; y: number } | null>(null);
  const isChat = Boolean(project.is_chat);
  const {
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: project.id, disabled: isChat });
  const dragStyle: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  const roster = useMemo(() => project.roster ?? [], [project.roster]);
  const isSingle = project.mode === 'single';

  // 匯總專案下所有會話的「有任務狀態」計數（正在處理 + 未處理）
  // 只要任何會話有 running / unread / approvals / error，角標就顯示
  // （running-only 時無計數，渲染為小圓點而非數字）。
  const projectBadge = useMemo(() => {
    let total = 0;
    let hasActive = false;
    for (const session of sessions) {
      const badges = sessionBadges.get(session.id);
      if (badges) {
        if (badges.running) hasActive = true;
        if (badges.unread > 0) hasActive = true;
        if (badges.approvals > 0) hasActive = true;
        if (badges.error) hasActive = true;
        total += badges.unread;
        total += badges.approvals;
      }
    }
    // 如果沒有任何 active 狀態，不顯示角標（即使 total > 0 也隱藏）
    return { hasActive, total };
  }, [sessions, sessionBadges]);
  const projectBadgeActive = projectBadge.hasActive;
  const projectUnread = projectBadge.total;

  const groups = useMemo<AgentGroupData[]>(() => {
    if (isSingle) return [];
    const rosterById = new Map<string, OrgRosterEntry>(roster.map((entry) => [entry.id, entry]));
    const byAgent = new Map<string, SessionSummary[]>();
    for (const session of sessions) {
      const agentId = session.agent_id || DEFAULT_AGENT_ID;
      const bucket = byAgent.get(agentId);
      if (bucket) bucket.push(session);
      else byAgent.set(agentId, [session]);
    }
    const knownAgentIds = roster.map((entry) => entry.id);
    const unknownIds = [...byAgent.keys()].filter((id) => !rosterById.has(id));
    // default_agent first; then known roster order; then unknown agent ids from sessions
    const ordered: string[] = [];
    if (rosterById.has(DEFAULT_AGENT_ID)) ordered.push(DEFAULT_AGENT_ID);
    for (const id of knownAgentIds) if (id !== DEFAULT_AGENT_ID) ordered.push(id);
    ordered.push(...unknownIds);

    const built: AgentGroupData[] = ordered
      .filter((id) => rosterById.has(id) || byAgent.has(id))
      .map((id) => {
        const entry = rosterById.get(id);
        const agentSessions = byAgent.get(id) ?? [];
        return {
          agentId: id,
          name: entry?.name || id,
          role: entry?.role ?? '',
          team: entry?.team ?? '',
          disabled: entry?.status === 'disabled',
          sessions: agentSessions,
        };
      });
    // groups sorted: default_agent first, then by newest session inside each group
    return built;
  }, [roster, sessions, isSingle]);

  const hasSessions = sessions.length > 0;
  const active = Boolean(activeSessionId && sessions.some((s) => s.id === activeSessionId)) || (activeProjectId === project.id && hasSessions);

  const handleTitleClick = () => {
    setExpanded((v) => !v);
    onSelectProject(project.id);
  };

  const projectMenuItems: ContextMenuItem[] = [
    { id: 'dashboard', label: t('sidebar.dashboard'), icon: <LayoutDashboard size={14} />, onSelect: () => onOpenDashboard(project.id) },
    ...(onOpenOrgSettings && !isSingle
      ? [{ id: 'team', label: t('sidebar.org_team_manage'), icon: <Users size={14} />, dividerBefore: true, onSelect: () => onOpenOrgSettings(project.id) }]
      : []),
    // 系统聊天项目不可重命名/删除（后端同样拒绝）。
    ...(isChat
      ? []
      : [
          { id: 'rename', label: t('sidebar.project_rename'), icon: <Pencil size={14} />, dividerBefore: true, onSelect: () => onRenameProject(project) },
          { id: 'delete', label: t('sidebar.project_delete'), icon: <Trash2 size={14} />, danger: true, onSelect: () => onDeleteProject(project.id) },
        ]),
  ];

  return (
    <>
    <div ref={setNodeRef} style={dragStyle} className="sidebar-project" data-dragging={isDragging}>
      <div
        className={`sidebar-project__title-row ${active ? 'sidebar-project__title-row--active' : ''}`}
        {...listeners}
        onContextMenu={(e) => { e.preventDefault(); setProjectMenu({ x: e.clientX, y: e.clientY }); }}
      >
        <div className="sidebar-project__title" onClick={handleTitleClick} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleTitleClick(); } }} role="button" tabIndex={0} style={{cursor:'pointer'}}>
          {isChat ? <MessageCircle size={16} /> : (expanded ? (isSingle ? <FolderOpen size={16} /> : <FolderTree size={16} />) : (isSingle ? <Folder size={16} /> : <FolderTree size={16} />))}
          {projectBadgeActive && (
            <span className={`sidebar-project__unread-badge${projectUnread === 0 ? ' sidebar-project__unread-badge--dot' : ''}`} aria-label={ projectUnread > 0 ? `${projectUnread} unread` : t('sidebar.project_has_pending') }>
              {projectUnread > 0 ? (projectUnread > 99 ? '99+' : projectUnread) : ''}
            </span>
          )}
          <span>{displayProjectName(project)}</span>
          <span className="sidebar-project__chevron-icon" onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }} onPointerDown={(e) => e.stopPropagation()}>
            {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </span>
          <div className="sidebar-project__actions" onPointerDown={(e) => e.stopPropagation()} onContextMenu={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild className="sidebar-project__more-trigger" aria-label="Project actions">
                <span className="more-icon-wrapper"><MoreHorizontal size={15} /></span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" alignOffset={-8} className="min-w-44">
                <DropdownMenuItem onClick={() => onOpenDashboard(project.id)}>
                  <LayoutDashboard size={14} />
                  {t('sidebar.dashboard')}
                </DropdownMenuItem>
                {onOpenOrgSettings && !isSingle && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => onOpenOrgSettings(project.id)}>
                      <Users size={14} />
                      {t('sidebar.org_team_manage')}
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuSeparator />
                {!isChat && (
                  <>
                    <DropdownMenuItem onClick={() => onRenameProject(project)}>
                      <Pencil size={14} />
                      {t('sidebar.project_rename')}
                    </DropdownMenuItem>
                    <DropdownMenuItem variant="destructive" onClick={() => onDeleteProject(project.id)}>
                      <Trash2 size={14} />
                      {t('sidebar.project_delete')}
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            <button type="button" className="sidebar-project__new-trigger" onClick={(e) => { e.stopPropagation(); onNewChat(project.id); }} title={t('sidebar.new_chat')} aria-label={t('sidebar.new_chat')}>
              <MessageSquarePlus size={15} />
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="sidebar-project__sessions">
          {sessions.length === 0 && (!isSingle ? roster.length === 0 : true) ? (
            <p className="sidebar-project__empty">{t('sidebar.project_empty')}</p>
          ) : (
            <>
              {isSingle ? (
                sessions.length > 0 && (
                  <div className="sidebar-project__flat-list">
                    {sessions.map((session) => (
                      <SessionRow
                        key={session.id}
                        session={session}
                        active={session.id === activeSessionId}
                        badges={sessionBadges}
                        onOpen={onOpenSession}
                        onDelete={onDeleteSession}
                      />
                    ))}
                  </div>
                )
              ) : (
                groups.map((group) => (
                  <AgentGroup
                    key={group.agentId}
                    group={group}
                    projectId={project.id}
                    {...(activeSessionId ? { activeSessionId } : {})}
                    sessionBadges={sessionBadges}
                    onNewChat={onNewChat}
                    onOpenSession={onOpenSession}
                    onDeleteSession={onDeleteSession}
                  />
                ))
              )}
              {sessions.length > 0 && !isSingle && groups.length === 0 && (
                <p className="sidebar-project__empty">{t('sidebar.project_empty')}</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
    <ContextMenu
      open={projectMenu !== null}
      x={projectMenu?.x ?? 0}
      y={projectMenu?.y ?? 0}
      onClose={() => setProjectMenu(null)}
      items={projectMenuItems}
    />
    </>
  );
}

export function WorkspaceSidebar({
  sessions,
  projects,
  activeView,
  activeSessionId,
  activeProjectId,
  sessionBadges,
  collapsed,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
  onViewChange,
  onNewChat,
  onOpenProject,
  onOpenDashboard,
  onSelectProject,
  onOpenSession,
  onDeleteSession,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
  onOpenOrgSettings,
}: WorkspaceSidebarProps) {
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(new Set());
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const [preferredOrder, setPreferredOrder] = useState<string[]>(() => loadProjectOrder());
  const orderedProjects = useMemo(
    () => orderProjects(projects, preferredOrder),
    [projects, preferredOrder],
  );
  const orderedProjectIds = useMemo(() => orderedProjects.map((p) => p.id), [orderedProjects]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleProjectDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = orderedProjectIds.indexOf(active.id as string);
    const newIndex = orderedProjectIds.indexOf(over.id as string);
    if (oldIndex < 0 || newIndex < 0) return;
    const next = arrayMove(orderedProjectIds, oldIndex, newIndex);
    setPreferredOrder(next);
    saveProjectOrder(next);
  };

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
        {!collapsed ? (
          <span className="sidebar__brand-lockup" aria-label="CoWorker">
            <img className="sidebar__brand-logo sidebar__brand-logo--dark" src={coworkerLogoBlack} alt="CoWorker" />
            <img className="sidebar__brand-logo sidebar__brand-logo--light" src={coworkerLogoWhite} alt="" aria-hidden="true" />
          </span>
        ) : (
          <>
            <img className="sidebar__brand-icon sidebar__brand-icon--dark" src={cwIconBlack} alt="CW" />
            <img className="sidebar__brand-icon sidebar__brand-icon--light" src={cwIconWhite} alt="CW" />
          </>
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
        <button className={`sidebar-nav-item ${activeView === 'mcp' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('mcp')}>
          <Network size={17} />
          {!collapsed && <span>{t('nav.mcp')}</span>}
        </button>
        <button className={`sidebar-nav-item ${activeView === 'skills' ? 'sidebar-nav-item--active' : ''}`} type="button" onClick={() => onViewChange('skills')}>
          <FileText size={17} />
          {!collapsed && <span>{t('nav.skills')}</span>}
        </button>
      </nav>

      <Separator />

      {!collapsed && (
        <div className="sidebar__scroll">
          <SidebarScrollbar>
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

            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleProjectDragEnd}>
              <SortableContext items={orderedProjectIds} strategy={verticalListSortingStrategy}>
                {orderedProjects.map((project) => (
                  <ProjectRow
                    key={project.id}
                    project={project}
                    sessions={sessions.filter((session) => session.project_id === project.id)}
                    {...(activeSessionId ? { activeSessionId } : {})}
                    {...(activeProjectId ? { activeProjectId } : {})}
                    sessionBadges={sessionBadges}
                    defaultExpanded={expandedProjectIds.has(project.id)}
                    onNewChat={onNewChat}
                    onOpenProject={onOpenProject}
                    onOpenDashboard={onOpenDashboard}
                    onSelectProject={onSelectProject}
                    onOpenSession={onOpenSession}
                    onDeleteSession={onDeleteSession}
                    onRenameProject={onRenameProject}
                    onDeleteProject={onDeleteProject}
                    {...(onOpenOrgSettings ? { onOpenOrgSettings } : {})}
                  />
                ))}
              </SortableContext>
            </DndContext>
            </section>
            </SidebarScrollbar>
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
