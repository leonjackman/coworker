import { useCallback } from 'react';
import { createPortal } from 'react-dom';

import { useLanguage } from '../../lib/i18n';
import {
  broadcastWorkflowsChanged,
  closeWorkflowEditor,
  emitWorkflowsChanged,
  useWorkflowEditorTarget,
} from '../../lib/workflowEditorBus';
import { WorkflowStudio } from './studio/WorkflowStudio';

/**
 * Standalone Workflow Studio.
 *
 * A self-contained full-window surface hosted ABOVE the cw shell. It is opened
 * by name / new via the workflow editor bus and autosaves back to the backend,
 * emitting a change event so cw's list/detail refresh in real time.
 */
export function WorkflowEditorApp({ mode = 'inapp' }: { mode?: 'window' | 'inapp' }) {
  useLanguage();
  const isWindow = mode === 'window';
  const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.userAgent);
  const busTarget = useWorkflowEditorTarget();

  const handleSaved = useCallback(() => {
    emitWorkflowsChanged();
    // Separate editor window → tell the main window to refresh too.
    broadcastWorkflowsChanged();
  }, []);

  if (!busTarget) return null;

  // Portal to <body> so the full-window overlay is never trapped by a
  // transformed/overflow-clipped ancestor in the cw shell.
  return createPortal(
    <div
      className={`wfe-root${isWindow ? ' wfe-root--window' : ''}${isWindow && isMac ? ' wfe-root--mac' : ''}`}
      role="dialog"
      aria-modal="true"
    >
      <WorkflowStudio
        key={`${busTarget.name}:${busTarget.isNew}`}
        target={{ name: busTarget.name, isNew: busTarget.isNew }}
        mode={mode}
        onClose={closeWorkflowEditor}
        onSaved={handleSaved}
      />
    </div>,
    document.body,
  );
}
