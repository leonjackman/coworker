import { useEffect, useRef, useState } from 'react';
import type { RuntimeConfig } from '../types';
import { t } from '../lib/i18n';
import { TerminalView } from './TerminalView';

export type BottomPanelView = 'terminal' | 'logs';

interface WorkspaceBottomPanelProps {
  view: BottomPanelView;
  runtimeStatus: 'connecting' | 'ready' | 'error';
  runtimeConfig?: RuntimeConfig | null;
  sessionCount: number;
  projectCount: number;
  messageCount: number;
  projectId?: string;
  workspaceLabel?: string;
  onViewChange: (view: BottomPanelView) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  onResizeHeight?: (height: number) => void;
}

const MIN_HEIGHT = 120;
const MAX_HEIGHT_RATIO = 0.8;

export function WorkspaceBottomPanel({
  view,
  sessionCount,
  projectCount,
  messageCount,
  projectId,
  onViewChange,
  onResizeStart,
  onResizeEnd,
  onResizeHeight,
}: WorkspaceBottomPanelProps) {
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);

  const handleResizePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const current = document.querySelector('.bottom-panel')?.getBoundingClientRect().height ?? 190;
    dragRef.current = { startY: event.clientY, startHeight: current };
    document.body.classList.add('bottom-panel-resizing');
    onResizeStart?.();
    window.addEventListener('pointermove', handleResizePointerMove);
    window.addEventListener('pointerup', handleResizePointerUp);
  };

  const handleResizePointerMove = (event: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const delta = drag.startY - event.clientY;
    const maxHeight = Math.round(window.innerHeight * MAX_HEIGHT_RATIO);
    const next = Math.min(Math.max(drag.startHeight + delta, MIN_HEIGHT), maxHeight);
    onResizeHeight?.(next);
  };

  const handleResizePointerUp = () => {
    dragRef.current = null;
    document.body.classList.remove('bottom-panel-resizing');
    onResizeEnd?.();
    window.removeEventListener('pointermove', handleResizePointerMove);
    window.removeEventListener('pointerup', handleResizePointerUp);
  };

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handleResizePointerMove);
      window.removeEventListener('pointerup', handleResizePointerUp);
      document.body.classList.remove('bottom-panel-resizing');
    };
  }, []);

  return (
    <section className="bottom-panel">
      <div
        className="bottom-panel__resizer"
        role="separator"
        aria-orientation="horizontal"
        aria-label={t('bottom_panel.resize')}
        onPointerDown={handleResizePointerDown}
      />
      <div className="bottom-panel__tabs">
        <button
          type="button"
          className={view === 'terminal' ? 'bottom-panel__tab bottom-panel__tab--active' : 'bottom-panel__tab'}
          onClick={() => onViewChange('terminal')}
        >
          {t('bottom_panel.terminal')}
        </button>
        <button
          type="button"
          className={view === 'logs' ? 'bottom-panel__tab bottom-panel__tab--active' : 'bottom-panel__tab'}
          onClick={() => onViewChange('logs')}
        >
          {t('bottom_panel.logs')}
        </button>
      </div>
      <div className="bottom-panel__content">
        {view === 'terminal' ? (
          <TerminalView {...(projectId ? { projectId } : {})} />
        ) : (
          <pre>{`runtime: ready\nsessions: ${sessionCount}\nprojects: ${projectCount}\nmessages: ${messageCount}`}</pre>
        )}
      </div>
    </section>
  );
}
