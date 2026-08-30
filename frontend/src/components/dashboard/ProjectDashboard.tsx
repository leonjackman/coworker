import { ArrowLeft, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { ProjectDashboardData, SessionSummary } from '../../types';
import { Button } from '../ui/button';
import { CategoryTabs } from '../ui/category-tabs';
import { WorkspacePage } from '../ui/workspace-page';
import { ProjectSessionList } from '../ProjectSessionList';
import { MemoryPanel } from '../MemoryPanel';
import { DashboardAgents } from './DashboardAgents';
import { DashboardFiles } from './DashboardFiles';
import { DashboardOverview } from './DashboardOverview';
import { DashboardTools } from './DashboardTools';

type DashboardTab = 'overview' | 'files' | 'agents' | 'tools' | 'memory' | 'sessions';

interface ProjectDashboardProps {
  projectId: string;
  projectName?: string;
  sessions: SessionSummary[];
  runningSessionIds?: Set<string>;
  onBack: () => void;
  onViewChange: (view: 'mcp' | 'skills') => void;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
}

export function ProjectDashboard({
  projectId,
  projectName,
  sessions,
  runningSessionIds,
  onBack,
  onViewChange,
  onNewChat,
  onOpenSession,
  onDeleteSession,
  onOpenOrgSettings,
}: ProjectDashboardProps) {
  const [data, setData] = useState<ProjectDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<DashboardTab>('overview');
  const memoryActionsHost = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const dashboard = await chatService.getProjectDashboard(projectId);
      setData(dashboard);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const title = useMemo(
    () => (projectName ? `${projectName} · ${t('dashboard.title')}` : t('dashboard.title')),
    [projectName],
  );

  const tabs = useMemo(
    () => [
      { id: 'overview', label: t('dashboard.overview') },
      { id: 'files', label: t('dashboard.files') },
      { id: 'agents', label: t('dashboard.agents') },
      { id: 'tools', label: t('dashboard.tools') },
      { id: 'memory', label: t('dashboard.memory') },
      { id: 'sessions', label: t('dashboard.sessions'), count: sessions.length },
    ],
    [sessions.length],
  );

  const sessionsByProject = useMemo(
    () => sessions.filter((session) => session.project_id === projectId),
    [sessions, projectId],
  );

  return (
    <WorkspacePage
      eyebrow={t('dashboard.title')}
      title={title}
      description={data?.project?.workspace_path ?? ''}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <div className="dashboard-tabs">
        <CategoryTabs categories={tabs} value={tab} onChange={(id) => setTab(id as DashboardTab)} />
        <div className="dashboard-tabs__actions" ref={memoryActionsHost} />
      </div>

      {loading ? (
        <div className="dashboard-state">
          <Loader2 size={18} className="animate-spin" />
          <span>{t('dashboard.loading')}</span>
        </div>
      ) : error ? (
        <div className="dashboard-state dashboard-state--error">
          <span>{t('dashboard.error')}: {error}</span>
          <Button variant="ghost" size="sm" onClick={() => void load()}>{t('dashboard.retry')}</Button>
        </div>
      ) : !data ? null : (
        <div className="dashboard-body">
          {tab === 'overview' && <DashboardOverview data={data} />}
          {tab === 'files' && (
            <DashboardFiles
              projectId={projectId}
              workspaceAvailable={data.project.workspace_available}
              workspacePath={data.project.workspace_path}
            />
          )}
          {tab === 'agents' && (
            <DashboardAgents
              data={data}
              onNewChat={onNewChat}
              {...(onOpenOrgSettings ? { onOpenOrgSettings } : {})}
            />
          )}
          {tab === 'tools' && <DashboardTools data={data} onViewChange={onViewChange} />}
          {tab === 'memory' && (
            <div className="dashboard-memory">
              <MemoryPanel projectId={projectId} scope="project" embedded actionsHost={memoryActionsHost} />
            </div>
          )}
          {tab === 'sessions' && (
            <div className="dashboard-sessions">
              {data && (
                <DashboardSessions
                  data={data}
                  sessions={sessionsByProject}
                  {...(runningSessionIds ? { runningSessionIds } : {})}
                  onNewChat={onNewChat}
                  onOpenSession={onOpenSession}
                  onDeleteSession={onDeleteSession}
                  {...(onOpenOrgSettings ? { onOpenOrgSettings } : {})}
                  hideHeading
                />
              )}
            </div>
          )}
        </div>
      )}
    </WorkspacePage>
  );
}

function DashboardSessions({
  data,
  sessions,
  runningSessionIds,
  onNewChat,
  onOpenSession,
  onDeleteSession,
  onOpenOrgSettings,
  hideHeading = false,
}: {
  data: ProjectDashboardData;
  sessions: SessionSummary[];
  runningSessionIds?: Set<string>;
  onNewChat: (projectId?: string, agentId?: string) => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenOrgSettings?: (projectId: string) => void;
  hideHeading?: boolean;
}) {
  return (
    <ProjectSessionList
      project={data.project}
      sessions={sessions}
      {...(runningSessionIds ? { runningSessionIds } : {})}
      onNewChat={onNewChat}
      onOpenSession={onOpenSession}
      onDeleteSession={onDeleteSession}
      {...(onOpenOrgSettings ? { onOpenOrgSettings } : {})}
      hideHeading={hideHeading}
    />
  );
}
