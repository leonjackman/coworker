import { ChevronDown, ChevronUp, MessageSquare, MessageSquarePlus, MoreHorizontal, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Button } from './ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './ui/dropdown-menu';
import { WorkspacePage } from './ui/workspace-page';
import { t } from '../lib/i18n';
import type { ProjectEntry, SessionSummary } from '../types';

interface ProjectSessionListProps {
  project: ProjectEntry;
  sessions: SessionSummary[];
  onNewChat: (projectId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
}

function formatTimeAgo(updatedAt: string): string {
  const now = Date.now();
  const then = new Date(updatedAt).getTime();
  const diffMs = Math.abs(now - then);
  const diffMinutes = Math.floor(diffMs / 60000);
  if (diffMinutes < 1) return t('project_session.just_now');
  if (diffMinutes < 60) return `${diffMinutes}m`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d`;
  const diffWeeks = Math.floor(diffDays / 7);
  return `${diffWeeks}w`;
}

export function ProjectSessionList({ project, sessions, onNewChat, onOpenSession, onDeleteSession }: ProjectSessionListProps) {
  const [expanded, setExpanded] = useState(false);
  const LIMIT = 10;
  const displaySessions = expanded ? sessions : sessions.slice(0, LIMIT);
  const hasMore = sessions.length > LIMIT;
  const isShowingMore = expanded;

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

        <Button type="button" className="project-session-list__new" onClick={() => onNewChat(project.id)}>
          <MessageSquarePlus size={16} />
          {t('project_session.new_chat_for_project', { name: project.name })}
        </Button>
      </section>
    </WorkspacePage>
  );
}
