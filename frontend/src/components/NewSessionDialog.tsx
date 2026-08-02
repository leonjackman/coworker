import { FolderPlus, MessageSquarePlus, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../lib/i18n';
import type { ProjectEntry } from '../types';
import { Button } from './ui/button';
import { Textarea } from './ui/textarea';

interface NewSessionDialogProps {
  open: boolean;
  projects: ProjectEntry[];
  initialProjectId?: string;
  initialMessage?: string;
  onClose: () => void;
  onPickWorkspace: () => Promise<string | null>;
  onCreateProject: (payload: { name: string; workspace_path: string }) => Promise<ProjectEntry>;
  onStart: (projectId: string, firstMessage: string) => void;
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || t('sidebar.project_new');
}

export function NewSessionDialog({
  open,
  projects,
  initialProjectId,
  initialMessage,
  onClose,
  onPickWorkspace,
  onCreateProject,
  onStart,
}: NewSessionDialogProps) {
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [firstMessage, setFirstMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setSelectedProjectId(initialProjectId || projects[0]?.id || '');
    setFirstMessage(initialMessage || '');
    setBusy(false);
    setError('');
  }, [open, initialProjectId, initialMessage]);

  useEffect(() => {
    if (!open || selectedProjectId || initialProjectId) return;
    setSelectedProjectId(projects[0]?.id || '');
  }, [open, selectedProjectId, initialProjectId, projects]);

  if (!open) return null;

  const createFromFolder = async () => {
    setBusy(true);
    setError('');
    try {
      const workspacePath = await onPickWorkspace();
      if (!workspacePath) return;
      const project = await onCreateProject({
        name: folderName(workspacePath),
        workspace_path: workspacePath,
      });
      setSelectedProjectId(project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    if (!selectedProjectId) return;
    onStart(selectedProjectId, firstMessage.trim());
    onClose();
  };

  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="workspace-dialog workspace-dialog--wide" role="dialog" aria-modal="true" aria-labelledby="new-session-title">
        <button className="workspace-dialog__close" type="button" onClick={onClose} aria-label={t('dialog.close')}>
          <X size={18} />
        </button>
        <div className="workspace-dialog__header">
          <p className="workspace-dialog__eyebrow">{t('session_dialog.eyebrow')}</p>
          <h2 id="new-session-title">{t('session_dialog.title')}</h2>
          <p>{t('session_dialog.description')}</p>
        </div>

        <div className="workspace-dialog__field">
          <label>{t('session_dialog.project_label')}</label>
          <div className="project-choice-list">
            {projects.map((project) => (
              <button
                key={project.id}
                type="button"
                className={`project-choice ${project.id === selectedProjectId ? 'project-choice--active' : ''}`}
                onClick={() => setSelectedProjectId(project.id)}
              >
                <span>{project.name}</span>
                <small>{project.workspace_path || t('project.workspace_missing')}</small>
              </button>
            ))}
            <button type="button" className="project-choice project-choice--create" onClick={createFromFolder} disabled={busy}>
              <FolderPlus size={16} />
              <span>{t('session_dialog.create_project_from_folder')}</span>
            </button>
          </div>
        </div>

        <div className="workspace-dialog__field">
          <label htmlFor="new-session-message">{t('session_dialog.task_label')}</label>
          <Textarea
            id="new-session-message"
            value={firstMessage}
            onChange={(event) => setFirstMessage(event.target.value)}
            placeholder={t('session_dialog.task_placeholder')}
          />
        </div>

        {selectedProject && <p className="workspace-dialog__hint">{t('session_dialog.workspace_hint', { path: selectedProject.workspace_path })}</p>}
        {error && <p className="workspace-dialog__error">{error}</p>}

        <div className="workspace-dialog__footer">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            {t('dialog.cancel')}
          </Button>
          <Button type="button" variant="primary" onClick={submit} disabled={busy || !selectedProjectId}>
            <MessageSquarePlus size={15} />
            {firstMessage.trim() ? t('session_dialog.start') : t('session_dialog.open_draft')}
          </Button>
        </div>
      </section>
    </div>
  );
}
