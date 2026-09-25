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

/** Collect step nodes/edges (no positions) for a workflow's step tree. */
export function collectStepGraph(
  steps: WorkflowStep[],
  status: Record<string, string> = {},
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const walk = (list: WorkflowStep[], listPath: Array<number | string>, parentId?: string, slot?: string) => {
    list.forEach((step, index) => {
      const path = [...listPath, index];
      const id = path.join('.');
      nodes.push({
        id,
        type: 'step',
        position: { x: 0, y: 0 },
        data: { step, ...(slot ? { slot } : {}), ...(status[step.id] ? { status: status[step.id] } : {}) },
      });
      if (parentId) {
        edges.push({ id: `e-${parentId}-${id}`, source: parentId, target: id, label: slot, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
      }
      SLOTS.forEach((s) => {
        const children = step[s] as WorkflowStep[] | undefined;
        if (children?.length) walk(children, [...path, s], id, s);
      });
    });
    // Within a list: use explicit ``next`` wiring when present, else sequential.
    // NOTE: node ids are path keys ("0","2.then.1"), but ``next`` stores step
    // ids ("open"), so map step id -> node (path) id for the edge target.
    const wired = list.some((step) => step.next);
    const idToNode = new Map(list.map((step, i) => [step.id, [...listPath, i].join('.')]));
    list.forEach((step, index) => {
      const from = [...listPath, index].join('.');
      const nextNode = wired
        ? step.next
          ? idToNode.get(step.next) ?? ''
          : ''
        : index + 1 < list.length
          ? [...listPath, index + 1].join('.')
          : '';
      if (!nextNode) return;
      edges.push({ id: `seq-${from}-${nextNode}`, source: from, target: nextNode, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } });
    });
  };
  walk(steps, []);
  return { nodes, edges };
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

export function buildGraph(steps: WorkflowStep[], status: Record<string, string> = {}): { nodes: Node[]; edges: Edge[] } {
  const { nodes, edges } = collectStepGraph(steps, status);
  return { nodes: layoutGraph(nodes, edges), edges };
}
