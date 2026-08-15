import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Edit3,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  RefreshCw,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type {
  MemoryAgentView,
  MemoryDiscoverResponse,
  MemoryFileContentResponse,
  MemoryNode,
  MemoryProjectView,
} from '../types';
import { Button } from './ui/button';
import { WorkspacePage } from './ui/workspace-page';

interface MemoryPanelProps {
  onClose?: () => void;
  projectId?: string | undefined;
}

type Flash = { kind: 'ok' | 'error'; text: string } | null;

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

function blockCount(text: string): number {
  return text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean).length;
}

export function MemoryPanel({ onClose, projectId }: MemoryPanelProps) {
  const [library, setLibrary] = useState<MemoryDiscoverResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editingRel, setEditingRel] = useState<string | null>(null);
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
      const res = await chatService.discoverMemory(projectId);
      setLibrary(res);
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (key: string) => {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const isCollapsed = (key: string): boolean => collapsed[key] ?? false;

  const openEditor = async (rel: string) => {
    try {
      const res: MemoryFileContentResponse = await chatService.getMemoryFile(rel);
      setEditorPath(res.rel);
      setContent(res.content);
      setEditingRel(res.rel);
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const saveEditor = async () => {
    if (!editingRel) return;
    setSaving(true);
    try {
      await chatService.saveMemoryFile(editingRel, content);
      notify('ok', t('memory.saved'));
      setEditingRel(null);
      await load();
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setSaving(false);
    }
  };

  const deleteFile = async (rel: string) => {
    const confirmed = window.confirm(`${t('common.delete')} ${rel}?`);
    if (!confirmed) return;
    try {
      await chatService.deleteMemoryFile(rel);
      notify('ok', t('memory.removed'));
      if (editingRel === rel) setEditingRel(null);
      await load();
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const migrate = async () => {
    try {
      const res = await chatService.migrateMemory();
      if (res.migrated) {
        notify('ok', t('memory.migrate_ok'));
      } else if (res.reason === 'already_migrated') {
        notify('ok', t('memory.migrate_already'));
      } else {
        notify('error', t('memory.migrate_failed'));
      }
      await load();
    } catch (error) {
      notify('error', translateError(error));
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

  // ----------------------------------------------------------------- editor
  if (editingRel) {
    return (
      <WorkspacePage
        eyebrow={t('memory.eyebrow')}
        title={t('memory.editor_title')}
        description={editorPath}
        action={(
          <Button variant="ghost" onClick={() => setEditingRel(null)}>
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
          <span>{blockCount(content)} {t('memory.entries_count')}</span>
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
          {canReveal && (
            <Button variant="outline" onClick={() => void reveal(editorPath)}>
              <FolderOpen size={14} />
              {t('memory.reveal')}
            </Button>
          )}
          <Button variant="destructive" size="sm" onClick={() => void deleteFile(editingRel)}>
            <Trash2 size={14} />
            {t('common.delete')}
          </Button>
          <Button variant="primary" disabled={saving} onClick={() => void saveEditor()}>
            {saving ? <Loader2 className="animate-spin" size={14} /> : <Save size={14} />}
            {t('memory.editor_save')}
          </Button>
        </div>
      </WorkspacePage>
    );
  }

  // ------------------------------------------------------------------- tree
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
        <div className="memory-library__actions">
          <Button variant="outline" size="sm" onClick={() => void migrate()}>
            <RefreshCw size={14} />
            {t('memory.migrate')}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            {loading ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />}
            {t('common.refresh')}
          </Button>
        </div>
      </div>

      {loading && !library ? (
        <div className="memory-loading">
          <Loader2 className="animate-spin" size={18} />
          <span>{t('common.loading')}</span>
        </div>
      ) : (
        <div className="memory-tree">
          <TreeSection
            label={t('memory.tree.system')}
            relPrefix=""
            nodes={library?.system ?? []}
            collapsed={isCollapsed('system')}
            onToggle={() => toggle('system')}
            onOpen={openEditor}
            onDelete={deleteFile}
          />

          {(library?.projects ?? []).map((project) => (
            <ProjectBranch
              key={project.rel}
              project={project}
              collapsed={isCollapsed(project.rel)}
              onToggle={() => toggle(project.rel)}
              onOpen={openEditor}
              onDelete={deleteFile}
            />
          ))}

          {(library?.projects ?? []).length === 0 && !loading && (
            <div className="memory-tree__empty">{t('memory.tree.no_projects')}</div>
          )}
        </div>
      )}
    </WorkspacePage>
  );
}

// ------------------------------------------------------------------- helpers

function TreeSection({
  label,
  relPrefix,
  nodes,
  collapsed,
  onToggle,
  onOpen,
  onDelete,
}: {
  label: string;
  relPrefix: string;
  nodes: MemoryNode[];
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
}) {
  return (
    <div className="memory-tree__section">
      <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={onToggle}>
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Folder size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{label}</span>
      </button>
      {!collapsed && (
        <div className="memory-tree__children">
          {nodes.length === 0 && <span className="memory-tree__placeholder">{t('memory.tree.empty')}</span>}
          {nodes.map((node) => (
            <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectBranch({
  project,
  collapsed,
  onToggle,
  onOpen,
  onDelete,
}: {
  project: MemoryProjectView;
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
}) {
  return (
    <div className="memory-tree__section">
      <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={onToggle}>
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Folder size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{t('memory.tree.project')}</span>
        <span className="memory-tree__rel">{project.name}</span>
      </button>
      {!collapsed && (
        <div className="memory-tree__children">
          <TreeSection
            label={t('memory.tree.base')}
            relPrefix={project.rel}
            nodes={project.base}
            collapsed={false}
            onToggle={() => undefined}
            onOpen={onOpen}
            onDelete={onDelete}
          />
          <TreeSection
            label={t('memory.tree.project_context')}
            relPrefix={`${project.rel}/BASE/PROJECT`}
            nodes={project.project}
            collapsed={false}
            onToggle={() => undefined}
            onOpen={onOpen}
            onDelete={onDelete}
          />
          {project.agents.map((agent) => (
            <AgentBranch
              key={agent.rel}
              agent={agent}
              collapsed={false}
              onOpen={onOpen}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentBranch({
  agent,
  collapsed,
  onOpen,
  onDelete,
}: {
  agent: MemoryAgentView;
  collapsed: boolean;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
}) {
  const core: Array<{ rel: string; node: MemoryNode | null; labelKey: string }> = [
    { rel: agent.soul?.rel ?? '', node: agent.soul, labelKey: 'memory.tree.soul' },
    { rel: agent.agent?.rel ?? '', node: agent.agent, labelKey: 'memory.tree.agent' },
    { rel: agent.memory?.rel ?? '', node: agent.memory, labelKey: 'memory.tree.memory' },
  ];
  return (
    <div className="memory-tree__section">
      <div className="memory-tree__node memory-tree__node--dir memory-tree__node--static">
        <FolderOpen size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{t('memory.tree.agent')}</span>
        <span className="memory-tree__rel">{agent.name}</span>
      </div>
      <div className="memory-tree__children">
        {core.map(
          (entry) =>
            entry.node && (
              <MemoryRow key={entry.rel} node={entry.node} onOpen={onOpen} onDelete={onDelete} />
            ),
        )}
        {agent.sessions.length > 0 && (
          <TreeSection
            label={t('memory.tree.sessions')}
            relPrefix={agent.rel}
            nodes={agent.sessions}
            collapsed={false}
            onToggle={() => undefined}
            onOpen={onOpen}
            onDelete={onDelete}
          />
        )}
      </div>
    </div>
  );
}

function MemoryRow({
  node,
  onOpen,
  onDelete,
}: {
  node: MemoryNode;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
}) {
  return (
    <div className="memory-tree__row">
      <button
        type="button"
        className="memory-tree__node memory-tree__node--file"
        onClick={() => onOpen(node.rel)}
      >
        <FileText size={13} className="memory-tree__icon" />
        <span className="memory-tree__label">{node.name}</span>
        {node.mtime > 0 && <span className="memory-tree__meta">{formatMtime(node.mtime)}</span>}
      </button>
      <button
        type="button"
        className="memory-tree__delete"
        aria-label={t('common.delete')}
        title={t('common.delete')}
        onClick={() => onDelete(node.rel)}
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

export default MemoryPanel;
