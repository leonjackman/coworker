import { useEffect, useRef, useState } from 'react';
import type { RuntimeConfig, ToolAuditEvent } from '../types';
import { t } from '../lib/i18n';
import { TerminalView } from './TerminalView';
import { chatService } from '../services/chatService';

export type BottomPanelView = 'terminal' | 'logs';

interface WorkspaceBottomPanelProps {
  view: BottomPanelView;
  runtimeStatus: 'connecting' | 'ready' | 'error';
  runtimeConfig?: RuntimeConfig | null;
  sessionCount: number;
  projectCount: number;
  projectId?: string;
  onViewChange: (view: BottomPanelView) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  onResizeHeight?: (height: number) => void;
}

const MIN_HEIGHT = 120;
const MAX_HEIGHT_RATIO = 0.8;

export function WorkspaceBottomPanel({
  view,
  runtimeStatus,
  runtimeConfig,
  sessionCount,
  projectCount,
  projectId,
  onViewChange,
  onResizeStart,
  onResizeEnd,
  onResizeHeight,
}: WorkspaceBottomPanelProps) {
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  const [auditEvents, setAuditEvents] = useState<ToolAuditEvent[]>([]);

  // Load real tool-audit events when the logs view is active (not a placeholder).
  useEffect(() => {
    if (view !== 'logs') return;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await chatService.listToolAudit(40);
        if (!cancelled) setAuditEvents(res.events || []);
      } catch {
        // backend unreachable — keep whatever we have
      }
    };
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [view]);

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
          <div className="bottom-panel__logs">
            <div className="bottom-panel__logs-meta">
              <span>{t('bottom_panel.runtime_status', { status: runtimeStatus })}</span>
              <span>{t('bottom_panel.runtime_mode', { mode: runtimeConfig?.default_mode ?? '-' })}</span>
              <span>{t('bottom_panel.sessions', { count: sessionCount })}</span>
              <span>{t('bottom_panel.projects', { count: projectCount })}</span>
            </div>
            <div className="bottom-panel__logs-list">
              {auditEvents.length === 0 ? (
                <p className="bottom-panel__logs-empty">{t('bottom_panel.logs_empty')}</p>
              ) : (
                auditEvents.slice().reverse().map((event, index) => (
                  <div className="bottom-panel__log-row" key={`${event.timestamp}-${index}`}>
                    <span className={`bottom-panel__log-status bottom-panel__log-status--${event.status}`}>{event.status}</span>
                    <span className="bottom-panel__log-op">{event.operation}</span>
                    <span className="bottom-panel__log-time">{new Date(event.timestamp).toLocaleTimeString()}</span>
                    <span className="bottom-panel__log-detail">
                      {[event.details?.path, Array.isArray(event.details?.command) ? event.details.command.join(' ') : ''].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
