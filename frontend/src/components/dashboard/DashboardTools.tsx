import { ExternalLink, Server, Sparkles } from 'lucide-react';
import { useMemo } from 'react';
import { t } from '../../lib/i18n';
import type { DashboardBuiltinTool, ProjectDashboardData } from '../../types';

interface DashboardToolsProps {
  data: ProjectDashboardData;
  onViewChange: (view: 'mcp' | 'skills') => void;
}

const GROUP_ORDER = ['workspace', 'memory', 'team', 'worker', 'web', 'browser', 'goal'];

export function DashboardTools({ data, onViewChange }: DashboardToolsProps) {
  const { tools, capabilities } = data;

  const groups = useMemo(() => {
    const byGroup = new Map<string, DashboardBuiltinTool[]>();
    for (const tool of tools.builtin) {
      const list = byGroup.get(tool.group) ?? [];
      list.push(tool);
      byGroup.set(tool.group, list);
    }
    const ordered = GROUP_ORDER.filter((group) => byGroup.has(group));
    const extra = [...byGroup.keys()].filter((group) => !GROUP_ORDER.includes(group));
    return [...ordered, ...extra].map((group) => ({ group, tools: byGroup.get(group) ?? [] }));
  }, [tools.builtin]);

  const enabledSkills = useMemo(() => tools.skills.filter((skill) => skill.enabled).length, [tools.skills]);
  const pendingSkills = useMemo(() => tools.skills.filter((skill) => skill.status === 'draft').length, [tools.skills]);

  return (
    <div className="dashboard-tools">
      <div className="dashboard-tools__grid">
        {groups.map(({ group, tools: groupTools }) => {
          const modeLimited = groupTools.some((tool) => tool.mode);
          return (
            <section key={group} className="settings-org-section dashboard-tool-group">
              <h4 className="settings-org-title">
                {t(`dashboard.group_${group}`)}
                {modeLimited && <span className="settings-chip settings-chip--mode">{t('dashboard.project_mode')}</span>}
                <span className="dashboard-agents__count">{groupTools.length}</span>
              </h4>
              <ul className="settings-org-list">
                {groupTools.map((tool) => (
                  <li key={tool.name} className="dashboard-tool">
                    <div className="dashboard-tool__main">
                      <span className="dashboard-tool__name">{tool.name}</span>
                      <span className={`dashboard-tool__access dashboard-tool__access--${tool.access}`}>
                        {t(`dashboard.access_${tool.access}`)}
                      </span>
                    </div>
                    <span className="dashboard-tool__desc">{tool.description}</span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <section className="settings-org-section dashboard-tool-group">
        <h4 className="settings-org-title">
          <Server size={14} />
          {t('dashboard.mcp_servers')}
          <span className="dashboard-agents__count">{tools.mcp_servers.length}</span>
          <button type="button" className="dashboard-deep-link dashboard-deep-link--inline" onClick={() => onViewChange('mcp')}>
            {t('dashboard.open_mcp')}
            <ExternalLink size={12} />
          </button>
        </h4>
        {tools.mcp_servers.length === 0 ? (
          <p className="settings-org-empty">{t('dashboard.mcp_empty')}</p>
        ) : (
          <ul className="settings-org-list">
            {tools.mcp_servers.map((server) => (
              <li key={server.id} className="dashboard-tool dashboard-tool--server">
                <div className="dashboard-tool__main">
                  <span className="dashboard-tool__name">{server.name}</span>
                  <span className={`settings-chip ${server.enabled ? 'settings-chip--ok' : 'settings-chip--dim'}`}>
                    {server.enabled ? t('dashboard.mcp_enabled') : t('dashboard.mcp_disabled')}
                  </span>
                  {server.status === 'connected' && <span className="settings-chip settings-chip--ok">{server.status}</span>}
                </div>
                <span className="dashboard-tool__desc">
                  {t('dashboard.mcp_tool_count', { count: server.tool_count || 0 })} · {server.transport}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="settings-org-section dashboard-tool-group">
        <h4 className="settings-org-title">
          <Sparkles size={14} />
          {t('dashboard.skills')}
          <span className="dashboard-agents__count">{enabledSkills}/{tools.skills.length}</span>
          {pendingSkills > 0 && (
            <span className="settings-chip settings-chip--mode" title={t('dashboard.skills_pending_tip')}>
              {t('dashboard.skills_pending', { count: pendingSkills })}
            </span>
          )}
          <button type="button" className="dashboard-deep-link dashboard-deep-link--inline" onClick={() => onViewChange('skills')}>
            {t('dashboard.open_skills')}
            <ExternalLink size={12} />
          </button>
        </h4>
        {tools.skills.length === 0 ? (
          <p className="settings-org-empty">{t('dashboard.skills_empty')}</p>
        ) : (
          <ul className="settings-org-list">
            {tools.skills.map((skill) => (
              <li key={skill.name} className="dashboard-tool dashboard-tool--skill">
                <span className="dashboard-tool__name">{skill.name}</span>
                <span className="dashboard-tool__desc">{skill.description}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="settings-org-section dashboard-tool-group">
        <h4 className="settings-org-title">{t('dashboard.capabilities')}</h4>
        <div className="dashboard-card__chips dashboard-card__chips--wrap">
          <span className={`settings-chip ${capabilities.memory_enabled ? 'settings-chip--ok' : 'settings-chip--dim'}`}>
            {t('dashboard.memory')}: {capabilities.memory_enabled ? t('common.on') : t('common.off')}
          </span>
          <span className={`settings-chip ${capabilities.web_enabled ? 'settings-chip--ok' : 'settings-chip--dim'}`}>
            {t('dashboard.web')}: {capabilities.web_enabled ? t('common.on') : t('common.off')}
          </span>
          <span className={`settings-chip ${capabilities.browser_enabled ? 'settings-chip--ok' : 'settings-chip--dim'}`}>
            {t('dashboard.browser')}: {capabilities.browser_enabled ? t('common.on') : t('common.off')}
          </span>
        </div>
      </section>
    </div>
  );
}
