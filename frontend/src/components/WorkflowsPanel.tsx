import { ArrowLeft, Check, Loader2, Play, Plus, RefreshCw, Trash2, Bot, Eye, Bell, CheckCircle2, XCircle, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { GridCard } from './ui/grid-card';
import { usePageNavPublish } from '../nav/PageNav';
import { WorkflowGraphEditor, type GraphEditorTarget } from './WorkflowGraphEditor';
import type {
  WorkflowEntry,
  WorkflowDraft,
  WorkflowStep,
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

function stepSummary(step: WorkflowStep, depth = 0): string {
  const icon = STEP_EMOJI[step.kind] ?? '•';
  const target = step.do || (step.locator ? JSON.stringify(step.locator) : '');
  const indent = '  '.repeat(depth);
  let line = `${indent}${icon} ${step.id}  ·  ${step.kind}${target ? `  →  ${target}` : ''}`;
  if (step.when) line += `  [when ${step.when}]`;
  if (step.foreach) line += `  [each ${step.foreach}]`;
  const children: string[] = [];
  for (const child of step.then ?? []) children.push(stepSummary(child, depth + 1));
  for (const child of step.else ?? []) children.push(stepSummary(child, depth + 1));
  for (const child of step.body ?? []) children.push(stepSummary(child, depth + 1));
  return [line, ...children].join('\n');
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
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRuns, setDetailRuns] = useState<WorkflowRun[]>([]);

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
    setDetailRuns([]);
    try {
      const [full, runs] = await Promise.all([
        chatService.getWorkflow(wf.name),
        chatService.listWorkflowRuns(wf.name).catch(() => ({ status: 'ok', runs: [] })),
      ]);
      setDetail(full.workflow);
      setDetailRuns(runs.runs);
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
      setEditor(null);
      setSubPage('list');
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setEditorBusy(false);
    }
  }, [editor, refresh]);

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
  useEffect(() => {
    if (subPage === 'list') {
      publishNav({ viewLabel: t('workflows.title') });
    } else {
      let leaf: string = t('workflows.title');
      if (subPage === 'detail') leaf = detail?.name || t('workflows.title');
      else if (subPage === 'editor') leaf = editor?.isNew ? t('workflows.new') : t('workflows.edit');
      else if (subPage === 'graph') leaf = t('workflows.visual_edit');
      else if (subPage === 'templates') leaf = t('workflows.templates');
      else if (subPage === 'run') leaf = `${t('workflows.run')}: ${runTarget?.name ?? ''}`;
      else if (subPage === 'pending') leaf = t('workflows.pending_review');
      publishNav({ viewLabel: t('workflows.title'), leafLabel: leaf, onBackToRoot: () => setSubPage('list') });
    }
    return () => publishNav(null);
  }, [publishNav, subPage, detail, editor, runTarget]);

  const backButton = (
    <Button variant="ghost" onClick={() => setSubPage('list')}>
      <ArrowLeft size={15} />
      {t('settings.back')}
    </Button>
  );

  // ── second-level pages (breadcrumb) ─────────────────────────────────

  if (subPage === 'graph') {
    return (
      <WorkspacePage eyebrow={t('workflows.title')} title={t('workflows.visual_edit')} action={backButton}>
        <div className="workspace-page__content">
          <WorkflowGraphEditor target={graphTarget} onClose={() => setSubPage('list')} onSaved={() => void refresh()} />
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'editor') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={editor?.isNew ? t('workflows.new') : t('workflows.edit')}
        action={backButton}
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
        action={backButton}
      >
        <div className="workspace-page__content">
          <div className="skills-pending__actions" style={{ marginBottom: 10 }}>
            <Button variant="primary" onClick={() => void executeRun()} disabled={runBusy}>
              {runBusy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              {t('workflows.run')}
            </Button>
          </div>
          <label className="add-skill-page__field">
            <span>{t('workflows.run_inputs')}</span>
            <textarea
              className="skills-pending__editor"
              style={{ minHeight: 120 }}
              value={runInputs}
              onChange={(e) => setRunInputs(e.target.value)}
              spellCheck={false}
            />
          </label>
          {runResult && (
            <div className="skill-detail__section">
              <div className="skill-detail__label">{t('workflows.result')}</div>
              <div className={`add-skill-page__msg ${statusClass(runResult.status)}`}>
                {runResult.status}
                {runResult.error ? ` — ${runResult.error}` : ''}
              </div>
              {runResult.status === 'needs_human' && runResult.pending_step ? (
                <div className="skills-pending__actions">
                  <Button variant="primary" size="sm" onClick={() => void resolveHuman(runResult.run_id, runResult.pending_step || '', true)}>
                    <CheckCircle2 size={14} />
                    {t('workflows.approve_step')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void resolveHuman(runResult.run_id, runResult.pending_step || '', false)}>
                    <XCircle size={14} />
                    {t('workflows.reject_step')}
                  </Button>
                </div>
              ) : null}
              <pre className="skill-detail__pre">{JSON.stringify(runResult.context?.vars ?? {}, null, 2)}</pre>
              {runEvents.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div className="skill-detail__label">{t('workflows.timeline')}</div>
                  <div className="schedule-history__row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                    {runEvents.map((event) => (
                      <div key={event.seq} className="schedule-history__row">
                        <span className={`settings-chip ${event.status === 'failed' ? 'settings-chip--error' : ''}`}>{event.type}</span>
                        <span>{event.step_id || '—'}</span>
                        <span className="schedule-history__trigger">{event.message}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {runEvidence.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div className="skill-detail__label">{t('workflows.evidence')}</div>
                  <div className="schedule-history__row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                    {runEvidence.map((item) => (
                      <div key={`${item.step_id}-${item.at}`} className="schedule-history__row">
                        <span className="settings-chip">{item.kind}</span>
                        <span>{item.step_id}</span>
                        <span className="schedule-history__trigger">{item.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </WorkspacePage>
    );
  }

  if (subPage === 'detail') {
    return (
      <WorkspacePage
        eyebrow={t('workflows.title')}
        title={detail?.name || t('workflows.title')}
        description={detail ? `v${detail.version} · ${detail.step_count} ${t('workflows.steps')}` : undefined}
        action={backButton}
      >
        <div className="workspace-page__content">
          {detail && (
            <div className="skills-pending__actions" style={{ marginBottom: 12 }}>
              <Button
                variant="secondary"
                onClick={() => {
                  setGraphTarget({
                    name: detail.name,
                    description: detail.description,
                    version: detail.version,
                    inputs: detail.inputs,
                    steps: detail.steps ?? [],
                    isNew: false,
                  });
                  setSubPage('graph');
                }}
              >
                {t('workflows.visual_edit')}
              </Button>
              <Button variant="ghost" onClick={() => { setEditor({ name: detail.name, isNew: false, content: detail.yaml || '' }); setSubPage('editor'); }}>
                {t('workflows.edit_yaml')}
              </Button>
              <Button variant="primary" onClick={() => openRun(detail)}>
                <Play size={14} />
                {t('workflows.run')}
              </Button>
              <Button variant="destructive" onClick={() => void removeWorkflow(detail)}>
                <Trash2 size={14} />
                {t('workflows.delete')}
              </Button>
            </div>
          )}
          {detailLoading ? (
            <div className="skill-empty">
              <Loader2 size={16} className="animate-spin" />
            </div>
          ) : detail ? (
            <div className="skill-detail">
              <div className="skill-detail__section">
                <div className="skill-detail__label">{t('workflows.description')}</div>
                <div className="skill-detail__value">{detail.description}</div>
              </div>
              {detail.inputs.length > 0 && (
                <div className="skill-detail__section">
                  <div className="skill-detail__label">{t('workflows.inputs')}</div>
                  <div className="skill-detail__value">
                    {detail.inputs.map((i) => `${i.name}:${i.type}${i.required ? '*' : ''}`).join('  ')}
                  </div>
                </div>
              )}
              <div className="skill-detail__section">
                <div className="skill-detail__label">{t('workflows.step_plan')}</div>
                <pre className="skill-detail__pre">{(detail.steps ?? []).map((s) => stepSummary(s)).join('\n')}</pre>
              </div>
              {detailRuns.length > 0 && (
                <div className="skill-detail__section">
                  <div className="skill-detail__label">{t('workflows.run_history')}</div>
                  {detailRuns.slice(0, 8).map((run) => (
                    <div key={run.run_id} className={`add-skill-page__msg ${statusClass(run.status)}`} style={{ marginBottom: 4 }}>
                      {run.status} · {run.trigger} · {run.completed.length} {t('workflows.steps')}
                      {run.error ? ` — ${run.error}` : ''}
                    </div>
                  ))}
                </div>
              )}
              <div className="skill-detail__section">
                <div className="skill-detail__label">{t('workflows.feedback')}</div>
                <textarea
                  className="skills-pending__editor"
                  style={{ minHeight: 80 }}
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder={t('workflows.feedback_placeholder')}
                />
                <div className="skills-pending__actions" style={{ marginTop: 6 }}>
                  <Button variant="primary" size="sm" disabled={feedbackBusy || !feedbackText.trim()} onClick={() => void submitFeedback()}>
                    {feedbackBusy ? <Loader2 size={14} className="animate-spin" /> : null}
                    {t('workflows.feedback_submit')}
                  </Button>
                </div>
              </div>
            </div>
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
