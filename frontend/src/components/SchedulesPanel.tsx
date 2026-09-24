import { Clock, Loader2, Play, Plus, RefreshCw, Trash2, History } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Switch } from './ui/switch';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import { DetailModal } from './ui/detail-modal';
import type { CronSchedule, ScheduleRunRecord, WorkflowEntry } from '../types';

const COMMON_TIMEZONES = [
  'UTC',
  'Asia/Shanghai',
  'Asia/Hong_Kong',
  'Asia/Tokyo',
  'Asia/Singapore',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Australia/Sydney',
];

const CRON_PRESETS: Array<{ key: string; cron: string }> = [
  { key: 'every5m', cron: '*/5 * * * *' },
  { key: 'hourly', cron: '0 * * * *' },
  { key: 'daily9', cron: '0 9 * * *' },
  { key: 'weeklyMon9', cron: '0 9 * * 1' },
  { key: 'monthly1_9', cron: '0 9 1 * *' },
];

interface EditorState {
  id: string;
  name: string;
  target_type: 'workflow' | 'command' | 'agent';
  workflow: string;
  command: string;
  prompt: string;
  inputsText: string;
  cron: string;
  timezone: string;
  enabled: boolean;
  overlap: 'skip' | 'queue' | 'replace' | 'allow';
  misfire: 'skip' | 'run_once' | 'catchup';
  grace_seconds: number;
  retry_max: number;
  retry_backoff: number;
  timeout_seconds: number;
}

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function emptyEditor(): EditorState {
  return {
    id: '',
    name: '',
    target_type: 'workflow',
    workflow: '',
    command: '',
    prompt: '',
    inputsText: '{}',
    cron: '0 9 * * *',
    timezone: localTimezone(),
    enabled: true,
    overlap: 'skip',
    misfire: 'skip',
    grace_seconds: 0,
    retry_max: 0,
    retry_backoff: 30,
    timeout_seconds: 0,
  };
}

function toEditor(schedule: CronSchedule): EditorState {
  return {
    id: schedule.id,
    name: schedule.name,
    target_type: schedule.target_type,
    workflow: schedule.workflow,
    command: schedule.command,
    prompt: schedule.prompt,
    inputsText: JSON.stringify(schedule.inputs ?? {}, null, 2),
    cron: schedule.cron,
    timezone: schedule.timezone,
    enabled: schedule.enabled,
    overlap: schedule.overlap,
    misfire: schedule.misfire,
    grace_seconds: schedule.grace_seconds,
    retry_max: schedule.retry_max,
    retry_backoff: schedule.retry_backoff,
    timeout_seconds: schedule.timeout_seconds,
  };
}

function relativeTime(value: string): string {
  if (!value) return '—';
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value;
  const diff = ts - Date.now();
  const abs = Math.abs(diff);
  const mins = Math.round(abs / 60000);
  const label =
    mins < 60 ? `${mins}m` : mins < 1440 ? `${Math.round(mins / 60)}h` : `${Math.round(mins / 1440)}d`;
  return diff >= 0 ? `in ${label}` : `${label} ago`;
}

function targetSummary(s: CronSchedule): string {
  if (s.target_type === 'workflow') return `workflow: ${s.workflow}`;
  if (s.target_type === 'command') return `command: ${s.command}`;
  return `agent: ${s.prompt}`;
}

export function SchedulesPanel() {
  const [schedules, setSchedules] = useState<CronSchedule[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<'ok' | 'error'>('ok');

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorBusy, setEditorBusy] = useState(false);
  const [preview, setPreview] = useState<{ description: string; runs: string[] } | null>(null);
  const [previewError, setPreviewError] = useState('');

  const [historyFor, setHistoryFor] = useState<CronSchedule | null>(null);
  const [historyRuns, setHistoryRuns] = useState<ScheduleRunRecord[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [list, wf] = await Promise.all([
        chatService.listSchedules(),
        chatService.listWorkflows().catch(() => ({ status: 'ok', workflows: [] as WorkflowEntry[] })),
      ]);
      setSchedules(list.schedules);
      setWorkflows(wf.workflows);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Live preview whenever cron/timezone change in the editor.
  useEffect(() => {
    if (!editor) {
      setPreview(null);
      setPreviewError('');
      return;
    }
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const result = await chatService.previewSchedule(editor.cron, editor.timezone, 5);
        if (cancelled) return;
        if (result.status === 'ok') {
          setPreview({ description: result.description ?? editor.cron, runs: result.runs });
          setPreviewError('');
        } else {
          setPreview(null);
          setPreviewError(result.message ?? 'invalid');
        }
      } catch {
        if (!cancelled) setPreviewError('invalid');
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [editor?.cron, editor?.timezone, editor]);

  const openNew = () => {
    const base = emptyEditor();
    if (workflows.length > 0) base.workflow = workflows[0]?.name ?? '';
    setEditor(base);
  };

  const openEdit = (schedule: CronSchedule) => setEditor(toEditor(schedule));

  const saveEditor = useCallback(async () => {
    if (!editor) return;
    setEditorBusy(true);
    try {
      let inputs: Record<string, unknown> = {};
      const text = editor.inputsText.trim();
      if (text) {
        try {
          inputs = JSON.parse(text);
        } catch {
          throw new Error(t('schedules.invalid_inputs_json'));
        }
      }
      const payload: Record<string, unknown> = {
        name: editor.name,
        target_type: editor.target_type,
        workflow: editor.workflow,
        command: editor.command,
        prompt: editor.prompt,
        inputs,
        cron: editor.cron,
        timezone: editor.timezone,
        enabled: editor.enabled,
        overlap: editor.overlap,
        misfire: editor.misfire,
        grace_seconds: editor.grace_seconds,
        retry_max: editor.retry_max,
        retry_backoff: editor.retry_backoff,
        timeout_seconds: editor.timeout_seconds,
      };
      if (editor.id) await chatService.updateSchedule(editor.id, payload);
      else await chatService.createSchedule(payload);
      setMessageType('ok');
      setMessage(t('schedules.saved'));
      setEditor(null);
      await refresh();
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error));
    } finally {
      setEditorBusy(false);
    }
  }, [editor, refresh]);

  const toggleEnabled = useCallback(
    async (schedule: CronSchedule, enabled: boolean) => {
      try {
        await chatService.setScheduleEnabled(schedule.id, enabled);
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const runNow = useCallback(
    async (schedule: CronSchedule) => {
      try {
        const result = await chatService.runScheduleNow(schedule.id);
        setMessageType(result.status === 'ok' ? 'ok' : 'error');
        setMessage(`${schedule.name}: ${result.status}`);
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (schedule: CronSchedule) => {
      if (!window.confirm(t('schedules.delete_confirm').replace('{name}', schedule.name))) return;
      try {
        await chatService.deleteSchedule(schedule.id);
        await refresh();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error));
      }
    },
    [refresh],
  );

  const openHistory = useCallback(async (schedule: CronSchedule) => {
    setHistoryFor(schedule);
    setHistoryRuns([]);
    try {
      const result = await chatService.listScheduleRuns(schedule.id);
      setHistoryRuns(result.runs);
    } catch {
      setHistoryRuns([]);
    }
  }, []);

  const zones = useMemo(() => {
    const set = new Set<string>(COMMON_TIMEZONES);
    if (editor?.timezone) set.add(editor.timezone);
    return Array.from(set);
  }, [editor?.timezone]);

  return (
    <WorkspacePage
      eyebrow={t('settings.eyebrow')}
      title={t('schedules.title')}
      description={t('schedules.subtitle')}
      action={
        <Button variant="primary" onClick={openNew} disabled={loading}>
          <Plus size={14} />
          {t('schedules.new')}
        </Button>
      }
    >
      <div className="workspace-page__content">
        {message && <div className={`skill-message ${messageType === 'error' ? 'skill-message--error' : ''}`}>{message}</div>}

        <div className="skills-header__actions" style={{ marginBottom: 12 }}>
          <span className="settings-chip">
            {t('schedules.master_hint')}
          </span>
          <Button variant="secondary" size="sm" onClick={() => void refresh()} aria-label={t('schedules.refresh')}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
        </div>

        {schedules.length === 0 ? (
          <div className="skill-empty">
            <Clock size={18} />
            <p>{t('schedules.empty')}</p>
          </div>
        ) : (
          <div className="skills-pending__list">
            {schedules.map((schedule) => (
              <div key={schedule.id} className="skills-pending__card">
                <div className="skills-pending__head">
                  <span className="skills-pending__name">{schedule.name}</span>
                  <span className="settings-chip">{schedule.cron}</span>
                  <span className="settings-chip">{schedule.timezone}</span>
                  {schedule.last_status !== 'idle' && (
                    <span className="settings-chip">
                      {t('schedules.last')}: {schedule.last_status}
                    </span>
                  )}
                </div>
                <p className="skills-pending__desc">
                  {targetSummary(schedule)} · {t('schedules.next')}: {relativeTime(schedule.next_run_at)}
                </p>
                <div className="skills-pending__actions">
                  <Switch
                    id={`sched-${schedule.id}`}
                    checked={schedule.enabled}
                    onChange={(e) => void toggleEnabled(schedule, e.target.checked)}
                    aria-label={t('schedules.enabled')}
                  />
                  <Button variant="primary" size="sm" onClick={() => void runNow(schedule)}>
                    <Play size={14} />
                    {t('schedules.run_now')}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => openEdit(schedule)}>
                    {t('schedules.edit')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void openHistory(schedule)}>
                    <History size={14} />
                    {t('schedules.history')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void remove(schedule)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Editor */}
        <DetailModal
          open={editor !== null}
          onClose={() => setEditor(null)}
          title={editor?.id ? t('schedules.edit') : t('schedules.new')}
          footer={
            editor && (
              <>
                <Button variant="ghost" onClick={() => setEditor(null)} disabled={editorBusy}>
                  {t('common.cancel')}
                </Button>
                <Button variant="primary" onClick={() => void saveEditor()} disabled={editorBusy}>
                  {editorBusy ? <Loader2 size={14} className="animate-spin" /> : null}
                  {t('schedules.save')}
                </Button>
              </>
            )
          }
        >
          {editor && (
            <div className="schedule-editor">
              <label className="add-skill-page__field">
                <span>{t('schedules.name')}</span>
                <Input value={editor.name} onChange={(e) => setEditor({ ...editor, name: e.target.value })} />
              </label>

              <label className="add-skill-page__field">
                <span>{t('schedules.target_type')}</span>
                <select
                  className="input"
                  value={editor.target_type}
                  onChange={(e) => setEditor({ ...editor, target_type: e.target.value as EditorState['target_type'] })}
                >
                  <option value="workflow">{t('schedules.target_workflow')}</option>
                  <option value="command">{t('schedules.target_command')}</option>
                  <option value="agent">{t('schedules.target_agent')}</option>
                </select>
              </label>

              {editor.target_type === 'workflow' && (
                <>
                  <label className="add-skill-page__field">
                    <span>{t('schedules.workflow')}</span>
                    <select
                      className="input"
                      value={editor.workflow}
                      onChange={(e) => setEditor({ ...editor, workflow: e.target.value })}
                    >
                      {workflows.map((wf) => (
                        <option key={wf.name} value={wf.name}>
                          {wf.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="add-skill-page__field">
                    <span>{t('schedules.inputs')}</span>
                    <Textarea
                      value={editor.inputsText}
                      onChange={(e) => setEditor({ ...editor, inputsText: e.target.value })}
                      spellCheck={false}
                    />
                  </label>
                </>
              )}

              {editor.target_type === 'command' && (
                <label className="add-skill-page__field">
                  <span>{t('schedules.command')}</span>
                  <Input value={editor.command} onChange={(e) => setEditor({ ...editor, command: e.target.value })} />
                </label>
              )}

              {editor.target_type === 'agent' && (
                <label className="add-skill-page__field">
                  <span>{t('schedules.prompt')}</span>
                  <Textarea value={editor.prompt} onChange={(e) => setEditor({ ...editor, prompt: e.target.value })} />
                </label>
              )}

              <label className="add-skill-page__field">
                <span>{t('schedules.cron')}</span>
                <Input value={editor.cron} onChange={(e) => setEditor({ ...editor, cron: e.target.value })} spellCheck={false} />
              </label>
              <div className="schedule-editor__presets">
                {CRON_PRESETS.map((preset) => (
                  <Button key={preset.key} variant="outline" size="sm" onClick={() => setEditor({ ...editor, cron: preset.cron })}>
                    {t(`schedules.preset_${preset.key}`)}
                  </Button>
                ))}
              </div>
              <div className="schedule-editor__preview">
                {previewError ? (
                  <span className="add-skill-page__msg add-skill-page__msg--error">{t('schedules.invalid_cron')}</span>
                ) : preview ? (
                  <>
                    <div>{preview.description}</div>
                    <ul className="schedule-editor__runs">
                      {preview.runs.map((run) => (
                        <li key={run}>{run}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </div>

              <label className="add-skill-page__field">
                <span>{t('schedules.timezone')}</span>
                <select
                  className="input"
                  value={editor.timezone}
                  onChange={(e) => setEditor({ ...editor, timezone: e.target.value })}
                >
                  {zones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}
                    </option>
                  ))}
                </select>
              </label>

              <div className="schedule-editor__row">
                <label className="add-skill-page__field">
                  <span>{t('schedules.overlap')}</span>
                  <select
                    className="input"
                    value={editor.overlap}
                    onChange={(e) => setEditor({ ...editor, overlap: e.target.value as EditorState['overlap'] })}
                  >
                    {['skip', 'queue', 'replace', 'allow'].map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="add-skill-page__field">
                  <span>{t('schedules.misfire')}</span>
                  <select
                    className="input"
                    value={editor.misfire}
                    onChange={(e) => setEditor({ ...editor, misfire: e.target.value as EditorState['misfire'] })}
                  >
                    {['skip', 'run_once', 'catchup'].map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="schedule-editor__row">
                <label className="add-skill-page__field">
                  <span>{t('schedules.grace_seconds')}</span>
                  <Input
                    type="number"
                    value={editor.grace_seconds}
                    onChange={(e) => setEditor({ ...editor, grace_seconds: Number(e.target.value) || 0 })}
                  />
                </label>
                <label className="add-skill-page__field">
                  <span>{t('schedules.retry_max')}</span>
                  <Input
                    type="number"
                    value={editor.retry_max}
                    onChange={(e) => setEditor({ ...editor, retry_max: Number(e.target.value) || 0 })}
                  />
                </label>
                <label className="add-skill-page__field">
                  <span>{t('schedules.timeout_seconds')}</span>
                  <Input
                    type="number"
                    value={editor.timeout_seconds}
                    onChange={(e) => setEditor({ ...editor, timeout_seconds: Number(e.target.value) || 0 })}
                  />
                </label>
              </div>

              <Switch
                id="schedule-enabled"
                checked={editor.enabled}
                onChange={(e) => setEditor({ ...editor, enabled: e.target.checked })}
                label={t('schedules.enabled')}
              />
            </div>
          )}
        </DetailModal>

        {/* History */}
        <DetailModal
          open={historyFor !== null}
          onClose={() => setHistoryFor(null)}
          title={`${t('schedules.history')}: ${historyFor?.name ?? ''}`}
        >
          {historyRuns.length === 0 ? (
            <p className="skill-empty">{t('schedules.no_history')}</p>
          ) : (
            <div className="skills-pending__list">
              {historyRuns.map((run, index) => (
                <div key={`${run.at}-${index}`} className="schedule-history__row">
                  <span className={`settings-chip ${run.status === 'ok' ? '' : 'settings-chip--error'}`}>{run.status}</span>
                  <span>{run.at}</span>
                  <span className="schedule-history__trigger">{run.trigger}</span>
                  {run.error ? <span className="schedule-history__error">{run.error}</span> : null}
                </div>
              ))}
            </div>
          )}
        </DetailModal>
      </div>
    </WorkspacePage>
  );
}
