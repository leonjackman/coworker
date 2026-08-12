import { BrainCircuit, Check, Loader2, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { MemoryProposalRecord, MemoryScope, MemoryScopeInfo, MemoryStatusResponse } from '../types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Switch } from './ui/switch';
import { WorkspacePage } from './ui/workspace-page';

interface MemoryPanelProps {
  onClose?: () => void;
}

type Flash = { kind: 'ok' | 'error'; text: string } | null;

const SCOPE_LABELS: Record<MemoryScope, string> = {
  project: 'memory.scope.project',
  user: 'memory.scope.user',
};

const SCOPE_SORT: MemoryScope[] = ['project', 'user'];

export function MemoryPanel({ onClose }: MemoryPanelProps) {
  const [status, setStatus] = useState<MemoryStatusResponse | null>(null);
  const [proposals, setProposals] = useState<MemoryProposalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [proposalsLoading, setProposalsLoading] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [activeScope, setActiveScope] = useState<MemoryScope>('project');
  const [newText, setNewText] = useState('');

  const notify = (kind: 'ok' | 'error', text: string) => {
    setFlash({ kind, text });
    window.setTimeout(() => setFlash(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p] = await Promise.all([chatService.getMemoryStatus(), chatService.listMemoryProposals()]);
      setStatus(s);
      setProposals(p.proposals);
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshProposals = useCallback(async () => {
    setProposalsLoading(true);
    try {
      const p = await chatService.listMemoryProposals();
      setProposals(p.proposals);
      const s = await chatService.getMemoryStatus();
      setStatus(s);
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setProposalsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const addEntry = async () => {
    const content = newText.trim();
    if (!content) return;
    try {
      const res = await chatService.writeMemoryEntry({ scope: activeScope, content });
      setNewText('');
      notify('ok', t('memory.added'));
      setStatus((cur) =>
        cur
          ? {
              ...cur,
              scopes: {
                ...cur.scopes,
                [activeScope]: {
                  ...(cur.scopes[activeScope] as MemoryScopeInfo),
                  entries: res.entries,
                  entry_count: res.entries.length,
                  char_count: res.entries.reduce((acc, e) => acc + e.length, 0),
                },
              },
            }
          : cur,
      );
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const removeEntry = async (target: string) => {
    try {
      const res = await chatService.removeMemoryEntry({ scope: activeScope, content: '', target });
      notify('ok', t('memory.removed'));
      setStatus((cur) =>
        cur
          ? {
              ...cur,
              scopes: {
                ...cur.scopes,
                [activeScope]: {
                  ...(cur.scopes[activeScope] as MemoryScopeInfo),
                  entries: res.entries,
                  entry_count: res.entries.length,
                  char_count: res.entries.reduce((acc, e) => acc + e.length, 0),
                },
              },
            }
          : cur,
      );
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const clearScope = async (scope: MemoryScope) => {
    try {
      await chatService.clearMemoryScope(scope);
      notify('ok', t('memory.cleared'));
      setStatus((cur) =>
        cur
          ? { ...cur, scopes: { ...cur.scopes, [scope]: { ...cur.scopes[scope], entries: [], entry_count: 0, char_count: 0 } as MemoryScopeInfo } }
          : cur,
      );
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const decideProposal = async (id: string, decision: 'approved' | 'rejected') => {
    try {
      await chatService.resolveMemoryProposal({ proposal_id: id, status: decision });
      notify('ok', decision === 'approved' ? t('memory.proposal.approved') : t('memory.proposal.rejected'));
      await refreshProposals();
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  const scopes = status ? SCOPE_SORT.filter((s) => status.scopes[s]) : [];

  return (
    <WorkspacePage
      eyebrow={t('memory.eyebrow')}
      title={t('memory.title')}
      description={t('memory.description')}
      action={
        onClose ? (
          <Button variant="outline" size="icon" aria-label={t('common.close')} onClick={onClose}>
            <X size={16} />
          </Button>
        ) : undefined
      }
    >
      {flash && (
        <div className={`memory-flash memory-flash--${flash.kind}`} role="status">
          {flash.text}
        </div>
      )}

      {loading && !status ? (
        <div className="memory-loading">
          <Loader2 className="animate-spin" size={18} />
          <span>{t('common.loading')}</span>
        </div>
      ) : (
        <>
          <section className="memory-status-row">
            <div className="memory-stat">
              <span className="memory-stat__label">{t('memory.status.enabled')}</span>
              <span className={`memory-stat__value ${status?.enabled ? 'memory-stat__value--ok' : ''}`}>
                {status?.enabled ? t('memory.enabled') : t('memory.disabled')}
              </span>
            </div>
            <div className="memory-stat">
              <span className="memory-stat__label">{t('memory.status.auto_extract')}</span>
              <span className={`memory-stat__value ${status?.auto_extract ? 'memory-stat__value--ok' : ''}`}>
                {status?.auto_extract ? t('memory.enabled') : t('memory.disabled')}
              </span>
            </div>
            <div className="memory-stat">
              <span className="memory-stat__label">{t('memory.status.nudge_interval')}</span>
              <span className="memory-stat__value">{status?.nudge_interval ?? '-'}</span>
            </div>
            <div className="memory-stat">
              <span className="memory-stat__label">{t('memory.status.char_limit')}</span>
              <span className="memory-stat__value">{status?.char_limit ?? '-'}</span>
            </div>
            <div className="memory-stat">
              <span className="memory-stat__label">{t('memory.status.proposals')}</span>
              <span className="memory-stat__value">{status?.proposals_pending ?? proposals.length}</span>
            </div>
            <div className="memory-status-row__actions">
              <Button variant="outline" size="sm" onClick={() => void load()}>
                <RefreshCw size={14} />
                {t('common.refresh')}
              </Button>
            </div>
          </section>

          <section className="memory-scopes">
            <div className="memory-subheading">{t('memory.entries_title')}</div>
            {scopes.map((scope) => {
              const info = status?.scopes[scope];
              return (
                <div key={scope} className="memory-scope-card">
                  <div className="memory-scope-card__header">
                    <div>
                      <span className="memory-scope-card__title">{t(SCOPE_LABELS[scope])}</span>
                      <span className="memory-scope-card__meta">
                        {info?.entry_count ?? 0} {t('memory.entries_count')} · {(info?.char_count ?? 0) >= (status?.char_limit ?? 0) ? `${info?.char_count ?? 0}/${status?.char_limit ?? 0} ${t('memory.chars')}` : `${info?.char_count ?? 0} ${t('memory.chars')}`}
                        {info?.path ? ` · ${info.path}` : ''}
                      </span>
                    </div>
                    <Button variant="destructive" size="icon-xs" disabled={!info?.entry_count} onClick={() => void clearScope(scope)}>
                      <Trash2 size={13} />
                    </Button>
                  </div>
                  <ul className="memory-entry-list">
                    {(info?.entries ?? []).map((entry) => (
                      <li key={entry} className="memory-entry">
                        <span className="memory-entry__text">{entry}</span>
                        <button className="memory-entry__remove" type="button" onClick={() => void removeEntry(entry)} aria-label={t('memory.remove_entry')}>
                          <X size={13} />
                        </button>
                      </li>
                    ))}
                    {!info?.entry_count && <li className="memory-entry memory-entry--empty">{t('memory.empty')}</li>}
                  </ul>
                </div>
              );
            })}
          </section>

          <section className="memory-add">
            <div className="memory-subheading">{t('memory.add_title')}</div>
            <div className="memory-add__row">
              <div className="memory-add__scope">
                <span className="memory-tab-label">{t('memory.scope_label')}</span>
                <div className="memory-scope-tabs">
                  {SCOPE_SORT.map((scope) => (
                    <button
                      key={scope}
                      className={`memory-scope-tab ${activeScope === scope ? 'memory-scope-tab--active' : ''}`}
                      type="button"
                      onClick={() => setActiveScope(scope)}
                    >
                      {t(SCOPE_LABELS[scope])}
                    </button>
                  ))}
                </div>
              </div>
              <div className="memory-add__input">
                <input
                  className="memory-add__field"
                  type="text"
                  value={newText}
                  placeholder={t('memory.add_placeholder')}
                  onChange={(e) => setNewText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void addEntry();
                  }}
                />
                <Button variant="primary" size="sm" disabled={!newText.trim()} onClick={() => void addEntry()}>
                  <Plus size={14} />
                  {t('memory.add_button')}
                </Button>
              </div>
            </div>
          </section>

          <section className="memory-proposals">
            <div className="memory-subheading">
              {t('memory.proposals_title')}
              { proposalsLoading && <Loader2 className="inline-block animate-spin ml-2" size={13} /> }
            </div>
            {proposals.length === 0 ? (
              <div className="memory-proposals__empty">{t('memory.proposals_empty')}</div>
            ) : (
              <ul className="memory-proposal-list">
                {proposals.map((p) => (
                  <li key={p.id} className="memory-proposal">
                    <div className="memory-proposal__body">
                      <span className="memory-proposal__text">{p.text}</span>
                      <Badge variant="secondary">{p.provider || t('memory.proposal.provider_unknown')}</Badge>
                    </div>
                    <div className="memory-proposal__actions">
                      <Button variant="outline" size="sm" onClick={() => void decideProposal(p.id, 'approved')}>
                        <Check size={13} />
                        {t('memory.proposal.approve')}
                      </Button>
                      <Button variant="destructive" size="sm" onClick={() => void decideProposal(p.id, 'rejected')}>
                        <X size={13} />
                        {t('memory.proposal.reject')}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </WorkspacePage>
  );
}

export default MemoryPanel;