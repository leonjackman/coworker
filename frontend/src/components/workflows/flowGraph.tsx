import { Handle, MarkerType, Position, type Edge, type Node, type NodeProps } from '@xyflow/react';
import dagre from 'dagre';
import { kindIcon, kindStripe } from './kinds';
import type { WorkflowStep } from '../../types';

export const NODE_W = 214;
export const NODE_H = 58;

const SLOTS: Array<'then' | 'else' | 'body'> = ['then', 'else', 'body'];

export interface StepNodeData extends Record<string, unknown> {
  step: WorkflowStep;
  slot?: string;
  status?: string;
}

export function StepNode({ data, selected }: NodeProps) {
  const d = data as StepNodeData;
  const step = d.step;
  const Icon = kindIcon(step.kind);
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
        {badge ? (
          <span className={`settings-chip wf-node__status settings-chip--${badge === 'failed' ? 'bad' : badge === 'ok' ? 'ok' : 'dim'}`}>
            {badge}
          </span>
        ) : null}
      </div>
      <div className="wf-node__meta">
        {d.slot ? `${d.slot} · ` : ''}
        {step.kind}
        {step.do ? ` · ${step.do}` : ''}
      </div>
      <div className="wf-node__badges">
        {step.mode === 'agent' ? <span className="wf-badge wf-badge--agent">agent</span> : null}
        {step.approval ? <span className="wf-badge wf-badge--human">approval</span> : null}
        {step.on_error && (step.on_error as { then?: string }).then ? (
          <span className="wf-badge">on_error: {(step.on_error as { then?: string }).then}</span>
        ) : null}
        {(step.success ?? []).length > 0 ? <span className="wf-badge">success</span> : null}
      </div>
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

export interface PillNodeData extends Record<string, unknown> {
  label: string;
  kind: 'trigger' | 'output';
}

export function PillNode({ data }: NodeProps) {
  const d = data as PillNodeData;
  return (
    <div className={`wf-pill wf-pill--${d.kind}`}>
      <Handle type="target" position={Position.Top} className="wf-handle" />
      <span className="wf-pill__label">{d.label}</span>
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

export const nodeTypes = { step: StepNode, pill: PillNode };

/**
 * Stable node id for a step. Based on the user-facing step id namespaced by its
 * parent, NOT the list index — so reordering/rewiring keeps a node's identity
 * (and thus its canvas position).
 */
function nodeKey(parentKey: string, slot: string | undefined, stepId: string): string {
  return parentKey ? `${parentKey}/${slot}/${stepId}` : stepId;
}

export interface CollectedGraph {
  nodes: Node[];
  edges: Edge[];
  /** node id -> data path (for locating/editing the underlying step). */
  nodeIdToPath: Map<string, string>;
}

/** Collect step nodes/edges (no positions) for a workflow's step tree. */
export function collectStepGraph(
  steps: WorkflowStep[],
  status: Record<string, string> = {},
): CollectedGraph {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const nodeIdToPath = new Map<string, string>();
  const walk = (
    list: WorkflowStep[],
    listPath: Array<number | string>,
    parentKey: string,
    parentId: string | undefined,
    slot: string | undefined,
  ) => {
    const idToKey = new Map(list.map((step) => [step.id, nodeKey(parentKey, slot, step.id)]));
    list.forEach((step, index) => {
      const id = nodeKey(parentKey, slot, step.id);
      const path = [...listPath, index].join('.');
      nodeIdToPath.set(id, path);
      nodes.push({
        id,
        type: 'step',
        position: { x: 0, y: 0 },
        data: { step, path, ...(slot ? { slot } : {}), ...(status[step.id] ? { status: status[step.id] } : {}) },
      });
      if (parentId) {
        edges.push({ id: `e-${parentId}-${id}`, source: parentId, target: id, label: slot, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
      }
      SLOTS.forEach((s) => {
        const children = step[s] as WorkflowStep[] | undefined;
        if (children?.length) walk(children, [...listPath, index, s], id, id, s);
      });
    });
    // Within a list: explicit ``next`` wiring when present, else sequential.
    const wired = list.some((step) => step.next);
    list.forEach((step, index) => {
      const from = idToKey.get(step.id);
      const nextLocal = wired ? step.next ?? '' : index + 1 < list.length ? list[index + 1]!.id : '';
      const to = nextLocal ? idToKey.get(nextLocal) : undefined;
      if (!from || !to) return;
      edges.push({ id: `seq-${from}-${to}`, source: from, target: to, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
    });
  };
  walk(steps, [], '', undefined, undefined);
  return { nodes, edges, nodeIdToPath };
}

/** dagre top-to-bottom layout for an arbitrary node/edge set. */
export function layoutGraph(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const pos = g.node(n.id);
    return { ...n, position: { x: (pos?.x ?? 0) - NODE_W / 2, y: (pos?.y ?? 0) - NODE_H / 2 } };
  });
}

export interface BuiltGraph {
  nodes: Node[];
  edges: Edge[];
  nodeIdToPath: Map<string, string>;
}

/**
 * Build the graph using stored positions (by node id). Nodes without a stored
 * position get a simple default offset — never an automatic re-layout, so
 * connecting/reordering keeps everything where the user put it. Call
 * ``layoutGraph`` explicitly for the "auto layout" action.
 */
export function buildGraph(
  steps: WorkflowStep[],
  status: Record<string, string> = {},
  positions?: Map<string, { x: number; y: number }>,
): BuiltGraph {
  const { nodes, edges, nodeIdToPath } = collectStepGraph(steps, status);
  const positioned = nodes.map((node, index) => {
    const stored = positions?.get(node.id);
    if (stored) return { ...node, position: stored };
    const fallback = { x: 80 + (index % 3) * 260, y: 60 + Math.floor(index / 3) * 120 };
    positions?.set(node.id, fallback);
    return { ...node, position: fallback };
  });
  return { nodes: positioned, edges, nodeIdToPath };
}
