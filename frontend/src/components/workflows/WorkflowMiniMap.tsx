import { useMemo } from 'react';
import type { Node } from '@xyflow/react';
import { NODE_W, NODE_H } from './flowGraph';
import { kindStripe } from './kinds';
import type { WorkflowStep } from '../../types';

interface Props {
  nodes: Node[];
  /** React Flow viewport: { x, y, zoom } (flow px). */
  viewport: { x: number; y: number; zoom: number };
  /** Canvas size in px. */
  canvas: { width: number; height: number };
  /** Called with flow coordinates to centre the viewport on. */
  onNavigate: (flowX: number, flowY: number) => void;
}

const PANEL_W = 260;
const PANEL_H = 160;
const PAD = 30;

function sizeOf(node: Node): { w: number; h: number } {
  const measured = (node as { measured?: { width?: number; height?: number } }).measured;
  if (node.type === 'pill') return { w: measured?.width ?? 180, h: measured?.height ?? 36 };
  return { w: measured?.width ?? NODE_W, h: measured?.height ?? NODE_H };
}

function colorOf(node: Node): string {
  if (node.type === 'pill') {
    const kind = (node.data as { kind?: string }).kind;
    return kind === 'trigger' ? 'var(--accent)' : 'var(--success)';
  }
  const step = (node.data as { step?: WorkflowStep }).step;
  return step ? kindStripe(step.kind) : 'var(--muted-foreground)';
}

/**
 * Photoshop-style navigator: renders all nodes scaled into a small panel plus
 * the current viewport rectangle, and centres the canvas on click/drag.
 */
export function WorkflowMiniMap({ nodes, viewport, canvas, onNavigate }: Props) {
  const scene = useMemo(() => {
    if (nodes.length === 0) return null;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    const boxes = nodes.map((node) => {
      const { w, h } = sizeOf(node);
      minX = Math.min(minX, node.position.x);
      minY = Math.min(minY, node.position.y);
      maxX = Math.max(maxX, node.position.x + w);
      maxY = Math.max(maxY, node.position.y + h);
      return { id: node.id, x: node.position.x, y: node.position.y, w, h, color: colorOf(node) };
    });
    const scale = Math.min((PANEL_W - PAD * 2) / (maxX - minX || 1), (PANEL_H - PAD * 2) / (maxY - minY || 1));
    return { boxes, minX, minY, scale };
  }, [nodes]);

  if (!scene) return null;
  const { boxes, minX, minY, scale } = scene;
  const toPanelX = (fx: number) => PAD + (fx - minX) * scale;
  const toPanelY = (fy: number) => PAD + (fy - minY) * scale;

  const view = {
    x: toPanelX(-viewport.x / viewport.zoom),
    y: toPanelY(-viewport.y / viewport.zoom),
    w: (canvas.width / viewport.zoom) * scale,
    h: (canvas.height / viewport.zoom) * scale,
  };

  const navigate = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * PANEL_W;
    const py = ((event.clientY - rect.top) / rect.height) * PANEL_H;
    onNavigate(minX + (px - PAD) / scale, minY + (py - PAD) / scale);
  };

  return (
    <svg
      className="wf-minimap"
      viewBox={`0 0 ${PANEL_W} ${PANEL_H}`}
      onMouseDown={navigate}
      onMouseMove={(event) => {
        if (event.buttons === 1) navigate(event);
      }}
      role="img"
      aria-label="mini-map"
    >
      <rect x={0} y={0} width={PANEL_W} height={PANEL_H} fill="var(--material-control)" rx={6} />
      {boxes.map((box) => (
        <rect
          key={box.id}
          x={toPanelX(box.x)}
          y={toPanelY(box.y)}
          width={Math.max(2, box.w * scale)}
          height={Math.max(2, box.h * scale)}
          rx={2}
          fill={box.color}
          opacity={0.85}
        />
      ))}
      <rect
        x={view.x}
        y={view.y}
        width={view.w}
        height={view.h}
        fill="color-mix(in oklch, var(--accent) 12%, transparent)"
        stroke="var(--accent)"
        strokeWidth={1.5}
      />
    </svg>
  );
}
