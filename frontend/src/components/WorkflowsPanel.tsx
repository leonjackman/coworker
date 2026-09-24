import { Check, Loader2, Play, Plus, RefreshCw, Trash2, Bot, Eye } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { GridCard } from './ui/grid-card';
import { DetailModal } from './ui/detail-modal';
import type { WorkflowEntry, WorkflowDraft, WorkflowStep, WorkflowRun } from '../types';

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

export function WorkflowsPanel() {
  const [workflows, setWorkflows] = useState<WorkflowEntry[]>([]);
  const [pending, setPending] = useState<WorkflowDraft[]>([]);
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

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [list, drafts] = await Promise.all([
        chatService.listWorkflows(),
        chatService.listPendingWorkflows().catch(() => ({ status: 'ok', pending: [] })),
      ]);
      setWorkflows(list.workflows);
      setPending(drafts.pending);
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
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const openRun = useCallback((wf: WorkflowEntry) => {
    setRunTarget(wf);
    setRunResult(null);
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
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setRunBusy(false);
    }
  }, [runTarget, runInputs, refresh]);

  const openPending = useCallback(async (name: string) => {
    setPendingBusy(name);
    try {
      const response = await chatService.getPendingWorkflow(name);
      setPendingReview({ name, content: response.content });
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

  return (
    <WorkspacePage
      eyebrow={t('settings.eyebrow')}
      title={t('workflows.title')}
      description={t('workflows.subtitle')}
      action={
        <Button variant="primary" onClick={() => setEditor({ name: '', isNew: true, content: '' })} disabled={loading}>
          <Plus size={14} />
          {t('workflows.new')}
        </Button>
      }
    >
      <div className="workspace-page__content">
        {message && (
          <div className={`skill-message ${messageType === 'error' ? 'skill-message--error' : ''}`}>{message}</div>
        )}

        {/* Pending review queue */}
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
            {visible.map((wf) => {
              return (
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
                        onClick={(e) => {
                          e.stopPropagation();
                          openRun(wf);
                        }}
                        aria-label={t('workflows.run')}
                        title={t('workflows.run')}
                      >
                        <Play size={14} />
                      </Button>
                      <Button
                        variant="destructive"
                        size="icon-xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          void removeWorkflow(wf);
                        }}
                        aria-label={t('workflows.delete')}
                      >
                        <Trash2 size={14} />
                      </Button>
                    </>
                  }
                />
              );
            })}
          </div>
        )}

        {/* Detail modal */}
        <DetailModal
          open={detail !== null}
          onClose={() => setDetail(null)}
          title={detail?.name}
          subtitle={detail ? `v${detail.version} · ${detail.step_count} ${t('workflows.steps')}` : undefined}
          footer={
            detail && (
              <>
                <Button variant="secondary" onClick={() => setEditor({ name: detail.name, isNew: false, content: detail.yaml || '' })}>
                  {t('workflows.edit')}
                </Button>
                <Button variant="primary" onClick={() => openRun(detail)}>
                  <Play size={14} />
                  {t('workflows.run')}
                </Button>
                <Button variant="destructive" onClick={() => void removeWorkflow(detail)}>
                  <Trash2 size={14} />
                  {t('workflows.delete')}
                </Button>
              </>
            )
          }
        >
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
                <pre className="skill-detail__pre">
                  {(detail.steps ?? []).map((s) => stepSummary(s)).join('\n')}
                </pre>
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
            </div>
          ) : null}
        </DetailModal>

        {/* YAML editor modal */}
        <DetailModal
          open={editor !== null}
          onClose={() => setEditor(null)}
          title={editor?.isNew ? t('workflows.new') : t('workflows.edit')}
          footer={
            editor && (
              <>
                <Button variant="ghost" onClick={() => setEditor(null)} disabled={editorBusy}>
                  {t('common.cancel')}
                </Button>
                <Button variant="primary" onClick={() => void saveEditor()} disabled={editorBusy}>
                  {editorBusy ? <Loader2 size={14} className="animate-spin" /> : null}
                  {t('workflows.save')}
                </Button>
              </>
            )
          }
        >
          <textarea
            className="skills-pending__editor"
            style={{ minHeight: 360 }}
            value={editor?.content ?? ''}
            onChange={(e) => editor && setEditor({ ...editor, content: e.target.value })}
            spellCheck={false}
            placeholder={t('workflows.yaml_placeholder')}
          />
        </DetailModal>

        {/* Pending review modal */}
        <DetailModal
          open={pendingReview !== null}
          onClose={() => setPendingReview(null)}
          title={t('workflows.pending_review')}
          footer={
            pendingReview && (
              <>
                <Button variant="ghost" onClick={() => setPendingReview(null)}>
                  {t('common.cancel')}
                </Button>
                <Button variant="primary" onClick={() => void approvePending(pendingReview.name)}>
                  <Check size={14} />
                  {t('workflows.approve')}
                </Button>
              </>
            )
          }
        >
          <textarea
            className="skills-pending__editor"
            style={{ minHeight: 360 }}
            value={pendingReview?.content ?? ''}
            onChange={(e) => pendingReview && setPendingReview({ ...pendingReview, content: e.target.value })}
            spellCheck={false}
          />
        </DetailModal>

        {/* Run modal */}
        <DetailModal
          open={runTarget !== null}
          onClose={() => {
            setRunTarget(null);
            setRunResult(null);
          }}
          title={`${t('workflows.run')}: ${runTarget?.name ?? ''}`}
          footer={
            runTarget && (
              <>
                <Button variant="ghost" onClick={() => { setRunTarget(null); setRunResult(null); }} disabled={runBusy}>
                  {t('common.cancel')}
                </Button>
                <Button variant="primary" onClick={() => void executeRun()} disabled={runBusy}>
                  {runBusy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  {t('workflows.run')}
                </Button>
              </>
            )
          }
        >
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
              <pre className="skill-detail__pre">
                {JSON.stringify(runResult.context?.vars ?? {}, null, 2)}
              </pre>
            </div>
          )}
        </DetailModal>
      </div>
    </WorkspacePage>
  );
}
