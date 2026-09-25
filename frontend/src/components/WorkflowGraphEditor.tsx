import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Boxes,
  Brain,
  Globe,
  GitBranch,
  LayoutGrid,
  Loader2,
  Monitor,
  Pin,
  Plus,
  Repeat,
  Save,
  ShieldCheck,
  Sparkles,
  Terminal,
  Timer,
  Trash2,
  UserCheck,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { WorkflowStep } from '../types';

const NODE_W = 214;
const NODE_H = 58;

type Family = 'action' | 'control' | 'verify' | 'agent' | 'human';
const FAMILY_VAR: Record<Family, string> = {
  action: 'var(--info)',
  control: 'var(--warning)',
  verify: 'var(--success)',
  agent: 'var(--accent)',
  human: 'var(--warning)',
};

const KIND_META: Record<string, { family: Family; icon: LucideIcon }> = {
  command: { family: 'action', icon: Terminal },
  tool: { family: 'action', icon: Wrench },
  browser: { family: 'action', icon: Globe },
  app: { family: 'action', icon: Monitor },
  computer: { family: 'action', icon: Monitor },
  skill: { family: 'agent', icon: Brain },
  agentic: { family: 'agent', icon: Bot },
  human: { family: 'human', icon: UserCheck },
  set: { family: 'verify', icon: Pin },
  assert: { family: 'verify', icon: ShieldCheck },
  wait: { family: 'verify', icon: Timer },
  branch: { family: 'control', icon: GitBranch },
  loop: { family: 'control', icon: Repeat },
  parallel: { family: 'control', icon: LayoutGrid },
  subworkflow: { family: 'control', icon: Boxes },
};

const KIND_OPTIONS = Object.keys(KIND_META);
const ACTION_KINDS = new Set(['command', 'tool', 'browser', 'app', 'computer', 'skill', 'human', 'agentic']);
const DO_KINDS = new Set([...ACTION_KINDS, 'subworkflow', 'assert']);
const PARAM_KINDS = new Set([...ACTION_KINDS, 'set', 'wait', 'subworkflow']);
const LOCATOR_KINDS = new Set(['browser', 'app', 'computer']);

const kindFamily = (kind: string): Family => KIND_META[kind]?.family ?? 'action';
const kindStripe = (kind: string): string => FAMILY_VAR[kindFamily(kind)];

// ── step tree helpers ───────────────────────────────────────────────────
type PathPart = number | 'then' | 'else' | 'body';
type StepPath = PathPart[];
const SLOTS: Array<'then' | 'else' | 'body'> = ['then', 'else', 'body'];

function pathKey(path: StepPath): string {
  return path.join('.');
}
function parsePath(key: string): StepPath {
  return key.split('.').map((tok) => (tok === 'then' || tok === 'else' || tok === 'body' ? tok : Number(tok))) as StepPath;
}
function getList(steps: WorkflowStep[], listPath: StepPath): WorkflowStep[] {
  let list = steps;
  let node: WorkflowStep | undefined;
  for (const tok of listPath) {
    if (typeof tok === 'number') node = list[tok];
    else list = (node?.[tok] as WorkflowStep[] | undefined) ?? [];
  }
  return list;
}
function locate(steps: WorkflowStep[], path: StepPath): WorkflowStep | null {
  let list = steps;
  let node: WorkflowStep | undefined;
  for (const tok of path) {
    if (typeof tok === 'number') node = list[tok];
    else list = (node?.[tok] as WorkflowStep[] | undefined) ?? [];
  }
  return node ?? null;
}
function updateLeaf(steps: WorkflowStep[], path: StepPath, patch: Partial<WorkflowStep>): WorkflowStep[] {
  const [head, ...rest] = path;
  if (typeof head !== 'number') return steps;
  const copy = [...steps];
  const node = copy[head];
  if (!node) return steps;
  if (rest.length === 0) {
    copy[head] = { ...node, ...patch };
    return copy;
  }
  const slot = rest[0] as 'then' | 'else' | 'body';
  copy[head] = { ...node, [slot]: updateLeaf((node[slot] as WorkflowStep[]) ?? [], rest.slice(1), patch) };
  return copy;
}
function removeLeaf(steps: WorkflowStep[], path: StepPath): WorkflowStep[] {
  const [head, ...rest] = path;
  if (typeof head !== 'number') return steps;
  const copy = [...steps];
  if (rest.length === 0) {
    copy.splice(head, 1);
    return copy;
  }
  const slot = rest[0] as 'then' | 'else' | 'body';
  const node = copy[head];
  if (!node) return steps;
  copy[head] = { ...node, [slot]: removeLeaf((node[slot] as WorkflowStep[]) ?? [], rest.slice(1)) };
  return copy;
}
function setList(steps: WorkflowStep[], listPath: StepPath, next: WorkflowStep[]): WorkflowStep[] {
  if (listPath.length === 0) return next;
  const slot = listPath[listPath.length - 1] as 'then' | 'else' | 'body';
  return updateLeaf(steps, listPath.slice(0, -1), { [slot]: next } as Partial<WorkflowStep>);
}
function defaultStep(index: number): WorkflowStep {
  return { id: `step${index + 1}`, kind: 'tool', do: '', params: {}, mode: 'auto' };
}

/** Validate the whole tree; returns human-readable errors with a step path. */
function validateTree(steps: WorkflowStep[]): string[] {
  const errors: string[] = [];
  const ids = new Set<string>();
  const walk = (list: WorkflowStep[], scope: string) => {
    list.forEach((step, index) => {
      const at = `${scope}/${step.id || `#${index + 1}`}`;
      if (!step.id) errors.push(`${at}: id is required`);
      else if (ids.has(step.id)) errors.push(`${at}: duplicate id '${step.id}'`);
      else ids.add(step.id);
      if (!KIND_META[step.kind]) errors.push(`${at}: unknown kind '${step.kind}'`);
      if (step.kind === 'branch' && (!step.when || !(step.then ?? []).length)) errors.push(`${at}: branch needs a condition and 'then' steps`);
      if (step.kind === 'loop' && !(step.body ?? []).length) errors.push(`${at}: loop needs body steps`);
      if (step.kind === 'parallel' && !(step.body ?? []).length) errors.push(`${at}: parallel needs body steps`);
      SLOTS.forEach((slot) => {
        const children = step[slot] as WorkflowStep[] | undefined;
        if (children?.length) walk(children, `${at}.${slot}`);
      });
    });
  };
  walk(steps, '');
  return errors;
}

// ── params key/value ────────────────────────────────────────────────────
interface KVRow {
  key: string;
  value: string;
}
function paramsToRows(params: Record<string, unknown> | undefined): KVRow[] {
  return Object.entries(params ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}
function rowsToParams(rows: KVRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    if (!row.key.trim()) continue;
    try {
      out[row.key] = JSON.parse(row.value);
    } catch {
      out[row.key] = row.value;
    }
  }
  return out;
}

interface StepNodeData extends Record<string, unknown> {
  step: WorkflowStep;
  slot?: string;
  status?: string;
}

function StepNode({ data, selected }: NodeProps) {
  const d = data as StepNodeData;
  const step = d.step;
  const meta = KIND_META[step.kind] ?? { family: 'action' as Family, icon: Wrench };
  const Icon = meta.icon;
  const badge = d.status;
  return (
    <div
      className={`wf-node${selected ? ' wf-node--selected' : ''}${badge ? ` wf-node--${badge}` : ''}`}
      style={{ ['--wf-stripe' as string]: kindStripe(step.kind) }}
    >
      <Handle type="target" position={Position.Top} className="wf-handle" />
      <div className="wf-node__head">
        <span className="wf-node__icon">
          <Icon size={14} />
        </span>
        <span className="wf-node__id">{step.id}</span>
        {badge ? <span className={`settings-chip wf-node__status settings-chip--${badge === 'failed' ? 'bad' : badge === 'ok' ? 'ok' : 'dim'}`}>{badge}</span> : null}
      </div>
      <div className="wf-node__meta">
        {d.slot ? `${d.slot} · ` : ''}
        {step.kind}
        {step.do ? ` · ${step.do}` : ''}
      </div>
      <div className="wf-node__badges">
        {step.mode === 'agent' ? <span className="wf-badge wf-badge--agent">agent</span> : null}
        {step.approval ? <span className="wf-badge wf-badge--human">approval</span> : null}
        {step.on_error && (step.on_error as { then?: string }).then ? <span className="wf-badge">on_error: {(step.on_error as { then?: string }).then}</span> : null}
        {(step.success ?? []).length > 0 ? <span className="wf-badge">success</span> : null}
      </div>
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

const nodeTypes = { step: StepNode };

function buildGraph(steps: WorkflowStep[], status: Record<string, string>): { nodes: Node<StepNodeData>[]; edges: Edge[] } {
  const rawNodes: Node<StepNodeData>[] = [];
  const rawEdges: Edge[] = [];
  const walk = (list: WorkflowStep[], listPath: StepPath, parentId?: string, slot?: string) => {
    list.forEach((step, index) => {
      const path: StepPath = [...listPath, index];
      const id = pathKey(path);
      rawNodes.push({
        id,
        type: 'step',
        position: { x: 0, y: 0 },
        data: { step, ...(slot ? { slot } : {}), ...(status[step.id] ? { status: status[step.id] } : {}) },
      });
      if (parentId) {
        rawEdges.push({ id: `e-${parentId}-${id}`, source: parentId, target: id, label: slot, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
      }
      SLOTS.forEach((s) => {
        const children = step[s] as WorkflowStep[] | undefined;
        if (children?.length) walk(children, [...path, s], id, s);
      });
    });
    for (let i = 0; i < list.length - 1; i += 1) {
      rawEdges.push({ id: `seq-${pathKey([...listPath, i])}-${pathKey([...listPath, i + 1])}`, source: pathKey([...listPath, i]), target: pathKey([...listPath, i + 1]), type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
    }
  };
  walk(steps, []);
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  rawNodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  rawEdges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const nodes = rawNodes.map((n) => {
    const pos = g.node(n.id);
    return { ...n, position: { x: (pos?.x ?? 0) - NODE_W / 2, y: (pos?.y ?? 0) - NODE_H / 2 } };
  });
  return { nodes, edges: rawEdges };
}

export interface GraphEditorTarget {
  name: string;
  description: string;
  version?: number;
  inputs?: unknown;
  steps: WorkflowStep[];
  isNew: boolean;
}

interface Props {
  target: GraphEditorTarget | null;
  onClose: () => void;
  onSaved: () => void;
}

export function WorkflowGraphEditor({ target, onClose, onSaved }: Props) {
  const [steps, setSteps] = useState<WorkflowStep[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<StepNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [baseline, setBaseline] = useState('');
  const [paramsJson, setParamsJson] = useState(false);
  const [fallbackJson, setFallbackJson] = useState(false);

  const applyGraph = useCallback(
    (next: WorkflowStep[], status: Record<string, string> = runStatus) => {
      setSteps(next);
      const built = buildGraph(next, status);
      setNodes(built.nodes);
      setEdges(built.edges);
    },
    [runStatus, setNodes, setEdges],
  );

  useEffect(() => {
    if (!target) return;
    const initial = target.steps.length > 0 ? target.steps : [defaultStep(0)];
    setName(target.name);
    setDescription(target.description);
    setRunStatus({});
    setErrors([]);
    setSelectedId(pathKey([0]));
    setParamsJson(false);
    setBaseline(JSON.stringify({ name: target.name, description: target.description, steps: initial }));
    const built = buildGraph(initial, {});
    setSteps(initial);
    setNodes(built.nodes);
    setEdges(built.edges);
    // Load the latest run's per-step status for the overlay (edit mode only).
    if (!target.isNew && target.name) {
      void (async () => {
        try {
          const runs = await chatService.listWorkflowRuns(target.name);
          const latest = runs.runs[0];
          if (!latest) return;
          const events = await chatService.getRunEvents(latest.run_id);
          const status: Record<string, string> = {};
          for (const ev of events.events) {
            if (!ev.step_id) continue;
            if (ev.type === 'step_start') status[ev.step_id] = 'running';
            else if (ev.type === 'step_end') status[ev.step_id] = ev.status === 'skipped' ? 'skipped' : 'ok';
          }
          setRunStatus(status);
          applyGraph(initial, status);
        } catch {
          /* overlay is best-effort */
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const selectedPath = useMemo<StepPath>(() => (selectedId ? parsePath(selectedId) : []), [selectedId]);
  const selected = useMemo(() => locate(steps, selectedPath), [steps, selectedPath]);
  const validation = useMemo(() => validateTree(steps), [steps]);
  const dirty = useMemo(
    () => JSON.stringify({ name, description, steps }) !== baseline,
    [name, description, steps, baseline],
  );

  const patchSelected = useCallback(
    (patch: Partial<WorkflowStep>) => {
      setSteps((current) => {
        const next = updateLeaf(current, selectedPath, patch);
        setNodes((cur) => cur.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, step: { ...n.data.step, ...patch } } } : n)));
        return next;
      });
    },
    [selectedPath, selectedId, setNodes],
  );

  const rebuild = useCallback(
    (next: WorkflowStep[]) => {
      const built = buildGraph(next, runStatus);
      setSteps(next);
      setNodes(built.nodes);
      setEdges(built.edges);
    },
    [runStatus, setNodes, setEdges],
  );

  const addTopStep = useCallback(() => {
    setSteps((current) => {
      const next = [...current, defaultStep(current.length)];
      rebuild(next);
      setSelectedId(pathKey([next.length - 1]));
      return next;
    });
  }, [rebuild]);

  const addChild = useCallback(
    (slot: 'then' | 'else' | 'body') => {
      if (!selected) return;
      setSteps((current) => {
        const parent = locate(current, selectedPath);
        if (!parent) return current;
        const children = ((parent[slot] as WorkflowStep[]) ?? []).slice();
        children.push(defaultStep(children.length));
        const next = updateLeaf(current, selectedPath, { [slot]: children } as Partial<WorkflowStep>);
        rebuild(next);
        setSelectedId(pathKey([...selectedPath, slot, children.length - 1]));
        return next;
      });
    },
    [selected, selectedPath, rebuild],
  );

  const deleteSelected = useCallback(() => {
    if (selectedPath.length === 0) return;
    setSteps((current) => {
      const next = removeLeaf(current, selectedPath);
      rebuild(next);
      setSelectedId('');
      return next;
    });
  }, [selectedPath, rebuild]);

  const move = useCallback(
    (dir: -1 | 1) => {
      const index = selectedPath[selectedPath.length - 1];
      if (typeof index !== 'number') return;
      const listPath = selectedPath.slice(0, -1);
      setSteps((current) => {
        const list = getList(current, listPath).slice();
        const target2 = index + dir;
        if (target2 < 0 || target2 >= list.length) return current;
        const [item] = list.splice(index, 1);
        if (item) list.splice(target2, 0, item);
        const next = setList(current, listPath, list);
        rebuild(next);
        setSelectedId(pathKey([...listPath, target2]));
        return next;
      });
    },
    [selectedPath, rebuild],
  );

  const relayout = useCallback(() => rebuild(steps), [steps, rebuild]);

  // onConnect: rewiring sibling nodes adjusts their shared list order.
  const onConnect = useCallback(
    (connection: Connection) => {
      const { source, target: targetId } = connection;
      if (!source || !targetId) return;
      const sourcePath = parsePath(source);
      const targetPath = parsePath(targetId);
      const sourceList = sourcePath.slice(0, -1);
      const targetList = targetPath.slice(0, -1);
      if (pathKey(sourceList) !== pathKey(targetList)) return;
      const sourceIndex = sourcePath[sourcePath.length - 1];
      const targetIndex = targetPath[targetPath.length - 1];
      if (typeof sourceIndex !== 'number' || typeof targetIndex !== 'number') return;
      setSteps((current) => {
        const list = getList(current, sourceList).slice();
        const sourceStep = list[sourceIndex];
        if (!sourceStep) return current;
        const [item] = list.splice(targetIndex, 1);
        if (!item) return current;
        const anchorIndex = list.indexOf(sourceStep);
        list.splice(anchorIndex + 1, 0, item);
        const next = setList(current, sourceList, list);
        rebuild(next);
        return next;
      });
    },
    [rebuild],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = 'source' in connection ? connection.source : '';
      const target2 = 'target' in connection ? connection.target : '';
      if (!source || !target2 || source === target2) return false;
      const sourceList = parsePath(source).slice(0, -1);
      const targetList = parsePath(target2).slice(0, -1);
      return pathKey(sourceList) === pathKey(targetList);
    },
    [],
  );

  const save = useCallback(async () => {
    setErrors([]);
    const problems = validateTree(steps);
    if (problems.length > 0) {
      setErrors(problems.slice(0, 6));
      return;
    }
    if (!name.trim()) {
      setErrors([t('workflows.name_required')]);
      return;
    }
    setBusy(true);
    try {
      const payload = { name: name.trim(), description, version: target?.version ?? 1, inputs: target?.inputs ?? [], steps, triggers: ['manual'] };
      const rendered = await chatService.renderWorkflowSteps(payload);
      if (rendered.errors.length > 0 || !rendered.yaml) {
        setErrors(rendered.errors.length ? rendered.errors : [t('workflows.graph_render_failed')]);
        return;
      }
      if (target?.isNew) {
        const result = await chatService.createWorkflow(rendered.yaml, true);
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
      } else {
        const result = await chatService.updateWorkflow(name.trim(), rendered.yaml);
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
      }
      setBaseline(JSON.stringify({ name: name.trim(), description, steps }));
      onSaved();
      onClose();
    } catch (error) {
      setErrors([translateError(error)]);
    } finally {
      setBusy(false);
    }
  }, [steps, name, description, target, onSaved, onClose]);

  const cancel = useCallback(() => {
    if (dirty && !window.confirm(t('workflows.unsaved_confirm'))) return;
    onClose();
  }, [dirty, onClose]);

  const afterIdChange = useCallback(
    (newId: string) => {
      patchSelected({ id: newId });
    },
    [patchSelected],
  );

  if (!target) return null;

  const canSave = !busy && !!name.trim() && validation.length === 0 && (target.isNew || dirty);
  const params = (selected?.params ?? {}) as Record<string, unknown>;
  const successList = selected?.success ?? [];
  const locator = (selected?.locator ?? {}) as Record<string, unknown>;
  const kind = selected?.kind ?? '';
  const family = kindStripe(kind);

  return (
    <div className="wf-graph">
      {/* Meta card */}
      <div className="wf-meta">
        <div className="wf-meta__row">
          <label className="add-skill-page__field">
            <span>{t('workflows.name')}</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('workflows.name_placeholder')} />
          </label>
          <label className="add-skill-page__field">
            <span>{t('workflows.description')}</span>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('workflows.description')} />
          </label>
        </div>
        <div className="wf-meta__status">
          <span className="settings-chip">v{target.version ?? 1}</span>
          <span className="settings-chip">{steps.length} {t('workflows.steps')}</span>
          {validation.length > 0 ? <span className="settings-chip settings-chip--bad">{validation.length} {t('workflows.errors')}</span> : null}
        </div>
      </div>

      {/* Toolbar */}
      <div className="wf-toolbar">
        <Button variant="secondary" size="sm" onClick={addTopStep}>
          <Plus size={14} />
          {t('workflows.add_step')}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => move(-1)} disabled={!selected}>
          <ArrowUp size={14} />
        </Button>
        <Button variant="ghost" size="sm" onClick={() => move(1)} disabled={!selected}>
          <ArrowDown size={14} />
        </Button>
        <Button variant="ghost" size="sm" onClick={deleteSelected} disabled={!selected}>
          <Trash2 size={14} />
        </Button>
        <Button variant="ghost" size="sm" onClick={relayout}>
          <Sparkles size={14} />
          {t('workflows.auto_layout')}
        </Button>
        <span className="wf-toolbar__spacer" />
        {dirty ? <span className="wf-toolbar__dirty">{t('workflows.unsaved')}</span> : null}
        <Button variant="ghost" size="sm" onClick={cancel} disabled={busy}>
          {t('common.cancel')}
        </Button>
        <Button variant="primary" size="sm" onClick={() => void save()} disabled={!canSave}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          {target.isNew ? t('workflows.create') : t('workflows.save')}
        </Button>
      </div>

      {errors.length > 0 && (
        <div className="add-skill-page__msg add-skill-page__msg--error">
          {errors.map((e) => <div key={e}>{e}</div>)}
        </div>
      )}

      <div className="wf-body">
        <div className="wf-canvas">
          {steps.length === 0 ? (
            <div className="wf-empty">
              <p>{t('workflows.empty_canvas')}</p>
              <Button variant="secondary" size="sm" onClick={addTopStep}>
                <Plus size={14} />
                {t('workflows.add_step')}
              </Button>
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={(_e, node) => setSelectedId(node.id)}
              onConnect={onConnect}
              isValidConnection={isValidConnection}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.2}
              maxZoom={2}
              proOptions={{ hideAttribution: false }}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          )}
        </div>

        {selected && (
          <aside className="wf-inspector">
            {/* Basic */}
            <div className="wf-section">
              <div className="wf-section__title">{t('workflows.section_basic')}</div>
              <label className="add-skill-page__field">
                <span>{t('workflows.step_id')}</span>
                <Input value={selected.id} onChange={(e) => afterIdChange(e.target.value)} />
              </label>
              <label className="add-skill-page__field">
                <span>{t('workflows.step_kind')}</span>
                <select className="input" value={kind} onChange={(e) => patchSelected({ kind: e.target.value })}>
                  {KIND_OPTIONS.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </label>
              {DO_KINDS.has(kind) && (
                <label className="add-skill-page__field">
                  <span>{t('workflows.step_do')}</span>
                  <Input value={selected.do ?? ''} onChange={(e) => patchSelected({ do: e.target.value })} />
                </label>
              )}
              <label className="add-skill-page__field">
                <span>{t('workflows.goal')}</span>
                <Input value={selected.goal ?? ''} onChange={(e) => patchSelected({ goal: e.target.value })} />
              </label>
            </div>

            {/* Execution */}
            <div className="wf-section">
              <div className="wf-section__title">{t('workflows.section_exec')}</div>
              <div className="wf-row">
                <label className="add-skill-page__field">
                  <span>{t('workflows.mode')}</span>
                  <select className="input" value={selected.mode ?? 'auto'} onChange={(e) => patchSelected({ mode: e.target.value })}>
                    <option value="auto">auto</option>
                    <option value="agent">agent</option>
                  </select>
                </label>
                <label className="add-skill-page__field">
                  <span>{t('workflows.on_error')}</span>
                  <select
                    className="input"
                    value={String((selected.on_error as { then?: string } | undefined)?.then ?? '')}
                    onChange={(e) => patchSelected({ on_error: e.target.value ? { then: e.target.value } : {} })}
                  >
                    <option value="">(default)</option>
                    {['agent', 'human', 'skip', 'abort', 'self_heal'].map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </label>
              </div>
              <div className="wf-row">
                <label className="add-skill-page__field">
                  <span>{t('workflows.step_when')}</span>
                  <Input value={selected.when ?? ''} onChange={(e) => patchSelected({ when: e.target.value })} />
                </label>
                <label className="add-skill-page__field">
                  <span>{t('workflows.step_foreach')}</span>
                  <Input value={selected.foreach ?? ''} onChange={(e) => patchSelected({ foreach: e.target.value })} disabled={kind !== 'loop'} />
                </label>
              </div>
              <label className="wf-checkbox">
                <input type="checkbox" checked={!!selected.approval} onChange={(e) => patchSelected({ approval: e.target.checked })} />
                <span>{t('workflows.step_approval')}</span>
              </label>
              {kind === 'branch' && (
                <div className="wf-row">
                  <Button variant="outline" size="sm" onClick={() => addChild('then')}>+ then</Button>
                  <Button variant="outline" size="sm" onClick={() => addChild('else')}>+ else</Button>
                </div>
              )}
              {(kind === 'loop' || kind === 'parallel') && (
                <Button variant="outline" size="sm" onClick={() => addChild('body')}>+ body</Button>
              )}
            </div>

            {/* Params */}
            {PARAM_KINDS.has(kind) && (
              <div className="wf-section">
                <div className="wf-section__title">
                  {t('workflows.step_params')}
                  <Button variant="ghost" size="xs" style={{ float: 'right' }} onClick={() => setParamsJson((v) => !v)}>
                    {paramsJson ? t('workflows.params_kv') : t('workflows.params_json')}
                  </Button>
                </div>
                {paramsJson ? (
                  <textarea
                    className="skills-pending__editor"
                    style={{ minHeight: 100 }}
                    spellCheck={false}
                    value={JSON.stringify(params, null, 2)}
                    onChange={(e) => {
                      try {
                        patchSelected({ params: JSON.parse(e.target.value) });
                      } catch {
                        /* keep typing */
                      }
                    }}
                  />
                ) : (
                  <div className="wf-kv">
                    {paramsToRows(params).map((row, index) => (
                      <div className="wf-kv__row" key={`${row.key}-${index}`}>
                        <Input
                          value={row.key}
                          placeholder="key"
                          onChange={(e) => {
                            const rows = paramsToRows(params);
                            rows[index] = { ...rows[index], key: e.target.value } as KVRow;
                            patchSelected({ params: rowsToParams(rows) });
                          }}
                        />
                        <Input
                          value={row.value}
                          placeholder="value"
                          onChange={(e) => {
                            const rows = paramsToRows(params);
                            rows[index] = { ...rows[index], value: e.target.value } as KVRow;
                            patchSelected({ params: rowsToParams(rows) });
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => {
                            const rows = paramsToRows(params);
                            rows.splice(index, 1);
                            patchSelected({ params: rowsToParams(rows) });
                          }}
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    ))}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        const rows = paramsToRows(params);
                        rows.push({ key: '', value: '' });
                        patchSelected({ params: rowsToParams(rows) });
                      }}
                    >
                      <Plus size={13} />
                      {t('workflows.add_param')}
                    </Button>
                  </div>
                )}
              </div>
            )}

            {/* Locator */}
            {LOCATOR_KINDS.has(kind) && (
              <div className="wf-section">
                <div className="wf-section__title">{t('workflows.section_locator')}</div>
                <div className="wf-row">
                  <label className="add-skill-page__field">
                    <span>role</span>
                    <Input value={String(locator.role ?? '')} onChange={(e) => patchSelected({ locator: { ...locator, role: e.target.value } })} />
                  </label>
                  <label className="add-skill-page__field">
                    <span>name</span>
                    <Input value={String(locator.name ?? '')} onChange={(e) => patchSelected({ locator: { ...locator, name: e.target.value } })} />
                  </label>
                </div>
                <label className="add-skill-page__field">
                  <span>selector</span>
                  <Input value={String(locator.selector ?? '')} onChange={(e) => patchSelected({ locator: { ...locator, selector: e.target.value } })} />
                </label>
                <Button variant="ghost" size="xs" onClick={() => setFallbackJson((v) => !v)}>
                  {fallbackJson ? t('workflows.hide_advanced') : t('workflows.show_advanced')}
                </Button>
                {fallbackJson && (
                  <textarea
                    className="skills-pending__editor"
                    style={{ minHeight: 70 }}
                    spellCheck={false}
                    value={JSON.stringify(locator.fallback ?? [], null, 2)}
                    onChange={(e) => {
                      try {
                        patchSelected({ locator: { ...locator, fallback: JSON.parse(e.target.value) } });
                      } catch {
                        /* keep typing */
                      }
                    }}
                  />
                )}
              </div>
            )}

            {/* Success checks */}
            <div className="wf-section">
              <div className="wf-section__title">{t('workflows.step_success')}</div>
              {successList.map((spec, index) => (
                <div className="wf-kv__row" key={`${spec}-${index}`}>
                  <Input
                    value={spec}
                    placeholder={t('workflows.success_placeholder')}
                    onChange={(e) => {
                      const next = [...successList];
                      next[index] = e.target.value;
                      patchSelected({ success: next });
                    }}
                    style={{ gridColumn: 'span 2' }}
                  />
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => patchSelected({ success: successList.filter((_, i) => i !== index) })}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              ))}
              <Button variant="outline" size="sm" onClick={() => patchSelected({ success: [...successList, ''] })}>
                <Plus size={13} />
                {t('workflows.add_success')}
              </Button>
            </div>

            <div className="wf-section">
              <div style={{ display: 'flex', gap: 6 }}>
                <span className="settings-chip" style={{ color: family }}>{kindFamily(kind)}</span>
                <span className="settings-chip">{selectedPath.join(' › ')}</span>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
