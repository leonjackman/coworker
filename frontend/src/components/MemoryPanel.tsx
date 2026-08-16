import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Download,
  Edit3,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Upload,
  Users,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type {
  MemoryAgentView,
  MemoryDiscoverResponse,
  MemoryFileContentResponse,
  MemoryFolderView,
  MemoryImportPreviewResponse,
  MemoryNode,
  MemoryProjectView,
  MemorySearchResult,
  MemoryTeamView,
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
  const lower = clean.toLowerCase();
  if (!lower.includes('.')) return `${clean}.md`;
  if (lower.endsWith('.markdown') || lower.endsWith('.md')) return clean;
  return ''; // invalid extension — caller handles error
}

const PROTECTED_ROOT = ['MEMORY.md', 'USER.md', 'AGENT.md'];
const PROTECTED_CORE = ['SOUL.md', 'AGENT.md', 'MEMORY.md'];

function isProtectedRel(rel: string): boolean {
  const parts = rel.split('/');
  if (parts.length === 1 && PROTECTED_ROOT.includes(rel)) return true;
  if (
    parts.length >= 2 &&
    parts[parts.length - 2] === 'BASE' &&
    PROTECTED_CORE.includes(parts[parts.length - 1] ?? '')
  ) {
    return true;
  }
  return false;
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

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MemorySearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const [exportOpen, setExportOpen] = useState(false);
  const [exportScope, setExportScope] = useState<'all' | 'system' | 'projects'>('all');
  const [exportProjectDirs, setExportProjectDirs] = useState<string[]>([]);
  const [exportBusy, setExportBusy] = useState(false);

  const [importBusy, setImportBusy] = useState(false);
  const [importConflict, setImportConflict] = useState<MemoryImportPreviewResponse | null>(null);
  const [importDecisions, setImportDecisions] = useState<Record<string, string>>({});
  const [importApplyBusy, setImportApplyBusy] = useState(false);

  const [movingRel, setMovingRel] = useState<string | null>(null);
  const [moveName, setMoveName] = useState('');
  const [moveTarget, setMoveTarget] = useState('');
  const [moveBusy, setMoveBusy] = useState(false);

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

  useEffect(() => {
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, []);

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
    const confirmed = window.confirm(t('memory.delete_confirm', { rel }));
    if (!confirmed) return;
    try {
      await chatService.deleteMemoryFile(rel);
      notify('ok', t('memory.removed_to_trash'));
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
    const name = file.name.toLowerCase();
    if (!name.endsWith('.md') && !name.endsWith('.markdown')) {
      notify('error', t('memory.extension_not_supported'));
      return;
    }
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

  // ---------------------------------------------------------------- search
  const runSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    try {
      const res = await chatService.searchMemory(query.trim(), 50);
      setSearchResults(res.results);
    } catch (error) {
      notify('error', translateError(error));
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }, []);

  const onSearchChange = (value: string) => {
    setSearchQuery(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      void runSearch(value);
    }, 300);
  };

  // --------------------------------------------------------------- export
  const toggleExportProject = (memoryDir: string) => {
    setExportProjectDirs((prev) =>
      prev.includes(memoryDir) ? prev.filter((d) => d !== memoryDir) : [...prev, memoryDir],
    );
  };

  const doExport = async () => {
    setExportBusy(true);
    try {
      const result = await chatService.exportMemory({
        scope: exportScope,
        project_dirs: exportScope === 'projects' ? exportProjectDirs : [],
      });
      if (result.status === 'canceled') {
        setExportOpen(false);
        return;
      }
      setExportOpen(false);
      notify('ok', t('memory.exported', { count: String(result.file_count ?? 0) }));
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setExportBusy(false);
    }
  };

  // --------------------------------------------------------------- import
  const doImport = async () => {
    setImportBusy(true);
    try {
      const picked = await chatService.importMemory();
      if (picked.status !== 'ok' || !picked.path) {
        if (picked.status === 'unsupported') notify('error', t('memory.import_unsupported'));
        return;
      }
      const preview = await chatService.previewMemoryImport(picked.path);
      const conflicts = preview.files.filter((f) => f.exists);
      if (conflicts.length === 0) {
        const applied = await chatService.applyMemoryImport(preview.token, {});
        notify('ok', t('memory.imported', { count: String(applied.imported) }));
        await load();
      } else {
        setImportConflict(preview);
        setImportDecisions({});
      }
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setImportBusy(false);
    }
  };

  const applyImport = async () => {
    if (!importConflict) return;
    setImportApplyBusy(true);
    try {
      const applied = await chatService.applyMemoryImport(importConflict.token, importDecisions);
      setImportConflict(null);
      notify('ok', t('memory.imported', { count: String(applied.imported) }));
      await load();
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setImportApplyBusy(false);
    }
  };

  const setAllDecisions = (decision: 'skip' | 'overwrite') => {
    if (!importConflict) return;
    const next: Record<string, string> = {};
    for (const f of importConflict.files) {
      if (f.exists) next[f.rel] = decision;
    }
    setImportDecisions(next);
  };

  // ----------------------------------------------------------- rename/move
  const moveTargets = useCallback((): Array<{ value: string; label: string }> => {
    const options: Array<{ value: string; label: string }> = [
      { value: '', label: '/' },
    ];
    if (library) {
      for (const project of library.projects) {
        const label = project.project_name || project.name;
        options.push({ value: `${project.rel}/BASE`, label: `${label} / BASE` });
        options.push({ value: `${project.rel}/BASE/PROJECT`, label: `${label} / BASE / PROJECT` });
        for (const agent of project.agents) {
          options.push({ value: `${agent.rel}/BASE`, label: `${label} / ${agent.name} / BASE` });
        }
        for (const folder of project.folders) {
          options.push({ value: folder.rel, label: `${label} / ${folder.name}` });
        }
        for (const team of project.teams ?? []) {
          options.push({ value: team.rel, label: `${label} / ${team.name} (部门)` });
        }
      }
    }
    return options;
  }, [library]);

  const startMove = (rel: string) => {
    const parts = rel.split('/');
    const name = parts[parts.length - 1] || rel;
    const target = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
    setMovingRel(rel);
    setMoveName(name);
    setMoveTarget(target);
  };

  const doMove = async () => {
    if (!movingRel) return;
    const name = sanitizeFileName(moveName);
    if (!name) {
      notify('error', t('memory.extension_not_supported'));
      return;
    }
    const newRel = moveTarget ? `${moveTarget}/${name}` : name;
    if (newRel === movingRel) {
      setMovingRel(null);
      return;
    }
    setMoveBusy(true);
    try {
      await chatService.moveMemoryFile(movingRel, newRel);
      notify('ok', t('memory.moved'));
      setMovingRel(null);
      await load();
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setMoveBusy(false);
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

  const showSearch = Boolean(searchQuery.trim());

  // ------------------------------------------------------------------- tree
  return (
    <WorkspacePage
      eyebrow={t('memory.eyebrow')}
      title={t('memory.title')}
      description={t('memory.description')}
      action={(
        <>
          {canReveal && (
            <>
              <Button variant="outline" size="sm" onClick={() => void doImport()} disabled={importBusy}>
                {importBusy ? <Loader2 className="animate-spin" size={15} /> : <Download size={15} />}
                {t('memory.import')}
              </Button>
              <Button variant="outline" size="sm" onClick={() => setExportOpen(true)}>
                <Upload size={15} />
                {t('memory.export')}
              </Button>
            </>
          )}
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

      <div className="memory-search">
        <Search size={14} className="memory-search__icon" />
        <input
          className="memory-search__field"
          placeholder={t('memory.search_placeholder')}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          spellCheck={false}
          aria-label={t('memory.search_placeholder')}
        />
        {searching && <Loader2 className="animate-spin memory-search__spin" size={14} />}
        {searchQuery && !searching && (
          <button
            type="button"
            className="memory-search__clear"
            aria-label={t('memory.search_clear')}
            onClick={() => {
              setSearchQuery('');
              setSearchResults(null);
            }}
          >
            <X size={13} />
          </button>
        )}
      </div>

      {loading && !library ? (
        <div className="memory-loading">
          <Loader2 className="animate-spin" size={18} />
          <span>{t('common.loading')}</span>
        </div>
      ) : showSearch ? (
        <div className="memory-search-results">
          {searchResults && searchResults.length === 0 ? (
            <span className="memory-tree__placeholder">{t('memory.search_no_results')}</span>
          ) : (
            searchResults?.map((result) => (
              <div
                key={result.rel}
                className="memory-search-result"
                role="button"
                tabIndex={0}
                onClick={() => void openEditor(result.rel)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void openEditor(result.rel);
                }}
              >
                <div className="memory-search-result__head">
                  <FileText size={13} className="memory-search-result__icon" />
                  <span className="memory-search-result__name">{result.name}</span>
                  <span className="memory-search-result__loc">{result.location}</span>
                  <span className="memory-search-result__count">{result.match_count}</span>
                </div>
                <div className="memory-search-result__snippet">{result.snippet}</div>
              </div>
            ))
          )}
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
            onRename={startMove}
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
            onRename={startMove}
            childrenOverride={(library?.projects ?? []).map((project) => (
              <ProjectBranch
                key={project.rel}
                project={project}
                collapsed={collapsed}
                toggle={toggle}
                onOpen={openEditor}
                onDelete={deleteFile}
                onRename={startMove}
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
          {addName && !sanitizeFileName(addName) && (
            <span className="memory-add__error">{t('memory.extension_not_supported')}</span>
          )}
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
            accept=".md,.markdown"
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

      <DetailModal
        open={movingRel !== null}
        onClose={() => setMovingRel(null)}
        icon={<Pencil size={18} />}
        title={t('memory.move_title')}
        subtitle={movingRel ?? ''}
        footer={(
          <>
            <Button variant="ghost" size="sm" onClick={() => setMovingRel(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={moveBusy || !sanitizeFileName(moveName)}
              onClick={() => void doMove()}
            >
              {moveBusy ? <Loader2 className="animate-spin" size={14} /> : <Save size={14} />}
              {t('memory.move_confirm')}
            </Button>
          </>
        )}
      >
        <div className="memory-move">
          <label className="memory-move__label" htmlFor="memory-move-name">
            {t('memory.move_name_label')}
          </label>
          <input
            id="memory-move-name"
            className="memory-add__name"
            value={moveName}
            onChange={(e) => setMoveName(e.target.value)}
            spellCheck={false}
            autoFocus
          />
          {moveName && !sanitizeFileName(moveName) && (
            <span className="memory-add__error">{t('memory.extension_not_supported')}</span>
          )}
          <label className="memory-move__label" htmlFor="memory-move-target">
            {t('memory.move_target_label')}
          </label>
          <select
            id="memory-move-target"
            className="memory-move__select"
            value={moveTarget}
            onChange={(e) => setMoveTarget(e.target.value)}
          >
            {moveTargets().map((option) => (
              <option key={option.value || 'root'} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </DetailModal>

      <DetailModal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        icon={<Upload size={18} />}
        title={t('memory.export_title')}
        subtitle={t('memory.export_desc')}
        footer={(
          <>
            <Button variant="ghost" size="sm" onClick={() => setExportOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={exportBusy || (exportScope === 'projects' && exportProjectDirs.length === 0)}
              onClick={() => void doExport()}
            >
              {exportBusy ? <Loader2 className="animate-spin" size={14} /> : <Upload size={14} />}
              {t('memory.export_confirm')}
            </Button>
          </>
        )}
      >
        <div className="memory-export">
          <label className="memory-move__label">{t('memory.export_scope_label')}</label>
          {(
            [
              ['all', t('memory.export_scope_all')],
              ['system', t('memory.export_scope_system')],
              ['projects', t('memory.export_scope_projects')],
            ] as Array<['all' | 'system' | 'projects', string]>
          ).map(([value, label]) => (
            <label key={value} className="memory-export__radio">
              <input
                type="radio"
                name="memory-export-scope"
                checked={exportScope === value}
                onChange={() => setExportScope(value)}
              />
              {label}
            </label>
          ))}
          {exportScope === 'projects' && (
            <div className="memory-export__projects">
              {(library?.projects ?? []).map((project) => (
                <label key={project.rel} className="memory-export__radio">
                  <input
                    type="checkbox"
                    checked={exportProjectDirs.includes(project.name)}
                    onChange={() => toggleExportProject(project.name)}
                  />
                  {project.project_name || project.name}
                </label>
              ))}
            </div>
          )}
        </div>
      </DetailModal>

      <DetailModal
        open={importConflict !== null}
        onClose={() => setImportConflict(null)}
        icon={<Download size={18} />}
        title={t('memory.import_conflict_title')}
        subtitle={t('memory.import_conflict_desc')}
        footer={(
          <>
            <Button variant="ghost" size="sm" onClick={() => setImportConflict(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setAllDecisions('skip')}>
              {t('memory.import_skip_all')}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setAllDecisions('overwrite')}>
              {t('memory.import_overwrite_all')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={importApplyBusy}
              onClick={() => void applyImport()}
            >
              {importApplyBusy ? <Loader2 className="animate-spin" size={14} /> : <Download size={14} />}
              {t('memory.import_confirm')}
            </Button>
          </>
        )}
      >
        <div className="memory-import-conflicts">
          {importConflict?.files.map((file) => {
            const isConflict = file.exists;
            return (
              <div key={file.rel} className="memory-import-conflict">
                <div className="memory-import-conflict__info">
                  <span className="memory-import-conflict__rel">{file.rel}</span>
                  {isConflict ? (
                    <span className="memory-import-conflict__badge">{t('memory.import_exists')}</span>
                  ) : (
                    <span className="memory-import-conflict__badge memory-import-conflict__badge--new">
                      {t('memory.import_new')}
                    </span>
                  )}
                </div>
                {isConflict && (
                  <select
                    className="memory-move__select"
                    value={importDecisions[file.rel] ?? 'skip'}
                    onChange={(e) =>
                      setImportDecisions((prev) => ({ ...prev, [file.rel]: e.target.value }))
                    }
                  >
                    <option value="skip">{t('memory.import_decision_skip')}</option>
                    <option value="overwrite">{t('memory.import_decision_overwrite')}</option>
                  </select>
                )}
              </div>
            );
          })}
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
  onRename,
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
  onRename: (rel: string) => void;
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
        <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} onRename={onRename} />
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
  onRename,
  onAddBase,
}: {
  project: MemoryProjectView;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onRename: (rel: string) => void;
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
            onRename={onRename}
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
            onRename={onRename}
          />
          {project.teams?.map((team) => (
            <TeamBranch
              key={team.rel}
              team={team}
              collapsed={collapsed}
              toggle={toggle}
              onOpen={onOpen}
              onDelete={onDelete}
              onRename={onRename}
            />
          ))}
          {project.folders.map((folder) => (
            <FolderBranch
              key={folder.rel}
              folder={folder}
              collapsed={collapsed}
              toggle={toggle}
              onOpen={onOpen}
              onDelete={onDelete}
              onRename={onRename}
            />
          ))}
          {project.agents.map((agent) => (
            <AgentBranch
              key={agent.rel}
              agent={agent}
              projectRel={project.rel}
              collapsed={collapsed}
              toggle={toggle}
              onOpen={onOpen}
              onDelete={onDelete}
              onRename={onRename}
              onAddBase={onAddBase}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FolderBranch({
  folder,
  collapsed,
  toggle,
  onOpen,
  onDelete,
  onRename,
}: {
  folder: MemoryFolderView;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onRename: (rel: string) => void;
}) {
  const isCollapsed = (key: string): boolean => collapsed[key] ?? true;
  const folderKey = `p:${folder.rel}:f`;
  return (
    <div className="memory-tree__section">
      <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={() => toggle(folderKey)}>
        {isCollapsed(folderKey) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Folder size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{folder.name}</span>
      </button>
      {!isCollapsed(folderKey) && (
        <div className="memory-tree__children">
          {folder.files.length === 0 && (
            <span className="memory-tree__placeholder">{t('memory.tree.empty')}</span>
          )}
          {folder.files.map((node) => (
            <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} onRename={onRename} />
          ))}
        </div>
      )}
    </div>
  );
}

function TeamBranch({
  team,
  collapsed,
  toggle,
  onOpen,
  onDelete,
  onRename,
}: {
  team: MemoryTeamView;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onRename: (rel: string) => void;
}) {
  const isCollapsed = (key: string): boolean => collapsed[key] ?? true;
  const teamKey = `p:${team.rel}:t`;
  const teamFiles = [team.goals, team.context, team.memory].filter((node): node is MemoryNode => node !== null);
  return (
    <div className="memory-tree__section">
      <button type="button" className="memory-tree__node memory-tree__node--dir" onClick={() => toggle(teamKey)}>
        {isCollapsed(teamKey) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <Users size={14} className="memory-tree__icon" />
        <span className="memory-tree__label">{team.name}</span>
      </button>
      {!isCollapsed(teamKey) && (
        <div className="memory-tree__children">
          {teamFiles.length === 0 && (
            <span className="memory-tree__placeholder">{t('memory.tree.empty')}</span>
          )}
          {teamFiles.map((node) => (
            <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} onRename={onRename} />
          ))}
          {team.files.map((node) => (
            <MemoryRow key={node.rel} node={node} onOpen={onOpen} onDelete={onDelete} onRename={onRename} />
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
  onRename,
  onAddBase,
}: {
  agent: MemoryAgentView;
  projectRel: string;
  collapsed: Record<string, boolean>;
  toggle: (key: string) => void;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onRename: (rel: string) => void;
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
            onRename={onRename}
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
            onRename={onRename}
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
  onRename,
}: {
  node: MemoryNode;
  onOpen: (rel: string) => void;
  onDelete: (rel: string) => void;
  onRename: (rel: string) => void;
}) {
  const protectedFile = isProtectedRel(node.rel);
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
      {!protectedFile && (
        <button
          type="button"
          className="memory-tree__action"
          aria-label={t('memory.rename')}
          title={t('memory.rename')}
          onClick={() => onRename(node.rel)}
        >
          <Pencil size={13} />
        </button>
      )}
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
