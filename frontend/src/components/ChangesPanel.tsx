import { FileDiff, GitBranch, RefreshCw, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { t } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { CurrentDiffResponse, DiffHunk, SessionChangesResponse } from '../types';
import { Button } from './ui/button';
import { FileDiffViewer } from './FileDiffViewer';

interface ChangesPanelProps {
  sessionId?: string;
  projectId?: string;
  open: boolean;
  onClose: () => void;
  onRefreshKey: number;
}

type TabSource = { kind: 'current' } | { kind: 'turn'; turn: number };

interface FileEntry {
  path: string;
  added: number;
  removed: number;
  kind: 'write' | 'edit' | 'binary';
}

interface SelectedDiff {
  path: string;
  hunks?: DiffHunk[];
  diffText?: string;
  kind: 'write' | 'edit' | 'binary';
}

export function ChangesPanel({ sessionId, projectId, open, onClose, onRefreshKey }: ChangesPanelProps) {
  const [sessionChanges, setSessionChanges] = useState<SessionChangesResponse | null>(null);
  const [currentDiff, setCurrentDiff] = useState<CurrentDiffResponse | null>(null);
  const [activeTab, setActiveTab] = useState<TabSource>({ kind: 'current' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const options: { projectId?: string; sessionId?: string } = {};
      if (projectId) options.projectId = projectId;
      if (sessionId) options.sessionId = sessionId;
      const [currentResult, sessionResult] = await Promise.all([
        chatService.getCurrentDiff(options),
        sessionId ? chatService.getSessionChanges(sessionId) : Promise.resolve(null),
      ]);
      setCurrentDiff(currentResult);
      setSessionChanges(sessionResult);
      const firstGitFile = currentResult.git && currentResult.files.length > 0 ? (currentResult.files[0]?.path ?? null) : null;
      const firstTurn = sessionResult?.turns.at(0);
      const firstTurnChange = firstTurn?.changes.at(0);
      setSelectedFile(firstGitFile ?? firstTurnChange?.file_path ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load changes');
    } finally {
      setLoading(false);
    }
  }, [projectId, sessionId]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load, onRefreshKey]);

  const turns = useMemo(() => sessionChanges?.turns ?? [], [sessionChanges]);

  const files: FileEntry[] = useMemo(() => {
    if (activeTab.kind === 'current') {
      return (currentDiff?.files ?? []).map((file) => ({
        path: file.path,
        added: file.added,
        removed: file.removed,
        kind: file.binary ? ('binary' as const) : ('edit' as const),
      }));
    }
    const turn = turns.find((item) => item.turn_index === activeTab.turn);
    return (turn?.changes ?? []).map((change) => ({
      path: change.file_path,
      added: change.added,
      removed: change.removed,
      kind: change.kind as FileEntry['kind'],
    }));
  }, [activeTab, currentDiff, turns]);

  const selectedDiff: SelectedDiff | null = useMemo(() => {
    if (activeTab.kind === 'current') {
      const file = (currentDiff?.files ?? []).find((item) => item.path === selectedFile);
      if (!file) return null;
      if (file.binary) return { path: file.path, kind: 'binary' as const };
      const result: SelectedDiff = { path: file.path, kind: 'edit' as const };
      if (file.diff) result.diffText = file.diff;
      return result;
    }
    const turn = turns.find((item) => item.turn_index === activeTab.turn);
    const change = (turn?.changes ?? []).find((item) => item.file_path === selectedFile);
    if (!change) return null;
    const result: SelectedDiff = { path: change.file_path, kind: change.kind as 'write' | 'edit' };
    if (change.hunks && change.hunks.length > 0) result.hunks = change.hunks;
    return result;
  }, [activeTab, currentDiff, selectedFile, turns]);

  const tabs = useMemo(() => {
    const list: Array<{ key: string; label: string; source: TabSource }> = [];
    list.push({ key: 'current', label: t('changes.current'), source: { kind: 'current' } });
    const sortedTurns = [...turns].sort((a, b) => b.turn_index - a.turn_index);
    for (const turn of sortedTurns) {
      list.push({ key: `turn-${turn.turn_index}`, label: `T${turn.turn_index}`, source: { kind: 'turn', turn: turn.turn_index } });
    }
    return list;
  }, [turns]);

  useEffect(() => {
    if (activeTab.kind === 'turn' && turns.length > 0 && !turns.some((item) => item.turn_index === activeTab.turn)) {
      const sorted = [...turns].sort((a, b) => b.turn_index - a.turn_index);
      const latest = sorted[0];
      if (latest) setActiveTab({ kind: 'turn', turn: latest.turn_index });
    }
  }, [turns, activeTab]);

  return (
    <aside className="changes-panel" aria-hidden={!open}>
      <div className="changes-panel__header">
        <span className="changes-panel__title">
          <FileDiff size={15} />
          {t('changes.title')}
        </span>
        <Button type="button" variant="icon" size="icon-sm" onClick={onClose} aria-label={t('changes.close')}>
          <X size={15} />
        </Button>
      </div>

      <div className="changes-panel__tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive(activeTab, tab.source)}
            className={`changes-tab ${isActive(activeTab, tab.source) ? 'changes-tab--active' : ''}`}
            onClick={() => {
              setActiveTab(tab.source);
              setSelectedFile(null);
            }}
          >
            {tab.label}
          </button>
        ))}
        <div className="changes-panel__tabs-spacer" />
        <Button type="button" variant="icon" size="icon-sm" onClick={() => void load()} disabled={loading} aria-label={t('changes.refresh')}>
          <RefreshCw size={13} className={loading ? 'changes-panel__spin' : ''} />
        </Button>
      </div>

      <div className="changes-panel__body">
        {error && <div className="changes-panel__empty">{error}</div>}
        {!error && loading && files.length === 0 && <div className="changes-panel__empty">{t('changes.loading')}</div>}
        {!error && !loading && files.length === 0 && <div className="changes-panel__empty">{t('changes.empty')}</div>}

        {!error && files.length > 0 && (
          <div className="changes-panel__files">
            {files.map((file) => (
              <button
                type="button"
                key={`${activeTab.kind}-${activeTab.kind === 'turn' ? activeTab.turn : 'current'}-${file.path}`}
                className={`changes-file ${selectedFile === file.path ? 'changes-file--active' : ''}`}
                onClick={() => setSelectedFile(file.path)}
              >
                <span className="changes-file__path">{file.path}</span>
                <span className="changes-file__stats">
                  {file.added > 0 && <span className="file-counts__add">+{file.added}</span>}
                  {file.removed > 0 && <span className="file-counts__del">-{file.removed}</span>}
                </span>
              </button>
            ))}
          </div>
        )}

        {!error && activeTab.kind === 'current' && currentDiff && !currentDiff.git && (
          <div className="changes-panel__notice">
            <GitBranch size={13} />
            {t('changes.no_git')}
          </div>
        )}

        {!error && activeTab.kind === 'current' && currentDiff && currentDiff.untracked.length > 0 && (
          <div className="changes-panel__untracked">
            <div className="changes-panel__untracked-label">{t('changes.untracked')}</div>
            {currentDiff.untracked.map((path) => (
              <div className="changes-file" key={path}>
                <span className="changes-file__path">{path}</span>
              </div>
            ))}
          </div>
        )}

        {!error && selectedDiff && selectedFile && (
          <div className="changes-panel__detail">
            {selectedDiff.kind === 'binary' ? (
              <div className="changes-panel__empty">{t('changes.binary')}</div>
            ) : selectedDiff.hunks || selectedDiff.diffText ? (
              <FileDiffViewer
                path={selectedDiff.path}
                {...(selectedDiff.hunks && selectedDiff.hunks.length > 0 ? { hunks: selectedDiff.hunks } : {})}
                {...(selectedDiff.diffText ? { diffText: selectedDiff.diffText } : {})}
                kind={selectedDiff.kind === 'write' ? 'write' : 'edit'}
              />
            ) : (
              <div className="changes-panel__empty">{t('changes.empty')}</div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function isActive(activeTab: TabSource, source: TabSource): boolean {
  if (activeTab.kind !== source.kind) return false;
  if (activeTab.kind === 'turn' && source.kind === 'turn') return activeTab.turn === source.turn;
  return true;
}
