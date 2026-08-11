import { Check, ChevronDown, ChevronUp, Copy, Loader2, MessageSquare, MoreHorizontal, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './ui/dropdown-menu';
import { WorkspacePage } from './ui/workspace-page';
import { t } from '../lib/i18n';
import { formatTimeAgo } from '../lib/utils';
import type { ProjectEntry, SessionSummary } from '../types';

interface ProjectSessionListProps {
  project: ProjectEntry;
  sessions: SessionSummary[];
  runningSessionIds?: Set<string>;
  onNewChat: (projectId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
}

export function ProjectSessionList({ project, sessions, runningSessionIds, onNewChat, onOpenSession, onDeleteSession }: ProjectSessionListProps) {
  const [expanded, setExpanded] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const LIMIT = 10;
  const displaySessions = expanded ? sessions : sessions.slice(0, LIMIT);
  const hasMore = sessions.length > LIMIT;
  const isShowingMore = expanded;

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

  return (
    <WorkspacePage
      className="workspace-page--sessions"
      contentClassName="workspace-page__content--sessions"
      eyebrow={project.workspace_path}
      title={project.name}
      description={sessions.length === 0 ? t('project_session.empty_state') : t('project_session.project_sessions_count', { count: sessions.length })}
    >
      <section className="project-session-list">
        <div className="project-session-list__items">
          {sessions.length === 0 ? (
            <p className="project-session-list__empty">{t('project_session.empty_state_text')}</p>
          ) : (
            <>
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
                  onClick={() => setExpanded(!expanded)}
                >
                  <ChevronUp size={14} className={isShowingMore ? 'project-session-list__toggle-chevron' : ''} />
                  {t(isShowingMore ? 'project_session.collapse' : 'project_session.view_all', { total: sessions.length })}
                </button>
              )}
            </>
          )}
        </div>
      </section>
    </WorkspacePage>
  );
}
