import { Folder, GitBranch, MessageSquare, Users } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { ProjectDashboardData } from '../../types';
import { formatTimeAgo } from '../../lib/utils';

interface DashboardOverviewProps {
  data: ProjectDashboardData;
}

export function DashboardOverview({ data }: DashboardOverviewProps) {
  const { project, git, agents, capabilities } = data;

  const changedFiles = (git.files?.length ?? 0) + (git.untracked?.length ?? 0);

  const modeLabel = capabilities.mode === 'multi' ? t('dashboard.mode_multi') : t('dashboard.mode_single');

  const statCards = [
    { key: 'agents', label: t('dashboard.stat_agents'), value: agents.length, icon: <Users size={15} /> },
    { key: 'sessions', label: t('dashboard.stat_sessions'), value: project.session_count, icon: <MessageSquare size={15} /> },
    { key: 'files', label: t('dashboard.stat_files'), value: changedFiles, icon: <Folder size={15} /> },
  ];

  return (
    <div className="dashboard-overview">
      <section className="dashboard-card">
        <div className="dashboard-card__row">
          <span className="dashboard-card__label">{t('dashboard.project_name')}</span>
          <span className="dashboard-card__value">{project.name}</span>
        </div>
        <div className="dashboard-card__row">
          <span className="dashboard-card__label">{t('dashboard.workspace')}</span>
          <span className="dashboard-card__value dashboard-card__value--mono">{project.workspace_path}</span>
        </div>
        <div className="dashboard-card__row">
          <span className="dashboard-card__label">{t('dashboard.project_mode')}</span>
          <span className="dashboard-card__chips">
            <span className="settings-chip settings-chip--mode">{modeLabel}</span>
            {project.is_chat && <span className="settings-chip">{t('dashboard.chat_project')}</span>}
          </span>
        </div>
        <div className="dashboard-card__row">
          <span className="dashboard-card__label">{t('dashboard.updated')}</span>
          <span className="dashboard-card__value">{formatTimeAgo(project.updated_at || project.created_at)}</span>
        </div>
      </section>

      <section className="dashboard-card dashboard-card--git">
        <div className="dashboard-card__git-row">
          <GitBranch size={15} />
          {git.branch ? (
            <span className="dashboard-card__value dashboard-card__value--mono">{git.branch}</span>
          ) : (
            <span className="dashboard-card__value">{git.is_repo === false ? t('dashboard.git_not_repo') : '—'}</span>
          )}
        </div>
        <div className="dashboard-card__git-detail">
          {git.git ? (
            <>
              <span>{t('dashboard.git_changed', { count: changedFiles })}</span>
              {(git.untracked?.length ?? 0) > 0 && <span>{t('dashboard.git_untracked', { count: git.untracked?.length ?? 0 })}</span>}
            </>
          ) : (
            <span>{t('dashboard.git_no_diff')}</span>
          )}
        </div>
      </section>

      <section className="dashboard-stats">
        {statCards.map((card) => (
          <div key={card.key} className="dashboard-stat">
            <div className="dashboard-stat__icon">{card.icon}</div>
            <div className="dashboard-stat__number">{card.value}</div>
            <div className="dashboard-stat__label">{card.label}</div>
          </div>
        ))}
      </section>
    </div>
  );
}
