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
  ArrowLeft,
  Copy,
  FileJson,
  FileText,
  Frame,
  Layers,
  Loader2,
  Lock,
  Map as MapIcon,
  Minus,
  Plus,
  Redo2,
  Save,
  Sparkles,
  Trash2,
  Undo2,
  Unlock,
  X,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '../../ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '../../ui/dropdown-menu';
import { t, translateError } from '../../../lib/i18n';
import { isEditableTarget } from '../../../lib/dom';
import { chatService } from '../../../services/chatService';
import {
  buildGraph,
  collectStepGraph,
  flowEndpoints,
  layoutGraph,
  nodeTypes,
  renumberWorkflowSteps,
} from '../flowGraph';
import { edgeTypes } from '../EditableEdge';
import { WorkflowMiniMap } from '../WorkflowMiniMap';
import { KIND_GROUPS, kindLabelKey } from '../kinds';
import { setCapabilities } from '../actions';
import {
  CommandPalette,
  NamePrompt,
  NodeInspector,
  NodePalette,
  OpenDialog,
  OutlinePanel,
  OutputPanel,
  RunsPanel,
  StartScreen,
  TemplatesPanel,
  VersionsPanel,
  WorkflowInspector,
  type BottomTab,
  type InspectorTab,
  type StudioCommand,
} from './panels';
import {
  getList,
  locate,
  newStep,
  parsePath,
  pathKey,
  removeLeaf,
  setList,
  updateLeaf,
  validateTree,
  wireList,
  type StepPath,
} from './workflowTree';
import { loadStudioSettings, saveStudioSettings, type EdgeStyle } from './studioSettings';
import type {
  WorkflowEntry,
  WorkflowEvidence,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowStep,
  WorkflowTemplate,
  WorkflowVersion,
} from '../../../types';

export interface StudioTarget {
  name: string;
  isNew: boolean;
}

interface Props {
  target: StudioTarget;
  mode?: 'window' | 'inapp';
  onClose: () => void;
  onSaved: () => void;
  openLibrary?: () => void;
}

type LeftTab = 'nodes' | 'outline' | 'versions' | 'templates' | 'runs';

const EDGE_LABEL: Record<string, string> = {
  smoothstep: 'workflows.edge_right_angle',
  straight: 'workflows.edge_straight',
  default: 'workflows.edge_curve',
};

export function WorkflowStudio({ target, mode = 'inapp', onClose, onSaved, openLibrary }: Props) {
  const isWindow = mode === 'window';
  const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.userAgent);

  // Persisted Studio preferences (edge style, minimap, dock layout…). Loaded
  // once so every useState below can seed from the user's last session.
  const initialSettings = useMemo(() => loadStudioSettings(), []);

  // ── document state ──────────────────────────────────────────────────
  const [name, setName] = useState(target.name);
  const [description, setDescription] = useState('');
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [inputs, setInputs] = useState<WorkflowEntry['inputs']>([]);
  const [triggers, setTriggers] = useState<string[]>(['manual']);
  const [outputs, setOutputs] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<WorkflowStep[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [baseline, setBaseline] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [started, setStarted] = useState(!target.isNew);

  // ── graph state ─────────────────────────────────────────────────────
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  const [edgeType, setEdgeType] = useState<EdgeStyle>(initialSettings.edgeType);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [interactive, setInteractive] = useState(initialSettings.interactive);
  const [showMinimap, setShowMinimap] = useState(initialSettings.showMinimap);

  // ── versions / runs / templates ─────────────────────────────────────
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState(1);
  const [versionBusy, setVersionBusy] = useState(false);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [templateBusy, setTemplateBusy] = useState<string | null>(null);
  const [library, setLibrary] = useState<WorkflowEntry[]>([]);

  // ── run/test ────────────────────────────────────────────────────────
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [events, setEvents] = useState<WorkflowRunEvent[]>([]);
  const [evidence, setEvidence] = useState<WorkflowEvidence[]>([]);
  const [inputsJson, setInputsJson] = useState('{}');
  const [runBusy, setRunBusy] = useState(false);

  // ── UI state (seeded from persisted Studio settings) ────────────────
  const [leftOpen, setLeftOpen] = useState(initialSettings.leftOpen);
  const [leftWidth, setLeftWidth] = useState(initialSettings.leftWidth);
  const [leftTab, setLeftTab] = useState<LeftTab>(initialSettings.leftTab as LeftTab);
  const [rightOpen, setRightOpen] = useState(initialSettings.rightOpen);
  const [rightWidth, setRightWidth] = useState(initialSettings.rightWidth);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(initialSettings.inspectorTab as InspectorTab);
  const [bottomOpen, setBottomOpen] = useState(initialSettings.bottomOpen);
  const [bottomTab, setBottomTab] = useState<BottomTab>(initialSettings.bottomTab as BottomTab);
  const [codeMode, setCodeMode] = useState(false);
  const [codeText, setCodeText] = useState('');
  const [yamlPreview, setYamlPreview] = useState('');
  const [cmdkOpen, setCmdkOpen] = useState(false);
  const [openDialog, setOpenDialog] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; path: string } | null>(null);

  // ── refs ────────────────────────────────────────────────────────────
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const rfRef = useRef<{
    zoomIn: () => void;
    zoomOut: () => void;
    fitView: () => void;
    getViewport: () => { x: number; y: number; zoom: number };
    setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => void;
    screenToFlowPosition?: (pos: { x: number; y: number }) => { x: number; y: number };
  } | null>(null);
  const reconnectHandledRef = useRef(false);
  const edgesRef = useRef<Edge[]>([]);
  const deleteEdgeRef = useRef<(id: string) => void>(() => {});
  const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const idToPathRef = useRef<Map<string, string>>(new Map());
  const historyRef = useRef<{ snaps: string[]; index: number }>({ snaps: [], index: -1 });
  const suspendHistoryRef = useRef(false);
  const persistedRef = useRef(!target.isNew);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  const validation = useMemo(() => validateTree(steps), [steps]);
  const dirty = useMemo(
    () => JSON.stringify({ name, description, steps }) !== baseline,
    [name, description, steps, baseline],
  );

  const selectedPath = useMemo<StepPath>(() => (selectedId ? parsePath(selectedId) : []), [selectedId]);
  const selected = useMemo(() => locate(steps, selectedPath), [steps, selectedPath]);

  const snapshotOf = useCallback(
    (s: WorkflowStep[], n: string, d: string) => JSON.stringify({ s, n, d }),
    [],
  );

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
    [snapshotOf, syncHistoryFlags],
  );

  const decorateEdges = useCallback(
    (list: Edge[]) =>
      list.map((e) => ({
        ...e,
        type: 'editable',
        data: {
          ...e.data,
          edgeType,
          ...(e.id.startsWith('endpoint-')
            ? {}
            : { onDelete: (id: string) => deleteEdgeRef.current(id) }),
        },
      })),
    [edgeType],
  );

  const withEndpoints = useCallback(
    (next: WorkflowStep[], status: Record<string, string>, positions: Map<string, { x: number; y: number }>) => {
      const built = buildGraph(next, status, positions);
      const ys = built.nodes.map((n) => n.position.y);
      const minY = ys.length ? Math.min(...ys) : 0;
      const maxY = ys.length ? Math.max(...ys) : 0;
      if (!positions.has('__input__')) positions.set('__input__', { x: 40, y: minY - 130 });
      if (!positions.has('__output__')) positions.set('__output__', { x: 40, y: maxY + 130 });
      const allNodes = [
        {
          id: '__input__',
          type: 'pill',
          position: positions.get('__input__')!,
          data: { label: t('workflows.trigger_node'), kind: 'trigger' },
        },
        ...built.nodes,
        {
          id: '__output__',
          type: 'pill',
          position: positions.get('__output__')!,
          data: { label: t('workflows.output_node'), kind: 'output' },
        },
      ];
      const marker = { type: MarkerType.ArrowClosed } as const;
      const allEdges = [...built.edges];
      const { inputTarget, outputSource } = flowEndpoints(next);
      if (inputTarget)
        allEdges.unshift({ id: 'endpoint-in', source: '__input__', target: inputTarget, type: 'smoothstep', markerEnd: marker });
      if (outputSource)
        allEdges.push({ id: 'endpoint-out', source: outputSource, target: '__output__', type: 'smoothstep', markerEnd: marker });
      return { nodes: allNodes, edges: allEdges, nodeIdToPath: built.nodeIdToPath };
    },
    [],
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

  const applyGraph = useCallback(
    (next: WorkflowStep[], status: Record<string, string>) => {
      const built = withEndpoints(next, status, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      setSteps(next);
      setNodes(built.nodes);
      setEdges(decorateEdges(built.edges));
    },
    [setNodes, setEdges, decorateEdges, withEndpoints],
  );

  // ── load document ───────────────────────────────────────────────────
  const loadDocument = useCallback(
    (data: {
      name: string;
      description: string;
      version?: number;
      inputs?: WorkflowEntry['inputs'];
      triggers?: string[];
      outputs?: Record<string, string>;
      steps: WorkflowStep[];
    }) => {
      const initial = data.steps.length > 0 ? data.steps : [newStep([])];
      setName(data.name);
      setDescription(data.description);
      setVersion(data.version);
      setInputs(data.inputs ?? []);
      setTriggers(data.triggers ?? ['manual']);
      setOutputs(data.outputs ?? {});
      setErrors([]);
      setRunStatus({});
      setRun(null);
      setEvents([]);
      setEvidence([]);
      setSelectedId(pathKey([0]));
      setBaseline(JSON.stringify({ name: data.name, description: data.description, steps: initial }));
      resetHistory(initial, data.name, data.description);
      const collected = collectStepGraph(initial, {});
      const laid = layoutGraph(collected.nodes, collected.edges);
      positionsRef.current = new Map(laid.map((n) => [n.id, n.position]));
      const built = withEndpoints(initial, {}, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      setSteps(initial);
      setNodes(built.nodes);
      setEdges(decorateEdges(built.edges));
      setStarted(true);
    },
    [resetHistory, withEndpoints, setNodes, setEdges, decorateEdges],
  );

  // Initial load (workflow by name) + shared catalogs.
  useEffect(() => {
    chatService
      .getWorkflowCapabilities()
      .then((caps) => {
        if (caps?.kinds) setCapabilities(caps.kinds);
      })
      .catch(() => undefined);
    chatService.listWorkflowTemplates().then((r) => setTemplates(r.templates)).catch(() => undefined);
    chatService.listWorkflows().then((r) => setLibrary(r.workflows)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (target.isNew) {
      setStarted(false);
      setName('');
      setDescription('');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await chatService.getWorkflow(target.name);
        if (cancelled) return;
        const wf = res.workflow;
        loadDocument({
          name: wf.name,
          description: wf.description,
          version: wf.version,
          inputs: wf.inputs,
          triggers: wf.triggers,
          outputs: wf.outputs,
          steps: wf.steps ?? [],
        });
        persistedRef.current = true;
        // versions
        chatService.listWorkflowVersions(wf.name).then((v) => setVersions(v.versions)).catch(() => undefined);
        // latest run overlay
        chatService
          .listWorkflowRuns(wf.name)
          .then(async (r) => {
            setRuns(r.runs);
            const latest = r.runs[0];
            if (!latest) return;
            const ev = await chatService.getRunEvents(latest.run_id).catch(() => ({ status: 'ok', events: [] }));
            const status: Record<string, string> = {};
            for (const e of ev.events) {
              if (!e.step_id) continue;
              if (e.type === 'step_start') status[e.step_id] = 'running';
              else if (e.type === 'step_end') status[e.step_id] = e.status === 'skipped' ? 'skipped' : 'ok';
            }
            setRunStatus(status);
            applyGraph(wf.steps ?? [], status);
          })
          .catch(() => undefined);
      } catch (error) {
        setErrors([translateError(error)]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Only reload when the target document changes — never on internal state
    // (edge style, run status) that loadDocument happens to depend on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.name, target.isNew]);

  // ── history tracking ────────────────────────────────────────────────
  useEffect(() => {
    if (!started) return;
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
  }, [steps, name, description, started]);

  const applySnapshot = useCallback(
    (snap: string) => {
      const parsed = JSON.parse(snap) as { s: WorkflowStep[]; n: string; d: string };
      suspendHistoryRef.current = true;
      setName(parsed.n);
      setDescription(parsed.d);
      rebuild(parsed.s);
      setSelectedId((cur) => (cur && locate(parsed.s, parsePath(cur)) ? cur : ''));
    },
    [rebuild],
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

  // ── canvas size ─────────────────────────────────────────────────────
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => setCanvasSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [started, codeMode]);

  // ── mutations ───────────────────────────────────────────────────────
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

  const addKind = useCallback(
    (kind: string, position?: { x: number; y: number }) => {
      setSteps((current) => {
        const wired = wireList(current);
        const step = newStep(wired, kind);
        const next = [...wired, step];
        const key = step.id;
        if (position) positionsRef.current.set(key, position);
        rebuild(next);
        setSelectedId(pathKey([next.length - 1]));
        return next;
      });
    },
    [rebuild],
  );

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

  const duplicateSelected = useCallback(() => {
    if (!selected || selectedPath.length === 0) return;
    const index = selectedPath[selectedPath.length - 1];
    if (typeof index !== 'number') return;
    const listPath = selectedPath.slice(0, -1);
    setSteps((current) => {
      const list = getList(current, listPath).slice();
      const source = list[index];
      if (!source) return current;
      const clone: WorkflowStep = {
        ...JSON.parse(JSON.stringify(source)),
        id: newStep(current).id,
        next: '',
      };
      list.splice(index + 1, 0, clone);
      const next = setList(current, listPath, list);
      rebuild(next);
      setSelectedId(pathKey([...listPath, index + 1]));
      return next;
    });
  }, [selected, selectedPath, rebuild]);

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

  const relayout = useCallback(() => {
    const collected = collectStepGraph(steps, runStatus);
    const laid = layoutGraph(collected.nodes, collected.edges);
    positionsRef.current = new Map(laid.map((n) => [n.id, n.position]));
    positionsRef.current.delete('__input__');
    positionsRef.current.delete('__output__');
    rebuild(steps);
  }, [steps, runStatus, rebuild]);

  const onNodeDragStop = useCallback((_e: unknown, node: Node) => {
    positionsRef.current.set(node.id, node.position);
  }, []);

  const connectSiblings = useCallback(
    (source: string, target2: string) => {
      const sourcePath = parsePath(source);
      const targetPath = parsePath(target2);
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

  const detachEdges = useCallback(
    (list: Edge[]) => {
      if (list.length === 0) return;
      setSteps((current) => {
        let next = current;
        for (const edge of list) {
          const sourcePathStr = idToPathRef.current.get(edge.source);
          const targetPathStr = idToPathRef.current.get(edge.target);
          if (!sourcePathStr || !targetPathStr) continue;
          const sourcePath = parsePath(sourcePathStr);
          const targetPath = parsePath(targetPathStr);
          const listPath = sourcePath.slice(0, -1);
          if (pathKey(listPath) !== pathKey(targetPath.slice(0, -1))) continue;
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
  const onReconnectStart = useCallback(() => {
    reconnectHandledRef.current = false;
  }, []);
  const onReconnect = useCallback(
    (_oldEdge: Edge, connection: Connection) => {
      reconnectHandledRef.current = true;
      onConnect(connection);
    },
    [onConnect],
  );
  const onReconnectEnd = useCallback(
    (_e: unknown, edge: Edge) => {
      if (reconnectHandledRef.current) return;
      detachEdges([edge]);
    },
    [detachEdges],
  );

  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  useEffect(() => {
    deleteEdgeRef.current = (id: string) => {
      const edge = edgesRef.current.find((e) => e.id === id);
      if (edge) detachEdges([edge]);
    };
  }, [detachEdges]);

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const source = 'source' in connection ? connection.source : '';
    const target2 = 'target' in connection ? connection.target : '';
    if (!source || !target2 || source === target2) return false;
    const sourcePath = idToPathRef.current.get(source);
    const targetPath = idToPathRef.current.get(target2);
    if (!sourcePath || !targetPath) return false;
    return pathKey(parsePath(sourcePath).slice(0, -1)) === pathKey(parsePath(targetPath).slice(0, -1));
  }, []);

  const cycleEdgeType = useCallback(() => {
    setEdgeType((current) => {
      const order: EdgeStyle[] = ['default', 'smoothstep', 'straight'];
      const nextType = order[(order.indexOf(current) + 1) % order.length]!;
      setEdges((cur) => cur.map((e) => ({ ...e, data: { ...e.data, edgeType: nextType } })));
      return nextType;
    });
  }, [setEdges]);

  // ── save / persist ──────────────────────────────────────────────────
  const persist = useCallback(
    async (opts: { closeAfter?: boolean; asName?: string } = {}): Promise<boolean> => {
      setErrors([]);
      const problems = validateTree(steps);
      if (problems.length > 0) {
        setErrors(problems.slice(0, 6));
        setBottomTab('problems');
        setBottomOpen(true);
        setSaveState('error');
        return false;
      }
      const finalName = (opts.asName ?? name).trim();
      if (!finalName) {
        setErrors([t('workflows.name_required')]);
        setSaveState('error');
        return false;
      }
      setBusy(true);
      setSaveState('saving');
      try {
        const finalSteps = renumberWorkflowSteps(steps);
        const payload = {
          name: finalName,
          description,
          version: version ?? 1,
          inputs: inputs ?? [],
          steps: finalSteps,
          triggers,
        };
        const rendered = await chatService.renderWorkflowSteps(payload);
        if (rendered.errors.length > 0 || !rendered.yaml) {
          setErrors(rendered.errors.length ? rendered.errors : [t('workflows.graph_render_failed')]);
          setSaveState('error');
          return false;
        }
        const isExisting = persistedRef.current && finalName === name && !opts.asName;
        const result = isExisting
          ? await chatService.updateWorkflow(finalName, rendered.yaml)
          : await chatService.createWorkflow(rendered.yaml, true);
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
        persistedRef.current = true;
        setName(finalName);
        setBaseline(JSON.stringify({ name: finalName, description, steps }));
        historyRef.current = { snaps: [snapshotOf(steps, finalName, description)], index: 0 };
        syncHistoryFlags();
        try {
          const list = await chatService.listWorkflowVersions(finalName);
          setVersions(list.versions);
          const current = list.versions.find((v) => v.is_current);
          if (current) {
            setSelectedVersion(current.version);
            setVersion(current.version);
          }
        } catch {
          /* best-effort */
        }
        setSaveState('saved');
        onSaved();
        if (opts.closeAfter) onClose();
        return true;
      } catch (error) {
        setErrors([translateError(error)]);
        setSaveState('error');
        return false;
      } finally {
        setBusy(false);
      }
    },
    [steps, name, description, version, inputs, triggers, snapshotOf, syncHistoryFlags, onSaved, onClose],
  );

  const save = useCallback(
    (closeAfter = false) => {
      void persist({ closeAfter });
    },
    [persist],
  );

  const saveAs = useCallback(() => setSaveAsOpen(true), []);

  const openByName = useCallback(
    async (wfName: string) => {
      try {
        const res = await chatService.getWorkflow(wfName);
        const wf = res.workflow;
        loadDocument({
          name: wf.name,
          description: wf.description,
          version: wf.version,
          inputs: wf.inputs,
          triggers: wf.triggers,
          outputs: wf.outputs,
          steps: wf.steps ?? [],
        });
        persistedRef.current = true;
        setVersions([]);
        setRuns([]);
        chatService.listWorkflowVersions(wf.name).then((v) => setVersions(v.versions)).catch(() => undefined);
        chatService.listWorkflowRuns(wf.name).then((r) => setRuns(r.runs)).catch(() => undefined);
      } catch (error) {
        setErrors([translateError(error)]);
      }
    },
    [loadDocument],
  );

  const importFile = useCallback(
    async (file: File) => {
      try {
        const text = await file.text();
        let result = await chatService.createWorkflow(text, false);
        if (result.status !== 'ok') {
          const msg = result.message || '';
          if (msg.includes('already exists')) {
            if (!window.confirm(t('workflows.import_overwrite'))) return;
            result = await chatService.createWorkflow(text, true);
          } else {
            throw new Error(msg || t('workflows.save_failed'));
          }
        }
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
        await openByName(file.name.replace(/\.(ya?ml|json)$/i, ''));
        chatService.listWorkflows().then((r) => setLibrary(r.workflows)).catch(() => undefined);
      } catch (error) {
        setErrors([translateError(error)]);
      }
    },
    [openByName],
  );

  const exportDoc = useCallback(
    async (format: 'yaml' | 'json') => {
      try {
        if (format === 'yaml' && persistedRef.current && name) {
          const result = await chatService.exportWorkflow(name);
          download(`${name}.yaml`, result.yaml, 'text/yaml;charset=utf-8');
          return;
        }
        const rendered = await chatService.renderWorkflowSteps({
          name: name || 'workflow',
          description,
          version: version ?? 1,
          inputs: inputs ?? [],
          steps: renumberWorkflowSteps(steps),
          triggers,
        });
        if (rendered.yaml) download(`${name || 'workflow'}.yaml`, rendered.yaml, 'text/yaml;charset=utf-8');
      } catch (error) {
        setErrors([translateError(error)]);
      }
    },
    [name, description, version, inputs, steps, triggers],
  );

  // ── versions / templates ────────────────────────────────────────────
  const loadVersion = useCallback(
    async (v: number) => {
      if (!name) return;
      setVersionBusy(true);
      try {
        const result = await chatService.getWorkflowVersion(name, v);
        const wf = result.workflow;
        const nextSteps = wf.steps ?? [];
        suspendHistoryRef.current = true;
        setName(wf.name);
        setVersion(wf.version);
        setSteps(nextSteps);
        setBaseline(JSON.stringify({ name: wf.name, description: wf.description, steps: nextSteps }));
        rebuild(nextSteps);
        setSelectedId(nextSteps.length > 0 ? pathKey([0]) : '');
        historyRef.current = { snaps: [snapshotOf(nextSteps, wf.name, wf.description)], index: 0 };
        syncHistoryFlags();
        setSelectedVersion(v);
        setErrors([]);
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setVersionBusy(false);
      }
    },
    [name, rebuild, snapshotOf, syncHistoryFlags],
  );

  const removeVersion = useCallback(
    async (v: number) => {
      if (!name) return;
      setVersionBusy(true);
      try {
        const result = await chatService.deleteWorkflowVersion(name, v);
        if (result.status !== 'ok') throw new Error(t('workflows.version_delete_blocked'));
        const list = await chatService.listWorkflowVersions(name);
        setVersions(list.versions);
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setVersionBusy(false);
      }
    },
    [name],
  );

  const installTemplate = useCallback(
    async (tpl: WorkflowTemplate) => {
      setTemplateBusy(tpl.id);
      try {
        const result = (await chatService.installWorkflowTemplate(tpl.id)) as unknown as {
          status: string;
          message?: string;
          workflow?: { name?: string };
        };
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
        const created = result.workflow?.name;
        chatService.listWorkflows().then((r) => setLibrary(r.workflows)).catch(() => undefined);
        if (created) await openByName(created);
        else setStarted(true);
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setTemplateBusy(null);
      }
    },
    [openByName],
  );

  // ── run ─────────────────────────────────────────────────────────────
  const loadRunDetail = useCallback(async (runId: string) => {
    const [ev, evi] = await Promise.all([
      chatService.getRunEvents(runId).catch(() => ({ status: 'ok', events: [] })),
      chatService.getRunEvidence(runId).catch(() => ({ status: 'ok', evidence: [] })),
    ]);
    setEvents(ev.events);
    setEvidence(evi.evidence);
  }, []);

  const executeRun = useCallback(async () => {
    if (!persistedRef.current || !name) {
      setErrors([t('workflows.run_requires_save')]);
      return;
    }
    setRunBusy(true);
    setRun(null);
    try {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(inputsJson || '{}');
      } catch {
        throw new Error(t('workflows.invalid_json'));
      }
      const result = await chatService.runWorkflow(name, parsed);
      setRun(result.run);
      void loadRunDetail(result.run.run_id);
    } catch (error) {
      setErrors([translateError(error)]);
    } finally {
      setRunBusy(false);
    }
  }, [name, inputsJson, loadRunDetail]);

  const resolveHuman = useCallback(
    async (runId: string, stepId: string, approved: boolean) => {
      try {
        const result = await chatService.resumeWorkflowRun(runId, { [stepId]: approved });
        setRun(result.run);
        void loadRunDetail(result.run.run_id);
      } catch (error) {
        setErrors([translateError(error)]);
      }
    },
    [loadRunDetail],
  );

  const openRun = useCallback(
    (r: WorkflowRun) => {
      setRun(r);
      setBottomTab('output');
      setBottomOpen(true);
      void loadRunDetail(r.run_id);
    },
    [loadRunDetail],
  );

  // ── code view ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!codeMode) return;
    setCodeText(JSON.stringify({ name, description, steps }, null, 2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [codeMode]);

  const applyCode = useCallback(
    (text: string) => {
      setCodeText(text);
      try {
        const parsed = JSON.parse(text) as { name?: string; description?: string; steps?: WorkflowStep[] };
        if (parsed.name !== undefined) setName(parsed.name);
        if (parsed.description !== undefined) setDescription(parsed.description);
        if (Array.isArray(parsed.steps)) rebuild(parsed.steps);
      } catch {
        /* keep typing */
      }
    },
    [rebuild],
  );

  const renderYamlPreview = useCallback(async () => {
    try {
      const rendered = await chatService.renderWorkflowSteps({
        name: name || 'workflow',
        description,
        version: version ?? 1,
        inputs: inputs ?? [],
        steps: renumberWorkflowSteps(steps),
        triggers,
      });
      setYamlPreview(rendered.yaml || rendered.errors.join('\n'));
    } catch (error) {
      setYamlPreview(translateError(error));
    }
  }, [name, description, version, inputs, steps, triggers]);

  // ── persist Studio settings ─────────────────────────────────────────
  useEffect(() => {
    saveStudioSettings({
      edgeType,
      showMinimap,
      interactive,
      leftOpen,
      leftWidth,
      leftTab,
      rightOpen,
      rightWidth,
      inspectorTab,
      bottomOpen,
      bottomTab,
    });
  }, [
    edgeType,
    showMinimap,
    interactive,
    leftOpen,
    leftWidth,
    leftTab,
    rightOpen,
    rightWidth,
    inspectorTab,
    bottomOpen,
    bottomTab,
  ]);

  // ── autosave ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isWindow || !started) return;
    if (!dirty || busy || !name.trim() || validation.length > 0 || steps.length === 0) return;
    const handle = setTimeout(() => {
      void persist();
    }, 800);
    return () => clearTimeout(handle);
  }, [isWindow, started, dirty, busy, name, validation.length, steps.length, persist]);

  // ── cancel / close ──────────────────────────────────────────────────
  const cancel = useCallback(() => {
    if (dirty && !window.confirm(t('workflows.unsaved_confirm'))) return;
    onClose();
  }, [dirty, onClose]);

  // ── return to Studio home ───────────────────────────────────────────
  // Leaves the current document (confirming unsaved edits) and shows the
  // start screen. Reset `persistedRef` so a subsequent blank/new document is
  // treated as NEW and never overwrites the workflow we just left.
  const goHome = useCallback(() => {
    if (dirty && !window.confirm(t('workflows.unsaved_confirm'))) return;
    persistedRef.current = false;
    setStarted(false);
  }, [dirty]);

  // ── keyboard shortcuts ──────────────────────────────────────────────
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setCmdkOpen((v) => !v);
        return;
      }
      if (mod && event.key.toLowerCase() === 's') {
        event.preventDefault();
        save(false);
        return;
      }
      if (mod && event.key.toLowerCase() === 'o') {
        event.preventDefault();
        setOpenDialog(true);
        return;
      }
      if (mod && event.key === 'Enter') {
        event.preventDefault();
        setBottomTab('output');
        setBottomOpen(true);
        void executeRun();
        return;
      }
      if (!mod && (event.key === 'Backspace' || event.key === 'Delete')) {
        if (!isEditableTarget(event.target) && selectedPath.length > 0) {
          event.preventDefault();
          deleteSelected();
        }
        return;
      }
      if (!mod) return;
      const key = event.key.toLowerCase();
      if (key === 'z' || key === 'y') {
        if (isEditableTarget(event.target)) return;
        event.preventDefault();
        if (key === 'y' || event.shiftKey) redo();
        else undo();
        return;
      }
      if (key === 'd') {
        if (isEditableTarget(event.target)) return;
        event.preventDefault();
        duplicateSelected();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [save, executeRun, deleteSelected, duplicateSelected, undo, redo, selectedPath.length]);

  // ── commands (palette) ──────────────────────────────────────────────
  const commands = useMemo<StudioCommand[]>(() => {
    const cmds: StudioCommand[] = [
      { id: 'save', label: t('workflows.save'), hint: '⌘S', icon: Save, run: () => save(false) },
      { id: 'save-as', label: t('workflows.save_as'), run: saveAs },
      { id: 'open', label: t('workflows.open'), hint: '⌘O', icon: FileText, run: () => setOpenDialog(true) },
      { id: 'run', label: t('workflows.run'), hint: '⌘↵', icon: Zap, run: () => { setBottomTab('output'); setBottomOpen(true); void executeRun(); } },
      { id: 'undo', label: t('workflows.undo'), hint: '⌘Z', icon: Undo2, run: undo },
      { id: 'redo', label: t('workflows.redo'), hint: '⇧⌘Z', icon: Redo2, run: redo },
      { id: 'layout', label: t('workflows.auto_layout'), icon: Sparkles, run: relayout },
      { id: 'toggle-code', label: codeMode ? t('workflows.view_canvas') : t('workflows.view_code'), icon: FileJson, run: () => setCodeMode((v) => !v) },
    ];
    for (const group of KIND_GROUPS) {
      for (const kind of group.kinds) {
        cmds.push({
          id: `add-${kind}`,
          label: `${t('workflows.add_step')} · ${t(kindLabelKey(kind))}`,
          group: t(group.labelKey),
          icon: Plus,
          run: () => addKind(kind),
        });
      }
    }
    return cmds;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [save, saveAs, executeRun, undo, redo, relayout, codeMode, addKind]);

  // ── panel resize ────────────────────────────────────────────────────
  const startResize = useCallback(
    (side: 'left' | 'right') =>
      (event: React.MouseEvent) => {
        event.preventDefault();
        const startX = event.clientX;
        const startW = side === 'left' ? leftWidth : rightWidth;
        const onMove = (e: MouseEvent) => {
          const delta = side === 'left' ? e.clientX - startX : startX - e.clientX;
          const next = Math.min(520, Math.max(180, startW + delta));
          if (side === 'left') setLeftWidth(next);
          else setRightWidth(next);
        };
        const onUp = () => {
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      },
    [leftWidth, rightWidth],
  );

  // ── start screen (new) ──────────────────────────────────────────────
  if (!started) {
    return (
      <div className="wfs-root">
        <StudioTopbar
          isWindow={isWindow}
          isMac={isMac}
          name={t('workflows.new')}
          dirty={false}
          busy={busy}
          canSave={false}
          canUndo={false}
          canRedo={false}
          menus={<span />}
          onBack={goHome}
          onSave={() => save(false)}
          onRun={() => undefined}
        />
        <ErrorBanner errors={errors} onDismiss={() => setErrors([])} />
        <StartScreen
          templates={templates}
          busyId={templateBusy}
          onBlank={() => {
            persistedRef.current = false;
            loadDocument({ name: '', description: '', steps: [] });
          }}
          onOpen={() => setOpenDialog(true)}
          onImport={() => fileInputRef.current?.click()}
          onTemplate={(tpl) => void installTemplate(tpl)}
        />
        <OpenDialog
          open={openDialog}
          workflows={library}
          onClose={() => setOpenDialog(false)}
          onPick={(w) => {
            setOpenDialog(false);
            void openByName(w.name);
          }}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept=".yaml,.yml,.json,text/yaml,application/x-yaml"
          style={{ display: 'none' }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void importFile(file);
            e.target.value = '';
          }}
        />
      </div>
    );
  }

  const menus = (
    <>
      <Menu label={t('workflows.menu_file')}>
        <DropdownMenuItem onSelect={goHome}>
          {t('workflows.new')}
          <DropdownMenuShortcut>⌘N</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setOpenDialog(true)}>
          {t('workflows.open')}
          <DropdownMenuShortcut>⌘O</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => fileInputRef.current?.click()}>{t('workflows.import')}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => save(false)}>
          {t('workflows.save')}
          <DropdownMenuShortcut>⌘S</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={saveAs}>{t('workflows.save_as')}</DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>{t('workflows.export')}</DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuItem onSelect={() => void exportDoc('yaml')}>YAML</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportDoc('json')}>JSON</DropdownMenuItem>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={cancel}>{t('common.close')}</DropdownMenuItem>
      </Menu>
      <Menu label={t('workflows.menu_edit')}>
        <DropdownMenuItem disabled={!canUndo} onSelect={undo}>
          {t('workflows.undo')}
          <DropdownMenuShortcut>⌘Z</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!canRedo} onSelect={redo}>
          {t('workflows.redo')}
          <DropdownMenuShortcut>⇧⌘Z</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={!selected} onSelect={duplicateSelected}>
          {t('workflows.duplicate')}
          <DropdownMenuShortcut>⌘D</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!selected} onSelect={() => move(-1)}>{t('workflows.move_up')}</DropdownMenuItem>
        <DropdownMenuItem disabled={!selected} onSelect={() => move(1)}>{t('workflows.move_down')}</DropdownMenuItem>
        <DropdownMenuItem disabled={!selected} onSelect={deleteSelected}>{t('workflows.delete')}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={relayout}>{t('workflows.auto_layout')}</DropdownMenuItem>
      </Menu>
      <Menu label={t('workflows.menu_view')}>
        <DropdownMenuItem onSelect={() => setLeftOpen((v) => !v)}>{t('workflows.toggle_left')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setRightOpen((v) => !v)}>{t('workflows.toggle_right')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setBottomOpen((v) => !v)}>{t('workflows.toggle_bottom')}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => setShowMinimap((v) => !v)}>{t('workflows.minimap')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={cycleEdgeType}>{t(EDGE_LABEL[edgeType] ?? 'workflows.edge_curve')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setCodeMode((v) => !v)}>
          {codeMode ? t('workflows.view_canvas') : t('workflows.view_code')}
        </DropdownMenuItem>
      </Menu>
      <Menu label={t('workflows.menu_run')}>
        <DropdownMenuItem
          onSelect={() => {
            setBottomTab('output');
            setBottomOpen(true);
            void executeRun();
          }}
        >
          {t('workflows.run')}
          <DropdownMenuShortcut>⌘↵</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={editorRunWithReveal}>{t('workflows.run_inputs')}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => setLeftTab('versions')}>{t('workflows.version')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setLeftTab('runs')}>{t('workflows.runs')}</DropdownMenuItem>
      </Menu>
      <Menu label={t('workflows.menu_help')}>
        <DropdownMenuItem onSelect={() => setCmdkOpen(true)}>
          {t('workflows.command_palette')}
          <DropdownMenuShortcut>⌘K</DropdownMenuShortcut>
        </DropdownMenuItem>
      </Menu>
    </>
  );

  function editorRunWithReveal() {
    setBottomTab('output');
    setBottomOpen(true);
  }

  return (
    <div className="wfs-root">
      <StudioTopbar
        isWindow={isWindow}
        isMac={isMac}
        name={name || t('workflows.untitled')}
        dirty={dirty}
        busy={busy}
        canSave={!busy && !!name.trim() && validation.length === 0}
        canUndo={canUndo}
        canRedo={canRedo}
        saveState={saveState}
        menus={menus}
        onBack={goHome}
        onSave={() => save(false)}
        onRun={() => {
          setBottomTab('output');
          setBottomOpen(true);
          void executeRun();
        }}
      />

      <ErrorBanner errors={errors} onDismiss={() => setErrors([])} />

      <div className="wfs-main">
        {leftOpen ? (
          <>
            <aside className="wfs-dock wfs-dock--left" style={{ width: leftWidth, flex: `0 0 ${leftWidth}px` }}>
              <div className="wfs-dock__tabs">
                <DockTab id="nodes" label={t('workflows.panel_nodes')} active={leftTab} onSelect={setLeftTab} />
                <DockTab id="outline" label={t('workflows.panel_outline')} active={leftTab} onSelect={setLeftTab} />
                <DockTab id="versions" label={t('workflows.panel_versions')} active={leftTab} onSelect={setLeftTab} />
                <DockTab id="runs" label={t('workflows.panel_runs')} active={leftTab} onSelect={setLeftTab} />
                <DockTab id="templates" label={t('workflows.panel_templates')} active={leftTab} onSelect={setLeftTab} />
              </div>
              <div className="wfs-dock__body">
                {leftTab === 'nodes' ? <NodePalette onAdd={(k) => addKind(k)} onDragKind={() => undefined} /> : null}
                {leftTab === 'outline' ? (
                  <OutlinePanel steps={steps} selectedId={selectedId} onSelect={(k) => setSelectedId(k)} />
                ) : null}
                {leftTab === 'versions' ? (
                  <VersionsPanel
                    versions={versions}
                    selectedVersion={selectedVersion}
                    busy={versionBusy}
                    canDelete={persistedRef.current}
                    onLoad={(v) => void loadVersion(v)}
                    onDelete={(v) => void removeVersion(v)}
                  />
                ) : null}
                {leftTab === 'runs' ? <RunsPanel runs={runs} onOpen={openRun} /> : null}
                {leftTab === 'templates' ? (
                  <TemplatesPanel
                    templates={templates}
                    busyId={templateBusy}
                    onInstall={(tpl) => void installTemplate(tpl)}
                  />
                ) : null}
              </div>
            </aside>
            <div className="wfs-resize" onMouseDown={startResize('left')} />
          </>
        ) : null}

        <div className="wfs-center">
          <div
            className={`wfs-canvas${codeMode ? ' wfs-canvas--code' : ''}`}
            ref={canvasRef}
            onDrop={(e) => {
              const kind = e.dataTransfer.getData('text/cw-workflow-kind');
              if (!kind) return;
              e.preventDefault();
              const pos = rfRef.current?.screenToFlowPosition?.({ x: e.clientX, y: e.clientY });
              addKind(kind, pos);
            }}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes('text/cw-workflow-kind')) e.preventDefault();
            }}
          >
            {codeMode ? (
              <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
                <div className="wfs-bottom__head" style={{ flex: '0 0 34px' }}>
                  <span className="wf-section__title" style={{ paddingLeft: 6 }}>
                    {t('workflows.code_json')}
                  </span>
                  <div className="wfs-bottom__toggle" style={{ gap: 8 }}>
                    <button type="button" className="wfs-toolbar-btn wfs-toolbar-btn--ghost" onClick={() => void renderYamlPreview()}>
                      <FileText size={13} /> {t('workflows.code_render_yaml')}
                    </button>
                  </div>
                </div>
                <textarea
                  className="wfs-code-editor"
                  spellCheck={false}
                  value={codeText}
                  onChange={(e) => applyCode(e.target.value)}
                />
                {yamlPreview ? (
                  <pre className="skill-detail__pre" style={{ maxHeight: 160, overflow: 'auto', margin: 0 }}>
                    {yamlPreview}
                  </pre>
                ) : null}
              </div>
            ) : steps.length === 0 ? (
              <div className="wf-empty" style={{ height: '100%' }}>
                <p>{t('workflows.empty_canvas')}</p>
                <Button variant="secondary" size="sm" onClick={() => addKind('tool')}>
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
                  onNodeClick={(_e, node) => {
                    const path = node.data.path as string;
                    if (path) setSelectedId(path);
                  }}
                  onNodeContextMenu={(e, node) => {
                    const path = node.data.path as string;
                    if (!path) return;
                    e.preventDefault();
                    setSelectedId(path);
                    setContextMenu({ x: e.clientX, y: e.clientY, path });
                  }}
                  onNodeDragStop={onNodeDragStop}
                  onConnect={onConnect}
                  onEdgesDelete={onEdgesDelete}
                  onReconnect={onReconnect}
                  onReconnectStart={onReconnectStart}
                  onReconnectEnd={onReconnectEnd}
                  edgesReconnectable
                  nodesDraggable={interactive}
                  nodesConnectable={interactive}
                  deleteKeyCode={null}
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

                {showMinimap ? (
                  <div style={{ position: 'absolute', right: 10, bottom: 44, zIndex: 6, width: 240 }}>
                    <WorkflowMiniMap
                      nodes={nodes}
                      viewport={viewport}
                      canvas={canvasSize}
                      onNavigate={(fx, fy) =>
                        rfRef.current?.setCenter(fx, fy, { zoom: viewport.zoom, duration: 200 })
                      }
                    />
                  </div>
                ) : null}

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
                    className={`wf-controls__btn${showMinimap ? ' wf-controls__btn--active' : ''}`}
                    onClick={() => setShowMinimap((v) => !v)}
                    title={t('workflows.minimap')}
                  >
                    <MapIcon size={14} />
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

          <div className={`wfs-bottom${bottomOpen ? '' : ' wfs-bottom--collapsed'}`}>
            <div className="wfs-bottom__head">
              <BottomTabButton
                id="problems"
                label={`${t('workflows.tab_problems')}${validation.length ? ` (${validation.length})` : ''}`}
                active={bottomTab}
                onSelect={(id) => {
                  setBottomTab(id);
                  setBottomOpen(true);
                }}
              />
              <BottomTabButton
                id="console"
                label={t('workflows.tab_console')}
                active={bottomTab}
                onSelect={(id) => {
                  setBottomTab(id);
                  setBottomOpen(true);
                }}
              />
              <BottomTabButton
                id="output"
                label={t('workflows.tab_output')}
                active={bottomTab}
                onSelect={(id) => {
                  setBottomTab(id);
                  setBottomOpen(true);
                }}
              />
              <button
                type="button"
                className="wfs-bottom__toggle"
                onClick={() => setBottomOpen((v) => !v)}
                title={bottomOpen ? t('workflows.collapse') : t('workflows.expand')}
              >
                {bottomOpen ? '▾' : '▴'}
              </button>
            </div>
            {bottomOpen ? (
              <div className="wfs-bottom__body">
                <OutputPanel
                  tab={bottomTab}
                  problems={validation}
                  run={run}
                  events={events}
                  evidence={evidence}
                  inputsJson={inputsJson}
                  runBusy={runBusy}
                  onInputsChange={setInputsJson}
                  onRun={() => void executeRun()}
                  onResolve={(stepId, approved) => run && void resolveHuman(run.run_id, stepId, approved)}
                />
              </div>
            ) : null}
          </div>
        </div>

        {rightOpen ? (
          <>
            <div className="wfs-resize" onMouseDown={startResize('right')} />
            <aside className="wfs-dock wfs-dock--right" style={{ width: rightWidth, flex: `0 0 ${rightWidth}px` }}>
              <div className="wfs-dock__body wfs-inspector">
                {selected ? (
                  <NodeInspector
                    selected={selected}
                    tab={inspectorTab}
                    onTab={setInspectorTab}
                    onPatch={patchSelected}
                    onAddChild={addChild}
                  />
                ) : (
                  <WorkflowInspector
                    name={name}
                    description={description}
                    version={version}
                    stepCount={steps.length}
                    triggers={triggers}
                    outputs={outputs}
                    inputs={inputs}
                    onName={setName}
                    onDescription={setDescription}
                  />
                )}
              </div>
            </aside>
          </>
        ) : null}
      </div>

      <div className="wfs-status">
        <span className="wfs-status__item">
          <Layers size={12} /> {steps.length} {t('workflows.steps')}
        </span>
        <span className="wfs-status__item">{selected ? selected.kind : name ? `v${version ?? 1}` : t('workflows.untitled')}</span>
        <span className={`wfs-status__item ${validation.length ? 'wfs-status__bad' : 'wfs-status__ok'}`}>
          {validation.length ? `${validation.length} ${t('workflows.errors')}` : t('workflows.status_ok')}
        </span>
        <span className="wfs-status__spacer" />
        {dirty ? <span className="wfs-status__item">{t('workflows.unsaved')}</span> : null}
        {saveState !== 'idle' ? (
          <span className={`wfs-status__item wfs-save-state--${saveState}`}>{saveStateLabel(saveState)}</span>
        ) : null}
        <span className="wfs-status__item">
          {Math.round(viewport.zoom * 100)}% · {Math.round(viewport.x)},{Math.round(viewport.y)}
        </span>
      </div>

      <CommandPalette open={cmdkOpen} commands={commands} onClose={() => setCmdkOpen(false)} />
      <OpenDialog
        open={openDialog}
        workflows={library}
        onClose={() => setOpenDialog(false)}
        onPick={(w) => {
          setOpenDialog(false);
          void openByName(w.name);
        }}
      />
      <NamePrompt
        open={saveAsOpen}
        title={t('workflows.save_as_prompt')}
        initial={name}
        confirmLabel={t('workflows.save_as')}
        onCancel={() => setSaveAsOpen(false)}
        onConfirm={(value) => {
          setSaveAsOpen(false);
          persistedRef.current = false;
          void persist({ asName: value });
        }}
      />

      {contextMenu ? (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 6400 }}
            onMouseDown={() => setContextMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setContextMenu(null);
            }}
          />
          <div className="wfs-context" style={{ left: contextMenu.x, top: contextMenu.y }}>
            <button
              type="button"
              onClick={() => {
                setSelectedId(contextMenu.path);
                duplicateSelected();
                setContextMenu(null);
              }}
            >
              <Copy size={13} /> {t('workflows.duplicate')}
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedId(contextMenu.path);
                move(-1);
                setContextMenu(null);
              }}
            >
              {t('workflows.move_up')}
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedId(contextMenu.path);
                move(1);
                setContextMenu(null);
              }}
            >
              {t('workflows.move_down')}
            </button>
            <button
              type="button"
              className="wfs-danger"
              onClick={() => {
                setSelectedId(contextMenu.path);
                // locate via path and remove
                const path = parsePath(contextMenu.path);
                setSteps((current) => {
                  const next = removeLeaf(current, path);
                  rebuild(next);
                  setSelectedId('');
                  return next;
                });
                setContextMenu(null);
              }}
            >
              <Trash2 size={13} /> {t('workflows.delete')}
            </button>
          </div>
        </>
      ) : null}

      <input
        ref={fileInputRef}
        type="file"
        accept=".yaml,.yml,.json,text/yaml,application/x-yaml"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void importFile(file);
          e.target.value = '';
        }}
      />
    </div>
  );
}

// ── small helpers ─────────────────────────────────────────────────────
function ErrorBanner({ errors, onDismiss }: { errors: string[]; onDismiss: () => void }) {
  if (errors.length === 0) return null;
  return (
    <div
      className="add-skill-page__msg add-skill-page__msg--error wfs-error-banner"
      style={{ margin: '6px 12px' }}
      role="alert"
    >
      <div className="wfs-error-banner__list">
        {errors.map((e) => (
          <div key={e}>{e}</div>
        ))}
      </div>
      <button
        type="button"
        className="wfs-error-banner__close"
        onClick={onDismiss}
        title={t('common.close')}
        aria-label={t('common.close')}
      >
        <X size={14} />
      </button>
    </div>
  );
}

function Menu({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="wfs-menu-trigger">
          {label}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="wfs-menu-pop">{children}</DropdownMenuContent>
    </DropdownMenu>
  );
}

function DockTab({
  id,
  label,
  active,
  onSelect,
}: {
  id: LeftTab;
  label: string;
  active: LeftTab;
  onSelect: (id: LeftTab) => void;
}) {
  return (
    <button
      type="button"
      className={`wfs-dock__tab${active === id ? ' wfs-dock__tab--active' : ''}`}
      onClick={() => onSelect(id)}
    >
      {label}
    </button>
  );
}

function BottomTabButton({
  id,
  label,
  active,
  onSelect,
}: {
  id: BottomTab;
  label: string;
  active: BottomTab;
  onSelect: (id: BottomTab) => void;
}) {
  return (
    <button
      type="button"
      className={`wfs-dock__tab${active === id ? ' wfs-dock__tab--active' : ''}`}
      onClick={() => onSelect(id)}
    >
      {label}
    </button>
  );
}

interface TopbarProps {
  isWindow: boolean;
  isMac: boolean;
  name: string;
  dirty: boolean;
  busy: boolean;
  canSave: boolean;
  canUndo: boolean;
  canRedo: boolean;
  saveState?: 'idle' | 'saving' | 'saved' | 'error';
  menus: React.ReactNode;
  onBack: () => void;
  onSave: () => void;
  onRun: () => void;
}

function StudioTopbar({
  isWindow,
  isMac,
  name,
  dirty,
  busy,
  canSave,
  canUndo,
  canRedo,
  saveState,
  menus,
  onBack,
  onSave,
  onRun,
}: TopbarProps) {
  return (
    <div className={`wfs-topbar${isWindow ? ' wfs-topbar--window' : ''}${isWindow && isMac ? ' wfs-topbar--mac' : ''}`}>
      <button
        type="button"
        className="wfs-topbar__back"
        onClick={onBack}
        title={t('workflows.back_to_home')}
        aria-label={t('workflows.back_to_home')}
      >
        <ArrowLeft size={14} />
        {t('workflows.back_to_home')}
      </button>
      <span className="wfs-doc">
        <span className="wfs-doc__dot" />
        <span className="wfs-doc__name">{name}</span>
        {dirty ? <span className="wfs-doc__dirty" title={t('workflows.unsaved')}>•</span> : null}
      </span>
      <div className="wfs-menubar">{menus}</div>
      <span className="wfs-topbar__spacer" />
      {saveState && saveState !== 'idle' ? (
        <span className={`wfs-save-state wfs-save-state--${saveState}`}>{saveStateLabel(saveState)}</span>
      ) : null}
      <button type="button" className="wfs-toolbar-btn" onClick={onSave} disabled={!canSave} title={`${t('workflows.save')} (⌘S)`}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
        {t('workflows.save')}
      </button>
      <button type="button" className="wfs-toolbar-btn wfs-toolbar-btn--primary" onClick={onRun} title={`${t('workflows.run')} (⌘↵)`}>
        <Zap size={14} />
        {t('workflows.run')}
      </button>
    </div>
  );
}

function saveStateLabel(state: 'idle' | 'saving' | 'saved' | 'error'): string {
  if (state === 'saving') return t('workflows.autosaving');
  if (state === 'saved') return t('workflows.autosaved');
  if (state === 'error') return t('workflows.autosave_error');
  return '';
}

function download(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
