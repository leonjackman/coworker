import { FolderPlus, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../lib/i18n';
import { Button } from './ui/button';
import { Input } from './ui/input';

interface CreateProjectDialogProps {
  open: boolean;
  onClose: () => void;
  onPickWorkspace: () => Promise<string | null>;
  onCreate: (payload: { name: string; workspace_path: string }) => Promise<unknown>;
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || t('sidebar.project_new');
}

export function CreateProjectDialog({ open, onClose, onPickWorkspace, onCreate }: CreateProjectDialogProps) {
  const [name, setName] = useState('');
  const [workspacePath, setWorkspacePath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setName('');
    setWorkspacePath('');
    setError('');
    setBusy(false);
  }, [open]);

  if (!open) return null;

  const pickWorkspace = async () => {
    setError('');
    const selected = await onPickWorkspace();
    if (!selected) return;
    setWorkspacePath(selected);
    setName((current) => current || folderName(selected));
  };

  const submit = async () => {
    if (!workspacePath || !name.trim()) return;
    setBusy(true);
    setError('');
    try {
      await onCreate({ name: name.trim(), workspace_path: workspacePath });
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
