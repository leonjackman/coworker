import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Edit3,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode } from 'react';
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
import { DetailModal } from './ui/detail-modal';
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

function sanitizeFileName(name: string): string {
  let clean = name.replace(/[/\\]+/g, '_').replace(/\.{2,}/g, '_').trim();
  if (!clean) return '';
  if (!clean.includes('.')) clean += '.md';
  return clean;
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
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [addExpandKey, setAddExpandKey] = useState('');
  const [addName, setAddName] = useState('');
  const [addContent, setAddContent] = useState('');
  const [addBusy, setAddBusy] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

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
    setCollapsed((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  };

  const isCollapsed = (key: string): boolean => collapsed[key] ?? true;

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

  const startAdd = (folderRel: string, expandKey: string) => {
    setAddingTo(folderRel);
    setAddExpandKey(expandKey);
    setAddName('');
    setAddContent('');
  };

  const onImportFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setAddContent(String(reader.result ?? ''));
      setAddName((prev) => (prev.trim() ? prev : file.name));
    };
    reader.readAsText(file);
  };

  const addFile = async () => {
    if (addingTo === null) return;
    const name = sanitizeFileName(addName);
    if (!name) return;
    const rel = addingTo ? `${addingTo}/${name}` : name;
    setAddBusy(true);
    try {
      await chatService.saveMemoryFile(rel, addContent);
      notify('ok', t('memory.file_added'));
      setCollapsed((prev) => ({ ...prev, [addExpandKey]: false }));
      setAddingTo(null);
      setAddName('');
      setAddContent('');
      await load();
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setAddBusy(false);
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
      action={(
        <>
          <Button
            variant="outline"
            size="icon"
            aria-label={t('common.refresh')}
            title={t('common.refresh')}
            onClick={() => void load()}
          >
            {loading ? <Loader2 className="animate-spin" size={16} /> : <RefreshCw size={16} />}
          </Button>
          {onClose && (
            <Button variant="outline" size="icon" aria-label={t('common.close')} onClick={onClose}>
              <X size={16} />
            </Button>
          )}
        </>
      )}
    >
      {flash && (
        <div className={`memory-flash memory-flash--${flash.kind}`} role="status">
          {flash.text}
        </div>
      )}

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
            onAdd={() => startAdd('', 'system')}
          />

          <TreeSection
            label={t('memory.tree.projects_list')}
            relPrefix=""
            nodes={[]}
            collapsed={isCollapsed('projects')}
            onToggle={() => toggle('projects')}
            onOpen={openEditor}
            onDelete={deleteFile}
childrenOverride={(library?.projects ?? []).map((project) => (
              <ProjectBranch
                key={project.rel}
                project={project}
                collapsed={collapsed}
                toggle={toggle}
                onOpen={openEditor}
                onDelete={deleteFile}
                onAddBase={startAdd}
              />
            ))}
            emptyText={
              (library?.projects ?? []).length === 0 ? t('memory.tree.no_projects') : undefined
            }
          />
        </div>
      )}

      <DetailModal
        open={addingTo !== null}
        onClose={() => setAddingTo(null)}
        icon={<Plus size={18} />}
        title={t('memory.add_file_title')}
        subtitle={addingTo ? addingTo : '/'}
        footer={(
          <>
            <Button variant="ghost" size="sm" onClick={() => setAddingTo(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={addBusy || !sanitizeFileName(addName)}
              onClick={() => void addFile()}
            >
              {addBusy ? <Loader2 className="animate-spin" size={14} /> : <Plus size={14} />}
              {t('memory.add_confirm')}
            </Button>
          </>
        )}
      >
        <div className="memory-add">
          <input
            className="memory-add__name"
            placeholder={t('memory.add_name_placeholder')}
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && sanitizeFileName(addName) && !addBusy) void addFile();
            }}
            spellCheck={false}
            autoFocus
            aria-label={t('memory.add_name_placeholder')}
          />
          <textarea
            className="memory-add__content"
            placeholder={t('memory.add_content_placeholder')}
            value={addContent}
            onChange={(e) => setAddContent(e.target.value)}
            spellCheck={false}
            aria-label={t('memory.add_content_placeholder')}
          />
          <input
            ref={importInputRef}
            type="file"
            accept=".md,.markdown,.txt,.text"
            className="memory-add__file-input"
            onChange={onImportFile}
          />
          <div className="memory-add__actions">
            <Button variant="outline" size="sm" onClick={() => importInputRef.current?.click()}>
              <FilePlus size={14} />
              {t('memory.add_import')}
            </Button>
          </div>
        </div>
      </DetailModal>
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
  onAdd,
  childrenOverride,
  emptyText,
}: {
  label: string;
  relPrefix: string;
  nodes: MemoryNode[];
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onAdd?: (() => void) | undefined;
  childrenOverride?: ReactNode | undefined;
  emptyText?: string | undefined;
}) {
  const hasOverride = Array.isArray(childrenOverride)
    ? childrenOverride.length > 0
    : Boolean(childrenOverride);
  const children = hasOverride ? childrenOverride : (
    <>
      {nodes.length === 0 && (
        <span className="memory-tree__placeholder">{emptyText ?? t('memory.tree.empty')}</span>
      )}
      {nodes.map((node) => (
        <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} />
      ))}
    </>
  );
  return (
    <div className="memory-tree__section">
      <div className="memory-tree__dir">
        <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={onToggle}>
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          <Folder size={14} className="memory-tree__icon" />
          <span className="memory-tree__label">{label}</span>
        </button>
        {onAdd && (
          <button
            type="button"
            className="memory-tree__add"
            aria-label={t('memory.add_file')}
            title={t('memory.add_file')}
            onClick={onAdd}
          >
            <Plus size={14} />
          </button>
        )}
      </div>
      {!collapsed && <div className="memory-tree__children">{children}</div>}
    </div>
  );
}

function ProjectBranch({
  project,
  collapsed,
  toggle,
  onOpen,
  onDelete,
  onAddBase,
}: {
  project: MemoryProjectView;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onAddBase: (folderRel: string, expandKey: string) => void;
}) {
  const isCollapsed = (key: string): boolean => collapsed[key] ?? true;
  const baseKey = `p:${project.rel}:base`;
  const projectKey = `p:${project.rel}:project`;
  return (
    <div className="memory-tree__section">
      <button
        type="button"
        className="memory-tree__node memory-tree__node--dir"
        onClick={() => toggle(`p:${project.rel}`)}
      >
        {isCollapsed(`p:${project.rel}`) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Folder size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{project.project_name || project.name}</span>
        <span className="memory-tree__rel">{project.name}</span>
      </button>
      {!isCollapsed(`p:${project.rel}`) && (
        <div className="memory-tree__children">
          <TreeSection
            label={t('memory.tree.base')}
            relPrefix={project.rel}
            nodes={project.base}
            collapsed={isCollapsed(baseKey)}
            onToggle={() => toggle(baseKey)}
            onOpen={onOpen}
            onDelete={onDelete}
            onAdd={() => onAddBase(`${project.rel}/BASE`, baseKey)}
          />
          <TreeSection
            label={t('memory.tree.project_context')}
            relPrefix={`${project.rel}/BASE/PROJECT`}
            nodes={project.project}
            collapsed={isCollapsed(projectKey)}
            onToggle={() => toggle(projectKey)}
            onOpen={onOpen}
            onDelete={onDelete}
          />
          {project.agents.map((agent) => (
            <AgentBranch
              key={agent.rel}
              agent={agent}
              projectRel={project.rel}
              collapsed={collapsed}
              toggle={toggle}
              onOpen={onOpen}
              onDelete={onDelete}
              onAddBase={onAddBase}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentBranch({
  agent,
  projectRel,
  collapsed,
  toggle,
  onOpen,
  onDelete,
  onAddBase,
}: {
  agent: MemoryAgentView;
  projectRel: string;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onAddBase: (folderRel: string, expandKey: string) => void;
}) {
  const isCollapsed = (key: string): boolean => collapsed[key] ?? true;
  const agentKey = `p:${projectRel}:a:${agent.name}`;
  const baseKey = `${agentKey}:base`;
  const sessionsKey = `${agentKey}:sessions`;

  const core: Array<MemoryNode> = [agent.soul, agent.agent, agent.memory].filter(
    (node): node is MemoryNode => Boolean(node),
  );
  return (
    <div className="memory-tree__section">
      <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={() => toggle(agentKey)}>
        {isCollapsed(agentKey) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Folder size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{agent.name}</span>
      </button>
      {!isCollapsed(agentKey) && (
        <div className="memory-tree__children">
          <TreeSection
            label={t('memory.tree.base')}
            relPrefix={agent.rel}
            nodes={[...core, ...agent.base]}
            collapsed={isCollapsed(baseKey)}
            onToggle={() => toggle(baseKey)}
            onOpen={onOpen}
            onDelete={onDelete}
            onAdd={() => onAddBase(`${agent.rel}/BASE`, baseKey)}
          />
          <TreeSection
            label={t('memory.tree.sessions')}
            relPrefix={agent.rel}
            nodes={agent.sessions}
            collapsed={isCollapsed(sessionsKey)}
            onToggle={() => toggle(sessionsKey)}
            onOpen={onOpen}
            onDelete={onDelete}
          />
        </div>
      )}
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
