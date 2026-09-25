import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  getSmoothStepPath,
  getStraightPath,
  type EdgeProps,
} from '@xyflow/react';

/**
 * Editable edge: renders the connection using the workflow's chosen style and,
 * when selected, shows a small ✕ button at the midpoint to disconnect it.
 */
export function EditableEdge(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerEnd,
    selected,
    data,
  } = props;
  const style = (data?.edgeType as string) ?? 'default';

  let path = '';
  if (style === 'straight') {
    [path] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  } else if (style === 'smoothstep') {
    [path] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  } else {
    [path] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  }

  const midX = (sourceX + targetX) / 2;
  const midY = (sourceY + targetY) / 2;
  const onDelete = data?.onDelete as ((edgeId: string) => void) | undefined;

  return (
    <>
      <BaseEdge id={id} path={path} {...(markerEnd ? { markerEnd } : {})} />
      {selected && onDelete ? (
        <EdgeLabelRenderer>
          <button
            type="button"
            className="wf-edge-del"
            style={{ transform: `translate(-50%, -50%) translate(${midX}px, ${midY}px)` }}
            onClick={(event) => {
              event.stopPropagation();
              onDelete(id);
            }}
            aria-label="disconnect"
          >
            ✕
          </button>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const edgeTypes = { editable: EditableEdge };
