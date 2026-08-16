import { AlertCircle, Check, Pencil, Plus, RefreshCw, Trash2, UserPlus, Users, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { OrgAgent, OrgConfig, OrgSnapshot, OrgTeam } from '../../types';
import { Button } from '../ui/button';

interface OrgSettingsPanelProps {
  projectId: string;
}

interface AgentForm {
  name: string;
  role: string;
  description: string;
  parent: string;
  team_id: string;
}

interface TeamForm {
  id: string;
  name: string;
  lead: string;
  parent_team_id: string;
}

const emptyAgent: AgentForm = { name: '', role: '', description: '', parent: '', team_id: '' };
const emptyTeam: TeamForm = { id: '', name: '', lead: '', parent_team_id: '' };

export function OrgSettingsPanel({ projectId }: OrgSettingsPanelProps) {
  const [org, setOrg] = useState<OrgSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [agentForm, setAgentForm] = useState<AgentForm>(emptyAgent);
  const [teamForm, setTeamForm] = useState<TeamForm>(emptyTeam);
  const [config, setConfig] = useState<OrgConfig | null>(null);
  const [renameAgentId, setRenameAgentId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');

  const load = useCallback(async () => {
    if (!projectId) {
      setError(t('settings.org_no_project'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const snapshot = await chatService.getOrg(projectId);
      setOrg(snapshot);
      setConfig(snapshot.config);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  async function createAgent() {
    setError('');
    if (!agentForm.name.trim()) {
      setError(t('settings.org_name_required'));
      return;
    }
    try {
      const snapshot = await chatService.createOrgAgent({
        project_id: projectId,
        name: agentForm.name.trim(),
        role: agentForm.role.trim(),
        description: agentForm.description.trim(),
        parent: agentForm.parent,
        team_id: agentForm.team_id,
      });
      setOrg(snapshot);
      setAgentForm(emptyAgent);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function updateAgent(id: string, patch: Partial<Pick<OrgAgent, 'status' | 'parent' | 'team_id' | 'role'>>) {
    setError('');
    try {
      const snapshot = await chatService.updateOrgAgent({ project_id: projectId, id, ...patch });
      setOrg(snapshot);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function submitRename(id: string) {
    const name = renameDraft.trim();
    if (!name) {
      setError(t('settings.org_name_required'));
      return;
    }
    setError('');
    try {
      const snapshot = await chatService.updateOrgAgent({ project_id: projectId, id, name });
      setOrg(snapshot);
      setRenameAgentId(null);
      setRenameDraft('');
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function deleteAgent(id: string) {
    if (!window.confirm(t('settings.org_delete_agent_confirm'))) return;
    setError('');
    try {
      const snapshot = await chatService.deleteOrgAgent(projectId, id);
      setOrg(snapshot);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function createTeam() {
    setError('');
    if (!teamForm.id.trim() || !teamForm.name.trim()) {
      setError(t('settings.org_name_required'));
      return;
    }
    try {
      const snapshot = await chatService.createOrgTeam({
        project_id: projectId,
        id: teamForm.id.trim(),
        name: teamForm.name.trim(),
        lead: teamForm.lead,
        parent_team_id: teamForm.parent_team_id,
      });
      setOrg(snapshot);
      setTeamForm(emptyTeam);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function deleteTeam(id: string) {
    if (!window.confirm(t('settings.org_delete_team_confirm'))) return;
    setError('');
    try {
      const snapshot = await chatService.deleteOrgTeam(projectId, id);
      setOrg(snapshot);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function saveConfig(patch: Partial<OrgConfig>) {
    setError('');
    try {
      const snapshot = await chatService.updateOrgConfig({ project_id: projectId, ...patch });
      setOrg(snapshot);
      setConfig(snapshot.config);
    } catch (exc) {
      setError(String(exc instanceof Error ? exc.message : exc));
    }
  }

  const agents = org?.agents ?? [];
  const teams = org?.teams ?? [];

  return (
    <div className="settings-org">
      <div className="settings-org-toolbar">
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {t('settings.org_refresh')}
        </Button>
        {projectId && <span className="settings-chip">{t('settings.org_project')}: {projectId}</span>}
      </div>

      {error && (
        <div className="settings-org-error">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {config && (
        <section className="settings-org-section">
          <h4 className="settings-org-title">{t('settings.org_config')}</h4>
          <div className="settings-org-config-row">
            <label className="settings-org-label">{t('settings.org_mode')}</label>
            <select
              value={config.mode}
              onChange={(event) => saveConfig({ mode: event.target.value as OrgConfig['mode'] })}
            >
              <option value="multi">{t('settings.org_mode_multi')}</option>
              <option value="single">{t('settings.org_mode_single')}</option>
            </select>
          </div>
          <div className="settings-org-config-row">
            <label className="settings-org-label">{t('settings.org_max_depth')}</label>
            <input
              type="number"
              min={1}
              value={config.max_depth}
              onChange={(event) => saveConfig({ max_depth: Math.max(1, Number(event.target.value) || 1) })}
            />
          </div>
          <div className="settings-org-config-row">
            <label className="settings-org-label">{t('settings.org_max_concurrent')}</label>
            <input
              type="number"
              min={1}
              value={config.max_concurrent}
              onChange={(event) => saveConfig({ max_concurrent: Math.max(1, Number(event.target.value) || 1) })}
            />
          </div>
          <label className="settings-org-config-row settings-org-checkbox">
            <input
              type="checkbox"
              checked={config.allow_agent_creation}
              onChange={(event) => saveConfig({ allow_agent_creation: event.target.checked })}
            />
            {t('settings.org_allow_agent_creation')}
          </label>
        </section>
      )}

      <section className="settings-org-section">
        <h4 className="settings-org-title">
          <Users size={14} />
          {t('settings.org_agents')} ({agents.length})
        </h4>
        {agents.length === 0 && <p className="settings-org-empty">{t('settings.org_empty_agents')}</p>}
        <ul className="settings-org-list">
          {agents.map((agent) => (
            <li key={agent.id} className="settings-org-member">
              <div className="settings-org-member-main">
                {renameAgentId === agent.id ? (
                  <input
                    className="settings-org-rename-input"
                    autoFocus
                    value={renameDraft}
                    placeholder={t('settings.org_rename_placeholder')}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void submitRename(agent.id);
                      if (event.key === 'Escape') {
                        setRenameAgentId(null);
                        setRenameDraft('');
                      }
                    }}
                  />
                ) : (
                  <strong>{agent.name}</strong>
                )}
                {agent.role && <span className="settings-chip">{agent.role}</span>}
                {agent.status === 'disabled' && <span className="settings-chip">{t('settings.org_disabled')}</span>}
              </div>
              <div className="settings-org-member-meta">
                {agent.parent && (
                  <span>{t('settings.org_superior')}: <strong>{agent.parent}</strong></span>
                )}
                {agent.team_id && (
                  <span>{t('settings.org_team')}: <strong>{teams.find((team) => team.id === agent.team_id)?.name ?? agent.team_id}</strong></span>
                )}
              </div>
              <div className="settings-org-member-actions">
                {renameAgentId === agent.id ? (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => void submitRename(agent.id)} title={t('settings.org_rename')}>
                      <Check size={14} />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => { setRenameAgentId(null); setRenameDraft(''); }} title={t('settings.org_cancel')}>
                      <X size={14} />
                    </Button>
                  </>
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => { setRenameAgentId(agent.id); setRenameDraft(agent.name); }} title={t('settings.org_rename')}>
                    <Pencil size={14} />
                  </Button>
                )}
                <select
                  value={agent.status}
                  onChange={(event) => updateAgent(agent.id, { status: event.target.value as OrgAgent['status'] })}
                  title={t('settings.org_status')}
                >
                  <option value="active">{t('settings.org_active')}</option>
                  <option value="disabled">{t('settings.org_disabled')}</option>
                </select>
                <Button variant="ghost" size="sm" onClick={() => deleteAgent(agent.id)}>
                  <Trash2 size={14} />
                </Button>
              </div>
            </li>
          ))}
        </ul>

        <div className="settings-org-form">
          <h5 className="settings-org-subtitle">
            <UserPlus size={13} />
            {t('settings.org_add_agent')}
          </h5>
          <div className="settings-org-form-row">
            <input
              placeholder={t('settings.org_agent_name')}
              value={agentForm.name}
              onChange={(event) => setAgentForm({ ...agentForm, name: event.target.value })}
            />
            <input
              placeholder={t('settings.org_agent_role')}
              value={agentForm.role}
              onChange={(event) => setAgentForm({ ...agentForm, role: event.target.value })}
            />
          </div>
          <div className="settings-org-form-row">
            <select
              value={agentForm.parent}
              onChange={(event) => setAgentForm({ ...agentForm, parent: event.target.value })}
            >
              <option value="">{t('settings.org_no_superior')}</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>{agent.name}</option>
              ))}
            </select>
            <select
              value={agentForm.team_id}
              onChange={(event) => setAgentForm({ ...agentForm, team_id: event.target.value })}
            >
              <option value="">{t('settings.org_no_team')}</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </select>
          </div>
          <input
            placeholder={t('settings.org_agent_desc')}
            value={agentForm.description}
            onChange={(event) => setAgentForm({ ...agentForm, description: event.target.value })}
          />
          <Button variant="outline" size="sm" onClick={createAgent}>
            <Plus size={14} />
            {t('settings.org_add')}
          </Button>
        </div>
      </section>

      <section className="settings-org-section">
        <h4 className="settings-org-title">
          <Users size={14} />
          {t('settings.org_teams')} ({teams.length})
        </h4>
        {teams.length === 0 && <p className="settings-org-empty">{t('settings.org_empty_teams')}</p>}
        <ul className="settings-org-list">
          {teams.map((team) => (
            <li key={team.id} className="settings-org-member">
              <div className="settings-org-member-main">
                <strong>{team.name}</strong>
                <span className="settings-chip">{team.id}</span>
                {team.lead && (
                  <span className="settings-chip">{t('settings.org_lead')}: {team.lead}</span>
                )}
              </div>
              <Button variant="ghost" size="sm" onClick={() => deleteTeam(team.id)}>
                <Trash2 size={14} />
              </Button>
            </li>
          ))}
        </ul>

        <div className="settings-org-form">
          <h5 className="settings-org-subtitle">
            <Plus size={13} />
            {t('settings.org_add_team')}
          </h5>
          <div className="settings-org-form-row">
            <input
              placeholder={t('settings.org_team_id')}
              value={teamForm.id}
              onChange={(event) => setTeamForm({ ...teamForm, id: event.target.value })}
            />
            <input
              placeholder={t('settings.org_team_name')}
              value={teamForm.name}
              onChange={(event) => setTeamForm({ ...teamForm, name: event.target.value })}
            />
          </div>
          <div className="settings-org-form-row">
            <select
              value={teamForm.lead}
              onChange={(event) => setTeamForm({ ...teamForm, lead: event.target.value })}
            >
              <option value="">{t('settings.org_no_lead')}</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>{agent.name}</option>
              ))}
            </select>
            <select
              value={teamForm.parent_team_id}
              onChange={(event) => setTeamForm({ ...teamForm, parent_team_id: event.target.value })}
            >
              <option value="">{t('settings.org_no_parent_team')}</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </select>
          </div>
          <Button variant="outline" size="sm" onClick={createTeam}>
            <Plus size={14} />
            {t('settings.org_add')}
          </Button>
        </div>
      </section>
    </div>
  );
}
