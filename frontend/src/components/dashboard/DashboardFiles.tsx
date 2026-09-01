import { ChevronDown, ChevronRight, ExternalLink, FileText, Folder, FolderSearch, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { FileTreeNode, WorkspaceDirEntry, WorkspaceFilePreview } from '../../types';
import { Button } from '../ui/button';
import { CodeBlock, MarkdownContent } from '../MarkdownContent';

interface DashboardFilesProps {
  projectId: string;
  workspaceAvailable: boolean;
  workspacePath?: string;
}

interface PreviewState {
  path: string;
  preview: WorkspaceFilePreview;
}

export function DashboardFiles({ projectId, workspaceAvailable, workspacePath }: DashboardFilesProps) {
  const [tree, setTree] = useState<FileTreeNode | null>(null);
  const [lazyChildren, setLazyChildren] = useState<Record<string, WorkspaceDirEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loadingDir, setLoadingDir] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [split, setSplit] = useState(50);
  const filesRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startPct: number } | null>(null);
  const [focusedPath, setFocusedPath] = useState<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const { visibleRows, parentOf } = useMemo(() => {
    const rows: Array<{ path: string; name: string; type: 'dir' | 'file' }> = [];
    const parents: Record<string, string> = {};
    const walk = (node: FileTreeNode | WorkspaceDirEntry, parent: string | null) => {
      rows.push({ path: node.path, name: node.name, type: node.type });
      if (parent !== null) parents[node.path] = parent;
      if (node.type !== 'dir') return;
      if (!expanded.has(node.path)) return;
      const children = lazyChildren[node.path] ?? (node as FileTreeNode).children ?? [];
      for (const child of children) walk(child, node.path);
    };
    const rootChildren = tree ? (lazyChildren[tree.path]?.map(toNode) ?? tree.children ?? []) : [];
    for (const child of rootChildren) walk(child, null);
    return { visibleRows: rows, parentOf: parents };
  }, [tree, expanded, lazyChildren]);

  const focusRow = useCallback((path: string | null) => {
    if (!path) return;
    setFocusedPath(path);
    const el = rowRefs.current.get(path);
    el?.scrollIntoView({ block: 'nearest' });
  }, []);

  useEffect(() => {
    if (!focusedPath) return;
    rowRefs.current.get(focusedPath)?.focus();
  }, [focusedPath]);

  const onDividerMouseDown = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startPct: split };
    document.body.classList.add('dashboard-files__dragging');
    const onMove = (ev: MouseEvent) => {
      const el = filesRef.current;
      if (!el || !dragRef.current) return;
      const rect = el.getBoundingClientRect();
      const dx = ev.clientX - dragRef.current.startX;
      const pct = Math.min(80, Math.max(20, dragRef.current.startPct + (dx / rect.width) * 100));
      setSplit(pct);
    };
    const onUp = () => {
      dragRef.current = null;
      document.body.classList.remove('dashboard-files__dragging');
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [split]);

  const loadTree = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!workspaceAvailable) {
      setTree(null);
      setLoading(false);
      return;
    }
    try {
      const response = await chatService.getWorkspaceTree(projectId);
      setTree(response.tree);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId, workspaceAvailable]);

  useEffect(() => {
    void loadTree();
  }, [loadTree]);

  const expandDir = useCallback(
    async (path: string) => {
      setExpanded((current) => {
        const next = new Set(current);
        next.add(path);
        return next;
      });
      const node = findNode(tree, path);
      const alreadyLoaded = lazyChildren[path];
      if (node && node.children && node.children.length > 0) return;
      if (alreadyLoaded && alreadyLoaded.length > 0) return;
      setLoadingDir(path);
      try {
        const response = await chatService.getWorkspaceDir(projectId, path);
        setLazyChildren((current) => ({ ...current, [path]: response.entries }));
      } catch {
        setLazyChildren((current) => ({ ...current, [path]: [] }));
      } finally {
        setLoadingDir(null);
      }
    },
    [tree, lazyChildren, projectId],
  );

  const collapseDir = useCallback((path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      next.delete(path);
      return next;
    });
  }, []);

  const toggleDir = useCallback(
    (path: string) => {
      if (expanded.has(path)) collapseDir(path);
      else void expandDir(path);
    },
    [expanded, expandDir, collapseDir],
  );

  const openFile = useCallback(
    async (path: string) => {
      setPreviewLoading(true);
      setPreview({ path, preview: { kind: 'text', mime: 'text/plain', size: 0, content: '' } });
      try {
        const response = await chatService.getWorkspaceFilePreview(projectId, path);
        setPreview({ path, preview: response.preview });
      } catch {
        setPreview({ path, preview: { kind: 'other', mime: '', size: 0, error: t('dashboard.file_error') } });
      } finally {
        setPreviewLoading(false);
      }
    },
    [projectId],
  );

  const onTreeKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      const index = focusedPath ? visibleRows.findIndex((row) => row.path === focusedPath) : -1;
      const current = index >= 0 ? visibleRows[index] : undefined;
      const focusIndex = (next: number) => {
        const clamped = (next + visibleRows.length) % visibleRows.length;
        const row = visibleRows[clamped];
        if (row) focusRow(row.path);
      };
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          focusIndex(index < 0 ? 0 : index + 1);
          break;
        case 'ArrowUp':
          e.preventDefault();
          focusIndex(index < 0 ? 0 : index - 1);
          break;
        case 'ArrowRight': {
          if (!current) return;
          e.preventDefault();
          if (current.type === 'dir' && expanded.has(current.path)) {
            const firstChild = visibleRows[index + 1];
            if (firstChild && parentOf[firstChild.path] === current.path) focusRow(firstChild.path);
          } else if (current.type === 'dir') {
            void expandDir(current.path);
          }
          break;
        }
        case 'ArrowLeft': {
          if (!current) return;
          e.preventDefault();
          if (current.type === 'dir' && expanded.has(current.path)) {
            collapseDir(current.path);
          } else {
            const parent = parentOf[current.path];
            if (parent) focusRow(parent);
          }
          break;
        }
        default:
          break;
      }
    },
    [focusedPath, visibleRows, parentOf, expanded, expandDir, collapseDir, focusRow],
  );

  const rootChildren = useMemo(() => {
    if (!tree) return [];
    return lazyChildren[tree.path]?.map(toNode) ?? tree.children ?? [];
  }, [tree, lazyChildren]);

  if (loading) {
    return (
      <div className="dashboard-state">
        <Loader2 size={18} className="animate-spin" />
        <span>{t('dashboard.loading')}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard-state dashboard-state--error">
        <span>{t('dashboard.error')}: {error}</span>
        <Button variant="ghost" size="sm" onClick={() => void loadTree()}>{t('dashboard.retry')}</Button>
      </div>
    );
  }

  if (!tree) {
    return <div className="dashboard-state">{t('dashboard.no_workspace')}</div>;
  }

  return (
    <div
      ref={filesRef}
      className="dashboard-files"
      style={{ gridTemplateColumns: `${split}% minmax(8px, 10px) ${100 - split}%` }}
    >
      <div
        className="dashboard-files__tree"
        role="tree"
        aria-label={t('dashboard.files')}
        onKeyDown={onTreeKeyDown}
      >
        {rootChildren.map((node) => (
          <TreeNode
            key={node.path}
            node={node}
            depth={0}
            expanded={expanded}
            loadingDir={loadingDir}
            lazyChildren={lazyChildren}
            focusedPath={focusedPath}
            rowRefs={rowRefs}
            onToggleDir={toggleDir}
            onOpenFile={(path) => void openFile(path)}
            onFocusPath={focusRow}
          />
        ))}
      </div>
      <div
        className="dashboard-files__divider"
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={Math.round(split)}
        onMouseDown={onDividerMouseDown}
      />
      <div className="dashboard-files__preview">
        {preview ? (
          <>
            <div className="dashboard-files__preview-head">{preview.path}</div>
            <div className="dashboard-files__preview-body">
              {previewLoading ? (
                <div className="dashboard-state">
                  <Loader2 size={16} className="animate-spin" />
                </div>
              ) : (
                <PreviewContent
                  path={preview.path}
                  preview={preview.preview}
                  {...(workspacePath ? { workspacePath } : {})}
                />
              )}
            </div>
          </>
        ) : (
          <div className="dashboard-state">{t('dashboard.select_file')}</div>
        )}
      </div>
    </div>
  );
}

function dataUrl(mime: string, data: string): string {
  return `data:${mime || 'application/octet-stream'};base64,${data}`;
}

function absPath(workspacePath: string | undefined, rel: string): string {
  if (!workspacePath) return rel;
  const clean = rel.replace(/^\.\//, '');
  return `${workspacePath.replace(/\/+$/, '')}/${clean}`;
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

const UNSAFE_KIND_KEYS: Record<string, string> = {
  office: 'dashboard.preview_office',
  design: 'dashboard.preview_design',
  archive: 'dashboard.preview_archive',
  font: 'dashboard.preview_font',
  executable: 'dashboard.preview_executable',
  other: 'dashboard.preview_not_supported',
};

/** Map a file path to a Shiki language name for syntax highlighting. */
function languageFromPath(path: string): string {
  const ext = (path.split('.').pop() ?? '').toLowerCase();
  const byExt: Record<string, string> = {
    py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
    ts: 'typescript', tsx: 'tsx', json: 'json', jsonc: 'jsonc', md: 'markdown',
    markdown: 'markdown', css: 'css', scss: 'css', html: 'html', htm: 'html',
    sh: 'bash', bash: 'bash', zsh: 'bash', yaml: 'yaml', yml: 'yaml', xml: 'xml',
    java: 'java', go: 'go', rs: 'rust', c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp',
    cc: 'cpp', cs: 'csharp', sql: 'sql', php: 'php', rb: 'ruby', kt: 'kotlin',
    kts: 'kotlin', swift: 'swift', ps1: 'powershell', toml: 'toml', ini: 'ini',
    cfg: 'ini', conf: 'ini', graphql: 'graphql', gql: 'graphql', vue: 'vue',
    svelte: 'svelte', scala: 'scala', dart: 'dart', lua: 'lua', r: 'r',
    hs: 'haskell', fish: 'fish', diff: 'diff', patch: 'diff', proto: 'protobuf',
    sol: 'solidity', zig: 'zig',
  };
  const byName: Record<string, string> = {
    makefile: 'makefile', dockerfile: 'dockerfile', gemfile: 'ruby', rakefile: 'ruby',
  };
  return byExt[ext] ?? byName[path.toLowerCase()] ?? 'text';
}

function PreviewContent({
  path,
  preview,
  workspacePath,
}: {
  path: string;
  preview: WorkspaceFilePreview;
  workspacePath?: string;
}) {
  const { kind, mime, data, content } = preview;

  if (kind === 'text') {
    if (/\.(md|markdown)$/i.test(path)) {
      return (
        <div className="dashboard-files__markdown">
          <MarkdownContent content={content ?? ''} />
        </div>
      );
    }
    return (
      <div className="dashboard-files__code">
        <CodeBlock code={content ?? ''} language={languageFromPath(path)} />
      </div>
    );
  }

  if (kind === 'table' && content) {
    return <TablePreview content={content} />;
  }

  if (kind === 'image' && data) {
    return <img className="dashboard-files__media" src={dataUrl(mime, data)} alt={path} />;
  }

  if (kind === 'pdf' && data) {
    return <iframe className="dashboard-files__media" title={path} src={dataUrl(mime, data)} />;
  }

  if (kind === 'audio' && data) {
    return <audio className="dashboard-files__media" controls src={dataUrl(mime, data)} />;
  }

  if (kind === 'video' && data) {
    return <video className="dashboard-files__media" controls src={dataUrl(mime, data)} />;
  }

  if (kind === 'office' && data) {
    return (
      <OfficePreview
        path={path}
        data={data}
        {...(workspacePath ? { workspacePath } : {})}
      />
    );
  }

  return (
    <UnsupportedBlock
      path={path}
      preview={preview}
      messageKey={UNSAFE_KIND_KEYS[kind] ?? 'dashboard.preview_not_supported'}
      {...(workspacePath ? { workspacePath } : {})}
    />
  );
}

function UnsupportedBlock({
  path,
  preview,
  messageKey,
  workspacePath,
}: {
  path: string;
  preview?: WorkspaceFilePreview;
  messageKey: string;
  workspacePath?: string;
}) {
  return (
    <div className="dashboard-files__unsupported">
      <FolderSearch size={28} className="dashboard-files__unsupported-icon" />
      <p>{preview?.error ?? t(messageKey)}</p>
      {preview?.too_large && <p className="dashboard-files__unsupported-note">{t('dashboard.preview_too_large')}</p>}
      {workspacePath && (
        <div className="dashboard-files__unsupported-actions">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void chatService.openFileExternally(absPath(workspacePath, path))}
          >
            <ExternalLink size={14} />
            {t('dashboard.preview_open_external')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void chatService.revealInFolder(absPath(workspacePath, path))}
          >
            {t('dashboard.preview_reveal')}
          </Button>
        </div>
      )}
    </div>
  );
}

/** Office renderer with graceful fallback to the external-open block. */
function OfficePreview({
  path,
  data,
  workspacePath,
}: {
  path: string;
  data: string;
  workspacePath?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <UnsupportedBlock
        path={path}
        messageKey="dashboard.preview_office"
        {...(workspacePath ? { workspacePath } : {})}
      />
    );
  }
  if (/\.(docx)$/i.test(path)) {
    return <DocxPreview data={data} onError={() => setFailed(true)} />;
  }
  if (/\.(xlsx|xls|xlsb|xlc)$/i.test(path)) {
    return <XlsxPreview data={data} onError={() => setFailed(true)} />;
  }
  return (
    <UnsupportedBlock
      path={path}
      messageKey="dashboard.preview_office"
      {...(workspacePath ? { workspacePath } : {})}
    />
  );
}

function parseDelimited(content: string, delimiter: ',' | '\t'): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  const chars = content.replace(/\r\n/g, '\n');
  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i];
    if (inQuotes) {
      if (ch === '"') {
        if (chars[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += ch;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell.trim() !== '')).slice(0, 500);
}

const TABLE_ROW_HEIGHT = 24;
const TABLE_OVERSCAN = 10;
const TABLE_MAX_COLS = 60;

/** Windowed table: only renders rows in (or near) the scroll viewport, so
 *  large sheets scroll smoothly instead of building tens of thousands of
 *  <td> nodes at once. */
function VirtualizedTable({ rows }: { rows: string[][] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(300);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setViewportH(el.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxCols = useMemo(
    () => Math.min(TABLE_MAX_COLS, rows.reduce((max, row) => Math.max(max, row.length), 0)),
    [rows],
  );
  const totalHeight = rows.length * TABLE_ROW_HEIGHT;
  const start = Math.max(0, Math.floor(scrollTop / TABLE_ROW_HEIGHT) - TABLE_OVERSCAN);
  const end = Math.min(rows.length, Math.ceil((scrollTop + viewportH) / TABLE_ROW_HEIGHT) + TABLE_OVERSCAN);

  return (
    <div
      ref={wrapRef}
      className="dashboard-files__table-wrap"
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
    >
      <div className="dashboard-files__table-spacer" style={{ height: totalHeight }}>
        <table className="dashboard-files__table" style={{ transform: `translateY(${start * TABLE_ROW_HEIGHT}px)` }}>
          <tbody>
            {rows.slice(start, end).map((row, i) => (
              <tr key={start + i} style={{ height: TABLE_ROW_HEIGHT }}>
                {Array.from({ length: maxCols }).map((_, c) => (
                  <td key={c}>{row[c] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TablePreview({ content }: { content: string }) {
  const isTsv = content.includes('\t');
  const rows = useMemo(() => parseDelimited(content, isTsv ? '\t' : ','), [content, isTsv]);
  return <VirtualizedTable rows={rows} />;
}

function DocxPreview({ data, onError }: { data: string; onError?: () => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { renderAsync } = await import('docx-preview');
        if (cancelled || !containerRef.current) return;
        await renderAsync(base64ToBytes(data), containerRef.current, undefined, {
          inWrapper: false,
          ignoreWidth: true,
          ignoreHeight: true,
          breakPages: false,
        });
      } catch {
        if (!cancelled) onError?.();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, onError]);
  return <div ref={containerRef} className="dashboard-files__docx" />;
}

function XlsxPreview({ data, onError }: { data: string; onError?: () => void }) {
  const [grid, setGrid] = useState<string[][] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const XLSX = await import('xlsx');
        const wb = XLSX.read(data, { type: 'base64' });
        const sheetName = wb.SheetNames[0];
        if (!sheetName) throw new Error('empty workbook');
        const sheet = wb.Sheets[sheetName];
        if (!sheet) throw new Error('empty workbook');
        const rows = XLSX.utils.sheet_to_json<string[]>(sheet, { header: 1, defval: '' });
        if (!cancelled) setGrid(rows.slice(0, 500));
      } catch {
        if (!cancelled) onError?.();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, onError]);
  if (!grid) {
    return (
      <div className="dashboard-state">
        <Loader2 size={16} className="animate-spin" />
      </div>
    );
  }
  return <VirtualizedTable rows={grid} />;
}

interface TreeNodeProps {
  node: FileTreeNode | WorkspaceDirEntry;
  depth: number;
  expanded: Set<string>;
  loadingDir: string | null;
  lazyChildren: Record<string, WorkspaceDirEntry[]>;
  focusedPath: string | null;
  rowRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  onToggleDir: (path: string) => void;
  onOpenFile: (path: string) => void;
  onFocusPath: (path: string) => void;
}

function toNode(entry: WorkspaceDirEntry): FileTreeNode {
  return { name: entry.name, path: entry.path, type: entry.type, size: entry.size ?? null };
}

function TreeNode({ node, depth, expanded, loadingDir, lazyChildren, focusedPath, rowRefs, onToggleDir, onOpenFile, onFocusPath }: TreeNodeProps) {
  const isDir = node.type === 'dir';
  const isExpanded = expanded.has(node.path);
  const isFocused = focusedPath === node.path;
  const children = isDir ? (lazyChildren[node.path] ?? (node as FileTreeNode).children ?? []) : [];
  const isLoading = loadingDir === node.path;

  return (
    <div className="dashboard-tree-node">
      <button
        ref={(el) => {
          if (el) rowRefs.current.set(node.path, el);
          else rowRefs.current.delete(node.path);
        }}
        type="button"
        role="treeitem"
        aria-selected={isFocused}
        {...(isDir ? { 'aria-expanded': isExpanded } : {})}
        className={`dashboard-tree-row${isFocused ? ' dashboard-tree-row--focused' : ''}`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => {
          onFocusPath(node.path);
          if (isDir) onToggleDir(node.path);
          else onOpenFile(node.path);
        }}
      >
        {isDir ? (
          isLoading ? (
            <Loader2 size={13} className="animate-spin dashboard-tree-icon" />
          ) : isExpanded ? (
            <ChevronDown size={13} className="dashboard-tree-icon" />
          ) : (
            <ChevronRight size={13} className="dashboard-tree-icon" />
          )
        ) : (
          <span className="dashboard-tree-icon" />
        )}
        {isDir ? <Folder size={14} className="dashboard-tree-folder" /> : <FileText size={14} className="dashboard-tree-file" />}
        <span className="dashboard-tree-name">{node.name}</span>
        {!isDir && typeof node.size === 'number' && (
          <span className="dashboard-tree-size">{formatBytes(node.size)}</span>
        )}
      </button>
      {isDir && isExpanded && children.length > 0 && (
        <div className="dashboard-tree-children">
          {children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              loadingDir={loadingDir}
              lazyChildren={lazyChildren}
              focusedPath={focusedPath}
              rowRefs={rowRefs}
              onToggleDir={onToggleDir}
              onOpenFile={onOpenFile}
              onFocusPath={onFocusPath}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function findNode(node: FileTreeNode | null, path: string): FileTreeNode | null {
  if (!node) return null;
  if (node.path === path) return node;
  for (const child of node.children ?? []) {
    const found = findNode(child, path);
    if (found) return found;
  }
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
