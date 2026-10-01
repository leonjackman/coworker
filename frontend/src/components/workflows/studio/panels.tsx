import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  Loader2,
  Play,
  Plus,
  RotateCw,
  Search,
  Trash2,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Textarea } from '../../ui/textarea';
import { t } from '../../../lib/i18n';
import { orderSteps } from '../flowGraph';
import { KIND_GROUPS, kindDescKey, kindIcon, kindLabelKey, kindStripe } from '../kinds';
import { VALUE_KINDS, actionDef, actionsFor, outputsFor, type ActionField } from '../actions';
import { LOCATOR_KINDS } from '../kinds';
import { chatService } from '../../../services/chatService';
import { SLOTS } from './workflowTree';
import { INPUT_TYPES, coerceInputDefault, type InputErrors, type InputFormValue } from './runInputs';
import type {
  WorkflowEntry,
  WorkflowEvidence,
  WorkflowInputSpec,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowStep,
  WorkflowStepState,
  WorkflowTemplate,
  WorkflowVersion,
} from '../../../types';

// ── shared ────────────────────────────────────────────────────────────
export interface StudioCommand {
  id: string;
  label: string;
  hint?: string;
  group?: string;
  icon?: LucideIcon;
  run: () => void;
}

// ── Node palette ──────────────────────────────────────────────────────
export function NodePalette({
  onAdd,
  onDragKind,
}: {
  onAdd: (kind: string) => void;
  onDragKind: (kind: string) => void;
}) {
  const [search, setSearch] = useState('');
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return KIND_GROUPS;
    return KIND_GROUPS.map((group) => ({
      ...group,
      kinds: group.kinds.filter(
        (k) =>
          t(kindLabelKey(k)).toLowerCase().includes(needle) ||
          k.toLowerCase().includes(needle),
      ),
    })).filter((group) => group.kinds.length > 0);
  }, [search]);

  return (
    <div className="wfs-palette">
      <input
        className="wfs-search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t('workflows.palette_search')}
        aria-label={t('workflows.palette_search')}
      />
      {filtered.length === 0 ? (
        <p className="wfs-empty-note">{t('workflows.palette_empty')}</p>
      ) : (
        filtered.map((group) => (
          <div className="wfs-palette__group" key={group.id}>
            <div className="wfs-palette__group-label">{t(group.labelKey)}</div>
            {group.kinds.map((kind) => {
              const Icon = kindIcon(kind);
              const label = t(kindLabelKey(kind));
              const desc = t(kindDescKey(kind));
              return (
                <button
                  key={kind}
                  type="button"
                  className="wfs-palette__item"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('text/cw-workflow-kind', kind);
                    e.dataTransfer.effectAllowed = 'copy';
                    onDragKind(kind);
                  }}
                  onClick={() => onAdd(kind)}
                  title={desc}
                >
                  <span className="wfs-palette__item-icon" style={{ color: kindStripe(kind) }}>
                    <Icon size={15} />
                  </span>
                  <span>
                    {label}
                    {desc && desc !== label ? <span className="wfs-palette__item-desc">{desc}</span> : null}
                  </span>
                </button>
              );
            })}
          </div>
        ))
      )}
    </div>
  );
}

// ── Outline ───────────────────────────────────────────────────────────
export function OutlinePanel({
  steps,
  selectedId,
  onSelect,
}: {
  steps: WorkflowStep[];
  selectedId: string;
  onSelect: (pathKey: string) => void;
}) {
  if (steps.length === 0) return <p className="wfs-empty-note">{t('workflows.no_steps')}</p>;
  return <div className="wfs-outline">{renderOutline(orderSteps(steps), [], selectedId, onSelect)}</div>;
}

function renderOutline(
  list: WorkflowStep[],
  base: Array<number | string>,
  selectedId: string,
  onSelect: (key: string) => void,
): ReactNode[] {
  const out: ReactNode[] = [];
  list.forEach((step, index) => {
    const path = [...base, index];
    const key = path.join('.');
    const Icon = kindIcon(step.kind);
    out.push(
      <button
        key={`row-${key}`}
        type="button"
        className={`wfs-outline__row${selectedId === key ? ' wfs-outline__row--active' : ''}`}
        onClick={() => onSelect(key)}
        style={{ paddingLeft: 7 + base.filter((p) => typeof p === 'string').length * 14 }}
      >
        <span style={{ color: kindStripe(step.kind), display: 'inline-flex' }}>
          <Icon size={13} />
        </span>
        <span>{t(kindLabelKey(step.kind))}</span>
        <span className="wf-flow-row__id">#{step.id}</span>
      </button>,
    );
    for (const slot of SLOTS) {
      const kids = (step[slot] as WorkflowStep[] | undefined) ?? [];
      if (kids.length === 0) continue;
      out.push(
        <div key={`slot-${slot}-${key}`} className="wfs-outline__slot">
          {slot}
        </div>,
      );
      out.push(...renderOutline(orderSteps(kids), [...path, slot], selectedId, onSelect));
    }
  });
  return out;
}

// ── Versions ──────────────────────────────────────────────────────────
export function VersionsPanel({
  versions,
  selectedVersion,
  busy,
  canDelete,
  onLoad,
  onDelete,
}: {
  versions: WorkflowVersion[];
  selectedVersion: number;
  busy: boolean;
  canDelete: boolean;
  onLoad: (v: number) => void;
  onDelete: (v: number) => void;
}) {
  if (versions.length === 0) return <p className="wfs-empty-note">{t('workflows.versions_empty')}</p>;
  return (
    <div className="wfs-versions">
      {versions
        .slice()
        .reverse()
        .map((v) => (
          <div
            key={v.version}
            className="wfs-outline__row"
            style={{ cursor: 'default', justifyContent: 'space-between' }}
          >
            <button
              type="button"
              className="wfs-outline__row"
              style={{ padding: 0, width: 'auto' }}
              onClick={() => onLoad(v.version)}
              disabled={busy}
            >
              <span>v{v.version}</span>
              {v.is_current ? <span className="settings-chip">{t('workflows.version_current')}</span> : null}
            </button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onDelete(v.version)}
              disabled={busy || v.is_current || !canDelete}
              title={t('workflows.version_delete')}
            >
              <Trash2 size={13} />
            </Button>
          </div>
        ))}
    </div>
  );
}

// ── Templates ─────────────────────────────────────────────────────────
export function TemplatesPanel({
  templates,
  busyId,
  onInstall,
}: {
  templates: WorkflowTemplate[];
  busyId: string | null;
  onInstall: (tpl: WorkflowTemplate) => void;
}) {
  if (templates.length === 0) return <p className="wfs-empty-note">{t('workflows.templates_empty')}</p>;
  return (
    <div className="wfs-templates">
      {templates.map((tpl) => (
        <div key={tpl.id} className="wfs-palette__group">
          <div className="wfs-palette__group-label">{tpl.name}</div>
          <p className="wfs-empty-note" style={{ padding: '0 2px 6px' }}>{tpl.description}</p>
          <Button variant="outline" size="sm" onClick={() => onInstall(tpl)} disabled={busyId === tpl.id}>
            {busyId === tpl.id ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
            {t('workflows.install')}
          </Button>
        </div>
      ))}
    </div>
  );
}

// ── Runs ──────────────────────────────────────────────────────────────
export function RunsPanel({
  runs,
  onOpen,
}: {
  runs: WorkflowRun[];
  onOpen: (run: WorkflowRun) => void;
}) {
  if (runs.length === 0) return <p className="wfs-empty-note">{t('workflows.runs_empty')}</p>;
  return (
    <div>
      {runs.map((run) => (
        <button key={run.run_id} type="button" className="wfs-outline__row" onClick={() => onOpen(run)}>
          <span
            className={`settings-chip settings-chip--${
              run.status === 'ok' ? 'ok' : run.status === 'failed' ? 'bad' : 'dim'
            }`}
          >
            {run.status}
          </span>
          <span className="wf-flow-row__id">{run.run_id.slice(0, 8)}</span>
          <span className="wf-timeline__msg">{run.trigger || 'manual'}</span>
        </button>
      ))}
    </div>
  );
}

// ── App picker (computer app params) ──────────────────────────────────
interface AppOption {
  displayName: string;
  bundleId: string;
  path?: string;
}

// Installed apps only (you may target an app that is not running yet). The
// list is cached briefly — a failed/unavailable lookup is NOT cached so it can
// recover on the next open.
const APP_CACHE_TTL_MS = 30_000;
let appCacheEntry: { at: number; data: { available: boolean; apps: AppOption[] } } | null = null;
let appInflight: Promise<{ available: boolean; apps: AppOption[] }> | null = null;

function loadComputerApps(): Promise<{ available: boolean; apps: AppOption[] }> {
  const now = Date.now();
  if (appCacheEntry && now - appCacheEntry.at < APP_CACHE_TTL_MS) return Promise.resolve(appCacheEntry.data);
  if (appInflight) return appInflight;
  appInflight = chatService
    .listComputerApps('installed')
    .then((r) => {
      const data = { available: r.available, apps: r.apps ?? [] };
      if (data.available) appCacheEntry = { at: Date.now(), data };
      else appCacheEntry = null;
      return data;
    })
    .catch(() => ({ available: false, apps: [] }))
    .finally(() => {
      appInflight = null;
    });
  return appInflight;
}

function AppField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [apps, setApps] = useState<AppOption[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [menuRect, setMenuRect] = useState<{ left: number; top: number; width: number } | null>(null);

  useEffect(() => setText(value), [value]);

  const openMenu = () => {
    const el = wrapRef.current;
    if (el) {
      const r = el.getBoundingClientRect();
      setMenuRect({ left: r.left, top: r.bottom + 4, width: r.width });
    }
    setOpen(true);
  };

  const ensureLoaded = () => {
    setLoading(true);
    void loadComputerApps().then((r) => {
      setApps(r.apps);
      setAvailable(r.available);
      setLoading(false);
    });
  };

  const query = text.trim().toLowerCase();
  const filtered = useMemo(() => {
    const list = apps ?? [];
    const hits = query
      ? list.filter(
          (a) => a.displayName.toLowerCase().includes(query) || a.bundleId.toLowerCase().includes(query),
        )
      : list;
    return hits.slice(0, 60);
  }, [apps, query]);

  const commit = (next: string) => {
    setText(next);
    onChange(next);
    setUnknown(false);
  };

  // Bridge unavailable (web/headless): plain free-text field.
  if (apps !== null && !available) {
    return (
      <Input
        value={text}
        onChange={(e) => commit(e.target.value)}
        placeholder={t('workflows.app_field_placeholder')}
      />
    );
  }

  return (
    <div className="wfs-appfield" ref={wrapRef}>
      <Input
        value={text}
        placeholder={t('workflows.app_field_placeholder')}
        onFocus={() => {
          openMenu();
          ensureLoaded();
        }}
        onChange={(e) => {
          commit(e.target.value);
          openMenu();
        }}
        onBlur={() => {
          window.setTimeout(() => {
            setOpen(false);
            const known =
              !query || !apps || apps.some((a) => a.displayName.toLowerCase() === query || a.bundleId.toLowerCase() === query);
            setUnknown(!known);
          }, 120);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && filtered[0]) {
            commit(filtered[0].displayName);
            setOpen(false);
          } else if (e.key === 'Escape') {
            setOpen(false);
          }
        }}
      />
      {open && menuRect ? (
        <div
          className="wfs-appfield__menu"
          style={{ position: 'fixed', left: menuRect.left, top: menuRect.top, width: menuRect.width }}
        >
          {loading ? <div className="wfs-appfield__note">{t('workflows.app_field_loading')}</div> : null}
          {!loading && filtered.length === 0 ? (
            <div className="wfs-appfield__note">{t('workflows.app_field_empty')}</div>
          ) : null}
          {filtered.map((app) => (
            <button
              type="button"
              key={`${app.bundleId}:${app.displayName}`}
              className="wfs-appfield__item"
              onMouseDown={(e) => {
                e.preventDefault();
                commit(app.displayName);
                setOpen(false);
              }}
            >
              <span className="wfs-appfield__name">{app.displayName}</span>
              <span className="wfs-appfield__id">{app.bundleId}</span>
            </button>
          ))}
        </div>
      ) : null}
      {unknown && !open ? <span className="wfs-input-error">{t('workflows.app_field_unknown')}</span> : null}
    </div>
  );
}

// ── Run inputs form ───────────────────────────────────────────────────
function inputErrorText(code: InputErrors[string]): string {
  if (code === 'required') return t('workflows.input_required_error');
  if (code === 'number') return t('workflows.input_number_error');
  return t('workflows.input_json_error');
}

function RunInputField({
  spec,
  value,
  error,
  onChange,
}: {
  spec: WorkflowInputSpec;
  value: InputFormValue | undefined;
  error: InputErrors[string] | undefined;
  onChange: (value: InputFormValue) => void;
}) {
  const label = (
    <span>
      {spec.name}
      {spec.required ? <span className="wfs-input-required">*</span> : null}
      <span className="wfs-input-type">{t(`workflows.input_type_${spec.type}`)}</span>
    </span>
  );

  let control: ReactNode;
  if (spec.type === 'boolean') {
    control = (
      <label className="wf-checkbox">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <span>{t('workflows.input_yes')}</span>
      </label>
    );
  } else if (spec.type === 'object') {
    control = (
      <Textarea
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        spellCheck={false}
        placeholder='{"key": "value"}'
      />
    );
  } else if (spec.type === 'number') {
    control = <Input type="number" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />;
  } else if (spec.type === 'list') {
    control = (
      <Input
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('workflows.input_list_placeholder')}
      />
    );
  } else {
    control = (
      <Input
        type={spec.type === 'secret' ? 'password' : 'text'}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  return (
    <div className="wfs-field">
      {label}
      {control}
      {spec.description ? <span className="wfs-input-desc">{spec.description}</span> : null}
      {error ? <span className="wfs-input-error">{inputErrorText(error)}</span> : null}
    </div>
  );
}

// ── Output / problems / console ───────────────────────────────────────
export type BottomTab = 'problems' | 'console' | 'output';

export function OutputPanel({
  tab,
  problems,
  run,
  events,
  evidence,
  inputSpecs,
  inputValues,
  inputErrors,
  runBusy,
  onInputChange,
  onRun,
  onResolve,
}: {
  tab: BottomTab;
  problems: string[];
  run: WorkflowRun | null;
  events: WorkflowRunEvent[];
  evidence: WorkflowEvidence[];
  inputSpecs: WorkflowInputSpec[];
  inputValues: Record<string, InputFormValue>;
  inputErrors: InputErrors;
  runBusy: boolean;
  onInputChange: (name: string, value: InputFormValue) => void;
  onRun: () => void;
  onResolve: (stepId: string, approved: boolean) => void;
}) {
  if (tab === 'problems') {
    if (problems.length === 0) {
      return (
        <p className="wfs-empty-note">
          <CheckCircle2 size={13} style={{ verticalAlign: '-2px', color: 'var(--success)' }} />{' '}
          {t('workflows.no_problems')}
        </p>
      );
    }
    return (
      <div>
        {problems.map((p) => (
          <div className="wfs-problem" key={p}>
            <span className="wfs-problem__badge">
              <AlertTriangle size={13} />
            </span>
            <span>{p}</span>
          </div>
        ))}
      </div>
    );
  }

  if (tab === 'console') {
    if (events.length === 0) return <p className="wfs-empty-note">{t('workflows.no_console')}</p>;
    return (
      <div className="wf-timeline">
        {events.map((event) => (
          <div className="wf-timeline__row" key={event.seq}>
            <span
              className={`settings-chip settings-chip--${
                event.status === 'failed' ? 'bad' : event.status === 'ok' ? 'ok' : 'dim'
              }`}
            >
              {event.type}
            </span>
            <span>{event.step_id || '—'}</span>
            <span className="wf-timeline__msg">{event.message}</span>
          </div>
        ))}
      </div>
    );
  }

  // output
  const invalid = Object.keys(inputErrors).length > 0;
  const vars = (run?.context?.vars as Record<string, unknown>) ?? {};
  const outputs = run?.outputs ?? {};
  // A `failure` gate is a retry/stop decision (backward compat: older runs with
  // no gate_kind default to the approve/reject authorization UI).
  const isFailure = run?.gate_kind === 'failure';
  return (
    <div className="wfs-out">
      {inputSpecs.length === 0 ? (
        <p className="wfs-empty-note">{t('workflows.run_no_inputs')}</p>
      ) : (
        <section className="wfs-out-section wfs-out-section--first">
          <div className="wfs-out-section__head">
            <span className="wf-section__title">{t('workflows.inputs')}</span>
          </div>
          <div className="wfs-run-inputs">
            {inputSpecs.map((spec) => (
              <RunInputField
                key={spec.name}
                spec={spec}
                value={inputValues[spec.name]}
                error={inputErrors[spec.name]}
                onChange={(v) => onInputChange(spec.name, v)}
              />
            ))}
          </div>
        </section>
      )}

      <div className="wfs-out-runbar">
        <Button variant="primary" size="sm" onClick={onRun} disabled={runBusy || invalid}>
          {runBusy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {t('workflows.run')}
        </Button>
      </div>

      {run ? (
        <>
          <section className="wfs-out-section">
            <div className="wfs-out-section__head">
              <span className="wf-section__title">{t('workflows.result')}</span>
              <span
                className={`settings-chip settings-chip--${
                  run.status === 'ok' ? 'ok' : run.status === 'failed' || isFailure ? 'bad' : 'warn'
                }`}
              >
                {run.status}
              </span>
            </div>
            {run.error ? (
              run.status === 'failed' || isFailure ? (
                <pre className="wfs-out-error">{run.error}</pre>
              ) : (
                <p className="wf-timeline__msg">{run.error}</p>
              )
            ) : null}
            {run.status === 'needs_human' && run.pending_step ? (
              isFailure ? (
                // A step FAILED and was escalated to a person: this is a retry/stop
                // decision, not an approve/reject authorization.
                <div className="wfs-out-actions">
                  <Button variant="primary" size="sm" onClick={() => onResolve(run.pending_step || '', true)}>
                    <RotateCw size={13} /> {t('workflows.retry')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => onResolve(run.pending_step || '', false)}>
                    <XCircle size={13} /> {t('workflows.stop')}
                  </Button>
                </div>
              ) : (
                <div className="wfs-out-actions">
                  <Button variant="primary" size="sm" onClick={() => onResolve(run.pending_step || '', true)}>
                    <CheckCircle2 size={13} /> {t('workflows.approve_step')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => onResolve(run.pending_step || '', false)}>
                    <XCircle size={13} /> {t('workflows.reject_step')}
                  </Button>
                </div>
              )
            ) : null}
          </section>

          {Object.keys(outputs).length > 0 ? (
            <section className="wfs-out-section">
              <div className="wfs-out-section__head">
                <span className="wf-section__title">{t('workflows.outputs')}</span>
              </div>
              <pre className="wfs-code">{JSON.stringify(outputs, null, 2)}</pre>
            </section>
          ) : null}

          {Object.keys(vars).length > 0 ? (
            <section className="wfs-out-section">
              <div className="wfs-out-section__head">
                <span className="wf-section__title">{t('workflows.variables')}</span>
              </div>
              <pre className="wfs-code">{JSON.stringify(vars, null, 2)}</pre>
            </section>
          ) : null}

          {evidence.length > 0 ? (
            <section className="wfs-out-section">
              <div className="wfs-out-section__head">
                <span className="wf-section__title">{t('workflows.evidence')}</span>
              </div>
              <div className="wf-timeline">
                {evidence.map((item) => (
                  <div className="wf-timeline__row" key={`${item.step_id}-${item.at}`}>
                    <span className="settings-chip">{item.kind}</span>
                    <span>{item.step_id}</span>
                    <span className="wf-timeline__msg">{item.path}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

// ── Inspector: workflow (nothing selected) ────────────────────────────
export function WorkflowInspector({
  name,
  description,
  version,
  stepCount,
  triggers,
  outputs,
  inputs,
  onName,
  onDescription,
  onInputsChange,
}: {
  name: string;
  description: string;
  version: number | undefined;
  stepCount: number;
  triggers: string[];
  outputs: Record<string, string>;
  inputs: WorkflowEntry['inputs'];
  onName: (v: string) => void;
  onDescription: (v: string) => void;
  onInputsChange: (inputs: WorkflowInputSpec[]) => void;
}) {
  const inputList: WorkflowInputSpec[] = Array.isArray(inputs) ? inputs : [];
  const patchInput = (index: number, patch: Partial<WorkflowInputSpec>) => {
    onInputsChange(inputList.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  };
  const addInput = () => {
    let n = inputList.length + 1;
    while (inputList.some((item) => item.name === `input${n}`)) n += 1;
    onInputsChange([
      ...inputList,
      { name: `input${n}`, type: 'string', required: false, default: null, description: '' },
    ]);
  };
  const removeInput = (index: number) => onInputsChange(inputList.filter((_, i) => i !== index));
  return (
    <div>
      <div className="wf-section__title">{t('workflows.section_workflow')}</div>
      <label className="wfs-field">
        <span>{t('workflows.name')}</span>
        <Input value={name} onChange={(e) => onName(e.target.value)} placeholder={t('workflows.name_placeholder')} />
      </label>
      <label className="wfs-field">
        <span>{t('workflows.description')}</span>
        <Textarea value={description} onChange={(e) => onDescription(e.target.value)} rows={3} />
      </label>
      <div className="wfs-divider" />
      <div className="wf-meta__status" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <span className="settings-chip">v{version ?? 1}</span>
        <span className="settings-chip">
          {stepCount} {t('workflows.steps')}
        </span>
        {triggers.map((tr) => (
          <span className="settings-chip" key={tr}>
            {tr}
          </span>
        ))}
      </div>
      <div className="wfs-divider" />
      <div className="wf-section__title wfs-inputs-head">
        <span>{t('workflows.inputs')}</span>
        <Button variant="ghost" size="xs" onClick={addInput}>
          <Plus size={13} /> {t('workflows.input_add')}
        </Button>
      </div>
      {inputList.length === 0 ? (
        <p className="wfs-empty-note">{t('workflows.inputs_empty')}</p>
      ) : (
        inputList.map((input, index) => (
          <div className="wfs-input-row" key={`${input.name}-${index}`}>
            <Input
              value={input.name}
              placeholder={t('workflows.input_name')}
              onChange={(e) => patchInput(index, { name: e.target.value })}
            />
            <select
              className="input"
              value={input.type}
              onChange={(e) => patchInput(index, { type: e.target.value })}
            >
              {INPUT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`workflows.input_type_${type}`)}
                </option>
              ))}
            </select>
            <Input
              value={input.default === null || input.default === undefined ? '' : String(input.default)}
              placeholder={t('workflows.input_default')}
              onChange={(e) => patchInput(index, { default: coerceInputDefault(input.type, e.target.value) })}
            />
            <Input
              value={input.description}
              placeholder={t('workflows.input_description')}
              onChange={(e) => patchInput(index, { description: e.target.value })}
            />
            <label className="wf-checkbox wfs-input-req">
              <input
                type="checkbox"
                checked={input.required}
                onChange={(e) => patchInput(index, { required: e.target.checked })}
              />
              <span>{t('workflows.input_required')}</span>
            </label>
            <Button variant="ghost" size="icon-xs" onClick={() => removeInput(index)}>
              <Trash2 size={13} />
            </Button>
          </div>
        ))
      )}
      {Object.keys(outputs).length > 0 ? (
        <>
          <div className="wfs-divider" />
          <div className="wf-section__title">{t('workflows.outputs')}</div>
          <table className="wf-table">
            <tbody>
              {Object.entries(outputs).map(([key, value]) => (
                <tr key={key}>
                  <td className="wf-table__key">{key}</td>
                  <td>
                    <code>{value}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

// ── Help icon (hover tooltip) ─────────────────────────────────────────
/** Localized help for a field key, falling back to the backend description. */
function fieldHelp(f: ActionField): string {
  const key = `workflows.help_${f.key}`;
  const localized = t(key);
  return localized === key ? f.help ?? '' : localized;
}

function HelpIcon({ text }: { text?: string }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  if (!text) return null;
  return (
    <span
      className="wfs-helpicon"
      ref={ref}
      onMouseEnter={() => {
        const r = ref.current?.getBoundingClientRect();
        if (r) setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
      }}
      onMouseLeave={() => setPos(null)}
    >
      <HelpCircle size={13} />
      {pos ? (
        <span className="wfs-helpicon__tip" style={{ position: 'fixed', top: pos.top, right: pos.right }}>
          {text}
        </span>
      ) : null}
    </span>
  );
}

// ── Inspector: node (selected) ────────────────────────────────────────
export type InspectorTab = 'basic' | 'advanced';

/** Kinds where a per-step timeout (seconds) is meaningful. */
const TIMEOUT_KINDS = new Set(['command', 'http', 'browser', 'computer', 'app', 'subworkflow']);

export function NodeInspector({
  selected,
  tab,
  onTab,
  onPatch,
  onAddChild,
  state,
}: {
  selected: WorkflowStep;
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
  onPatch: (patch: Partial<WorkflowStep>) => void;
  onAddChild: (slot: 'then' | 'else' | 'body') => void;
  state?: WorkflowStepState | undefined;
}) {
  const kind = selected.kind ?? '';
  const KindIcon = kindIcon(kind);
  const params = (selected.params ?? {}) as Record<string, unknown>;
  const action = actionDef(kind, selected.do ?? '');
  const locator = (selected.locator ?? {}) as Record<string, unknown>;
  const postList = selected.post ?? [];

  const readField = (field: ActionField): unknown => (field.key === '$do' ? selected.do ?? '' : params[field.key]);
  const writeField = (field: ActionField, value: unknown) => {
    if (field.key === '$do') onPatch({ do: String(value ?? '') });
    else onPatch({ params: { ...params, [field.key]: value } });
  };

  const field = (f: ActionField) => {
    const value = readField(f);
    const help = fieldHelp(f);
    if (f.type === 'app') {
      return (
        <label className="wfs-field" key={f.key}>
          <span>
            {t(f.labelKey)} <HelpIcon text={help} />
          </span>
          <AppField value={String(value ?? '')} onChange={(v) => writeField(f, v)} />
        </label>
      );
    }
    if (f.type === 'boolean') {
      return (
        <label className="wf-checkbox" key={f.key}>
          <input type="checkbox" checked={!!value} onChange={(e) => writeField(f, e.target.checked)} />
          <span>
            {t(f.labelKey)} <HelpIcon text={help} />
          </span>
        </label>
      );
    }
    if (f.type === 'textarea') {
      return (
        <label className="wfs-field" key={f.key}>
          <span>
            {t(f.labelKey)} <HelpIcon text={help} />
          </span>
          <Textarea value={String(value ?? '')} onChange={(e) => writeField(f, e.target.value)} rows={3} />
        </label>
      );
    }
    if (f.type === 'number') {
      return (
        <label className="wfs-field" key={f.key}>
          <span>
            {t(f.labelKey)} <HelpIcon text={help} />
          </span>
          <Input
            type="number"
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => writeField(f, e.target.value === '' ? '' : Number(e.target.value))}
          />
        </label>
      );
    }
    if (f.type === 'csv') {
      const text = Array.isArray(value) ? value.join(', ') : String(value ?? '');
      return (
        <label className="wfs-field" key={f.key}>
          <span>
            {t(f.labelKey)} <HelpIcon text={help} />
          </span>
          <Input
            value={text}
            onChange={(e) =>
              writeField(
                f,
                e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
          />
        </label>
      );
    }
    return (
      <label className="wfs-field" key={f.key}>
        <span>
          {t(f.labelKey)} <HelpIcon text={help} />
        </span>
        <Input value={String(value ?? '')} onChange={(e) => writeField(f, e.target.value)} />
      </label>
    );
  };

  return (
    <div>
      {/* Node card: type + id, always visible above the tabs. */}
      <div className="wfs-node-card" style={{ borderLeftColor: kindStripe(kind) }}>
        <span className="wfs-node-card__icon" style={{ color: kindStripe(kind) }}>
          <KindIcon size={16} />
        </span>
        <div className="wfs-node-card__meta">
          <span className="wfs-node-card__type">{t(kindLabelKey(kind))}</span>
          <label className="wfs-node-card__idrow">
            <span className="wfs-node-card__hash">#</span>
            <Input
              className="wfs-node-card__id"
              value={selected.id}
              onChange={(e) => onPatch({ id: e.target.value })}
              aria-label={t('workflows.step_id')}
              placeholder={t('workflows.step_id')}
            />
          </label>
        </div>
      </div>

      <div className="wfs-subtabs">
        <button
          type="button"
          className={`wfs-subtab${tab === 'basic' ? ' wfs-subtab--active' : ''}`}
          onClick={() => onTab('basic')}
        >
          {t('workflows.inspector_basic')}
        </button>
        <button
          type="button"
          className={`wfs-subtab${tab === 'advanced' ? ' wfs-subtab--active' : ''}`}
          onClick={() => onTab('advanced')}
        >
          {t('workflows.inspector_advanced')}
        </button>
      </div>

      {tab === 'basic' ? (
        <>
          {/* ── BEHAVIOR / BINDING ──
              The step INTENT is the 目標 (goal); the machine success criterion
              (成功標準) sits right below it; then the 絕對遵守 hard constraint. */}
          <div className="wf-section__title">{t('workflows.section_binding')}</div>

          <label className="wfs-field">
            <span>{t('workflows.goal')}</span>
            <Textarea
              value={selected.goal ?? selected.description ?? ''}
              onChange={(e) => onPatch({ goal: e.target.value, description: e.target.value })}
              rows={2}
              placeholder={t('workflows.goal_placeholder')}
            />
          </label>

          <div className="wfs-field">
            <span>{t('workflows.step_success')}</span>
            {postList.map((spec, index) => (
              <div className="wf-kv__row" key={`${spec}-${index}`} style={{ marginBottom: 6 }}>
                <Input
                  value={spec}
                  placeholder={t('workflows.success_placeholder')}
                  onChange={(e) => {
                    const next = [...postList];
                    next[index] = e.target.value;
                    onPatch({ post: next });
                  }}
                  style={{ gridColumn: 'span 2' }}
                />
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => onPatch({ post: postList.filter((_, i) => i !== index) })}
                >
                  <Trash2 size={13} />
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => onPatch({ post: [...postList, ''] })}>
              <Plus size={13} />
              {t('workflows.add_success')}
            </Button>
          </div>

          {actionsFor(kind).length > 0 ? (
            <>
              <label className="wfs-field">
                <span>{t('workflows.step_action')}</span>
                <select
                  className="input"
                  value={selected.do ?? ''}
                  onChange={(e) => {
                    // Switching action drops params the new action doesn't accept
                    // (keeps any that are shared) so stale keys can't linger.
                    const nextDo = e.target.value;
                    const def = actionDef(kind, nextDo);
                    const valid = new Set((def?.fields ?? []).map((f) => f.key));
                    onPatch({
                      do: nextDo,
                      params: Object.fromEntries(Object.entries(params).filter(([k]) => valid.has(k))),
                    });
                  }}
                >
                  <option value="">{t('workflows.step_action_choose')}</option>
                  {actionsFor(kind).map((a) => (
                    <option key={a.action} value={a.action}>
                      {t(a.labelKey)}
                    </option>
                  ))}
                  {selected.do && !actionDef(kind, selected.do) ? (
                    <option value={selected.do}>{`${t('workflows.action_custom')}: ${selected.do}`}</option>
                  ) : null}
                </select>
              </label>
              {action?.fields.map(field)}
            </>
          ) : null}

          {actionsFor(kind).length === 0 && VALUE_KINDS[kind]
            ? field({ key: '$do', type: VALUE_KINDS[kind]!.type, labelKey: VALUE_KINDS[kind]!.labelKey })
            : null}

          <div className="wfs-divider" />
          <div className="wf-section__title">{t('workflows.section_exec')}</div>
          <label className="wfs-field">
            <span>{t('workflows.mode')}</span>
            <select className="input" value={selected.mode ?? 'auto'} onChange={(e) => onPatch({ mode: e.target.value })}>
              <option value="auto">{t('workflows.mode_auto')}</option>
              <option value="agent">{t('workflows.mode_agent')}</option>
            </select>
          </label>
          <label className="wfs-field">
            <span>{t('workflows.on_error')}</span>
            <select
              className="input"
              value={String((selected.on_error as { then?: string } | undefined)?.then ?? '')}
              onChange={(e) => onPatch({ on_error: e.target.value ? { then: e.target.value } : {} })}
            >
              <option value="">{t('workflows.onerror_default')}</option>
              <option value="agent">{t('workflows.onerror_agent')}</option>
              <option value="human">{t('workflows.onerror_human')}</option>
              <option value="skip">{t('workflows.onerror_skip')}</option>
              <option value="abort">{t('workflows.onerror_abort')}</option>
              <option value="self_heal">{t('workflows.onerror_self_heal')}</option>
            </select>
          </label>
          <label className="wf-checkbox">
            <input
              type="checkbox"
              checked={!!selected.approval}
              onChange={(e) => onPatch({ approval: e.target.checked })}
            />
            <span>{t('workflows.step_approval')}</span>
          </label>
          {kind === 'branch' ? (
            <div className="wf-row" style={{ marginTop: 10 }}>
              <Button variant="outline" size="sm" onClick={() => onAddChild('then')}>
                {t('workflows.add_then')}
              </Button>
              <Button variant="outline" size="sm" onClick={() => onAddChild('else')}>
                {t('workflows.add_else')}
              </Button>
            </div>
          ) : null}
          {kind === 'loop' || kind === 'parallel' ? (
            <Button variant="outline" size="sm" style={{ marginTop: 10 }} onClick={() => onAddChild('body')}>
              {t('workflows.add_body')}
            </Button>
          ) : null}
          <label className="wf-checkbox" style={{ marginTop: 10 }}>
            <input
              type="checkbox"
              checked={!!selected.absolute}
              onChange={(e) => onPatch({ absolute: e.target.checked })}
            />
            <span>
              {t('workflows.badge_absolute')} <HelpIcon text={t('workflows.help_absolute')} />
            </span>
          </label>
        </>
      ) : (
        <>
          {state ? (
            <>
              <div className="wf-section__title">{t('workflows.history_title')}</div>
              <div className="wf-help">
                {t('workflows.history_status')}: {state.status}
                {state.resolved ? '' : ` · ${t('workflows.state_unresolved')}`}
                {state.origin ? ` · ${t('workflows.history_origin')}: ${state.origin}` : ''}
                {state.healed ? ` · ${t('workflows.history_healed')} ×${state.heal_count ?? 1}` : ''}
                {state.takeover ? ` · ${t('workflows.history_takeover')}` : ''}
              </div>
              {state.error ? <div className="wf-help wfs-history__error">{state.error}</div> : null}
              <div className="wfs-divider" />
            </>
          ) : null}
          {/* Control — shown for every node (wiring/condition), plus the
              kind-specific extras below. */}
          <div className="wf-section__title">{t('workflows.section_control')}</div>
          <label className="wfs-field">
            <span>{t('workflows.step_when')}</span>
            <Input value={selected.when ?? ''} onChange={(e) => onPatch({ when: e.target.value })} />
          </label>
          {kind === 'loop' ? (
            <label className="wfs-field">
              <span>{t('workflows.step_foreach')}</span>
              <Input value={selected.foreach ?? ''} onChange={(e) => onPatch({ foreach: e.target.value })} />
            </label>
          ) : null}
          {TIMEOUT_KINDS.has(kind) ? (
            <label className="wfs-field">
              <span>{t('workflows.step_timeout')}</span>
              <Input
                type="number"
                value={String(selected.timeout ?? 30)}
                onChange={(e) => onPatch({ timeout: Number(e.target.value) || 30 })}
              />
            </label>
          ) : null}

          {/* Locator — only meaningful for GUI-driving kinds. */}
          {LOCATOR_KINDS.has(kind) ? (
            <>
              <div className="wfs-divider" />
              <div className="wf-section__title">{t('workflows.section_locator')}</div>
              <label className="wfs-field">
                <span>role</span>
                <Input
                  value={String(locator.role ?? '')}
                  onChange={(e) => onPatch({ locator: { ...locator, role: e.target.value } })}
                />
              </label>
              <label className="wfs-field">
                <span>name</span>
                <Input
                  value={String(locator.name ?? '')}
                  onChange={(e) => onPatch({ locator: { ...locator, name: e.target.value } })}
                />
              </label>
              {kind === 'browser' ? (
                <label className="wfs-field">
                  <span>text</span>
                  <Input
                    value={String(locator.text ?? '')}
                    onChange={(e) => onPatch({ locator: { ...locator, text: e.target.value } })}
                  />
                </label>
              ) : null}
              <label className="wfs-field">
                <span>selector</span>
                <Input
                  value={String(locator.selector ?? '')}
                  onChange={(e) => onPatch({ locator: { ...locator, selector: e.target.value } })}
                />
              </label>
              <label className="wfs-field">
                <span>{t('workflows.locator_fallback')}</span>
                <LocatorFallbackEditor
                  fallback={locator.fallback}
                  onChange={(next) => onPatch({ locator: { ...locator, fallback: next } })}
                />
              </label>
            </>
          ) : null}

          {outputsFor(kind, selected.do ?? '').length > 0 ? (
            <div className="wf-help" style={{ marginTop: 10 }}>
              {t('workflows.outputs')}:{' '}
              {outputsFor(kind, selected.do ?? '')
                .map((o) => `{{steps.${selected.id}.${o}}}`)
                .join('  ')}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

/** Edit the locator fallback ladder as JSON (applied on blur). */
function LocatorFallbackEditor({
  fallback,
  onChange,
}: {
  fallback: unknown;
  onChange: (next: unknown[]) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(fallback ?? [], null, 0));
  useEffect(() => {
    setText(JSON.stringify(fallback ?? [], null, 0));
  }, [fallback]);
  return (
    <Textarea
      rows={3}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        try {
          const parsed = JSON.parse(text || '[]');
          onChange(Array.isArray(parsed) ? parsed : []);
        } catch {
          /* keep invalid text until the user fixes it */
        }
      }}
      placeholder='[{"role":"button","name":"..."}]'
    />
  );
}

// ── Command palette ───────────────────────────────────────────────────
export function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean;
  commands: StudioCommand[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  if (!open) return null;
  const filtered = commands.filter((c) => c.label.toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <div className="wfs-cmdk-backdrop" onMouseDown={onClose}>
      <div className="wfs-cmdk" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          className="wfs-cmdk__input"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          placeholder={t('workflows.cmdk_placeholder')}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            else if (e.key === 'ArrowDown') setActive((i) => Math.min(i + 1, filtered.length - 1));
            else if (e.key === 'ArrowUp') setActive((i) => Math.max(i - 1, 0));
            else if (e.key === 'Enter' && filtered[active]) {
              filtered[active]!.run();
              onClose();
            }
          }}
        />
        <div className="wfs-cmdk__list">
          {filtered.length === 0 ? (
            <p className="wfs-empty-note">{t('workflows.cmdk_empty')}</p>
          ) : (
            filtered.map((c, index) => {
              const Icon = c.icon;
              return (
                <button
                  key={c.id}
                  type="button"
                  className={`wfs-cmdk__item${index === active ? ' wfs-cmdk__item--active' : ''}`}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => {
                    c.run();
                    onClose();
                  }}
                >
                  {Icon ? <Icon size={14} /> : <Search size={14} />}
                  <span>{c.label}</span>
                  {c.hint ? <span className="wfs-cmdk__item-hint">{c.hint}</span> : null}
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

// ── Open dialog ───────────────────────────────────────────────────────
export function OpenDialog({
  open,
  workflows,
  onClose,
  onPick,
}: {
  open: boolean;
  workflows: WorkflowEntry[];
  onClose: () => void;
  onPick: (entry: WorkflowEntry) => void;
}) {
  const [query, setQuery] = useState('');
  if (!open) return null;
  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? workflows.filter((w) => w.name.toLowerCase().includes(needle) || w.description.toLowerCase().includes(needle))
    : workflows;
  return (
    <div className="wfs-open-backdrop" onMouseDown={onClose}>
      <div className="wfs-open" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          className="wfs-cmdk__input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('workflows.search_placeholder')}
        />
        <div className="wfs-open__list">
          {filtered.length === 0 ? (
            <p className="wfs-empty-note">{t('workflows.no_match')}</p>
          ) : (
            filtered.map((w) => (
              <button key={w.name} type="button" className="wfs-open__item" onClick={() => onPick(w)}>
                <div style={{ minWidth: 0 }}>
                  <div className="wfs-open__name">{w.name}</div>
                  <div className="wfs-open__desc">{w.description}</div>
                </div>
                <span className="wfs-open__meta">
                  v{w.version} · {w.step_count} {t('workflows.steps')}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// ── Start screen (new workflow) ───────────────────────────────────────
export function StartScreen({
  recent,
  onBlank,
  onOpen,
  onImport,
  onOpenWorkflow,
}: {
  recent: WorkflowEntry[];
  onBlank: () => void;
  onOpen: () => void;
  onImport: () => void;
  onOpenWorkflow: (entry: WorkflowEntry) => void;
}) {
  return (
    <div className="wfs-start">
      <div className="wfs-start__title">{t('workflows.studio_new_title')}</div>
      <p className="wfs-start__sub">{t('workflows.studio_new_sub')}</p>
      <div className="wfs-start__actions">
        <Button variant="primary" onClick={onBlank}>
          <Plus size={14} /> {t('workflows.studio_blank')}
        </Button>
        <Button variant="secondary" onClick={onOpen}>
          {t('workflows.open')}
        </Button>
        <Button variant="secondary" onClick={onImport}>
          {t('workflows.import')}
        </Button>
      </div>
      {recent.length > 0 ? (
        <>
          <div className="wf-section__title" style={{ marginTop: 20 }}>
            {t('workflows.recently_opened')}
          </div>
          <div className="wfs-start__actions">
            {recent.map((entry) => (
              <div className="wfs-start__item" key={entry.name}>
                <button
                  type="button"
                  className="wfs-start__card"
                  onClick={() => onOpenWorkflow(entry)}
                >
                  <span className="wfs-start__card-name">{entry.name}</span>
                  <span className="wfs-start__card-desc">{entry.description}</span>
                </button>
                <span className="wfs-start__card-date">
                  {t('workflows.updated_at')}: {formatDate(entry.updated_at)}
                </span>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

/** Locale-aware short date/time for workflow cards. */
function formatDate(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ── Name prompt (Save As) ─────────────────────────────────────────────
export function NamePrompt({
  open,
  title,
  initial,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  initial: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    if (open) setValue(initial);
  }, [open, initial]);
  if (!open) return null;
  return (
    <div className="wfs-open-backdrop" onMouseDown={onCancel}>
      <div className="wfs-open" style={{ maxHeight: 'unset' }} onMouseDown={(e) => e.stopPropagation()}>
        <div style={{ padding: 16, display: 'grid', gap: 12 }}>
          <div className="wf-section__title">{title}</div>
          <input
            autoFocus
            className="wfs-search"
            style={{ margin: 0, height: 34 }}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && value.trim()) onConfirm(value.trim());
              if (e.key === 'Escape') onCancel();
            }}
          />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Button variant="ghost" size="sm" onClick={onCancel}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" disabled={!value.trim()} onClick={() => onConfirm(value.trim())}>
              {confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Re-export the chevrons so the orchestrator can use them without extra imports.
export const Chevrons = { ChevronDown, ChevronRight };
