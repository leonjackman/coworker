import { Check, ChevronDown, ChevronRight, ChevronUp, Copy, Loader2, MessageSquare, MessageSquarePlus, MoreHorizontal, Trash2, Users } from 'lucide-react';
import { useMemo, useState } from 'react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './ui/dropdown-menu';
import { WorkspacePage } from './ui/workspace-page';
import { t } from '../lib/i18n';
import { displayProjectName } from '../lib/projectName';
import { formatTimeAgo } from '../lib/utils';
import type { OrgRosterEntry, ProjectEntry, SessionSummary } from '../types';

interface ProjectSessionListProps {
  project: ProjectEntry;
  sessions: SessionSummary[];
  runningSessionIds?: Set<string>;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
}

const DEFAULT_AGENT_ID = 'default_agent';
const PAGE_SIZE = 10;

interface AgentGroupData {
  agentId: string;
  name: string;
  role: string;
  team: string;
  disabled: boolean;
  sessions: SessionSummary[];
}

export function ProjectSessionList({ project, sessions, runningSessionIds, onNewChat, onOpenSession, onDeleteSession, onOpenOrgSettings }: ProjectSessionListProps) {
  const [expandedAgentIds, setExpandedAgentIds] = useState<Set<string>>(() => new Set());
  const [expandedLists, setExpandedLists] = useState<Set<string>>(() => new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const isSingle = project.mode === 'single';

  const groups = useMemo<AgentGroupData[]>(() => {
    if (isSingle) return [];
    const roster = project.roster ?? [];
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
    const ordered: string[] = [];
    if (rosterById.has(DEFAULT_AGENT_ID)) ordered.push(DEFAULT_AGENT_ID);
    for (const id of knownAgentIds) if (id !== DEFAULT_AGENT_ID) ordered.push(id);
    ordered.push(...unknownIds);

    return ordered
      .filter((id) => rosterById.has(id) || byAgent.has(id))
      .map((id) => {
        const entry = rosterById.get(id);
        return {
          agentId: id,
          name: entry?.name || id,
          role: entry?.role ?? '',
          team: entry?.team ?? '',
          disabled: entry?.status === 'disabled',
          sessions: (byAgent.get(id) ?? []).sort((a, b) => {
            const ta = new Date(a.updated_at || a.created_at).getTime();
            const tb = new Date(b.updated_at || b.created_at).getTime();
            return tb - ta;
          }),
        };
      });
  }, [project.roster, sessions, isSingle]);

  const handleCopyId = async (sessionId: string) => {
    try {
      await navigator.clipboard.writeText(sessionId);
      setCopiedId(sessionId);
      window.setTimeout(() => setCopiedId(null), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    await onDeleteSession(sessionId);
  };

  const toggleAgent = (agentId: string) => {
    setExpandedAgentIds((current) => {
      const next = new Set(current);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  };

  const toggleList = (agentId: string) => {
    setExpandedLists((current) => {
      const next = new Set(current);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  };

  return (
    <WorkspacePage
      className="workspace-page--sessions"
      contentClassName="workspace-page__content--sessions"
      eyebrow={project.workspace_path}
      title={displayProjectName(project)}
      description={sessions.length === 0 ? t('project_session.empty_state') : t('project_session.project_sessions_count', { count: sessions.length })}
      action={onOpenOrgSettings && !isSingle ? (
        <button type="button" className="project-session-list__team-btn" onClick={() => onOpenOrgSettings(project.id)}>
          <Users size={15} />
          {t('sidebar.org_team_manage')}
        </button>
      ) : undefined}
    >
      <section className="project-session-list">
        <div className="project-session-list__items">
          {sessions.length === 0 && (!isSingle ? (project.roster?.length ?? 0) === 0 : true) ? (
            <p className="project-session-list__empty">{t('project_session.empty_state_text')}</p>
          ) : (
            <>
              {isSingle ? (
                sessions.map((session) => (
                  <div key={session.id} className="project-session-list__item">
                    <button
                      className="project-session-list__item-content"
                      type="button"
                      onClick={() => onOpenSession(session.id)}
                    >
                      {runningSessionIds?.has(session.id) && <Loader2 size={15} className="project-session-list__running-icon" aria-label="Running" />}
                      <MessageSquare size={15} className="project-session-list__item-icon" />
                      <span className="project-session-list__item-title">{session.title}</span>
                      <span className="project-session-list__item-time">{formatTimeAgo(session.updated_at || session.created_at)}</span>
                    </button>
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        className="project-session-list__item-more"
                        type="button"
                        aria-label={t('titlebar.session_actions')}
                      >
                        <MoreHorizontal size={15} />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" alignOffset={-8}>
                        <DropdownMenuItem onClick={() => onOpenSession(session.id)}>
                          <MessageSquare size={14} />
                          {t('sidebar.session_open')}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => void handleCopyId(session.id)}>
                          {copiedId === session.id ? <Check size={14} /> : <Copy size={14} />}
                          {copiedId === session.id ? t('sidebar.session_id_copied') : t('sidebar.session_copy_id')}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={(e) => void handleDeleteSession(session.id, e)}
                        >
                          <Trash2 size={14} />
                          {t('common.delete')}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))
              ) : (
                groups.map((group) => {
                const expanded = expandedAgentIds.has(group.agentId);
                const listExpanded = expandedLists.has(group.agentId);
                const hasMore = group.sessions.length > PAGE_SIZE;
                const shownCount = listExpanded ? group.sessions.length : PAGE_SIZE;
                const displaySessions = group.sessions.slice(0, shownCount);
                const heading = [group.name, group.role && group.role].filter(Boolean).join(' · ');
                return (
                  <div key={group.agentId} className={`project-session-list__agent ${group.disabled ? 'project-session-list__agent--disabled' : ''}`}>
                    <div className="project-session-list__agent-heading">
                      <button
                        type="button"
                        className="project-session-list__agent-toggle"
                        onClick={() => toggleAgent(group.agentId)}
                      >
                        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        <Users size={14} className="project-session-list__agent-icon" />
                        <span className="project-session-list__agent-name">{heading}</span>
                        <span className="project-session-list__agent-count">{t('sidebar.agent_sessions_count', { count: group.sessions.length })}</span>
                        {group.team && <span className="project-session-list__agent-team">{group.team}</span>}
                      </button>
                      <button
                        type="button"
                        className="project-session-list__agent-new"
                        title={t('sidebar.new_chat')}
                        aria-label={t('sidebar.new_chat')}
                        onClick={() => onNewChat(project.id, group.agentId)}
                      >
                        <MessageSquarePlus size={14} />
                      </button>
                    </div>
                    {expanded && (
                      <div className="project-session-list__agent-sessions">
                        {displaySessions.map((session) => (
                          <div key={session.id} className="project-session-list__item">
                            <button
                              className="project-session-list__item-content"
                              type="button"
                              onClick={() => onOpenSession(session.id)}
                            >
                              {runningSessionIds?.has(session.id) && <Loader2 size={15} className="project-session-list__running-icon" aria-label="Running" />}
                              <MessageSquare size={15} className="project-session-list__item-icon" />
                              <span className="project-session-list__item-title">{session.title}</span>
                              <span className="project-session-list__item-time">{formatTimeAgo(session.updated_at || session.created_at)}</span>
                            </button>
                            <DropdownMenu>
                              <DropdownMenuTrigger
                                className="project-session-list__item-more"
                                type="button"
                                aria-label={t('titlebar.session_actions')}
                              >
                                <MoreHorizontal size={15} />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" alignOffset={-8}>
                                <DropdownMenuItem onClick={() => onOpenSession(session.id)}>
                                  <MessageSquare size={14} />
                                  {t('sidebar.session_open')}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => void handleCopyId(session.id)}>
                                  {copiedId === session.id ? <Check size={14} /> : <Copy size={14} />}
                                  {copiedId === session.id ? t('sidebar.session_id_copied') : t('sidebar.session_copy_id')}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  variant="destructive"
                                  onClick={(e) => void handleDeleteSession(session.id, e)}
                                >
                                  <Trash2 size={14} />
                                  {t('common.delete')}
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        ))}
                        {hasMore && (
                          <button
                            className="project-session-list__toggle"
                            type="button"
                            onClick={() => toggleList(group.agentId)}
                          >
                            <ChevronDown size={14} className={listExpanded ? 'project-session-list__toggle-chevron' : ''} />
                            {t(listExpanded ? 'project_session.collapse' : 'project_session.view_all', { total: group.sessions.length })}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
              )}
            </>
          )}
        </div>
      </section>
    </WorkspacePage>
  );
}
