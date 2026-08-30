import { ChevronDown, ChevronRight, FileText, Folder, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { FileTreeNode, WorkspaceDirEntry } from '../../types';
import { Button } from '../ui/button';

interface DashboardFilesProps {
  projectId: string;
  workspaceAvailable: boolean;
}

interface PreviewState {
  path: string;
  content: string;
  binary: boolean;
  hint?: string;
}

export function DashboardFiles({ projectId, workspaceAvailable }: DashboardFilesProps) {
  const [tree, setTree] = useState<FileTreeNode | null>(null);
  const [lazyChildren, setLazyChildren] = useState<Record<string, WorkspaceDirEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [loadingDir, setLoadingDir] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
      setPreview({ path, content: '', binary: false });
      try {
        const response = await chatService.getWorkspaceFile(projectId, path);
        const file = response.file;
        if (file.binary) {
          setPreview({ path, content: '', binary: true, ...(file.hint ? { hint: file.hint } : {}) });
        } else {
          setPreview({ path, content: file.content ?? '', binary: false });
        }
      } catch {
        setPreview({ path, content: t('dashboard.file_error'), binary: false });
      }
    },
    [projectId],
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
    <div className="dashboard-files">
      <div className="dashboard-files__tree">
        {rootChildren.map((node) => (
          <TreeNode
            key={node.path}
            node={node}
            depth={0}
            expanded={expanded}
            loadingDir={loadingDir}
            lazyChildren={lazyChildren}
            onToggleDir={toggleDir}
            onOpenFile={(path) => void openFile(path)}
          />
        ))}
      </div>
      <div className="dashboard-files__preview">
        {preview ? (
          <>
            <div className="dashboard-files__preview-head">{preview.path}</div>
            <div className="dashboard-files__preview-body">
              {preview.binary ? (
                <span className="dashboard-files__binary">{preview.hint ?? t('dashboard.binary_file')}</span>
              ) : (
                <pre>{preview.content || ' '}</pre>
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

interface TreeNodeProps {
  node: FileTreeNode | WorkspaceDirEntry;
  depth: number;
  expanded: Set<string>;
  loadingDir: string | null;
  lazyChildren: Record<string, WorkspaceDirEntry[]>;
  onToggleDir: (path: string) => void;
  onOpenFile: (path: string) => void;
}

function toNode(entry: WorkspaceDirEntry): FileTreeNode {
  return { name: entry.name, path: entry.path, type: entry.type, size: entry.size ?? null };
}

function TreeNode({ node, depth, expanded, loadingDir, lazyChildren, onToggleDir, onOpenFile }: TreeNodeProps) {
  const isDir = node.type === 'dir';
  const isExpanded = expanded.has(node.path);
  const children = isDir ? (lazyChildren[node.path] ?? (node as FileTreeNode).children ?? []) : [];
  const isLoading = loadingDir === node.path;

  return (
    <div className="dashboard-tree-node">
      <button
        type="button"
        className="dashboard-tree-row"
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => (isDir ? onToggleDir(node.path) : onOpenFile(node.path))}
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
              onToggleDir={onToggleDir}
              onOpenFile={onOpenFile}
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
