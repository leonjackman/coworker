import { useEffect, useState } from 'react';
import { WorkflowGraphEditor, type GraphEditorTarget } from './WorkflowGraphEditor';
import { chatService } from '../services/chatService';
import { applyTheme, getThemeSettings } from '../lib/theme';
import { t } from '../lib/i18n';

/**
 * Standalone entry rendered inside a dedicated Electron window
 * (``#canvas=<workflow-name>``) that hosts the visual workflow editor.
 */
export function CanvasEditorWindow({ name }: { name: string }) {
  const [target, setTarget] = useState<GraphEditorTarget | null>(null);
  const [error, setError] = useState('');

  // Follow the main window's theme (read persisted settings + live storage sync).
  useEffect(() => {
    applyTheme(getThemeSettings());
    const onStorage = () => applyTheme(getThemeSettings());
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  useEffect(() => {
    document.title = name || 'Workflow';
    void (async () => {
      try {
        const detail = await chatService.getWorkflow(name);
        const wf = detail.workflow;
        setTarget({
          name: wf.name,
          description: wf.description,
          version: wf.version,
          inputs: wf.inputs,
          steps: wf.steps ?? [],
          isNew: false,
        });
      } catch (err) {
        setError(String(err));
      }
    })();
  }, [name]);

  return (
    <div className="wf-popup-shell">
      {error ? (
        <p className="skill-empty">{error}</p>
      ) : target ? (
        <WorkflowGraphEditor target={target} onClose={() => window.close()} onSaved={() => {}} standalone />
      ) : (
        <p className="skill-empty">{t('workflows.loading')}</p>
      )}
    </div>
  );
}
