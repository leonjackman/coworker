import { useEffect, useRef } from 'react';

interface UsePanelResizeOptions {
  /** CSS class added to <body> while dragging (cursor + resizer highlight). */
  bodyClassName: string;
  min: number;
  max: number;
  /** Sign applied to the drag delta. Right-side panels use -1: dragging their
   * left-edge handle leftward grows the panel. Left-side panels use +1. */
  direction?: 1 | -1;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
}

/**
 * Horizontal drag-resize for side panels (right inspector, changes panel).
 * Mirrors the sidebar resizer pattern: a pointer-down on the handle measures
 * the panel's current width, then tracks pointermove/up on window.
 */
export function usePanelResize({
  bodyClassName,
  min,
  max,
  direction = 1,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
}: UsePanelResizeOptions) {
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const handlePointerMove = (event: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const next = Math.min(max, Math.max(min, drag.startWidth + direction * (event.clientX - drag.startX)));
    onResizeWidth(next);
  };

  const handlePointerUp = () => {
    dragRef.current = null;
    document.body.classList.remove(bodyClassName);
    onResizeEnd();
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('pointerup', handlePointerUp);
  };

  useEffect(() => {
    return () => {
      dragRef.current = null;
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      document.body.classList.remove(bodyClassName);
    };
  }, []);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const panel = (event.currentTarget as HTMLElement).parentElement;
    const startWidth = panel?.getBoundingClientRect().width ?? 300;
    dragRef.current = { startX: event.clientX, startWidth };
    document.body.classList.add(bodyClassName);
    onResizeStart();
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
  };

  return handlePointerDown;
}
