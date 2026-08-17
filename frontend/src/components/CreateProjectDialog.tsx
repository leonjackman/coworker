import { FolderPlus, Users, User, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { t } from '../lib/i18n';
import type { ProjectEntry, ProjectMode } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';

interface CreateProjectDialogProps {
  open: boolean;
  onClose: () => void;
  onPickWorkspace: () => Promise<string | null>;
  onCreate: (payload: { name: string; workspace_path: string; mode: ProjectMode }) => Promise<unknown>;
  projects?: ProjectEntry[];
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || t('sidebar.project_new');
}

function normalizePath(path: string): string {
  return path.trim().replace(/\/+$/, '');
}

export function CreateProjectDialog({ open, onClose, onPickWorkspace, onCreate, projects = [] }: CreateProjectDialogProps) {
  const [name, setName] = useState('');
  const [workspacePath, setWorkspacePath] = useState('');
  const [mode, setMode] = useState<ProjectMode>('single');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setName('');
    setWorkspacePath('');
    setMode('single');
    setError('');
    setBusy(false);
  }, [open]);

  const takenModes = useMemo<Set<ProjectMode>>(() => {
    if (!workspacePath) return new Set();
    const normalized = normalizePath(workspacePath);
    const taken = new Set<ProjectMode>();
    for (const project of projects) {
      if (project.workspace_path && normalizePath(project.workspace_path) === normalized && project.mode) {
        taken.add(project.mode);
      }
    }
    return taken;
  }, [workspacePath, projects]);

  const singleTaken = takenModes.has('single');
  const multiTaken = takenModes.has('multi');

  if (!open) return null;

  const pickWorkspace = async () => {
    setError('');
    try {
      const selected = await onPickWorkspace();
      if (!selected) return;
      setWorkspacePath(selected);
      setName((current) => current || folderName(selected));
    } catch (err) {
      // Directory picker is desktop-only; in the browser show a hint instead of
      // an unhandled rejection.
      setError(err instanceof Error ? err.message : 'Unable to pick a workspace directory');
    }
  };

  const submit = async () => {
    if (!workspacePath || !name.trim()) return;
    if (takenModes.has(mode)) {
      setError(t('project_dialog.mode_taken', { mode: mode === 'single' ? t('project_dialog.mode_single') : t('project_dialog.mode_multi') }));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onCreate({ name: name.trim(), workspace_path: workspacePath, mode });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="workspace-dialog" role="dialog" aria-modal="true" aria-labelledby="create-project-title">
        <button className="workspace-dialog__close" type="button" onClick={onClose} aria-label={t('dialog.close')}>
          <X size={18} />
        </button>
        <div className="workspace-dialog__header">
          <p className="workspace-dialog__eyebrow">{t('project_dialog.eyebrow')}</p>
          <h2 id="create-project-title">{t('project_dialog.title')}</h2>
          <p>{t('project_dialog.description')}</p>
        </div>

        <div className="workspace-dialog__field">
          <label>{t('project_dialog.workspace_label')}</label>
          <button className="workspace-picker-card" type="button" onClick={pickWorkspace}>
            <FolderPlus size={24} />
            <span>{workspacePath || t('project_dialog.pick_workspace')}</span>
          </button>
        </div>

        <div className="workspace-dialog__field">
          <label htmlFor="create-project-name">{t('project_dialog.name_label')}</label>
          <Input
            id="create-project-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t('project_dialog.name_placeholder')}
          />
        </div>

        <div className="workspace-dialog__field">
          <label>{t('project_dialog.mode_label')}</label>
          <div className="project-mode-picker">
            <button
              type="button"
              disabled={singleTaken}
              className={`project-mode-picker__card ${mode === 'single' ? 'project-mode-picker__card--active' : ''} ${singleTaken ? 'project-mode-picker__card--taken' : ''}`}
              onClick={() => setMode('single')}
            >
              <User size={20} />
              <span className="project-mode-picker__title">{t('project_dialog.mode_single')}</span>
              <span className="project-mode-picker__desc">
                {singleTaken ? t('project_dialog.mode_taken', { mode: t('project_dialog.mode_single') }) : t('project_dialog.mode_single_desc')}
              </span>
            </button>
            <button
              type="button"
              disabled={multiTaken}
              className={`project-mode-picker__card ${mode === 'multi' ? 'project-mode-picker__card--active' : ''} ${multiTaken ? 'project-mode-picker__card--taken' : ''}`}
              onClick={() => setMode('multi')}
            >
              <Users size={20} />
              <span className="project-mode-picker__title">{t('project_dialog.mode_multi')}</span>
              <span className="project-mode-picker__desc">
                {multiTaken ? t('project_dialog.mode_taken', { mode: t('project_dialog.mode_multi') }) : t('project_dialog.mode_multi_desc')}
              </span>
            </button>
          </div>
        </div>

        {workspacePath && <p className="workspace-dialog__hint">{t('project_dialog.workspace_hint', { path: workspacePath })}</p>}
        {error && <p className="workspace-dialog__error">{error}</p>}

        <div className="workspace-dialog__footer">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            {t('dialog.cancel')}
          </Button>
          <Button type="button" variant="primary" onClick={submit} disabled={busy || !workspacePath || !name.trim()}>
            {t('project_dialog.create')}
          </Button>
        </div>
      </section>
    </div>
  );
}
