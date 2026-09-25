import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useMemo } from 'react';
import { t } from '../../lib/i18n';
import { collectStepGraph, layoutGraph, nodeTypes } from './flowGraph';
import type { WorkflowStep } from '../../types';

interface Props {
  steps: WorkflowStep[];
  triggers: string[];
  outputs: Record<string, string>;
}

/** Read-only visualisation of a workflow: trigger → steps → outputs. */
export function WorkflowFlowGraph({ steps, triggers, outputs }: Props) {
  const { nodes, edges } = useMemo(() => {
    const { nodes: stepNodes, edges: stepEdges } = collectStepGraph(steps, {});
    const triggerId = '__trigger';
    const outputId = '__output';
    const triggerLabel = `${t('workflows.trigger_node')} · ${(triggers.length ? triggers : ['manual']).join(', ')}`;
    const outputLabel = `${t('workflows.output_node')}${Object.keys(outputs).length ? ` · ${Object.keys(outputs).join(', ')}` : ''}`;
    const allNodes: Node[] = [
      { id: triggerId, type: 'pill', position: { x: 0, y: 0 }, data: { label: triggerLabel, kind: 'trigger' } },
      ...stepNodes,
      { id: outputId, type: 'pill', position: { x: 0, y: 0 }, data: { label: outputLabel, kind: 'output' } },
    ];
    const marker = { type: MarkerType.ArrowClosed } as const;
    const allEdges: Edge[] = [...stepEdges];
    if (steps.length > 0) {
      // Top-level node ids are the step ids (stable, not path keys).
      allEdges.push({ id: 'to-first', source: triggerId, target: steps[0]!.id, type: 'smoothstep', markerEnd: marker });
      allEdges.push({ id: 'to-out', source: steps[steps.length - 1]!.id, target: outputId, type: 'smoothstep', markerEnd: marker });
    } else {
      allEdges.push({ id: 'trig-out', source: triggerId, target: outputId, type: 'smoothstep', markerEnd: marker });
    }
    return { nodes: layoutGraph(allNodes, allEdges), edges: allEdges };
  }, [steps, triggers, outputs]);

  return (
    <div className="wf-canvas wf-canvas--readonly">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        minZoom={0.2}
        maxZoom={2}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
