import { ArrowLeft, Check, CopyPlus, Download, Eye, FileText, Loader2, Play, Plus, RefreshCw, Trash2, Bot, Bell, CheckCircle2, XCircle, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { GridCard } from './ui/grid-card';
import { usePageNavPublish } from '../nav/PageNav';
import { WorkflowGraphEditor, type GraphEditorTarget } from './WorkflowGraphEditor';
import { WorkflowStepList } from './workflows/WorkflowStepList';
import { WorkflowFlowGraph } from './workflows/WorkflowFlowGraph';
import type {
  WorkflowEntry,
  WorkflowDraft,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowEvidence,
  NotificationRecord,
  WorkflowTemplate,
} from '../types';

const STEP_EMOJI: Record<string, string> = {
  command: '⌘',
  tool: '🧰',
  browser: '🌐',
  app: '🖥️',
  computer: '🖥️',
  skill: '🧠',
  human: '🙋',
  agentic: '🤖',
  set: '📌',
  assert: '✅',
  wait: '⏳',
  branch: '🔀',
  loop: '🔁',
  parallel: '⏸',
  subworkflow: '🧩',
};

function relTime(value: string): string {
  if (!value) return '—';
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value;
  const diff = Date.now() - ts;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

function statusClass(status: string): string {
  if (status === 'ok' || status === 'active') return 'add-skill-page__msg--ok';
  if (status === 'failed') return 'add-skill-page__msg--error';
  return '';
}

export function WorkflowsPanel({ sessionId }: { sessionId?: string | undefined }) {
  const [workflows, setWorkflows] = useState<WorkflowEntry[]>([]);
  const [pending, setPending] = useState<WorkflowDraft[]>([]);
  const [notifications, setNotifications] = useState<NotificationRecord[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [graphTarget, setGraphTarget] = useState<GraphEditorTarget | null>(null);
  const [subPage, setSubPage] = useState<'list' | 'detail' | 'editor' | 'graph' | 'templates' | 'run' | 'pending'>('list');
  const [feedbackText, setFeedbackText] = useState('');
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<'ok' | 'error'>('ok');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');

  const [detail, setDetail] = useState<WorkflowEntry | null>(null);
  const [flowView, setFlowView] = useState<'list' | 'graph'>('list');
  const [detailLoading, setDetailLoading] = useState(false);

  const [editor, setEditor] = useState<{ name: string; isNew: boolean; content: string } | null>(null);
  const [editorBusy, setEditorBusy] = useState(false);

  const [pendingReview, setPendingReview] = useState<{ name: string; content: string } | null>(null);
  const [pendingBusy, setPendingBusy] = useState<string | null>(null);

  const [runTarget, setRunTarget] = useState<WorkflowEntry | null>(null);
  const [runInputs, setRunInputs] = useState('{}');
  const [runBusy, setRunBusy] = useState(false);
  const [runResult, setRunResult] = useState<WorkflowRun | null>(null);
  const [runEvents, setRunEvents] = useState<WorkflowRunEvent[]>([]);
  const [runEvidence, setRunEvidence] = useState<WorkflowEvidence[]>([]);

  const loadRunDetail = useCallback(async (runId: string) => {
    const [events, evidence] = await Promise.all([
      chatService.getRunEvents(runId).catch(() => ({ status: 'ok', events: [] })),
      chatService.getRunEvidence(runId).catch(() => ({ status: 'ok', evidence: [] })),
    ]);
    setRunEvents(events.events);
    setRunEvidence(evidence.evidence);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [list, drafts, notes, tmpl] = await Promise.all([
        chatService.listWorkflows(),
        chatService.listPendingWorkflows().catch(() => ({ status: 'ok', pending: [] })),
        chatService.listNotifications().catch(() => ({ status: 'ok', notifications: [], unread: 0 })),
        chatService.listWorkflowTemplates().catch(() => ({ status: 'ok', templates: [] })),
      ]);
      setWorkflows(list.workflows);
      setPending(drafts.pending);
      setNotifications(notes.notifications);
      setTemplates(tmpl.templates);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error) || t('workflows.failed_to_load'));
    } finally {
      setLoading(false);
    }
  }, []);

  const reopenDetail = useCallback(async (target: string) => {
    setSubPage('detail');
    setDetailLoading(true);
    try {
      const full = await chatService.getWorkflow(target);
      setDetail(full.workflow);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const categories = useMemo<CategoryTabItem[]>(() => {
    const items: CategoryTabItem[] = [{ id: 'all', label: t('workflows.cat_all'), count: workflows.length }];
    const sources = new Map<string, number>();
    for (const wf of workflows) sources.set(wf.source, (sources.get(wf.source) ?? 0) + 1);
    for (const [source, count] of sources) items.push({ id: source, label: source, count });
    const manual = workflows.filter((w) => w.triggers.includes('manual') || w.triggers.length === 0).length;
    const scheduled = workflows.filter((w) => w.triggers.some((x) => x.startsWith('cron'))).length;
    if (scheduled > 0) items.push({ id: 'scheduled', label: t('workflows.scheduled'), count: scheduled });
    if (manual > 0) items.push({ id: 'manual', label: t('workflows.manual'), count: manual });
    return items;
  }, [workflows]);

  const visible = useMemo(() => {
    let list = workflows;
    if (filter === 'scheduled') list = list.filter((w) => w.triggers.some((x) => x.startsWith('cron')));
    else if (filter === 'manual') list = list.filter((w) => w.triggers.includes('manual') || w.triggers.length === 0);
    else if (filter !== 'all') list = list.filter((w) => w.source === filter);
    const needle = search.trim().toLowerCase();
    if (needle) {
      list = list.filter(
        (w) => w.name.toLowerCase().includes(needle) || w.description.toLowerCase().includes(needle),
      );
    }
    return list;
  }, [workflows, filter, search]);

  const openDetail = useCallback(async (wf: WorkflowEntry) => {
    setSubPage('detail');
    setDetail(wf);
    setDetailLoading(true);
    setFlowView('list');
    try {
      const full = await chatService.getWorkflow(wf.name);
      setDetail(full.workflow);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const saveEditor = useCallback(async () => {
    if (!editor) return;
    setEditorBusy(true);
    try {
      const validation = await chatService.validateWorkflow(editor.content);
      if (!validation.valid) {
        throw new Error(validation.errors.join('; '));
      }
      const result = editor.isNew
        ? await chatService.createWorkflow(editor.content, true)
        : await chatService.updateWorkflow(editor.name, editor.content);
      if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
      setMessageType('ok');
      setMessage(t('workflows.saved'));
      const savedName = editor.name;
      const wasNew = editor.isNew;
      setEditor(null);
      await refresh();
      if (wasNew) setSubPage('list');
      else void reopenDetail(savedName);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setEditorBusy(false);
    }
  }, [editor, refresh, reopenDetail]);

  const removeWorkflow = useCallback(
    async (wf: WorkflowEntry) => {
      if (!window.confirm(t('workflows.delete_confirm', { name: wf.name }))) return;
      try {
        await chatService.deleteWorkflow(wf.name);
        setDetail(null);
        setSubPage('list');
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const duplicateWorkflow = useCallback(
    async (wf: WorkflowEntry) => {
      try {
        const result = await chatService.duplicateWorkflow(wf.name);
        if (result.status !== 'ok') throw new Error(t('workflows.save_failed'));
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const exportWorkflow = useCallback(async (wf: WorkflowEntry) => {
    try {
      const result = await chatService.exportWorkflow(wf.name);
      const blob = new Blob([result.yaml], { type: 'text/yaml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${wf.name}.yaml`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    }
  }, []);

  const openRun = useCallback((wf: WorkflowEntry) => {
    setSubPage('run');
    setRunTarget(wf);
    setRunResult(null);
    setRunEvents([]);
    setRunEvidence([]);
    const preset: Record<string, unknown> = {};
    for (const input of wf.inputs) if (input.default !== undefined) preset[input.name] = input.default;
    setRunInputs(JSON.stringify(preset, null, 2));
  }, []);

  const executeRun = useCallback(async () => {
    if (!runTarget) return;
    setRunBusy(true);
    setRunResult(null);
    try {
      let inputs: Record<string, unknown> = {};
      try {
        inputs = JSON.parse(runInputs || '{}');
      } catch {
        throw new Error(t('workflows.invalid_json'));
      }
      const result = await chatService.runWorkflow(runTarget.name, inputs);
      setRunResult(result.run);
      void loadRunDetail(result.run.run_id);
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setRunBusy(false);
    }
  }, [runTarget, runInputs, refresh, loadRunDetail]);

  const recordFromSession = useCallback(async () => {
    if (!sessionId) return;
    setMessageType('ok');
    setMessage(t('workflows.recording'));
    try {
      const result = await chatService.recordFromSession(sessionId);
      const action = (result.review?.action as string) || 'none';
      setMessageType('ok');
      setMessage(action === 'create' ? t('workflows.record_staged') : t('workflows.record_none'));
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    }
  }, [sessionId, refresh]);

  const clearNotifications = useCallback(async () => {
    try {
      await chatService.clearNotifications();
      await refresh();
    } catch {
      /* ignore */
    }
  }, [refresh]);

  const installTemplate = useCallback(
    async (templateId: string) => {
      try {
        const result = await chatService.installWorkflowTemplate(templateId);
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
        setSubPage('list');
        setMessageType('ok');
        setMessage(t('workflows.template_installed'));
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const submitFeedback = useCallback(async () => {
    if (!detail || !feedbackText.trim()) return;
    setFeedbackBusy(true);
    try {
      const result = await chatService.submitWorkflowFeedback(detail.name, feedbackText.trim());
      if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
      setFeedbackText('');
      setMessageType('ok');
      setMessage(t('workflows.feedback_staged'));
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setFeedbackBusy(false);
    }
  }, [detail, feedbackText, refresh]);

  const resolveHuman = useCallback(
    async (runId: string, stepId: string, approved: boolean) => {
      try {
        const result = await chatService.resumeWorkflowRun(runId, { [stepId]: approved });
        setRunResult(result.run);
        void loadRunDetail(result.run.run_id);
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh, loadRunDetail],
  );

  const openGraph = useCallback((wf: WorkflowEntry) => {
    setGraphTarget({
      name: wf.name,
      description: wf.description,
      version: wf.version,
      inputs: wf.inputs,
      steps: wf.steps ?? [],
      isNew: false,
    });
    setSubPage('graph');
  }, []);

  const openPending = useCallback(async (name: string) => {
    setPendingBusy(name);
    try {
      const response = await chatService.getPendingWorkflow(name);
      setPendingReview({ name, content: response.content });
      setSubPage('pending');
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setPendingBusy(null);
    }
  }, []);

  const approvePending = useCallback(
    async (name: string) => {
      setPendingBusy(name);
      try {
        await chatService.approvePendingWorkflow(name);
        setPendingReview(null);
        setSubPage('list');
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      } finally {
        setPendingBusy(null);
      }
    },
    [refresh],
  );

  const rejectPending = useCallback(
    async (name: string) => {
      setPendingBusy(name);
      try {
        await chatService.rejectPendingWorkflow(name);
        setPendingReview(null);
        setSubPage('list');
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      } finally {
        setPendingBusy(null);
      }
    },
    [refresh],
  );

  const publishNav = usePageNavPublish();

  // The two edit pages are a level below the workflow detail (3-level crumbs).
  const editUnderDetail = (subPage === 'editor' && !editor?.isNew) || (subPage === 'graph' && !graphTarget?.isNew);
  const editParentName = (subPage === 'editor' ? editor?.name : graphTarget?.name) ?? '';

  useEffect(() => {
    if (subPage === 'list') {
      publishNav({ viewLabel: t('workflows.title') });
    } else if (editUnderDetail) {
      publishNav({
        viewLabel: t('workflows.title'),
        onBackToRoot: () => setSubPage('list'),
        midLabel: detail?.name || editParentName || t('workflows.title'),
        onBackToMid: () => void reopenDetail(editParentName),
        leafLabel: subPage === 'editor' ? t('workflows.edit') : t('workflows.visual_edit'),
        onBack: () => void reopenDetail(editParentName),
      });
    } else {
      let leaf: string = t('workflows.title');
      if (subPage === 'detail') leaf = detail?.name || t('workflows.title');
      else if (subPage === 'editor') leaf = t('workflows.new');
      else if (subPage === 'graph') leaf = graphTarget?.isNew ? t('workflows.new') : t('workflows.visual_edit');
      else if (subPage === 'templates') leaf = t('workflows.templates');
      else if (subPage === 'run') leaf = `${t('workflows.run')}: ${runTarget?.name ?? ''}`;
      else if (subPage === 'pending') leaf = t('workflows.pending_review');
      publishNav({
        viewLabel: t('workflows.title'),
        leafLabel: leaf,
        onBackToRoot: () => setSubPage('list'),
        onBack: () => setSubPage('list'),
      });
    }
    return () => publishNav(null);
  }, [publishNav, subPage, detail, editor, graphTarget, runTarget, editUnderDetail, editParentName, reopenDetail]);

  const backButton = (
    <Button variant="ghost" onClick={() => setSubPage('list')}>
      <ArrowLeft size={15} />
      {t('settings.back')}
    </Button>
  );

  // Back from the edit pages: to the workflow detail (or list for a new one).
  const goBackFromEdit = useCallback(() => {
    if (subPage === 'graph' && graphTarget?.isNew) setSubPage('list');
    else if (editParentName) void reopenDetail(editParentName);
    else setSubPage('list');
  }, [subPage, graphTarget, editParentName, reopenDetail]);

  const editBackButton = (
    <Button variant="ghost" onClick={goBackFromEdit}>
      <ArrowLeft size={15} />
      {t('settings.back')}
    </Button>
  );

  // ── second-level pages (breadcrumb) ─────────────────────────────────

  if (subPage === 'graph') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={graphTarget?.isNew ? t('workflows.new') : t('workflows.visual_edit')}
        action={editBackButton}
      >
        <div className="workspace-page__content">
          <WorkflowGraphEditor target={graphTarget} onClose={goBackFromEdit} onSaved={() => void refresh()} />
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'editor') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={editor?.isNew ? t('workflows.new') : t('workflows.edit')}
        action={editBackButton}
      >
        <div className="workspace-page__content">
          <div className="skills-pending__actions" style={{ marginBottom: 10 }}>
            <Button variant="primary" onClick={() => void saveEditor()} disabled={editorBusy}>
              {editorBusy ? <Loader2 size={14} className="animate-spin" /> : null}
              {t('workflows.save')}
            </Button>
          </div>
          <textarea
            className="skills-pending__editor"
            style={{ minHeight: 460 }}
            value={editor?.content ?? ''}
            onChange={(e) => editor && setEditor({ ...editor, content: e.target.value })}
            spellCheck={false}
            placeholder={t('workflows.yaml_placeholder')}
          />
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'pending') {
    return (
      <WorkspacePage eyebrow={t('workflows.title')} title={t('workflows.pending_review')} action={backButton}>
        <div className="workspace-page__content">
          <div className="skills-pending__actions" style={{ marginBottom: 10 }}>
            <Button variant="primary" onClick={() => pendingReview && void approvePending(pendingReview.name)}>
              <Check size={14} />
              {t('workflows.approve')}
            </Button>
            <Button variant="ghost" onClick={() => pendingReview && void rejectPending(pendingReview.name)}>
              <Trash2 size={14} />
              {t('workflows.reject')}
            </Button>
          </div>
          <textarea
            className="skills-pending__editor"
            style={{ minHeight: 460 }}
            value={pendingReview?.content ?? ''}
            onChange={(e) => pendingReview && setPendingReview({ ...pendingReview, content: e.target.value })}
            spellCheck={false}
          />
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'templates') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={t('workflows.templates')}
        description={t('workflows.templates_subtitle')}
        action={backButton}
      >
        <div className="workspace-page__content">
          {templates.length === 0 ? (
            <p className="skill-empty">{t('workflows.templates_empty')}</p>
          ) : (
            <div className="skills-pending__list">
              {templates.map((template) => (
                <div key={template.id} className="skills-pending__card">
                  <div className="skills-pending__head">
                    <span className="skills-pending__name">{template.name}</span>
                    <span className="settings-chip">{template.category}</span>
                    {template.platform ? <span className="settings-chip">{template.platform}</span> : null}
                  </div>
                  <p className="skills-pending__desc">{template.description}</p>
                  <div className="skills-pending__actions">
                    <Button variant="primary" size="sm" onClick={() => void installTemplate(template.id)}>
                      <Plus size={14} />
                      {t('workflows.install')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'run') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={`${t('workflows.run')}: ${runTarget?.name ?? ''}`}
        description={runResult ? <span className="wf-meta__status"><span className={`settings-chip settings-chip--${runResult.status === 'ok' ? 'ok' : runResult.status === 'failed' ? 'bad' : 'dim'}`}>{runResult.status}</span></span> : undefined}
        action={backButton}
      >
        <div className="workspace-page__content">
          <div className="settings-card" style={{ padding: 14 }}>
            <div className="settings-row__copy" style={{ marginBottom: 8 }}>
              <label>{t('workflows.run_inputs')}</label>
            </div>
            <textarea
              className="skills-pending__editor"
              style={{ minHeight: 110 }}
              value={runInputs}
              onChange={(e) => setRunInputs(e.target.value)}
              spellCheck={false}
            />
            <div className="skills-pending__actions" style={{ marginTop: 8 }}>
              <Button variant="primary" onClick={() => void executeRun()} disabled={runBusy}>
                {runBusy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {t('workflows.run')}
              </Button>
            </div>
          </div>

          {runResult ? (
            <>
              <div className="settings-card">
                <div className="settings-row">
                  <div className="settings-row__copy"><label>{t('workflows.result')}</label>{runResult.error ? <p>{runResult.error}</p> : null}</div>
                  <div className="settings-row__control">
                    <span className={`settings-chip settings-chip--${runResult.status === 'ok' ? 'ok' : runResult.status === 'failed' ? 'bad' : 'warn'}`}>{runResult.status}</span>
                  </div>
                </div>
                {runResult.status === 'needs_human' && runResult.pending_step ? (
                  <div className="settings-row">
                    <div className="settings-row__copy"><label>{runResult.pending_step}</label></div>
                    <div className="settings-row__control skills-pending__actions">
                      <Button variant="primary" size="sm" onClick={() => void resolveHuman(runResult.run_id, runResult.pending_step || '', true)}>
                        <CheckCircle2 size={14} /> {t('workflows.approve_step')}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => void resolveHuman(runResult.run_id, runResult.pending_step || '', false)}>
                        <XCircle size={14} /> {t('workflows.reject_step')}
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>

              {Object.keys(runResult.context?.vars ?? {}).length > 0 ? (
                <div className="settings-card" style={{ padding: 14 }}>
                  <div className="settings-row__copy" style={{ marginBottom: 8 }}><label>{t('workflows.outputs')}</label></div>
                  <pre className="skill-detail__pre">{JSON.stringify(runResult.context?.vars ?? {}, null, 2)}</pre>
                </div>
              ) : null}

              {runEvents.length > 0 ? (
                <div className="settings-card" style={{ padding: 14 }}>
                  <div className="settings-row__copy" style={{ marginBottom: 8 }}><label>{t('workflows.timeline')}</label></div>
                  <div className="wf-timeline">
                    {runEvents.map((event) => (
                      <div className="wf-timeline__row" key={event.seq}>
                        <span className={`settings-chip settings-chip--${event.status === 'failed' ? 'bad' : event.status === 'ok' ? 'ok' : 'dim'}`}>{event.type}</span>
                        <span>{event.step_id || '—'}</span>
                        <span className="wf-timeline__msg">{event.message}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {runEvidence.length > 0 ? (
                <div className="settings-card" style={{ padding: 14 }}>
                  <div className="settings-row__copy" style={{ marginBottom: 8 }}><label>{t('workflows.evidence')}</label></div>
                  <div className="wf-timeline">
                    {runEvidence.map((item) => (
                      <div className="wf-timeline__row" key={`${item.step_id}-${item.at}`}>
                        <span className="settings-chip">{item.kind}</span>
                        <span>{item.step_id}</span>
                        <span className="wf-timeline__msg">{item.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'detail') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={detail?.name || t('workflows.title')}
        description={detail?.description || undefined}
        action={backButton}
      >
        <div className="workspace-page__content">
          {detailLoading ? (
            <div className="skill-empty">
              <Loader2 size={16} className="animate-spin" />
            </div>
          ) : detail ? (
            <>
              {/* Action bar */}
              <div className="wf-action-bar" role="toolbar" aria-label={t('workflows.more')}>
                <div className="wf-action-bar__group">
                  <Button variant="primary" onClick={() => openRun(detail)}>
                    <Play size={14} /> {t('workflows.run')}
                  </Button>
                  <Button variant="secondary" onClick={() => openGraph(detail)}>
                    <Wand2 size={14} /> {t('workflows.visual_edit')}
                  </Button>
                  <Button variant="secondary" onClick={() => { setEditor({ name: detail.name, isNew: false, content: detail.yaml || '' }); setSubPage('editor'); }}>
                    <FileText size={14} /> {t('workflows.edit_yaml')}
                  </Button>
                  <Button variant="secondary" onClick={() => void exportWorkflow(detail)}>
                    <Download size={14} /> {t('workflows.export')}
                  </Button>
                </div>
                <div className="wf-action-bar__group wf-action-bar__group--end">
                  <Button variant="destructive" onClick={() => void removeWorkflow(detail)}>
                    <Trash2 size={14} /> {t('workflows.delete')}
                  </Button>
                </div>
              </div>

              {/* Overview first */}
              <div className="settings-group">
                <div className="settings-group__heading">
                  <h2>{t('workflows.tab_overview')}</h2>
                </div>
                <div className="settings-card" style={{ padding: 14 }}>
                  <table className="wf-table">
                    <tbody>
                      <tr>
                        <td className="wf-table__key">{t('workflows.version')}</td>
                        <td>v{detail.version}</td>
                      </tr>
                      <tr>
                        <td className="wf-table__key">{t('workflows.status')}</td>
                        <td><span className="settings-chip">{detail.status}</span></td>
                      </tr>
                      <tr>
                        <td className="wf-table__key">{t('workflows.step_count')}</td>
                        <td>{detail.step_count} {t('workflows.steps')}</td>
                      </tr>
                      <tr>
                        <td className="wf-table__key">{t('workflows.triggers')}</td>
                        <td>
                          <span className="settings-chip">{(detail.triggers.length ? detail.triggers : ['manual']).join(', ')}</span>
                        </td>
                      </tr>
                      <tr>
                        <td className="wf-table__key">{t('workflows.source')}</td>
                        <td><span className="settings-chip">{detail.source}</span></td>
                      </tr>
                      <tr>
                        <td className="wf-table__key">{t('workflows.updated')}</td>
                        <td>{relTime(detail.updated_at)}</td>
                      </tr>
                    </tbody>
                  </table>

                  {detail.inputs.length > 0 ? (
                    <>
                      <div className="wf-subhead">{t('workflows.inputs')}</div>
                      <table className="wf-table">
                        <thead>
                          <tr>
                            <th style={{ width: '24%' }}>{t('workflows.col_name')}</th>
                            <th style={{ width: '14%' }}>{t('workflows.col_type')}</th>
                            <th style={{ width: '12%' }}>{t('workflows.col_required')}</th>
                            <th>{t('workflows.col_desc')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.inputs.map((input) => (
                            <tr key={input.name}>
                              <td><strong>{input.name}</strong></td>
                              <td className="wf-table__muted">{input.type}</td>
                              <td>{input.required ? <span className="wf-table__req">*</span> : <span className="wf-table__muted">—</span>}</td>
                              <td className="wf-table__muted">{input.description || String(input.default ?? '') || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : null}

                  {Object.keys(detail.outputs ?? {}).length > 0 ? (
                    <>
                      <div className="wf-subhead">{t('workflows.outputs')}</div>
                      <table className="wf-table">
                        <thead>
                          <tr>
                            <th style={{ width: '30%' }}>{t('workflows.col_name')}</th>
                            <th>{t('workflows.col_expr')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(detail.outputs ?? {}).map(([key, value]) => (
                            <tr key={key}>
                              <td><strong>{key}</strong></td>
                              <td><code>{value}</code></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : null}
                </div>
              </div>

              {/* Flow (steps) */}
              <div className="settings-group">
                <div className="settings-group__heading wf-flow-heading">
                  <h2>{t('workflows.flow')}</h2>
                  <div className="wf-flow-toggle">
                    <button type="button" className={flowView === 'list' ? 'wf-seg wf-seg--active' : 'wf-seg'} onClick={() => setFlowView('list')}>
                      {t('workflows.view_list')}
                    </button>
                    <button type="button" className={flowView === 'graph' ? 'wf-seg wf-seg--active' : 'wf-seg'} onClick={() => setFlowView('graph')}>
                      {t('workflows.view_graph')}
                    </button>
                  </div>
                </div>
                {flowView === 'list' ? (
                  <WorkflowStepList steps={detail.steps ?? []} triggers={detail.triggers} outputs={detail.outputs ?? {}} />
                ) : (
                  <WorkflowFlowGraph steps={detail.steps ?? []} triggers={detail.triggers} outputs={detail.outputs ?? {}} />
                )}
              </div>

              {/* Feedback */}
              <div className="settings-group">
                <div className="settings-group__heading">
                  <h2>{t('workflows.tab_feedback')}</h2>
                  <p>{t('workflows.feedback_hint')}</p>
                </div>
                <div className="settings-card" style={{ padding: 14 }}>
                  <textarea
                    className="skills-pending__editor"
                    style={{ minHeight: 100 }}
                    value={feedbackText}
                    onChange={(e) => setFeedbackText(e.target.value)}
                    placeholder={t('workflows.feedback_placeholder')}
                  />
                  <div className="skills-pending__actions" style={{ marginTop: 8 }}>
                    <Button variant="primary" size="sm" disabled={feedbackBusy || !feedbackText.trim()} onClick={() => void submitFeedback()}>
                      {feedbackBusy ? <Loader2 size={14} className="animate-spin" /> : null}
                      {t('workflows.feedback_submit')}
                    </Button>
                  </div>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      eyebrow={t('settings.eyebrow')}
      title={t('workflows.title')}
      description={t('workflows.subtitle')}
      action={
        <>
          {sessionId && (
            <Button variant="secondary" onClick={() => void recordFromSession()} disabled={loading}>
              <Wand2 size={14} />
              {t('workflows.record_from_session')}
            </Button>
          )}
          <Button variant="secondary" onClick={() => setSubPage('templates')} disabled={loading}>
            {t('workflows.from_template')}
          </Button>
          <Button
            variant="primary"
            onClick={() => { setGraphTarget({ name: '', description: '', steps: [], isNew: true }); setSubPage('graph'); }}
            disabled={loading}
          >
            <Plus size={14} />
            {t('workflows.new')}
          </Button>
        </>
      }
    >
      <div className="workspace-page__content">
        {message && (
          <div className={`skill-message ${messageType === 'error' ? 'skill-message--error' : ''}`}>{message}</div>
        )}

        {notifications.length > 0 && (
          <div className="skills-pending" style={{ borderColor: 'var(--destructive, #d9534f)' }}>
            <div className="skills-pending__title">
              <Bell size={14} />
              {t('workflows.notifications')}
              <span className="skills-pending__count">{notifications.length}</span>
              <Button variant="ghost" size="sm" onClick={() => void clearNotifications()}>
                {t('workflows.notifications_clear')}
              </Button>
            </div>
            <div className="skills-pending__list">
              {notifications.slice(0, 6).map((note) => (
                <div key={note.id} className="skills-pending__card">
                  <div className="skills-pending__head">
                    <span className="skills-pending__name">{note.title}</span>
                    <span className="settings-chip">{note.kind}</span>
                  </div>
                  <p className="skills-pending__desc">{note.detail || note.at}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {pending.length > 0 && (
          <div className="skills-pending">
            <div className="skills-pending__title">
              <Bot size={14} />
              {t('workflows.pending')}
              <span className="skills-pending__count">{pending.length}</span>
            </div>
            <div className="skills-pending__list">
              {pending.map((draft) => (
                <div key={draft.name} className="skills-pending__card">
                  <div className="skills-pending__head">
                    <span className="skills-pending__name">{draft.name}</span>
                    <span className="settings-chip">{t('workflows.agent_generated')}</span>
                  </div>
                  <p className="skills-pending__desc">{draft.description}</p>
                  <div className="skills-pending__actions">
                    <Button variant="outline" size="sm" onClick={() => void openPending(draft.name)}>
                      <Eye size={14} />
                      {t('workflows.preview')}
                    </Button>
                    <Button variant="primary" size="sm" disabled={pendingBusy === draft.name} onClick={() => void approvePending(draft.name)}>
                      <Check size={14} />
                      {t('workflows.approve')}
                    </Button>
                    <Button variant="ghost" size="sm" disabled={pendingBusy === draft.name} onClick={() => void rejectPending(draft.name)}>
                      <Trash2 size={14} />
                      {t('workflows.reject')}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="skills-header__actions" style={{ marginBottom: 12 }}>
          <CategoryTabs categories={categories} value={filter} onChange={setFilter} />
          <input
            className="tag-bar__search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('workflows.search_placeholder')}
          />
          <Button variant="secondary" size="sm" onClick={() => void refresh()} aria-label={t('workflows.refresh')}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
        </div>

        {visible.length === 0 ? (
          <div className="skill-empty">
            <p>{workflows.length === 0 ? t('workflows.empty') : t('workflows.no_match')}</p>
          </div>
        ) : (
          <div className="skills-grid">
            {visible.map((wf) => (
              <GridCard
                key={wf.name}
                icon={<span className="skill-emoji">{STEP_EMOJI[wf.steps?.[0]?.kind ?? 'tool'] ?? '🧩'}</span>}
                title={wf.name}
                subtitle={`v${wf.version} · ${wf.step_count} ${t('workflows.steps')} · ${wf.triggers.join(', ') || 'manual'}`}
                description={wf.description}
                added={wf.status === 'active'}
                onClick={() => void openDetail(wf)}
                trailing={
                  <>
                    <Button
                      variant="primary"
                      size="icon-xs"
                      onClick={(e) => { e.stopPropagation(); openRun(wf); }}
                      aria-label={t('workflows.run')}
                      title={t('workflows.run')}
                    >
                      <Play size={14} />
                    </Button>
                    <Button
                      variant="secondary"
                      size="icon-xs"
                      onClick={(e) => { e.stopPropagation(); void duplicateWorkflow(wf); }}
                      aria-label={t('workflows.duplicate')}
                      title={t('workflows.duplicate')}
                    >
                      <CopyPlus size={14} />
                    </Button>
                    <Button
                      variant="destructive"
                      size="icon-xs"
                      onClick={(e) => { e.stopPropagation(); void removeWorkflow(wf); }}
                      aria-label={t('workflows.delete')}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </>
                }
              />
            ))}
          </div>
        )}
      </div>
    </WorkspacePage>
  );
}
