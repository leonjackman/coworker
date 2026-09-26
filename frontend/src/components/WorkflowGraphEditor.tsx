import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Frame,
  Loader2,
  Lock,
  Map as MapIcon,
  Maximize2,
  Minus,
  Plus,
  Redo2,
  Save,
  Sparkles,
  Spline,
  Trash2,
  Undo2,
  Unlock,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { t, translateError } from '../lib/i18n';
import { isEditableTarget } from '../lib/dom';
import { chatService } from '../services/chatService';
import { buildGraph, collectStepGraph, flowEndpoints, layoutGraph, nodeTypes, renumberWorkflowSteps } from './workflows/flowGraph';
import { WorkflowMiniMap } from './workflows/WorkflowMiniMap';
import { edgeTypes } from './workflows/EditableEdge';
import { DO_KINDS, DO_SUGGESTIONS, KIND_GROUPS, KIND_META, LOCATOR_KINDS, PARAM_KINDS, kindLabelKey } from './workflows/kinds';
import type { WorkflowStep, WorkflowVersion } from '../types';


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
function collectAllIds(steps: WorkflowStep[], out: Set<string> = new Set()): Set<string> {
  for (const step of steps) {
    out.add(step.id);
    for (const slot of SLOTS) {
      const kids = step[slot] as WorkflowStep[] | undefined;
      if (kids?.length) collectAllIds(kids, out);
    }
  }
  return out;
}

/** Next system id in the ``id:N`` scheme (scans the whole tree). */
function newStep(steps: WorkflowStep[]): WorkflowStep {
  let max = 0;
  for (const id of collectAllIds(steps)) {
    const match = /^id:(\d+)$/.exec(id);
    if (match) max = Math.max(max, Number(match[1]));
  }
  return { id: `id:${max + 1}`, kind: 'tool', do: '', params: {}, mode: 'auto' };
}

/**
 * Materialise an explicit ``next`` chain ONLY when the list isn't wired yet.
 * Once wired (any step has ``next``), existing links are preserved so adding a
 * node or connecting/disconnecting one edge never rewires the others.
 */
function wireList(list: WorkflowStep[]): WorkflowStep[] {
  if (list.some((step) => step.next)) return list;
  return list.map((step, index) => ({
    ...step,
    next: index + 1 < list.length ? list[index + 1]!.id : '',
  }));
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
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [baseline, setBaseline] = useState('');
  const [paramsJson, setParamsJson] = useState(false);
  const [paramRows, setParamRows] = useState<KVRow[]>([]);
  const [fallbackJson, setFallbackJson] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number>(1);
  const [versionBusy, setVersionBusy] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [inlineFullscreen, setInlineFullscreen] = useState(false);
  const [edgeType, setEdgeType] = useState<'smoothstep' | 'straight' | 'default'>('smoothstep');
  const [showMinimap, setShowMinimap] = useState(true);
  const [interactive, setInteractive] = useState(true);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const rfRef = useRef<{
    zoomIn: () => void;
    zoomOut: () => void;
    fitView: () => void;
    getViewport: () => { x: number; y: number; zoom: number };
    setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => void;
  } | null>(null);
  const reconnectHandledRef = useRef(false);
  const edgesRef = useRef<Edge[]>([]);
  const deleteEdgeRef = useRef<(id: string) => void>(() => {});
  // Stored node positions keyed by stable node id (survive connect/reorder).
  const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  // Stable node id -> step path, for locating steps from edge endpoints.
  const idToPathRef = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);
  const decorateEdges = useCallback(
    (list: Edge[]) =>
      list.map((e) => ({
        ...e,
        type: 'editable',
        data: {
          ...e.data,
          edgeType,
          // Endpoint (input/output) edges are structural — no disconnect button.
          ...(e.id.startsWith('endpoint-') ? {} : { onDelete: (id: string) => deleteEdgeRef.current(id) }),
        },
      })),
    [edgeType],
  );

  // Undo/redo history (max 50 snapshots). Cleared after save.
  const historyRef = useRef<{ snaps: string[]; index: number }>({ snaps: [], index: -1 });
  const suspendHistoryRef = useRef(false);
  const snapshotOf = (s: WorkflowStep[], n: string, d: string) => JSON.stringify({ s, n, d });
  const syncHistoryFlags = useCallback(() => {
    const h = historyRef.current;
    setCanUndo(h.index > 0);
    setCanRedo(h.index >= 0 && h.index < h.snaps.length - 1);
  }, []);
  const resetHistory = useCallback(
    (s: WorkflowStep[], n: string, d: string) => {
      suspendHistoryRef.current = true;
      historyRef.current = { snaps: [snapshotOf(s, n, d)], index: 0 };
      syncHistoryFlags();
    },
    [syncHistoryFlags],
  );

  // Build the graph including the input/output endpoint (pill) nodes.
  const withEndpoints = useCallback(
    (next: WorkflowStep[], status: Record<string, string>, positions: Map<string, { x: number; y: number }>) => {
      const built = buildGraph(next, status, positions);
      const ys = built.nodes.map((n) => n.position.y);
      const minY = ys.length ? Math.min(...ys) : 0;
      const maxY = ys.length ? Math.max(...ys) : 0;
      if (!positions.has('__input__')) positions.set('__input__', { x: 40, y: minY - 130 });
      if (!positions.has('__output__')) positions.set('__output__', { x: 40, y: maxY + 130 });
      const nodes = [
        { id: '__input__', type: 'pill', position: positions.get('__input__')!, data: { label: t('workflows.trigger_node'), kind: 'trigger' } },
        ...built.nodes,
        { id: '__output__', type: 'pill', position: positions.get('__output__')!, data: { label: t('workflows.output_node'), kind: 'output' } },
      ];
      const marker = { type: MarkerType.ArrowClosed } as const;
      const edges = [...built.edges];
      const { inputTarget, outputSource } = flowEndpoints(next);
      if (inputTarget) edges.unshift({ id: 'endpoint-in', source: '__input__', target: inputTarget, type: 'smoothstep', markerEnd: marker });
      if (outputSource) edges.push({ id: 'endpoint-out', source: outputSource, target: '__output__', type: 'smoothstep', markerEnd: marker });
      return { nodes, edges, nodeIdToPath: built.nodeIdToPath };
    },
    [],
  );

  const applyGraph = useCallback(
    (next: WorkflowStep[], status: Record<string, string> = runStatus) => {
      setSteps(next);
      const built = withEndpoints(next, status, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      setNodes(built.nodes);
      setEdges(decorateEdges(built.edges));
    },
    [runStatus, setNodes, setEdges, decorateEdges, withEndpoints],
  );

  const rebuild = useCallback(
    (next: WorkflowStep[]) => {
      const built = withEndpoints(next, runStatus, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      setSteps(next);
      setNodes(built.nodes);
      setEdges(decorateEdges(built.edges));
    },
    [runStatus, setNodes, setEdges, decorateEdges, withEndpoints],
  );

  const EDGE_LABEL: Record<string, string> = {
    smoothstep: 'workflows.edge_right_angle',
    straight: 'workflows.edge_straight',
    default: 'workflows.edge_curve',
  };
  const cycleEdgeType = useCallback(() => {
    setEdgeType((current) => {
      const order = ['smoothstep', 'straight', 'default'] as const;
      const nextType = order[(order.indexOf(current) + 1) % order.length] as (typeof order)[number];
      setEdges((cur) => cur.map((e) => ({ ...e, data: { ...e.data, edgeType: nextType } })));
      return nextType;
    });
  }, [setEdges]);

  // Toggle full-screen editing inside the main window.
  const toggleFullscreen = useCallback(() => setInlineFullscreen((v) => !v), []);

  useEffect(() => {
    if (!target) return;
    const initial = target.steps.length > 0 ? target.steps : [newStep([])];
    setName(target.name);
    setDescription(target.description);
    setRunStatus({});
    setErrors([]);
    setSelectedId(pathKey([0]));
    setParamsJson(false);
    setBaseline(JSON.stringify({ name: target.name, description: target.description, steps: initial }));
    setSelectedVersion(target.version ?? 1);
    resetHistory(initial, target.name, target.description);
    // Seed initial positions with one dagre pass; afterwards positions are
    // preserved and only change on explicit "auto layout" or drag.
    const collected = collectStepGraph(initial, {});
    const laid = layoutGraph(collected.nodes, collected.edges);
    positionsRef.current = new Map(laid.map((n) => [n.id, n.position]));
    const built = withEndpoints(initial, {}, positionsRef.current);
    idToPathRef.current = built.nodeIdToPath;
    setSteps(initial);
    setNodes(built.nodes);
    setEdges(decorateEdges(built.edges));
    // Load versions (edit mode only).
    if (!target.isNew && target.name) {
      void (async () => {
        try {
          const list = await chatService.listWorkflowVersions(target.name);
          setVersions(list.versions);
        } catch {
          /* versions are best-effort */
        }
      })();
    }
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

  // Record every edit into the undo history (coalesced by identical snapshots).
  useEffect(() => {
    if (!target) return;
    if (suspendHistoryRef.current) {
      suspendHistoryRef.current = false;
      return;
    }
    const snap = snapshotOf(steps, name, description);
    const h = historyRef.current;
    if (h.index >= 0 && h.snaps[h.index] === snap) return;
    const trimmed = h.snaps.slice(0, h.index + 1);
    trimmed.push(snap);
    const limited = trimmed.slice(-50);
    historyRef.current = { snaps: limited, index: limited.length - 1 };
    syncHistoryFlags();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, name, description, target]);

  const applySnapshot = useCallback(
    (snap: string) => {
      const parsed = JSON.parse(snap) as { s: WorkflowStep[]; n: string; d: string };
      suspendHistoryRef.current = true;
      setName(parsed.n);
      setDescription(parsed.d);
      rebuild(parsed.s);
      setSelectedId((cur) => (cur && locate(parsed.s, parsePath(cur)) ? cur : ''));
    },
    // rebuild is defined below; referenced lazily via ref-safe closure
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const undo = useCallback(() => {
    const h = historyRef.current;
    if (h.index <= 0) return;
    const idx = h.index - 1;
    historyRef.current = { ...h, index: idx };
    applySnapshot(h.snaps[idx] as string);
    syncHistoryFlags();
  }, [applySnapshot, syncHistoryFlags]);

  const redo = useCallback(() => {
    const h = historyRef.current;
    if (h.index < 0 || h.index >= h.snaps.length - 1) return;
    const idx = h.index + 1;
    historyRef.current = { ...h, index: idx };
    applySnapshot(h.snaps[idx] as string);
    syncHistoryFlags();
  }, [applySnapshot, syncHistoryFlags]);

  useEffect(() => {
    if (!target) return;
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      if (isEditableTarget(event.target)) return;
      event.preventDefault();
      if (key === 'y' || (key === 'z' && event.shiftKey)) redo();
      else undo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [target, undo, redo]);

  // Track the canvas size for the sidebar navigator.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => setCanvasSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [target]);

  const loadVersion = useCallback(
    async (version: number) => {
      if (!target) return;
      setVersionBusy(true);
      try {
        const result = await chatService.getWorkflowVersion(target.name, version);
        const wf = result.workflow;
        const nextSteps = wf.steps ?? [];
        suspendHistoryRef.current = true;
        setName(wf.name);
        setDescription(wf.description);
        rebuild(nextSteps);
        setSelectedId(nextSteps.length > 0 ? pathKey([0]) : '');
        setBaseline(JSON.stringify({ name: wf.name, description: wf.description, steps: nextSteps }));
        historyRef.current = { snaps: [snapshotOf(nextSteps, wf.name, wf.description)], index: 0 };
        syncHistoryFlags();
        setSelectedVersion(version);
        setErrors([]);
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setVersionBusy(false);
      }
    },
    [target, rebuild, syncHistoryFlags],
  );

  const removeVersion = useCallback(
    async (version: number) => {
      if (!target) return;
      setVersionBusy(true);
      try {
        const result = await chatService.deleteWorkflowVersion(target.name, version);
        if (result.status !== 'ok') throw new Error(t('workflows.version_delete_blocked'));
        const list = await chatService.listWorkflowVersions(target.name);
        setVersions(list.versions);
        if (selectedVersion === version) {
          const current = list.versions.find((v) => v.is_current);
          if (current) setSelectedVersion(current.version);
        }
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setVersionBusy(false);
      }
    },
    [target, selectedVersion],
  );

  const selectedPath = useMemo<StepPath>(() => (selectedId ? parsePath(selectedId) : []), [selectedId]);
  const selected = useMemo(() => locate(steps, selectedPath), [steps, selectedPath]);

  // Keep the key/value rows as local state while editing so an empty row can
  // stay visible until the user types a key (params only store non-empty keys).
  useEffect(() => {
    if (paramsJson) return;
    setParamRows(paramsToRows((selected?.params ?? {}) as Record<string, unknown>));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, paramsJson]);
  const validation = useMemo(() => validateTree(steps), [steps]);
  const dirty = useMemo(
    () => JSON.stringify({ name, description, steps }) !== baseline,
    [name, description, steps, baseline],
  );

  const patchSelected = useCallback(
    (patch: Partial<WorkflowStep>) => {
      setSteps((current) => {
        const next = updateLeaf(current, selectedPath, patch);
        setNodes((cur) =>
          cur.map((n) =>
            n.id === selectedId
              ? { ...n, data: { ...n.data, step: { ...(n.data.step as WorkflowStep), ...patch } } }
              : n,
          ),
        );
        return next;
      });
    },
    [selectedPath, selectedId, setNodes],
  );

  const commitParamRows = useCallback(
    (rows: KVRow[]) => {
      setParamRows(rows);
      patchSelected({ params: rowsToParams(rows) });
    },
    [patchSelected],
  );

  // Add an empty, unconnected node (the user wires it up afterwards).
  const addTopStep = useCallback(() => {
    setSteps((current) => {
      const wired = wireList(current);
      const step = newStep(wired);
      const next = [...wired, step];
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
        children.push(newStep(current));
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

  // Auto layout: the ONLY action that repositions nodes.
  const relayout = useCallback(() => {
    const collected = collectStepGraph(steps, runStatus);
    const laid = layoutGraph(collected.nodes, collected.edges);
    positionsRef.current = new Map(laid.map((n) => [n.id, n.position]));
    // Let the endpoint pills be repositioned relative to the new layout.
    positionsRef.current.delete('__input__');
    positionsRef.current.delete('__output__');
    rebuild(steps);
  }, [steps, runStatus, rebuild]);

  const onNodeDragStop = useCallback((_event: unknown, node: Node) => {
    positionsRef.current.set(node.id, node.position);
  }, []);

  // Connect two sibling nodes: set the source's explicit ``next``.
  const connectSiblings = useCallback(
    (source: string, target: string) => {
      const sourcePath = parsePath(source);
      const targetPath = parsePath(target);
      const listPath = sourcePath.slice(0, -1);
      if (pathKey(listPath) !== pathKey(targetPath.slice(0, -1))) return;
      const si = sourcePath[sourcePath.length - 1];
      const ti = targetPath[targetPath.length - 1];
      if (typeof si !== 'number' || typeof ti !== 'number') return;
      setSteps((current) => {
        const list = wireList(getList(current, listPath).slice());
        const sourceStep = list[si];
        const targetStep = list[ti];
        if (!sourceStep || !targetStep) return current;
        // single incoming edge per node: clear any other step pointing at target
        const resolved = list.map((s) =>
          s.next === targetStep.id && s.id !== sourceStep.id ? { ...s, next: '' } : s,
        );
        const idx = resolved.findIndex((s) => s.id === sourceStep.id);
        resolved[idx] = { ...resolved[idx]!, next: targetStep.id };
        const next = setList(current, listPath, resolved);
        rebuild(next);
        return next;
      });
    },
    [rebuild],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const sourcePath = idToPathRef.current.get(connection.source);
      const targetPath = idToPathRef.current.get(connection.target);
      if (sourcePath && targetPath) connectSiblings(sourcePath, targetPath);
    },
    [connectSiblings],
  );

  // Clear a sibling edge (disconnect): drop the source's ``next`` link.
  const detachEdges = useCallback(
    (list: Edge[]) => {
      if (list.length === 0) return;
      setSteps((current) => {
        let next = current;
        for (const edge of list) {
          // Edge endpoints are stable node ids; resolve to step paths.
          const sourcePathStr = idToPathRef.current.get(edge.source);
          const targetPathStr = idToPathRef.current.get(edge.target);
          if (!sourcePathStr || !targetPathStr) continue;
          const sourcePath = parsePath(sourcePathStr);
          const targetPath = parsePath(targetPathStr);
          const listPath = sourcePath.slice(0, -1);
          if (pathKey(listPath) !== pathKey(targetPath.slice(0, -1))) continue; // structural edge
          const stepsInList = getList(next, listPath).slice();
          const ti = targetPath[targetPath.length - 1];
          const targetStep = typeof ti === 'number' ? stepsInList[ti] : undefined;
          const si = sourcePath[sourcePath.length - 1];
          if (!targetStep || typeof si !== 'number' || !stepsInList[si]) continue;
          stepsInList[si] = { ...stepsInList[si]!, next: '' };
          next = setList(next, listPath, stepsInList);
        }
        rebuild(next);
        return next;
      });
    },
    [rebuild],
  );

  const onEdgesDelete = useCallback((deleted: Edge[]) => detachEdges(deleted), [detachEdges]);
  const onReconnectStart = useCallback(() => { reconnectHandledRef.current = false; }, []);
  const onReconnect = useCallback(
    (oldEdge: Edge, connection: Connection) => {
      reconnectHandledRef.current = true;
      onConnect(connection);
    },
    [onConnect],
  );
  const onReconnectEnd = useCallback(
    (_event: unknown, edge: Edge) => {
      if (reconnectHandledRef.current) return;
      detachEdges([edge]);
    },
    [detachEdges],
  );

  // Drag a connection out and release on empty canvas → add an empty node.
  const onConnectEnd = useCallback(
    (_event: unknown, state: { toNode?: unknown; fromNode?: { id: string } | null }) => {
      if (state.toNode || !state.fromNode) return;
      const fromPathStr = idToPathRef.current.get(state.fromNode.id);
      if (!fromPathStr) return;
      const fromPath = parsePath(fromPathStr);
      if (fromPath.length !== 1) return; // top-level only
      const fromIndex = fromPath[0];
      if (typeof fromIndex !== 'number') return;
      setSteps((current) => {
        const wired = wireList(current);
        const step = newStep(wired);
        const next = [...wired, step];
        if (next[fromIndex]) next[fromIndex] = { ...next[fromIndex]!, next: step.id };
        rebuild(next);
        setSelectedId(pathKey([next.length - 1]));
        return next;
      });
    },
    [rebuild],
  );

  // Route the edge ✕ button to detachEdges using the latest edges.
  useEffect(() => {
    deleteEdgeRef.current = (id: string) => {
      const edge = edgesRef.current.find((e) => e.id === id);
      if (edge) detachEdges([edge]);
    };
  }, [detachEdges]);

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = 'source' in connection ? connection.source : '';
      const target2 = 'target' in connection ? connection.target : '';
      if (!source || !target2 || source === target2) return false;
      // Endpoints are stable node ids; only same-list siblings may connect.
      const sourcePath = idToPathRef.current.get(source);
      const targetPath = idToPathRef.current.get(target2);
      if (!sourcePath || !targetPath) return false;
      const sourceList = parsePath(sourcePath).slice(0, -1);
      const targetList = parsePath(targetPath).slice(0, -1);
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
      // Normalise ids to the system scheme (id:1, id:2, …) before saving.
      const finalSteps = renumberWorkflowSteps(steps);
      const payload = { name: name.trim(), description, version: target?.version ?? 1, inputs: target?.inputs ?? [], steps: finalSteps, triggers: ['manual'] };
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
      // Saved state is a new clean point: clear the undo history.
      historyRef.current = { snaps: [snapshotOf(steps, name.trim(), description)], index: 0 };
      syncHistoryFlags();
      if (!target?.isNew && target?.name) {
        try {
          const list = await chatService.listWorkflowVersions(target.name);
          setVersions(list.versions);
          const current = list.versions.find((v) => v.is_current);
          if (current) setSelectedVersion(current.version);
        } catch {
          /* best-effort */
        }
      }
      onSaved();
      onClose();
    } catch (error) {
      setErrors([translateError(error)]);
    } finally {
      setBusy(false);
    }
  }, [steps, name, description, target, onSaved, onClose, syncHistoryFlags]);

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

  const editor = (
    <div className="wf-graph">
      {/* Meta card */}
      <div className="wf-meta">
        <label className="add-skill-page__field">
          <span>{t('workflows.name')}</span>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('workflows.name_placeholder')} />
        </label>
        <label className="add-skill-page__field">
          <span>{t('workflows.description')}</span>
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('workflows.description')}
            rows={2}
          />
        </label>
        <div className="wf-meta__status">
          <span className="settings-chip">v{target.version ?? 1}</span>
          <span className="settings-chip">{steps.length} {t('workflows.steps')}</span>
          {validation.length > 0 ? <span className="settings-chip settings-chip--bad">{validation.length} {t('workflows.errors')}</span> : null}
        </div>
      </div>

      {/* Toolbar */}
      <div className="wf-toolbar">
        <Button variant="ghost" size="sm" onClick={undo} disabled={!canUndo} title={`${t('workflows.undo')} (⌘Z)`}>
          <Undo2 size={14} />
        </Button>
        <Button variant="ghost" size="sm" onClick={redo} disabled={!canRedo} title={`${t('workflows.redo')} (⇧⌘Z)`}>
          <Redo2 size={14} />
        </Button>
        <span className="wf-toolbar__sep" />
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
        <Button variant="ghost" size="sm" onClick={cycleEdgeType} title={t('workflows.edge_style')}>
          <Spline size={14} />
          {t(EDGE_LABEL[edgeType] ?? 'workflows.edge_curve')}
        </Button>
        {!target.isNew && versions.length > 0 ? (
          <>
            <span className="wf-toolbar__sep" />
            <span className="wf-version-picker">
              <select
                className="input"
                value={selectedVersion}
                onChange={(e) => void loadVersion(Number(e.target.value))}
                disabled={versionBusy}
                aria-label={t('workflows.version')}
              >
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}{v.is_current ? ` · ${t('workflows.version_current')}` : ''}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => void removeVersion(selectedVersion)}
                disabled={versionBusy || (versions.find((v) => v.version === selectedVersion)?.is_current ?? true)}
                title={t('workflows.version_delete')}
              >
                <Trash2 size={13} />
              </Button>
            </span>
          </>
        ) : null}
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
        <div className="wf-canvas" ref={canvasRef}>
          {steps.length === 0 ? (
            <div className="wf-empty">
              <p>{t('workflows.empty_canvas')}</p>
              <Button variant="secondary" size="sm" onClick={addTopStep}>
                <Plus size={14} />
                {t('workflows.add_step')}
              </Button>
            </div>
          ) : (
            <>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onInit={(instance) => {
                  rfRef.current = instance as unknown as typeof rfRef.current;
                  setViewport(instance.getViewport());
                }}
                onMove={(_e, vp) => setViewport(vp)}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={(_e, node) => setSelectedId((node.data.path as string) ?? '')}
                onNodeDragStop={onNodeDragStop}
                onConnect={onConnect}
                onConnectEnd={onConnectEnd}
                onEdgesDelete={onEdgesDelete}
                onReconnect={onReconnect}
                onReconnectStart={onReconnectStart}
                onReconnectEnd={onReconnectEnd}
                edgesReconnectable
                nodesDraggable={interactive}
                nodesConnectable={interactive}
                deleteKeyCode={['Backspace', 'Delete']}
                isValidConnection={isValidConnection}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                fitView
                minZoom={0.2}
                maxZoom={2}
                proOptions={{ hideAttribution: true }}
              >
                <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
              </ReactFlow>
              {/* Bottom-left canvas controls (fullscreen is second-to-last) */}
              <div className="wf-controls">
                <button type="button" className="wf-controls__btn" onClick={() => rfRef.current?.zoomIn()} title={t('workflows.zoom_in')}>
                  <Plus size={14} />
                </button>
                <button type="button" className="wf-controls__btn" onClick={() => rfRef.current?.zoomOut()} title={t('workflows.zoom_out')}>
                  <Minus size={14} />
                </button>
                <button type="button" className="wf-controls__btn" onClick={() => rfRef.current?.fitView()} title={t('workflows.fit_view')}>
                  <Frame size={14} />
                </button>
                <button
                  type="button"
                  className={`wf-controls__btn${inlineFullscreen ? ' wf-controls__btn--active' : ''}`}
                  onClick={toggleFullscreen}
                  title={t('workflows.fullscreen')}
                >
                  <Maximize2 size={14} />
                </button>
                <button
                  type="button"
                  className="wf-controls__btn"
                  onClick={() => setInteractive((v) => !v)}
                  title={interactive ? t('workflows.lock_canvas') : t('workflows.unlock_canvas')}
                >
                  {interactive ? <Unlock size={14} /> : <Lock size={14} />}
                </button>
              </div>
            </>
          )}
        </div>

        <aside className="wf-inspector">
            {/* Navigator (collapsible, Photoshop-style) */}
            <div className="wf-section">
              <button type="button" className="wf-adv-toggle wf-minimap-toggle" onClick={() => setShowMinimap((v) => !v)}>
                {showMinimap ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <MapIcon size={13} />
                {t('workflows.minimap')}
              </button>
              {showMinimap ? (
                <WorkflowMiniMap
                  nodes={nodes}
                  viewport={viewport}
                  canvas={canvasSize}
                  onNavigate={(fx, fy) => rfRef.current?.setCenter(fx, fy, { zoom: viewport.zoom, duration: 200 })}
                />
              ) : null}
            </div>

            {selected ? (
            <>
            {/* Step type (friendly picker) */}
            <div className="wf-section">
              <div className="wf-section__title">{t('workflows.section_basic')}</div>
              <label className="add-skill-page__field">
                <span>{t('workflows.step_kind')}</span>
                <select className="input" value={kind} onChange={(e) => patchSelected({ kind: e.target.value })}>
                  {KIND_GROUPS.map((group) => (
                    <optgroup key={group.id} label={t(group.labelKey)}>
                      {group.kinds.map((k) => (
                        <option key={k} value={k}>{t(kindLabelKey(k))}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              {DO_KINDS.has(kind) ? (
                <label className="add-skill-page__field">
                  <span>{t('workflows.step_do')}</span>
                  <Input
                    list="wf-do-options"
                    value={selected.do ?? ''}
                    onChange={(e) => patchSelected({ do: e.target.value })}
                    placeholder={t('workflows.step_do_placeholder')}
                  />
                  <datalist id="wf-do-options">
                    {(DO_SUGGESTIONS[kind] ?? []).map((option) => (
                      <option key={option} value={option} />
                    ))}
                  </datalist>
                </label>
              ) : null}
              <label className="add-skill-page__field">
                <span>{t('workflows.goal')}</span>
                <Input value={selected.goal ?? ''} onChange={(e) => patchSelected({ goal: e.target.value })} placeholder={t('workflows.goal_placeholder')} />
              </label>
            </div>

            {/* How it runs */}
            <div className="wf-section">
              <div className="wf-section__title">{t('workflows.section_exec')}</div>
              <label className="add-skill-page__field">
                <span>{t('workflows.mode')}</span>
                <select className="input" value={selected.mode ?? 'auto'} onChange={(e) => patchSelected({ mode: e.target.value })}>
                  <option value="auto">{t('workflows.mode_auto')}</option>
                  <option value="agent">{t('workflows.mode_agent')}</option>
                </select>
              </label>
              <label className="add-skill-page__field">
                <span>{t('workflows.on_error')}</span>
                <select
                  className="input"
                  value={String((selected.on_error as { then?: string } | undefined)?.then ?? '')}
                  onChange={(e) => patchSelected({ on_error: e.target.value ? { then: e.target.value } : {} })}
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
                <input type="checkbox" checked={!!selected.approval} onChange={(e) => patchSelected({ approval: e.target.checked })} />
                <span>{t('workflows.step_approval')}</span>
              </label>
              {kind === 'branch' ? (
                <div className="wf-row">
                  <Button variant="outline" size="sm" onClick={() => addChild('then')}>{t('workflows.add_then')}</Button>
                  <Button variant="outline" size="sm" onClick={() => addChild('else')}>{t('workflows.add_else')}</Button>
                </div>
              ) : null}
              {kind === 'loop' || kind === 'parallel' ? (
                <Button variant="outline" size="sm" onClick={() => addChild('body')}>{t('workflows.add_body')}</Button>
              ) : null}
            </div>

            {/* Params */}
            {PARAM_KINDS.has(kind) ? (
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
                    {paramRows.map((row, index) => (
                      <div className="wf-kv__row" key={index}>
                        <Input
                          value={row.key}
                          placeholder={t('workflows.param_key')}
                          onChange={(e) => {
                            const rows = [...paramRows];
                            rows[index] = { ...rows[index]!, key: e.target.value };
                            commitParamRows(rows);
                          }}
                        />
                        <Input
                          value={row.value}
                          placeholder={t('workflows.param_value')}
                          onChange={(e) => {
                            const rows = [...paramRows];
                            rows[index] = { ...rows[index]!, value: e.target.value };
                            commitParamRows(rows);
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => commitParamRows(paramRows.filter((_, i) => i !== index))}
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    ))}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setParamRows([...paramRows, { key: '', value: '' }])}
                    >
                      <Plus size={13} />
                      {t('workflows.add_param')}
                    </Button>
                  </div>
                )}
              </div>
            ) : null}

            {/* Advanced (collapsed) */}
            <div className="wf-section">
              <button type="button" className="wf-adv-toggle" onClick={() => setShowAdvanced((v) => !v)}>
                {showAdvanced ? t('workflows.hide_advanced') : t('workflows.show_advanced')}
              </button>
              {showAdvanced ? (
                <>
                  <label className="add-skill-page__field">
                    <span>{t('workflows.step_id')}</span>
                    <Input value={selected.id} onChange={(e) => afterIdChange(e.target.value)} />
                    <span className="wf-help">{t('workflows.step_id_hint')}</span>
                  </label>
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

                  {LOCATOR_KINDS.has(kind) ? (
                    <>
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
                    </>
                  ) : null}

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
                </>
              ) : null}
            </div>
            </>
            ) : null}
          </aside>
      </div>
    </div>
  );

  if (inlineFullscreen) {
    return (
      <div className="wf-fullscreen-overlay">
        <div className="wf-popup-shell">{editor}</div>
        <Button
          className="wf-fullscreen-close"
          variant="secondary"
          size="sm"
          onClick={() => setInlineFullscreen(false)}
          title={t('common.cancel')}
        >
          ✕
        </Button>
      </div>
    );
  }
  return editor;
}
