import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  SelectionMode,
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
  DropdownMenuCheckboxItem,
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
import { useTitlebarOverlay } from '../../../lib/useTitlebarOverlay';
import { chatService } from '../../../services/chatService';
import {
  buildGraph,
  collectStepGraph,
  flowEndpoints,
  layoutGraph,
  nodeTypes,
  remapEndpointIds,
  renumberWorkflowSteps,
} from '../flowGraph';
import { edgeTypes } from '../EditableEdge';
import { WorkflowMiniMap } from '../WorkflowMiniMap';
import { KIND_GROUPS, kindLabelKey } from '../kinds';
import { PLATFORM_NAMES, setCapabilities } from '../actions';
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
  chainWiring,
  duplicateLeaves,
  getList,
  locate,
  newStep,
  parsePath,
  pathKey,
  pruneWiring,
  removeLeaf,
  removeLeaves,
  setList,
  updateLeaf,
  validateTree,
  type StepPath,
} from './workflowTree';
import { loadStudioSettings, saveStudioSettings, type EdgeStyle } from './studioSettings';
import {
  collectRunInputs,
  initRunInputForm,
  loadRememberedRunInputs,
  mergeRunInputForm,
  rememberRunInputs,
  type InputFormValue,
} from './runInputs';
import type {
  WorkflowEntry,
  WorkflowEvidence,
  WorkflowRun,
  WorkflowRunEvent,
  WorkflowStep,
  WorkflowStepState,
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
  // Workflow `platform` declaration: '' / 'any' = runs on every OS.
  const [platform, setPlatform] = useState('');
  const [baselinePlatform, setBaselinePlatform] = useState('');
  // The OS this Studio is running on (for the compatibility badge / Run gate).
  const [hostPlatform, setHostPlatform] = useState('linux');
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [inputs, setInputs] = useState<WorkflowEntry['inputs']>([]);
  const [triggers, setTriggers] = useState<string[]>(['manual']);
  const [outputs, setOutputs] = useState<Record<string, string>>({});
  // Explicit endpoint wiring: null = derive from step order.
  const [entry, setEntry] = useState<string | null>(null);
  const [exits, setExits] = useState<string[] | null>(null);
  const entryRef = useRef<string | null>(null);
  const exitsRef = useRef<string[] | null>(null);
  const [steps, setSteps] = useState<WorkflowStep[]>([]);
  const [selectedId, setSelectedId] = useState('');
  // Marquee / multi selection (React Flow node ids, excluding endpoint pills).
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([]);
  const [baseline, setBaseline] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [started, setStarted] = useState(!target.isNew);

  // ── graph state ─────────────────────────────────────────────────────
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  // Persisted per-step binding state (observability) from the last run.
  const [stepState, setStepState] = useState<Record<string, WorkflowStepState>>({});
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
  const [runInputs, setRunInputs] = useState<Record<string, InputFormValue>>({});
  const [runBusy, setRunBusy] = useState(false);

  // ── UI state (seeded from persisted Studio settings) ────────────────
  const [autoSave, setAutoSave] = useState(initialSettings.autoSave);
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
  // Latest step tree as it exists in the canvas (after materialising legacy
  // wiring). Async overlays must rebuild from THIS, never from the raw fetch.
  const stepsRef = useRef<WorkflowStep[]>([]);
  const historyRef = useRef<{ snaps: string[]; index: number }>({ snaps: [], index: -1 });
  const suspendHistoryRef = useRef(false);
  // Last document revision an autosave was attempted for (avoids retry loops).
  const autoSaveTriedRef = useRef('');

  // Name this document was last SAVED as on the backend ('' = never saved).
  // Renaming the doc keeps this as the update target so renames work.
  const persistedNameRef = useRef(target.isNew ? '' : target.name);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  const validation = useMemo(() => validateTree(steps), [steps]);
  const runInputErrors = useMemo(() => collectRunInputs(inputs, runInputs).errors, [inputs, runInputs]);
  // Recently opened = the library sorted by last update.
  const recentWorkflows = useMemo(
    () =>
      library
        .slice()
        .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
        .slice(0, 6),
    [library],
  );
  const dirty = useMemo(
    () => docKey(name, description, steps, entry, exits) !== baseline
      || platformTagsOf(platform).join(',') !== platformTagsOf(baselinePlatform).join(','),
    [name, description, steps, entry, exits, baseline, platform, baselinePlatform],
  );
  const platformTags = useMemo(() => platformTagsOf(platform), [platform]);
  const platformCompatible = platformTags.includes(hostPlatform);
  const platformLabelText = platformCompatible ? '' : platformTags.map((x) => PLATFORM_NAMES[x] ?? x).join(' / ');

  const selectedPath = useMemo<StepPath>(() => (selectedId ? parsePath(selectedId) : []), [selectedId]);
  const selected = useMemo(() => locate(steps, selectedPath), [steps, selectedPath]);

  const syncHistoryFlags = useCallback(() => {
    const h = historyRef.current;
    setCanUndo(h.index > 0);
    setCanRedo(h.index >= 0 && h.index < h.snaps.length - 1);
  }, []);

  const resetHistory = useCallback(
    (s: WorkflowStep[], n: string, d: string, e: string | null, x: string[] | null) => {
      suspendHistoryRef.current = true;
      historyRef.current = { snaps: [docKey(n, d, s, e, x)], index: 0 };
      syncHistoryFlags();
    },
    [syncHistoryFlags],
  );

  const decorateEdges = useCallback(
    (list: Edge[]) =>
      list.map((e) => ({
        ...e,
        type: 'editable',
        data: {
          ...e.data,
          edgeType,
          onDelete: (id: string) => deleteEdgeRef.current(id),
        },
      })),
    [edgeType],
  );

  const withEndpoints = useCallback(
    (next: WorkflowStep[], status: Record<string, string>, positions: Map<string, { x: number; y: number }>) => {
      const built = buildGraph(next, status, positions, { sequential: false });
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
      // Explicit endpoint wiring (entry/exits) wins; otherwise derive from the
      // step order. An explicit empty value means "not connected".
      const derived = flowEndpoints(next);
      const entryVal = entryRef.current;
      const exitsVal = exitsRef.current;
      const inputTarget = entryVal !== null ? entryVal : derived.inputTarget;
      const exitSources = exitsVal !== null ? exitsVal : derived.outputSource ? [derived.outputSource] : [];
      if (inputTarget)
        allEdges.unshift({ id: 'endpoint-in', source: '__input__', target: inputTarget, type: 'smoothstep', markerEnd: marker });
      exitSources.forEach((source, index) => {
        allEdges.push({ id: `endpoint-out-${index}`, source, target: '__output__', type: 'smoothstep', markerEnd: marker });
      });
      return { nodes: allNodes, edges: allEdges, nodeIdToPath: built.nodeIdToPath };
    },
    [],
  );

  const rebuild = useCallback(
    (next: WorkflowStep[]) => {
      const built = withEndpoints(next, runStatus, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      stepsRef.current = next;
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
      stepsRef.current = next;
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
      entry?: string | null;
      exits?: string[] | null;
      steps: WorkflowStep[];
      /** Materialise legacy implicit wiring on load (default true). */
      materialize?: boolean;
    }) => {
      let initial = data.steps.length > 0 ? data.steps : [newStep([])];
      let entryVal = data.entry ?? null;
      let exitsVal = data.exits ?? null;
      if (data.materialize === false) {
        // Brand-new document: nothing wired, and nothing auto-connects.
        entryVal = '';
        exitsVal = [];
      } else if (!initial.some((step) => step.next)) {
        // Legacy document with no explicit ``next`` wiring: convert the
        // implicit sequential order + endpoints into real, deletable edges.
        // NOTE: the API derives ``entry``/``exits`` even for unwired docs, so
        // they are NOT a reliable "has wiring" signal — the ``next`` links are.
        initial = chainWiring(initial);
        const derived = flowEndpoints(initial);
        entryVal = derived.inputTarget || '';
        exitsVal = derived.outputSource ? [derived.outputSource] : [];
      }
      setName(data.name);
      setDescription(data.description);
      setVersion(data.version);
      const inputSpecs = data.inputs ?? [];
      setInputs(inputSpecs);
      setRunInputs(initRunInputForm(inputSpecs, loadRememberedRunInputs(data.name)));
      setTriggers(data.triggers ?? ['manual']);
      setOutputs(data.outputs ?? {});
      entryRef.current = entryVal;
      exitsRef.current = exitsVal;
      setEntry(entryVal);
      setExits(exitsVal);
      setErrors([]);
      setRunStatus({});
      setRun(null);
      setEvents([]);
      setEvidence([]);
      setSelectedId(pathKey([0]));
      setBaseline(docKey(data.name, data.description, initial, entryVal, exitsVal));
      resetHistory(initial, data.name, data.description, entryVal, exitsVal);
      const collected = collectStepGraph(initial, {}, { sequential: false });
      const laid = layoutGraph(collected.nodes, collected.edges);
      positionsRef.current = new Map(laid.map((n) => [n.id, n.position]));
      const built = withEndpoints(initial, {}, positionsRef.current);
      idToPathRef.current = built.nodeIdToPath;
      stepsRef.current = initial;
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
        if (caps?.platform) setHostPlatform(caps.platform);
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
        setPlatform(wf.platform || '');
        setBaselinePlatform(wf.platform || '');
        const persisted = wf.state ?? {};
        setStepState(persisted);
        // Seed run status from the persisted last-run state so badges show even
        // before a fresh run (live run events override this below).
        const seeded: Record<string, string> = {};
        for (const [sid, st] of Object.entries(persisted)) seeded[sid] = st.status === 'failed' ? 'failed' : 'ok';
        if (Object.keys(seeded).length) setRunStatus(seeded);
        loadDocument({
          name: wf.name,
          description: wf.description,
          version: wf.version,
          inputs: wf.inputs,
          triggers: wf.triggers,
          outputs: wf.outputs,
          entry: wf.entry ?? null,
          exits: wf.exits ?? null,
          steps: wf.steps ?? [],
        });
        persistedNameRef.current = wf.name;
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
            if (stepsRef.current.length > 0) applyGraph(stepsRef.current, status);
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
    const snap = docKey(name, description, steps, entry, exits);
    const h = historyRef.current;
    if (h.index >= 0 && h.snaps[h.index] === snap) return;
    const trimmed = h.snaps.slice(0, h.index + 1);
    trimmed.push(snap);
    const limited = trimmed.slice(-50);
    historyRef.current = { snaps: limited, index: limited.length - 1 };
    syncHistoryFlags();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, name, description, entry, exits, started]);

  // NOTE: validation problems are NOT auto-shown in the banner while editing.
  // They live in the status bar + Problems tab; the banner is reserved for an
  // explicit Run (see `executeRun`) or a real save/run failure.

  const applySnapshot = useCallback(
    (snap: string) => {
      const parsed = JSON.parse(snap) as {
        s: WorkflowStep[];
        n: string;
        d: string;
        e?: string | null;
        x?: string[] | null;
      };
      suspendHistoryRef.current = true;
      setName(parsed.n);
      setDescription(parsed.d);
      const e = parsed.e ?? null;
      const x = parsed.x ?? null;
      entryRef.current = e;
      exitsRef.current = x;
      setEntry(e);
      setExits(x);
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
  // NOTE: these deliberately compute the next tree from the current `steps`
  // value and then call `rebuild` — they must NOT perform side effects inside a
  // `setSteps(updater)` callback, because React StrictMode double-invokes
  // updaters (which would add/delete nodes twice in development).
  const patchSelected = useCallback(
    (patch: Partial<WorkflowStep>) => {
      setSteps(updateLeaf(steps, selectedPath, patch));
      setNodes((cur) =>
        cur.map((n) =>
          n.id === selectedId
            ? { ...n, data: { ...n.data, step: { ...(n.data.step as WorkflowStep), ...patch } } }
            : n,
        ),
      );
    },
    [steps, selectedPath, selectedId, setNodes],
  );

  const addKind = useCallback(
    (kind: string, position?: { x: number; y: number }) => {
      // Append WITHOUT wiring: a new node starts unconnected and the author
      // draws the connections (deleting them then sticks).
      const step = newStep(steps, kind);
      const next = [...steps, step];
      if (position) positionsRef.current.set(step.id, position);
      rebuild(next);
      setSelectedId(pathKey([next.length - 1]));
    },
    [steps, rebuild],
  );

  const addChild = useCallback(
    (slot: 'then' | 'else' | 'body') => {
      if (!selected) return;
      const parent = locate(steps, selectedPath);
      if (!parent) return;
      const children = ((parent[slot] as WorkflowStep[]) ?? []).slice();
      children.push(newStep(steps));
      rebuild(updateLeaf(steps, selectedPath, { [slot]: children } as Partial<WorkflowStep>));
      setSelectedId(pathKey([...selectedPath, slot, children.length - 1]));
    },
    [selected, steps, selectedPath, rebuild],
  );

  // When steps are removed, drop any wiring that now points at a missing step
  // (entry/exits + dangling `next`) so saving can't fail validation.
  const pruneWiringFor = useCallback((nextSteps: WorkflowStep[]): WorkflowStep[] => {
    const pruned = pruneWiring(nextSteps, entryRef.current, exitsRef.current);
    entryRef.current = pruned.entry;
    exitsRef.current = pruned.exits;
    setEntry(pruned.entry);
    setExits(pruned.exits);
    return pruned.steps;
  }, []);

  const deleteSelected = useCallback(() => {
    if (selectedPath.length === 0) return;
    rebuild(pruneWiringFor(removeLeaf(steps, selectedPath)));
    setSelectedId('');
  }, [steps, selectedPath, rebuild, pruneWiringFor]);

  const duplicateSelected = useCallback(() => {
    if (!selected || selectedPath.length === 0) return;
    const index = selectedPath[selectedPath.length - 1];
    if (typeof index !== 'number') return;
    const listPath = selectedPath.slice(0, -1);
    const list = getList(steps, listPath).slice();
    const source = list[index];
    if (!source) return;
    const clone: WorkflowStep = {
      ...JSON.parse(JSON.stringify(source)),
      id: newStep(steps).id,
      next: '',
    };
    list.splice(index + 1, 0, clone);
    rebuild(setList(steps, listPath, list));
    setSelectedId(pathKey([...listPath, index + 1]));
  }, [selected, steps, selectedPath, rebuild]);

  const move = useCallback(
    (dir: -1 | 1) => {
      const index = selectedPath[selectedPath.length - 1];
      if (typeof index !== 'number') return;
      const listPath = selectedPath.slice(0, -1);
      const list = getList(steps, listPath).slice();
      const target2 = index + dir;
      if (target2 < 0 || target2 >= list.length) return;
      const [item] = list.splice(index, 1);
      if (item) list.splice(target2, 0, item);
      rebuild(setList(steps, listPath, list));
      setSelectedId(pathKey([...listPath, target2]));
    },
    [steps, selectedPath, rebuild],
  );

  const relayout = useCallback(() => {
    const collected = collectStepGraph(steps, runStatus, { sequential: false });
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
      // Only the explicit connection is written — never materialise a chain.
      const list = getList(steps, listPath).slice();
      const sourceStep = list[si];
      const targetStep = list[ti];
      if (!sourceStep || !targetStep) return;
      const resolved = list.map((s) =>
        s.next === targetStep.id && s.id !== sourceStep.id ? { ...s, next: '' } : s,
      );
      const idx = resolved.findIndex((s) => s.id === sourceStep.id);
      resolved[idx] = { ...resolved[idx]!, next: targetStep.id };
      rebuild(setList(steps, listPath, resolved));
    },
    [steps, rebuild],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const src = connection.source;
      const tgt = connection.target;
      if (!src || !tgt) return;
      // Trigger (input) → top-level step: set the workflow entry.
      if (src === '__input__') {
        const path = idToPathRef.current.get(tgt);
        const step = path && parsePath(path).length === 1 ? locate(steps, parsePath(path)) : null;
        if (!step) return;
        entryRef.current = step.id;
        setEntry(step.id);
        rebuild(steps);
        return;
      }
      // Top-level step → Output: add an exit.
      if (tgt === '__output__') {
        const path = idToPathRef.current.get(src);
        const step = path && parsePath(path).length === 1 ? locate(steps, parsePath(path)) : null;
        if (!step) return;
        const derived = flowEndpoints(steps).outputSource;
        const base = exitsRef.current ?? (derived ? [derived] : []);
        exitsRef.current = Array.from(new Set([...base, step.id]));
        setExits(exitsRef.current);
        rebuild(steps);
        return;
      }
      const sourcePath = idToPathRef.current.get(src);
      const targetPath = idToPathRef.current.get(tgt);
      if (sourcePath && targetPath) connectSiblings(sourcePath, targetPath);
    },
    [connectSiblings, steps, rebuild],
  );

  const detachEdges = useCallback(
    (list: Edge[]) => {
      if (list.length === 0) return;
      let next = steps;
      let wiringChanged = false;
      for (const edge of list) {
        // Trigger → step: disconnect the input (entry becomes explicit-empty).
        if (edge.source === '__input__') {
          entryRef.current = '';
          setEntry('');
          wiringChanged = true;
          continue;
        }
        // step → Output: remove that step from the exits.
        if (edge.target === '__output__') {
          const sp = idToPathRef.current.get(edge.source);
          const step = sp ? locate(steps, parsePath(sp)) : null;
          if (step) {
            const derived = flowEndpoints(steps).outputSource;
            const base = exitsRef.current ?? (derived ? [derived] : []);
            exitsRef.current = base.filter((id) => id !== step.id);
            setExits(exitsRef.current);
            wiringChanged = true;
          }
          continue;
        }
        // Internal sibling link: clear the successor.
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
      if (wiringChanged || next !== steps) rebuild(next);
    },
    [steps, rebuild],
  );

  // ── multi-selection (marquee) ───────────────────────────────────────
  const selectionPaths = useMemo<StepPath[]>(
    () =>
      selectedNodeIds
        .map((id) => idToPathRef.current.get(id))
        .filter((p): p is string => !!p)
        .map((p) => parsePath(p)),
    [selectedNodeIds],
  );

  const deleteSelection = useCallback(() => {
    const paths = selectedNodeIds
      .map((id) => idToPathRef.current.get(id))
      .filter((p): p is string => !!p)
      .map((p) => parsePath(p));
    const edgesToDelete = edgesRef.current.filter((e) => selectedEdgeIds.includes(e.id));
    if (paths.length > 0) {
      rebuild(pruneWiringFor(removeLeaves(steps, paths)));
      setSelectedId('');
    }
    if (edgesToDelete.length > 0) detachEdges(edgesToDelete);
    setSelectedNodeIds([]);
    setSelectedEdgeIds([]);
  }, [selectedNodeIds, selectedEdgeIds, steps, rebuild, detachEdges, pruneWiringFor]);

  const duplicateSelection = useCallback(() => {
    const paths = selectedNodeIds
      .map((id) => idToPathRef.current.get(id))
      .filter((p): p is string => !!p)
      .map((p) => parsePath(p));
    if (paths.length > 1) {
      rebuild(duplicateLeaves(steps, paths));
      setSelectedId('');
      setSelectedNodeIds([]);
      return;
    }
    duplicateSelected();
  }, [selectedNodeIds, steps, rebuild, duplicateSelected]);

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
      setSelectedEdgeIds([]);
    };
  }, [detachEdges]);

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const source = 'source' in connection ? connection.source : '';
    const target2 = 'target' in connection ? connection.target : '';
    if (!source || !target2 || source === target2) return false;
    // Endpoints are directional: the Trigger only emits, the Output only receives.
    if (source === '__output__' || target2 === '__input__') return false;
    const sourcePath = idToPathRef.current.get(source);
    const targetPath = idToPathRef.current.get(target2);
    // Trigger → any top-level step.
    if (source === '__input__') return !!targetPath && parsePath(targetPath).length === 1;
    // Any top-level step → Output.
    if (target2 === '__output__') return !!sourcePath && parsePath(sourcePath).length === 1;
    // Otherwise only siblings (same parent list) may be wired.
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
    async (opts: { closeAfter?: boolean; asName?: string; silent?: boolean } = {}): Promise<boolean> => {
      // Saving is NEVER gated on validation: the document is persisted as-is so
      // authors can keep an in-progress/broken workflow. Validation problems are
      // surfaced at RUN time (see `executeRun`), not here. Autosave passes
      // `silent` so a failing autosave never pops/keeps a banner.
      if (!opts.silent) setErrors([]);
      const finalName = (opts.asName ?? name).trim();
      if (!finalName) {
        if (!opts.silent) setErrors([t('workflows.name_required')]);
        setSaveState('error');
        return false;
      }
      setBusy(true);
      setSaveState('saving');
      try {
        const pruned = pruneWiring(steps, entryRef.current, exitsRef.current);
        const finalSteps = renumberWorkflowSteps(pruned.steps);
        const wiring = remapEndpointIds(pruned.steps, pruned.entry, pruned.exits);
        // The backend requires a non-empty description; fall back to the name so
        // a brand-new workflow can always be saved without forcing the user to
        // fill a description field first.
        const finalDescription = description.trim() || finalName;
        const payload = {
          name: finalName,
          description: finalDescription,
          version: version ?? 1,
          inputs: inputs ?? [],
          steps: finalSteps,
          triggers,
          platform,
          entry: wiring.entry,
          exits: wiring.exits,
        };
        const rendered = await chatService.renderWorkflowSteps(payload);
        // Diagnostics (unknown params, missing required params, …) do NOT block a
        // save — only a genuine render failure (no YAML at all) does.
        if (!rendered.yaml) {
          if (!opts.silent) setErrors(rendered.errors.length ? rendered.errors : [t('workflows.graph_render_failed')]);
          setSaveState('error');
          return false;
        }
        // Update under the name it was last SAVED as (so renaming the document
        // renames the backend workflow via update). Save As always creates.
        const existingName = persistedNameRef.current;
        const isExisting = !!existingName && !opts.asName;
        // Studio saves are DRAFTS: capability problems (unknown/missing params …)
        // never block a save; they are enforced when the workflow is run.
        const result = isExisting
          ? await chatService.updateWorkflow(existingName, rendered.yaml, true)
          : await chatService.createWorkflow(rendered.yaml, true, true);
        if (result.status !== 'ok') throw new Error(result.message || t('workflows.save_failed'));
        persistedNameRef.current = finalName;
        setName(finalName);
        setBaseline(docKey(finalName, description, steps, entryRef.current, exitsRef.current));
        setBaselinePlatform(platform);
        historyRef.current = { snaps: [docKey(finalName, description, steps, entryRef.current, exitsRef.current)], index: 0 };
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
        if (!opts.silent) setErrors([translateError(error)]);
        setSaveState('error');
        return false;
      } finally {
        setBusy(false);
      }
    },
    [steps, name, description, version, inputs, triggers, syncHistoryFlags, onSaved, onClose],
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
          entry: wf.entry ?? null,
          exits: wf.exits ?? null,
          steps: wf.steps ?? [],
        });
        persistedNameRef.current = wf.name;
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
        // The workflow's real name lives inside the YAML, which may differ from
        // the file basename; prefer it so we open the thing we just created.
        const yamlName = /^\s*name:\s*["']?([^"'\n]+?)["']?\s*$/m.exec(text)?.[1]?.trim() ?? '';
        const before = (await chatService.listWorkflows().catch(() => ({ workflows: [] }))).workflows.map((w) => w.name);
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
        const after = (await chatService.listWorkflows()).workflows.map((w) => w.name);
        const created = yamlName || after.find((n) => !before.includes(n)) || file.name.replace(/\.(ya?ml|json)$/i, '');
        setLibrary((await chatService.listWorkflows()).workflows);
        await openByName(created);
      } catch (error) {
        setErrors([translateError(error)]);
      }
    },
    [openByName],
  );

  const exportDoc = useCallback(
    async (format: 'yaml' | 'json') => {
      try {
        const base = name || 'workflow';
        const pruned = pruneWiring(steps, entryRef.current, exitsRef.current);
        const wiring = remapEndpointIds(pruned.steps, pruned.entry, pruned.exits);
        if (format === 'json') {
          const payload = {
            name: base,
            description: description.trim() || base,
            version: version ?? 1,
            inputs: inputs ?? [],
            steps: renumberWorkflowSteps(pruned.steps),
            triggers,
            entry: wiring.entry,
            exits: wiring.exits,
          };
          download(`${base}.json`, JSON.stringify(payload, null, 2), 'application/json;charset=utf-8');
          return;
        }
        if (persistedNameRef.current && name) {
          const result = await chatService.exportWorkflow(name);
          download(`${name}.yaml`, result.yaml, 'text/yaml;charset=utf-8');
          return;
        }
        const rendered = await chatService.renderWorkflowSteps({
          name: base,
          description: description.trim() || base,
          version: version ?? 1,
          inputs: inputs ?? [],
          steps: renumberWorkflowSteps(pruned.steps),
          triggers,
          platform,
          entry: wiring.entry,
          exits: wiring.exits,
        });
        if (rendered.yaml) download(`${base}.yaml`, rendered.yaml, 'text/yaml;charset=utf-8');
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
        let nextSteps = wf.steps ?? [];
        let entryVal = wf.entry ?? null;
        let exitsVal = wf.exits ?? null;
        if (entryVal === null && exitsVal === null) {
          nextSteps = chainWiring(nextSteps);
          const derived = flowEndpoints(nextSteps);
          entryVal = derived.inputTarget || '';
          exitsVal = derived.outputSource ? [derived.outputSource] : [];
        }
        suspendHistoryRef.current = true;
        entryRef.current = entryVal;
        exitsRef.current = exitsVal;
        setEntry(entryVal);
        setExits(exitsVal);
        setName(wf.name);
        setVersion(wf.version);
        setSteps(nextSteps);
        setBaseline(docKey(wf.name, wf.description, nextSteps, entryVal, exitsVal));
        rebuild(nextSteps);
        setSelectedId(nextSteps.length > 0 ? pathKey([0]) : '');
        historyRef.current = { snaps: [docKey(wf.name, wf.description, nextSteps, entryVal, exitsVal)], index: 0 };
        syncHistoryFlags();
        setSelectedVersion(v);
        setErrors([]);
      } catch (error) {
        setErrors([translateError(error)]);
      } finally {
        setVersionBusy(false);
      }
    },
    [name, rebuild, syncHistoryFlags],
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
    if (!persistedNameRef.current || !name) {
      setErrors([t('workflows.run_requires_save')]);
      return;
    }
    if (!platformCompatible) {
      setErrors([
        t('workflows.platform_mismatch', {
          platforms: platformTags.map((x) => PLATFORM_NAMES[x] ?? x).join(' / '),
          current: PLATFORM_NAMES[hostPlatform] ?? hostPlatform,
        }),
      ]);
      setBottomTab('problems');
      setBottomOpen(true);
      return;
    }
    const { inputs: resolved, errors } = collectRunInputs(inputs, runInputs);
    if (Object.keys(errors).length > 0) {
      // Inline field errors are shown in the Output tab; just reveal it.
      setBottomTab('output');
      setBottomOpen(true);
      return;
    }
    // Persist unsaved edits first (saving is never blocked) so the run matches
    // exactly what is on screen.
    if (dirty && !(await persist())) return;
    // Pre-flight: validation problems BLOCK the run (and raise the banner).
    try {
      const pruned = pruneWiring(steps, entryRef.current, exitsRef.current);
      const wiring = remapEndpointIds(pruned.steps, pruned.entry, pruned.exits);
      const rendered = await chatService.renderWorkflowSteps({
        name: name || 'workflow',
        description: description.trim() || name || 'workflow',
        version: version ?? 1,
        inputs: inputs ?? [],
        steps: renumberWorkflowSteps(pruned.steps),
        triggers,
        entry: wiring.entry,
        exits: wiring.exits,
      });
      const problems = [...validateTree(steps), ...rendered.errors];
      if (problems.length > 0) {
        setErrors(problems.slice(0, 6));
        setBottomTab('problems');
        setBottomOpen(true);
        return;
      }
    } catch (error) {
      setErrors([translateError(error)]);
      return;
    }
    setRunBusy(true);
    setRun(null);
    try {
      rememberRunInputs(name, inputs, resolved);
      const result = await chatService.runWorkflow(name, resolved);
      setRun(result.run);
      void loadRunDetail(result.run.run_id);
    } catch (error) {
      setErrors([translateError(error)]);
    } finally {
      setRunBusy(false);
    }
  }, [name, inputs, runInputs, steps, description, version, triggers, dirty, persist, loadRunDetail, platformCompatible, platformTags, hostPlatform]);

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
      const pruned = pruneWiring(steps, entryRef.current, exitsRef.current);
      const wiring = remapEndpointIds(pruned.steps, pruned.entry, pruned.exits);
      const rendered = await chatService.renderWorkflowSteps({
        name: name || 'workflow',
        description: description.trim() || name || 'workflow',
        version: version ?? 1,
        inputs: inputs ?? [],
        steps: renumberWorkflowSteps(pruned.steps),
        triggers,
        entry: wiring.entry,
        exits: wiring.exits,
      });
      setYamlPreview(rendered.yaml || rendered.errors.join('\n'));
    } catch (error) {
      setYamlPreview(translateError(error));
    }
  }, [name, description, version, inputs, steps, triggers]);

  // ── keep the run-inputs form in sync with the declared inputs ───────
  useEffect(() => {
    setRunInputs((prev) => mergeRunInputForm(inputs, prev));
  }, [inputs]);

  // ── persist Studio settings ─────────────────────────────────────────
  useEffect(() => {
    saveStudioSettings({
      edgeType,
      autoSave,
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
    autoSave,
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
  // Gated by the "Auto save" setting (File menu). Failures are silent and are
  // attempted only once per document revision, so a workflow the backend keeps
  // rejecting can never spam/flicker the error banner in a retry loop.
  useEffect(() => {
    if (!autoSave || !started) return;
    if (!dirty || busy || !name.trim() || validateTree(steps).length > 0 || steps.length === 0) return;
    const snap = JSON.stringify({ name, description, steps });
    if (autoSaveTriedRef.current === snap) return;
    const handle = setTimeout(() => {
      autoSaveTriedRef.current = snap;
      void persist({ silent: true });
    }, 800);
    return () => clearTimeout(handle);
  }, [autoSave, started, dirty, busy, name, description, steps, persist]);

  // ── cancel / close ──────────────────────────────────────────────────
  const cancel = useCallback(() => {
    if (dirty && !window.confirm(t('workflows.unsaved_confirm'))) return;
    onClose();
  }, [dirty, onClose]);

  // ── return to Studio home ───────────────────────────────────────────
  // Leaves the current document (confirming unsaved edits) and shows the
  // start screen. Reset the persisted name so a subsequent blank/new document
  // is treated as NEW and never overwrites the workflow we just left.
  const goHome = useCallback(() => {
    if (dirty && !window.confirm(t('workflows.unsaved_confirm'))) return;
    persistedNameRef.current = '';
    setStarted(false);
    // Refresh the library so the home screen's "recently opened" is current.
    chatService.listWorkflows().then((r) => setLibrary(r.workflows)).catch(() => undefined);
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
        if (isEditableTarget(event.target)) return;
        // Selected connections are deleted first — never the nodes they touch.
        if (selectedEdgeIds.length > 0 || selectedNodeIds.length > 1) {
          event.preventDefault();
          deleteSelection();
          return;
        }
        if (selectedPath.length > 0) {
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
        if (selectedNodeIds.length > 1) duplicateSelection();
        else duplicateSelected();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [
    save,
    executeRun,
    deleteSelected,
    deleteSelection,
    duplicateSelected,
    duplicateSelection,
    undo,
    redo,
    selectedPath.length,
    selectedEdgeIds,
    selectedNodeIds,
  ]);

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
          platform={platform}
          platformCompatible={platformCompatible}
          platformLabel={platformLabelText}
          onPlatformChange={setPlatform}
          onBack={goHome}
          onSave={() => save(false)}
          onRun={() => undefined}
        />
        <ErrorBanner errors={errors} onDismiss={() => setErrors([])} />
        <StartScreen
          recent={recentWorkflows}
          onBlank={() => {
            persistedNameRef.current = '';
            loadDocument({ name: '', description: '', steps: [], materialize: false });
          }}
          onOpen={() => setOpenDialog(true)}
          onImport={() => fileInputRef.current?.click()}
          onOpenWorkflow={(entry) => void openByName(entry.name)}
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
        <DropdownMenuCheckboxItem checked={autoSave} onCheckedChange={(v) => setAutoSave(!!v)}>
          {t('workflows.autosave_toggle')}
        </DropdownMenuCheckboxItem>
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
        <DropdownMenuItem
          disabled={selectionPaths.length === 0}
          onSelect={() => (selectionPaths.length > 1 ? duplicateSelection() : duplicateSelected())}
        >
          {t('workflows.duplicate')}
          <DropdownMenuShortcut>⌘D</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!selected || selectionPaths.length > 1} onSelect={() => move(-1)}>{t('workflows.move_up')}</DropdownMenuItem>
        <DropdownMenuItem disabled={!selected || selectionPaths.length > 1} onSelect={() => move(1)}>{t('workflows.move_down')}</DropdownMenuItem>
        <DropdownMenuItem
          disabled={selectionPaths.length === 0 && selectedEdgeIds.length === 0}
          onSelect={() => (selectionPaths.length > 1 || selectedEdgeIds.length > 0 ? deleteSelection() : deleteSelected())}
        >
          {t('workflows.delete')}
        </DropdownMenuItem>
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
        <DropdownMenuItem onSelect={editorRunWithReveal}>{t('workflows.run_with_inputs')}</DropdownMenuItem>
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
        canSave={!busy && !!name.trim() && dirty}
        canUndo={canUndo}
        canRedo={canRedo}
        saveState={saveState}
        menus={menus}
        platform={platform}
        platformCompatible={platformCompatible}
        platformLabel={platformLabelText}
        onPlatformChange={setPlatform}
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
                {leftTab === 'nodes' ? <NodePalette onDragKind={() => undefined} /> : null}
                {leftTab === 'outline' ? (
                  <OutlinePanel steps={steps} selectedId={selectedId} onSelect={(k) => setSelectedId(k)} />
                ) : null}
                {leftTab === 'versions' ? (
                  <VersionsPanel
                    versions={versions}
                    selectedVersion={selectedVersion}
                    busy={versionBusy}
                    canDelete={!!persistedNameRef.current}
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
                    <button
                      type="button"
                      className="wfs-toolbar-btn wfs-toolbar-btn--ghost"
                      onClick={() => (yamlPreview ? setYamlPreview('') : void renderYamlPreview())}
                    >
                      <FileText size={13} /> {t('workflows.code_render_yaml')}
                    </button>
                    <button
                      type="button"
                      className="wfs-error-banner__close"
                      onClick={() => setCodeMode(false)}
                      title={t('common.close')}
                      aria-label={t('common.close')}
                    >
                      <X size={14} />
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
                  <div className="wfs-yaml-preview">
                    <div className="wfs-yaml-preview__head">
                      <span className="wf-section__title">YAML</span>
                      <button
                        type="button"
                        className="wfs-error-banner__close"
                        onClick={() => setYamlPreview('')}
                        title={t('common.close')}
                        aria-label={t('common.close')}
                      >
                        <X size={14} />
                      </button>
                    </div>
                    <pre className="skill-detail__pre wfs-yaml-preview__code">{yamlPreview}</pre>
                  </div>
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
                  onPaneClick={() => {
                    setSelectedId('');
                    setSelectedNodeIds([]);
                    setSelectedEdgeIds([]);
                  }}
                  onSelectionChange={(params) => {
                    // React Flow can emit this repeatedly; only commit when the
                    // ids actually change, otherwise we feed the store back into
                    // React state and hit an infinite update loop.
                    const nodeIds = params.nodes.map((n) => n.id).filter((id) => idToPathRef.current.has(id));
                    const edgeIds = params.edges.map((e) => e.id);
                    setSelectedNodeIds((prev) =>
                      prev.length === nodeIds.length && prev.every((v, i) => v === nodeIds[i]) ? prev : nodeIds,
                    );
                    setSelectedEdgeIds((prev) =>
                      prev.length === edgeIds.length && prev.every((v, i) => v === edgeIds[i]) ? prev : edgeIds,
                    );
                    if (nodeIds.length === 1) {
                      const path = idToPathRef.current.get(nodeIds[0]!);
                      if (path) setSelectedId(path);
                    }
                  }}
                  onEdgeClick={(_e, edge) => {
                    setSelectedEdgeIds((prev) => (prev.length === 1 && prev[0] === edge.id ? prev : [edge.id]));
                    setSelectedNodeIds((prev) => (prev.length === 0 ? prev : []));
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
                  selectionOnDrag
                  selectionMode={SelectionMode.Partial}
                  panOnDrag={[1, 2]}
                  panOnScroll={false}
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

                {selectedNodeIds.length > 1 ? (
                  <div className="wfs-selbar" role="toolbar" aria-label={t('workflows.multi_select')}>
                    <span className="wfs-selbar__count">
                      {selectedNodeIds.length} {t('workflows.selected')}
                    </span>
                    <button
                      type="button"
                      className="wfs-selbar__btn"
                      onClick={duplicateSelection}
                      title={t('workflows.duplicate')}
                    >
                      <Copy size={14} />
                    </button>
                    <button type="button" className="wfs-selbar__btn" onClick={relayout} title={t('workflows.auto_layout')}>
                      <Sparkles size={14} />
                    </button>
                    <button
                      type="button"
                      className="wfs-selbar__btn wfs-selbar__btn--danger"
                      onClick={deleteSelection}
                      title={t('workflows.delete')}
                    >
                      <Trash2 size={14} />
                    </button>
                    <button
                      type="button"
                      className="wfs-selbar__btn"
                      onClick={() => {
                        setSelectedNodeIds([]);
                        setSelectedEdgeIds([]);
                        setSelectedId('');
                      }}
                      title={t('workflows.clear_selection')}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ) : null}

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
                  inputSpecs={inputs}
                  inputValues={runInputs}
                  inputErrors={runInputErrors}
                  runBusy={runBusy}
                  onInputChange={(n, v) => setRunInputs((prev) => ({ ...prev, [n]: v }))}
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
                {selectedNodeIds.length > 1 ? (
                  <div>
                    <div className="wf-section__title">{t('workflows.multi_select')}</div>
                    <p className="wfs-empty-note" style={{ padding: '2px 0 10px' }}>
                      {selectedNodeIds.length} {t('workflows.selected')}
                    </p>
                    <div className="wfs-multi-actions">
                      <Button variant="secondary" size="sm" onClick={duplicateSelection}>
                        <Copy size={14} /> {t('workflows.duplicate')}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={relayout}>
                        <Sparkles size={14} /> {t('workflows.auto_layout')}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={deleteSelection}>
                        <Trash2 size={14} /> {t('workflows.delete')}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setSelectedNodeIds([]);
                          setSelectedEdgeIds([]);
                          setSelectedId('');
                        }}
                      >
                        <X size={14} /> {t('workflows.clear_selection')}
                      </Button>
                    </div>
                  </div>
                ) : selected ? (
                  <NodeInspector
                    selected={selected}
                    tab={inspectorTab}
                    onTab={setInspectorTab}
                    onPatch={patchSelected}
                    onAddChild={addChild}
                    state={stepState[selected.id]}
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
                    onInputsChange={setInputs}
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
  /** Current `platform` declaration ('' / 'any' = all OSes). */
  platform: string;
  /** False when the workflow targets a different OS than this machine. */
  platformCompatible: boolean;
  /** Human label of the required platforms when incompatible. */
  platformLabel: string;
  onPlatformChange: (value: string) => void;
  onBack: () => void;
  onSave: () => void;
  onRun: () => void;
}

const isWinPlatform = typeof window !== 'undefined' && window.electronAPI?.platform === 'win32';

const PLATFORM_ALIASES: Record<string, string> = {
  mac: 'darwin', macos: 'darwin', osx: 'darwin', darwin: 'darwin',
  win: 'win32', windows: 'win32', win32: 'win32',
  linux: 'linux',
};

/** Normalize a workflow `platform` declaration to canonical tags. */
function platformTagsOf(value: string): string[] {
  const raw = (value || '').trim().toLowerCase();
  if (!raw || raw === 'any' || raw === '*') return ['darwin', 'win32', 'linux'];
  const tags = raw
    .replace(/,/g, ' ')
    .split(/\s+/)
    .map((token) => PLATFORM_ALIASES[token])
    .filter((token): token is string => Boolean(token));
  return tags.length ? Array.from(new Set(tags)) : ['darwin', 'win32', 'linux'];
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
  platform,
  platformCompatible,
  platformLabel,
  onPlatformChange,
  onBack,
  onSave,
  onRun,
}: TopbarProps) {
  const topbarRef = useRef<HTMLDivElement | null>(null);
  // Mirror the macOS traffic-light inset on Windows: keep the native window
  // controls overlay (top-right) clear of the toolbar buttons, and sync the
  // overlay colors to this bar's theme.
  useTitlebarOverlay(topbarRef, 44);

  return (
    <div
      ref={isWindow ? topbarRef : undefined}
      className={`wfs-topbar${isWindow ? ' wfs-topbar--window' : ''}${isWindow && isMac ? ' wfs-topbar--mac' : ''}${isWindow && isWinPlatform ? ' wfs-topbar--win' : ''}`}
    >
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
      {!platformCompatible ? (
        <span className="wfs-platform-badge" title={platformLabel}>{platformLabel}</span>
      ) : null}
      <select
        className="wfs-platform-select"
        value={['darwin', 'win32', 'linux'].includes(platform.toLowerCase()) ? platform.toLowerCase() : ''}
        onChange={(e) => onPlatformChange(e.target.value)}
        title={t('workflows.platform')}
        aria-label={t('workflows.platform')}
      >
        <option value="">{t('workflows.platform_any')}</option>
        <option value="darwin">macOS</option>
        <option value="win32">Windows</option>
        <option value="linux">Linux</option>
      </select>
      {saveState && saveState !== 'idle' ? (
        <span className={`wfs-save-state wfs-save-state--${saveState}`}>{saveStateLabel(saveState)}</span>
      ) : null}
      <button type="button" className="wfs-toolbar-btn" onClick={onSave} disabled={!canSave} title={`${t('workflows.save')} (⌘S)`}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
        {t('workflows.save')}
      </button>
      <button
        type="button"
        className="wfs-toolbar-btn wfs-toolbar-btn--primary"
        onClick={onRun}
        disabled={!platformCompatible}
        title={platformCompatible ? `${t('workflows.run')} (⌘↵)` : platformLabel}
      >
        <Zap size={14} />
        {t('workflows.run')}
      </button>
    </div>
  );
}

/** Serialise the document (including endpoint wiring) for dirty/history checks. */
function docKey(
  name: string,
  description: string,
  steps: WorkflowStep[],
  entry: string | null,
  exits: string[] | null,
): string {
  return JSON.stringify({ n: name, d: description, s: steps, e: entry, x: exits });
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
