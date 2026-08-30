import { MessageSquarePlus, User, Users } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { ProjectDashboardData } from '../../types';

interface DashboardAgentsProps {
  data: ProjectDashboardData;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
}

export function DashboardAgents({ data, onNewChat, onOpenOrgSettings }: DashboardAgentsProps) {
  const { project, agents } = data;
  const isMulti = project.mode === 'multi';

  return (
    <div className="dashboard-agents">
      <section className="settings-org-section">
        <h4 className="settings-org-title">
          <Users size={14} />
          {t('dashboard.agents_title')}
          <span className="dashboard-agents__count">{agents.length}</span>
        </h4>
        <ul className="settings-org-list">
          {agents.map((agent) => (
            <li key={agent.id} className="settings-org-member dashboard-agent">
              <div className="settings-org-member-main">
                <User size={14} className="dashboard-agent__icon" />
                <span className="dashboard-agent__name">{agent.name}</span>
                {agent.is_default && <span className="settings-chip">{t('dashboard.default_agent')}</span>}
                {agent.role && <span className="settings-chip">{agent.role}</span>}
                {agent.team && <span className="settings-chip">{agent.team}</span>}
                {agent.status === 'disabled' && <span className="settings-chip settings-chip--dim">{t('dashboard.disabled')}</span>}
              </div>
              <div className="settings-org-member-meta">
                <span>{t('dashboard.session_count', { count: agent.session_count })}</span>
              </div>
              <div className="settings-org-member-actions">
                <button
                  type="button"
                  className="dashboard-agent__new-chat"
                  title={t('dashboard.new_chat')}
                  aria-label={t('dashboard.new_chat')}
                  disabled={agent.status === 'disabled'}
                  onClick={() => onNewChat(project.id, agent.is_default ? undefined : agent.id)}
                >
                  <MessageSquarePlus size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {isMulti && onOpenOrgSettings && (
        <button type="button" className="dashboard-deep-link" onClick={() => onOpenOrgSettings(project.id)}>
          <Users size={14} />
          {t('dashboard.org_settings')}
        </button>
      )}
    </div>
  );
}
