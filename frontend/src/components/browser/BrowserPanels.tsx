import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, ExternalLink, FolderOpen, Pause, Play, Plus, Search, Trash2, X, Download as DownloadIcon, Star } from 'lucide-react';
import { t } from '../../lib/i18n';
import { dayLabel, displayTitle, formatBytes, formatTime, hostOf } from '../../lib/browser';
import type { BrowserBookmark, BrowserDownload, BrowserHistoryEntry } from '../../types';

const api = () => window.electronAPI;

function Overlay({ title, onClose, children, actions }: { title: string; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="browser-overlay" role="presentation" onMouseDown={onClose}>
      <div className="browser-overlay__panel" role="dialog" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="browser-overlay__head">
          <span className="browser-overlay__title">{title}</span>
          <div className="browser-overlay__actions">{actions}</div>
          <button type="button" className="browser-view__nav" onClick={onClose} aria-label={t('common.close')}>
            <X size={13} />
          </button>
        </div>
        <div className="browser-overlay__body">{children}</div>
      </div>
    </div>
  );
}

function dayHeading(key: string): string {
  if (key === 'today') return t('browser.history_today');
  if (key === 'yesterday') return t('browser.history_yesterday');
  if (key === 'earlier_week') return t('browser.history_earlier_week');
  return t('browser.history_earlier');
}

export function BrowserHistoryPanel({ open, onClose, onNavigate }: { open: boolean; onClose: () => void; onNavigate: (url: string) => void }) {
  const [items, setItems] = useState<BrowserHistoryEntry[]>([]);
  const [query, setQuery] = useState('');

  const load = (q: string) => {
    void api()?.browserHistoryList({ query: q, limit: 500 }).then((r) => setItems(r?.items ?? []));
  };

  useEffect(() => {
    if (open) load('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const groups = useMemo(() => {
    const ordered: Array<{ key: string; entries: BrowserHistoryEntry[] }> = [];
    for (const entry of items) {
      const key = dayLabel(entry.visited_at);
      const last = ordered[ordered.length - 1];
      if (last && last.key === key) last.entries.push(entry);
      else ordered.push({ key, entries: [entry] });
    }
    return ordered;
  }, [items]);

  if (!open) return null;

  return (
    <Overlay
      title={t('browser.history_title')}
      onClose={onClose}
      actions={
        <button
          type="button"
          className="browser-overlay__link"
          onClick={() => { void api()?.browserHistoryClear().then(() => setItems([])); }}
        >
          {t('browser.clear_all')}
        </button>
      }
    >
      <div className="browser-overlay__search">
        <Search size={13} />
        <input
          value={query}
          placeholder={t('browser.history_search')}
          onChange={(e) => { setQuery(e.target.value); load(e.target.value); }}
        />
      </div>
      {items.length === 0 ? (
        <p className="browser-overlay__empty">{t('browser.history_empty')}</p>
      ) : (
        groups.map((group) => (
          <div key={group.key} className="browser-history__group">
            <div className="browser-history__day">{dayHeading(group.key)}</div>
            {group.entries.map((entry) => (
              <div key={entry.id} className="browser-history__row">
                <button type="button" className="browser-history__main" onClick={() => { onNavigate(entry.url); onClose(); }} title={entry.url}>
                  <span className="browser-history__title">{displayTitle(entry.title, entry.url)}</span>
                  <span className="browser-history__meta">{hostOf(entry.url)} · {formatTime(entry.visited_at)}</span>
                </button>
                <button
                  type="button"
                  className="browser-history__remove"
                  aria-label={t('browser.remove')}
                  onClick={() => { void api()?.browserHistoryRemove(entry.id).then(() => setItems((prev) => prev.filter((x) => x.id !== entry.id))); }}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        ))
      )}
    </Overlay>
  );
}

function downloadStateLabel(state: BrowserDownload['state']): string {
  if (state === 'completed') return t('browser.download_completed');
  if (state === 'cancelled') return t('browser.download_cancelled');
  if (state === 'interrupted') return t('browser.download_interrupted');
  if (state === 'paused') return t('browser.download_paused');
  return t('browser.download_progressing');
}

export function BrowserDownloadsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<BrowserDownload[]>([]);

  useEffect(() => {
    if (!open) return;
    const load = () => { void api()?.browserDownloadsList(200).then((r) => setItems(r?.items ?? [])); };
    load();
    const unsub = api()?.onBrowserDownload((download) => {
      setItems((prev) => {
        const existing = prev.find((d) => d.id === download.id);
        // Progress/control events are partial: merge onto the known row.
        const merged = existing ? { ...existing, ...download } : download;
        return [merged, ...prev.filter((d) => d.id !== download.id)];
      });
    });
    return () => unsub?.();
  }, [open]);

  if (!open) return null;

  return (
    <Overlay
      title={t('browser.downloads_title')}
      onClose={onClose}
      actions={
        <button type="button" className="browser-overlay__link" onClick={() => { void api()?.browserDownloadsClear().then(() => setItems([])); }}>
          {t('browser.clear_all')}
        </button>
      }
    >
      {items.length === 0 ? (
        <p className="browser-overlay__empty">{t('browser.downloads_empty')}</p>
      ) : (
        items.map((item) => {
          const percent = item.total_bytes > 0 ? Math.min(100, Math.round((item.received_bytes / item.total_bytes) * 100)) : 0;
          return (
            <div key={item.id} className="browser-download__row">
              <DownloadIcon size={14} className="browser-download__icon" />
              <div className="browser-download__info">
                <span className="browser-download__name">{item.filename || item.url}</span>
                <span className="browser-download__meta">
                  {downloadStateLabel(item.state)}
                  {item.total_bytes > 0 ? ` · ${formatBytes(item.received_bytes)} / ${formatBytes(item.total_bytes)}` : ''}
                </span>
                {(item.state === 'progressing' || item.state === 'paused') && item.total_bytes > 0 && (
                  <div className="browser-download__bar"><span style={{ width: `${percent}%` }} /></div>
                )}
              </div>
              <div className="browser-download__actions">
                {item.state === 'progressing' && (
                  <button type="button" className="browser-view__nav" title={t('browser.download_pause')} onClick={() => void api()?.browserDownloadPause(item.id)}>
                    <Pause size={12} />
                  </button>
                )}
                {item.state === 'paused' && (
                  <button type="button" className="browser-view__nav" title={t('browser.download_resume')} onClick={() => void api()?.browserDownloadResume(item.id)}>
                    <Play size={12} />
                  </button>
                )}
                {(item.state === 'progressing' || item.state === 'paused') && (
                  <button type="button" className="browser-view__nav" title={t('browser.download_cancel')} onClick={() => void api()?.browserDownloadCancel(item.id)}>
                    <X size={12} />
                  </button>
                )}
                {item.state === 'completed' && item.path && (
                  <>
                    <button type="button" className="browser-view__nav" title={t('browser.download_open')} onClick={() => void api()?.browserDownloadOpen(item.path)}>
                      <ExternalLink size={12} />
                    </button>
                    <button type="button" className="browser-view__nav" title={t('browser.download_reveal')} onClick={() => void api()?.browserDownloadReveal(item.path)}>
                      <FolderOpen size={12} />
                    </button>
                  </>
                )}
                <button type="button" className="browser-view__nav" title={t('browser.remove')} onClick={() => void api()?.browserDownloadRemove(item.id).then(() => setItems((prev) => prev.filter((x) => x.id !== item.id)))}>
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          );
        })
      )}
    </Overlay>
  );
}

export function BrowserBookmarkManager({ open, onClose, onNavigate, currentUrl, currentTitle }: { open: boolean; onClose: () => void; onNavigate: (url: string) => void; currentUrl: string; currentTitle: string }) {
  const [items, setItems] = useState<BrowserBookmark[]>([]);

  const load = () => {
    void api()?.browserBookmarksList().then((r) => setItems(r?.items ?? []));
  };

  useEffect(() => {
    if (open) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const move = (index: number, delta: number) => {
    const next = [...items];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target]!, next[index]!];
    setItems(next);
    void api()?.browserBookmarksReorder(next.map((b) => b.id));
  };

  if (!open) return null;

  const addCurrent = () => {
    if (!currentUrl) return;
    void api()?.browserBookmarkAdd({ url: currentUrl, title: currentTitle || currentUrl }).then(load);
  };

  return (
    <Overlay
      title={t('browser.bookmarks_title')}
      onClose={onClose}
      actions={
        <button type="button" className="browser-overlay__link" onClick={addCurrent} disabled={!currentUrl}>
          {t('browser.bookmark_add_current')}
        </button>
      }
    >
      {items.length === 0 ? (
        <p className="browser-overlay__empty">{t('browser.bookmarks_empty')}</p>
      ) : (
        items.map((item, index) => (
          <div key={item.id} className="browser-bookmark__row">
            <button type="button" className="browser-bookmark__main" onClick={() => { onNavigate(item.url); onClose(); }} title={item.url}>
              <span className="browser-bookmark__title">{displayTitle(item.title, item.url)}</span>
              <span className="browser-bookmark__meta">{hostOf(item.url)}</span>
            </button>
            <div className="browser-bookmark__actions">
              <button type="button" className="browser-view__nav" onClick={() => move(index, -1)} aria-label={t('browser.move_up')}>
                <ArrowLeft size={12} style={{ transform: 'rotate(90deg)' }} />
              </button>
              <button type="button" className="browser-view__nav" onClick={() => move(index, 1)} aria-label={t('browser.move_down')}>
                <ArrowRight size={12} style={{ transform: 'rotate(90deg)' }} />
              </button>
              <button
                type="button"
                className="browser-view__nav"
                onClick={() => {
                  const title = window.prompt(t('browser.bookmark_rename_prompt'), item.title);
                  if (title == null) return;
                  void api()?.browserBookmarkUpdate({ id: item.id, title }).then(load);
                }}
                aria-label={t('browser.rename')}
              >
                <Star size={12} />
              </button>
              <button type="button" className="browser-view__nav" onClick={() => void api()?.browserBookmarkRemove(item.id).then(load)} aria-label={t('browser.remove')}>
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))
      )}
    </Overlay>
  );
}

export function BrowserBookmarksBar({ items, onNavigate, onManage }: { items: BrowserBookmark[]; onNavigate: (url: string) => void; onManage: () => void }) {
  if (items.length === 0) return null;
  return (
    <div className="browser-bookmarks-bar">
      {items.slice(0, 24).map((item) => (
        <button key={item.id} type="button" className="browser-bookmarks-bar__item" title={item.url} onClick={() => onNavigate(item.url)}>
          {displayTitle(item.title, item.url)}
        </button>
      ))}
      <button type="button" className="browser-bookmarks-bar__manage" onClick={onManage} aria-label={t('browser.bookmarks_title')}>
        <Plus size={12} />
      </button>
    </div>
  );
}
