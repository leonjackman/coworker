import { Check, ChevronDown, Loader2, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Button } from './ui/button';
import { GridCard } from './ui/grid-card';
import { TagBar } from './ui/tag-bar';
import type { CategoryTabItem } from './ui/category-tabs';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type {
  MarketCategory,
  MarketQuery,
  MarketSkill,
  MarketSkillsResponse,
  MarketSource,
} from '../types';

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;
const ALL_CATEGORY = 'all';

interface SkillsMarketTabProps {
  onSkillsChange?: () => void;
  installedSlugs?: string[];
}

// ── Deterministic emoji fallback per skill (stable across renders) ───────────
const MARKET_EMOJIS = ['🔧', '📊', '📝', '💬', '🎨', '🔍', '📅', '🧪', '🚀', '🛡️', '⚙️', '📦', '🌐', '🤖', '📁', '🔐'];

function marketEmoji(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return MARKET_EMOJIS[h % MARKET_EMOJIS.length] ?? '🔧';
}

// ── Source labels ────────────────────────────────────────────────────────────
const SOURCE_LABELS: Record<string, string> = {
  skillhub: 'skills.market_source_skillhub',
  clawhub: 'skills.market_source_clawhub',
};

function sourceLabel(id: string): string {
  return t(SOURCE_LABELS[id] ?? id);
}

/** Stable identity for a market row. ClawHub slugs collide across owners, so
 *  `slug` alone is unsafe as a React key or dedupe key. */
function skillUid(skill: MarketSkill): string {
  return skill.uid || `${skill.source}:${skill.owner ?? ''}/${skill.slug}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Feed state machine
//
// Every request is stamped with a monotonic id. A response is applied only when
// its id still matches the newest request, so a slow reply from an abandoned
// query (a stale keystroke, a source the user switched away from, a page that
// was superseded) can never merge into the current list. That single rule
// replaces the tangle of `page` / `hasMore` / `isSearchMode` / `isLoadingRef` /
// `pageRef` / `hotSkillsRef` flags that used to race against each other.
// ─────────────────────────────────────────────────────────────────────────────

type FeedPhase = 'loading' | 'appending' | 'ready' | 'error';

interface FeedState {
  requestId: number;
  items: MarketSkill[];
  /** uids already rendered — guards against upstream ordering drift. */
  seen: Set<string>;
  /** Rows received so far; this is the next `offset`, not `items.length`
   *  (which shrinks whenever a duplicate is dropped). */
  fetched: number;
  cursor: string | null;
  hasMore: boolean;
  total: number | null;
  phase: FeedPhase;
  error: string | null;
}

type FeedAction =
  | { type: 'load'; requestId: number }
  | { type: 'append'; requestId: number }
  | { type: 'resolved'; requestId: number; append: boolean; page: MarketSkillsResponse }
  | { type: 'failed'; requestId: number; error: string };

const INITIAL_FEED: FeedState = {
  requestId: 0,
  items: [],
  seen: new Set<string>(),
  fetched: 0,
  cursor: null,
  hasMore: false,
  total: null,
  phase: 'loading',
  error: null,
};

function feedReducer(state: FeedState, action: FeedAction): FeedState {
  switch (action.type) {
    case 'load':
      return { ...INITIAL_FEED, seen: new Set<string>(), requestId: action.requestId, phase: 'loading' };

    case 'append':
      return { ...state, requestId: action.requestId, phase: 'appending', error: null };

    case 'resolved': {
      if (action.requestId !== state.requestId) return state; // stale response
      const incoming = action.page.skills ?? [];
      const seen = new Set(action.append ? state.seen : []);
      const items = action.append ? [...state.items] : [];
      for (const skill of incoming) {
        const uid = skillUid(skill);
        if (seen.has(uid)) continue;
        seen.add(uid);
        items.push(skill);
      }
      return {
        requestId: state.requestId,
        items,
        seen,
        fetched: (action.append ? state.fetched : 0) + incoming.length,
        cursor: action.page.next_cursor ?? null,
        // Trust the server. Inferring from page length made the button immortal.
        hasMore:
          typeof action.page.has_more === 'boolean'
            ? action.page.has_more
            : incoming.length >= PAGE_SIZE,
        total: action.page.total ?? null,
        phase: 'ready',
        error: null,
      };
    }

    case 'failed':
      if (action.requestId !== state.requestId) return state;
      return { ...state, phase: 'error', error: action.error };

    default:
      return state;
  }
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** Remote icon with an emoji fallback when the URL is missing or broken. */
function MarketIcon({ skill }: { skill: MarketSkill }) {
  const [broken, setBroken] = useState(false);
  const url = skill.icon_url;
  if (!url || broken) {
    return <span className="skill-emoji">{marketEmoji(skillUid(skill))}</span>;
  }
  return (
    <img
      className="skill-market-icon"
      src={url}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

export function SkillsMarketTab({ onSkillsChange, installedSlugs = [] }: SkillsMarketTabProps) {
  const [sources, setSources] = useState<MarketSource[]>([]);
  const [activeSource, setActiveSource] = useState('skillhub');
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, SEARCH_DEBOUNCE_MS);
  const [activeCategory, setActiveCategory] = useState(ALL_CATEGORY);
  const [categories, setCategories] = useState<MarketCategory[]>([]);
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installMessage, setInstallMessage] = useState<{ text: string; type: 'ok' | 'error' } | null>(null);

  const [feed, dispatch] = useReducer(feedReducer, INITIAL_FEED);

  // Latest feed snapshot for callbacks that must not re-bind on every render.
  const feedRef = useRef(feed);
  feedRef.current = feed;

  const requestSeq = useRef(0);

  // Source dropdown
  const [showSourceDropdown, setShowSourceDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowSourceDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // ── Sources (once) ─────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    chatService
      .listMarketSources()
      .then((response) => {
        if (cancelled) return;
        const list = response.sources ?? [];
        setSources(list);
        // Only correct the selection when the current one is not offered,
        // otherwise this would re-trigger the whole feed on mount.
        setActiveSource((prev) => (list.some((s) => s.id === prev) ? prev : list[0]?.id ?? prev));
      })
      .catch(() => {
        /* silently fall back to the default source */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Categories follow the source; sources without a vocabulary hide the bar ─
  useEffect(() => {
    let cancelled = false;
    setActiveCategory(ALL_CATEGORY);
    setCategories([]);
    chatService
      .listMarketCategories(activeSource)
      .then((response) => {
        if (!cancelled) setCategories(response.categories ?? []);
      })
      .catch(() => {
        if (!cancelled) setCategories([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSource]);

  // ── The single fetch path ──────────────────────────────────────────────────
  const runQuery = useCallback(
    async (append: boolean) => {
      const requestId = requestSeq.current + 1;
      requestSeq.current = requestId;
      dispatch(append ? { type: 'append', requestId } : { type: 'load', requestId });

      const query: MarketQuery = { source: activeSource, limit: PAGE_SIZE };
      if (activeCategory !== ALL_CATEGORY) query.category = activeCategory;
      if (append) {
        // Cursor sources (ClawHub) must continue from the opaque token;
        // offset is only the fallback for sources that support it.
        if (feedRef.current.cursor) query.cursor = feedRef.current.cursor;
        else query.offset = feedRef.current.fetched;
      }

      const term = debouncedSearch.trim();
      try {
        const page = term
          ? await chatService.searchMarketSkills({ ...query, q: term })
          : await chatService.listHotSkills(query);
        dispatch({ type: 'resolved', requestId, append, page });
      } catch (error) {
        dispatch({ type: 'failed', requestId, error: translateError(error) });
      }
    },
    [activeSource, activeCategory, debouncedSearch],
  );

  // One effect owns the feed. Its identity changes only when the query itself
  // changes, so switching source / category / search term fires exactly one
  // request — no duplicated initial load, no cross-mode leakage.
  useEffect(() => {
    void runQuery(false);
  }, [runQuery]);

  const loadMore = useCallback(() => {
    const current = feedRef.current;
    if (current.phase === 'loading' || current.phase === 'appending') return;
    if (!current.hasMore) return;
    void runQuery(true);
  }, [runQuery]);

  const refresh = useCallback(() => {
    void runQuery(false);
  }, [runQuery]);

  // ── Install ────────────────────────────────────────────────────────────────
  const handleInstall = useCallback(
    async (skill: MarketSkill) => {
      if (installedSlugs.includes(skill.slug)) return;
      const uid = skillUid(skill);
      setInstalling((prev) => new Set(prev).add(uid));
      try {
        // `owner` disambiguates colliding slugs; without it ClawHub answers 409.
        const response = await chatService.installMarketSkill(skill.source, skill.slug, skill.owner ?? null);
        if (response.status === 'ok') {
          setInstallMessage({ text: response.message || t('skills.market_installed'), type: 'ok' });
          onSkillsChange?.();
        } else {
          setInstallMessage({ text: response.message || t('skills.market_install_failed'), type: 'error' });
        }
      } catch (error) {
        setInstallMessage({ text: translateError(error) || t('skills.market_install_failed'), type: 'error' });
      } finally {
        setInstalling((prev) => {
          const next = new Set(prev);
          next.delete(uid);
          return next;
        });
      }
    },
    [installedSlugs, onSkillsChange],
  );

  const isInstalled = useCallback((slug: string) => installedSlugs.includes(slug), [installedSlugs]);

  // ── Derived view state ─────────────────────────────────────────────────────
  // Category filtering is executed upstream, so the list is rendered verbatim.
  const skills = feed.items;
  const initialLoading = feed.phase === 'loading';
  const appending = feed.phase === 'appending';

  const categoryTabs = useMemo<CategoryTabItem[]>(() => {
    if (categories.length === 0) return [];
    return [
      { id: ALL_CATEGORY, label: t('skills.cat_all') },
      ...categories.map((cat) => ({ id: cat.key, label: cat.name })),
    ];
  }, [categories]);

  const sourceItems = useMemo(() => sources.map((s) => ({ id: s.id, label: sourceLabel(s.id) })), [sources]);

  const summary = useMemo(() => {
    if (skills.length === 0) return null;
    if (typeof feed.total === 'number') {
      return t('skills.market_showing', { shown: skills.length, total: feed.total });
    }
    return t('skills.market_count', { count: skills.length });
  }, [skills.length, feed.total]);

  return (
    <div className="skills-market-tab">
      {installMessage && (
        <div className={`skill-message ${installMessage.type === 'error' ? 'skill-message--error' : ''}`}>
          {installMessage.text}
        </div>
      )}

      <TagBar
        tagSlot={
          <div className="skill-market-toolbar">
            <div className="source-dropdown" ref={dropdownRef} style={{ position: 'relative' }}>
              <Button
                variant="secondary"
                onClick={() => setShowSourceDropdown(!showSourceDropdown)}
                className="source-dropdown__btn"
              >
                {sourceLabel(activeSource)}
                <ChevronDown size={14} />
              </Button>
              {showSourceDropdown && (
                <div className="source-dropdown__menu">
                  {sourceItems.map((source) => (
                    <button
                      key={source.id}
                      className={`source-dropdown__item ${activeSource === source.id ? 'active' : ''}`}
                      onClick={() => {
                        setActiveSource(source.id);
                        setShowSourceDropdown(false);
                        setSearch('');
                      }}
                    >
                      {source.label}
                      {activeSource === source.id && <Check size={14} />}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        }
        categories={categoryTabs}
        category={activeCategory}
        onCategoryChange={setActiveCategory}
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder={t('skills.market_search')}
        refreshLoading={initialLoading}
        onRefresh={refresh}
        refreshAriaLabel={t('skills.refresh')}
        className="skill-market-tagbar"
      />

      {initialLoading ? (
        <div className="skill-market-loading-overlay">
          <Loader2 size={32} className="animate-spin" />
          <p style={{ marginTop: '16px', color: '#666' }}>{t('skills.loading')}</p>
        </div>
      ) : skills.length === 0 ? (
        <div className="skill-empty">
          <p>
            {feed.phase === 'error'
              ? feed.error || t('skills.market_load_failed')
              : search
                ? t('skills.no_match')
                : t('skills.empty')}
          </p>
          {feed.phase === 'error' && (
            <Button variant="secondary" onClick={refresh}>
              {t('skills.market_retry')}
            </Button>
          )}
        </div>
      ) : (
        <>
          {summary && <div className="skill-market-summary">{summary}</div>}

          <div className="skills-grid">
            {skills.map((skill) => {
              const uid = skillUid(skill);
              const installed = isInstalled(skill.slug);
              const busy = installing.has(uid);
              return (
                <GridCard
                  key={uid}
                  icon={<MarketIcon skill={skill} />}
                  title={skill.name}
                  subtitle={sourceLabel(skill.source)}
                  description={skill.description}
                  added={installed}
                  disabled={installed || busy}
                  trailing={
                    installed ? (
                      <Check size={14} />
                    ) : busy ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Button
                        variant="primary"
                        size="icon-xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleInstall(skill);
                        }}
                        aria-label={t('skills.market_install')}
                        title={t('skills.market_install')}
                      >
                        <Plus size={14} />
                      </Button>
                    )
                  }
                  onClick={
                    installed
                      ? undefined
                      : () => {
                          handleInstall(skill);
                        }
                  }
                />
              );
            })}
          </div>

          <div className="skill-market-footer">
            {feed.phase === 'error' ? (
              <div className="skill-market-footer__error">
                <span>{feed.error || t('skills.market_load_failed')}</span>
                <Button variant="secondary" onClick={loadMore}>
                  {t('skills.market_retry')}
                </Button>
              </div>
            ) : feed.hasMore ? (
              <Button variant="secondary" onClick={loadMore} disabled={appending}>
                {appending ? (
                  <>
                    <Loader2 size={14} className="animate-spin" />
                    {t('skills.loading_more')}
                  </>
                ) : (
                  t('skills.load_more')
                )}
              </Button>
            ) : (
              <span className="skill-market-footer__end">{t('skills.market_no_more')}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
