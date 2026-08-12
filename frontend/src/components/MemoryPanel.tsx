import { ArrowLeft, Edit3, FolderOpen, Loader2, RefreshCw, Save, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { MemoryFileInfo, MemoryScope } from '../types';
import { Button } from './ui/button';
import { WorkspacePage } from './ui/workspace-page';

interface MemoryPanelProps {
  onClose?: () => void;
}

type Flash = { kind: 'ok' | 'error'; text: string } | null;

const SCOPE_LABELS: Record<MemoryScope, string> = {
  project: 'memory.scope.project',
  user: 'memory.scope.user',
};

function formatMtime(mtime: number): string {
  if (!mtime) return '';
  const date = new Date(mtime * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function splitEntriesLocal(text: string): string[] {
  return text
    .split(/\n*\s*§\s*\n+/)
    .map((part) => part.trim().replace(/^#\s*Coworker\s*记忆\s*$/m, '').trim())
    .filter(Boolean);
}

export function MemoryPanel({ onClose }: MemoryPanelProps) {
  const [files, setFiles] = useState<MemoryFileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<MemoryScope | null>(null);
  const [editorPath, setEditorPath] = useState('');
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);

  const canReveal = typeof window !== 'undefined' && Boolean((window as { electronAPI?: unknown }).electronAPI);

  const notify = (kind: 'ok' | 'error', text: string) => {
    setFlash({ kind, text });
    window.setTimeout(() => setFlash(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await chatService.getMemoryFiles();
      setFiles(res.files);
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openEditor = async (scope: MemoryScope) => {
    try {
      const res = await chatService.getMemoryFileContent(scope);
      setEditorPath(res.path);
      setContent(res.content);
      setEditing(scope);
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const saveEditor = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await chatService.saveMemoryFile(editing, content);
      notify('ok', t('memory.saved'));
      setEditing(null);
      await load();
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setSaving(false);
    }
  };

  const reveal = async (path: string) => {
    try {
      const res = await chatService.revealInFolder(path);
      if (res.status === 'unsupported') notify('error', t('memory.reveal_unsupported'));
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  if (editing) {
    const entries = splitEntriesLocal(content);
    return (
      <WorkspacePage
        eyebrow={t('memory.eyebrow')}
        title={t('memory.editor_title')}
        description={editorPath}
        action={(
          <Button variant="ghost" onClick={() => setEditing(null)}>
            <ArrowLeft size={15} />
            {t('memory.back')}
          </Button>
        )}
      >
        {flash && (
          <div className={`memory-flash memory-flash--${flash.kind}`} role="status">
            {flash.text}
          </div>
        )}
        <div className="memory-editor__meta">
          <span>{entries.length} {t('memory.entries_count')}</span>
          <span>{content.length} {t('memory.chars')}</span>
          <span className="memory-editor__hint">{t('memory.editor_hint')}</span>
        </div>
        <textarea
          className="memory-editor__field"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          spellCheck={false}
          aria-label={t('memory.editor_title')}
        />
        <div className="memory-editor__actions">
          <Button variant="primary" disabled={saving} onClick={() => void saveEditor()}>
            {saving ? <Loader2 className="animate-spin" size={14} /> : <Save size={14} />}
            {t('memory.editor_save')}
          </Button>
        </div>
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      eyebrow={t('memory.eyebrow')}
      title={t('memory.title')}
      description={t('memory.description')}
      action={onClose ? (
        <Button variant="outline" size="icon" aria-label={t('common.close')} onClick={onClose}>
          <X size={16} />
        </Button>
      ) : undefined}
    >
      {flash && (
        <div className={`memory-flash memory-flash--${flash.kind}`} role="status">
          {flash.text}
        </div>
      )}

      <div className="memory-library__toolbar">
        <span className="memory-subheading">{t('memory.library_title')}</span>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw size={14} />
          {t('common.refresh')}
        </Button>
      </div>

      {loading && !files.length ? (
        <div className="memory-loading">
          <Loader2 className="animate-spin" size={18} />
          <span>{t('common.loading')}</span>
        </div>
      ) : (
        <div className="memory-library">
          {files.map((file) => (
            <div key={file.scope} className="memory-file-card">
              <div className="memory-file-card__header">
                <div>
                  <span className="memory-file-card__title">{t(SCOPE_LABELS[file.scope])}</span>
                  <span className="memory-file-card__meta">
                    {file.entry_count} {t('memory.entries_count')} · {file.char_count} {t('memory.chars')}
                    {formatMtime(file.mtime) ? ` · ${t('memory.updated')} ${formatMtime(file.mtime)}` : ''}
                  </span>
                </div>
                <div className="memory-file-card__actions">
                  {canReveal && (
                    <Button variant="ghost" size="sm" onClick={() => void reveal(file.path)}>
                      <FolderOpen size={14} />
                      {t('memory.reveal')}
                    </Button>
                  )}
                  <Button variant="secondary" size="sm" onClick={() => void openEditor(file.scope)}>
                    <Edit3 size={14} />
                    {t('memory.edit')}
                  </Button>
                </div>
              </div>
              <button type="button" className="memory-file-card__body" onClick={() => void openEditor(file.scope)}>
                <span className="memory-file-card__path">{file.path || t('memory.no_path')}</span>
                {file.entries.length > 0 ? (
                  <p className="memory-file-card__preview">{file.entries[0]}</p>
                ) : (
                  <p className="memory-file-card__empty">{t('memory.empty')}</p>
                )}
              </button>
            </div>
          ))}
          {!files.length && <div className="memory-proposals__empty">{t('memory.no_files')}</div>}
        </div>
      )}
    </WorkspacePage>
  );
}

export default MemoryPanel;
