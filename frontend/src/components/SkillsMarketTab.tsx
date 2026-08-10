import { Check, ChevronDown, Loader2, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from './ui/button';
import { GridCard } from './ui/grid-card';
import { SearchInput } from './ui/search-input';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { TagBar } from './ui/tag-bar';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import type { MarketSkill, MarketSource } from '../types';

const PAGE_SIZE = 20;

interface SkillsMarketTabProps {
  onSkillsChange?: () => void;
  installedSlugs?: string[];
}

// ── Deterministic emoji per skill name (stable across renders) ───────────────
const MARKET_EMOJIS = ['🔧', '📊', '📝', '💬', '🎨', '🔍', '📅', '🧪', '🚀', '🛡️', '⚙️', '📦', '🌐', '🤖', '📁', '🔐'];

function marketEmoji(slug: string): string {
  let h = 0;
  for (let i = 0; i < slug.length; i += 1) h = (h * 31 + slug.charCodeAt(i)) >>> 0;
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

// ── Category definitions ─────────────────────────────────────────────────────
const CATEGORY_ITEMS: CategoryTabItem[] = [
  { id: 'all', label: '全部', count: 0 },
  { id: 'office', label: '办公', count: 0 },
  { id: 'development', label: '开发', count: 0 },
  { id: 'finance', label: '理财', count: 0 },
  { id: 'efficiency', label: '效率', count: 0 },
  { id: 'daily', label: '日常', count: 0 },
  { id: 'creative', label: '创作', count: 0 },
];

export function SkillsMarketTab({ onSkillsChange, installedSlugs = [] }: SkillsMarketTabProps) {
  const [sources, setSources] = useState<MarketSource[]>([]);
  const [activeSource, setActiveSource] = useState('skillhub');
  const [search, setSearch] = useState('');
  const [hotSkills, setHotSkills] = useState<MarketSkill[]>([]);
  const [searchResults, setSearchResults] = useState<MarketSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installMessage, setInstallMessage] = useState<{ text: string; type: 'ok' | 'error' } | null>(null);

  // Pagination state
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [isSearchMode, setIsSearchMode] = useState(false);
  
  // Refs to prevent stale closure issues and concurrent requests
  const isLoadingRef = useRef(false);
  const hotSkillsRef = useRef<MarketSkill[]>([]);
  const pageRef = useRef(1);
  
  // Keep refs in sync
  useEffect(() => {
    hotSkillsRef.current = hotSkills;
  }, [hotSkills]);
  
  useEffect(() => {
    pageRef.current = page;
  }, [page]);

  // Category state
  const [activeCategory, setActiveCategory] = useState('all');
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});

  // Source dropdown
  const [showSourceDropdown, setShowSourceDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowSourceDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Load sources on mount
  useEffect(() => {
    void loadSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload when source changes
  useEffect(() => {
    if (activeSource) {
      // Direct API call to avoid dependency on loadHot
      const fetchHotSkills = async () => {
        if (isLoadingRef.current) return;
        isLoadingRef.current = true;
        setLoading(true);
        setHotSkills([]);
        setPage(1);
        try {
          const response = await chatService.listHotSkills(activeSource, PAGE_SIZE, 0);
          setHotSkills(response.skills || []);
          setHasMore((response.skills || []).length >= PAGE_SIZE);
          
          // Calculate category counts
          const counts: Record<string, number> = {};
          (response.skills || []).forEach((skill) => {
            const cat = (skill as any).category || 'all';
            counts[cat] = (counts[cat] || 0) + 1;
          });
          setCategoryCounts(counts);
        } catch {
          setHotSkills([]);
          setCategoryCounts({});
        } finally {
          setLoading(false);
          isLoadingRef.current = false;
        }
      };
      void fetchHotSkills();
    }
  }, [activeSource]);

  const loadSources = useCallback(async () => {
    try {
      const response = await chatService.listMarketSources();
      if (response.sources.length > 0) {
        setSources(response.sources);
        const firstSource = response.sources[0];
        if (firstSource && firstSource.id !== activeSource) {
          setActiveSource(firstSource.id);
        }
      }
    } catch {
      /* silently fail - show empty state */
    }
  }, [activeSource]);

  const loadHot = useCallback(async (resetPage = true) => {
    // Prevent concurrent requests
    if (isLoadingRef.current) return;
    isLoadingRef.current = true;
    
    if (resetPage) {
      setLoading(true);
      setHotSkills([]);
      setPage(1);
    } else {
      setLoadingMore(true);
    }
    
    try {
      // Use ref to get current page without adding to dependency array
      const currentPage = resetPage ? 1 : pageRef.current;
      const offset = (currentPage - 1) * PAGE_SIZE;
      
      const response = await chatService.listHotSkills(activeSource, PAGE_SIZE, offset);
      const newSkills = response.skills || [];
      
      if (resetPage) {
        setHotSkills(newSkills);
      } else {
        setHotSkills((prev) => [...prev, ...newSkills]);
      }
      
      // Check if there are more skills
      setHasMore(newSkills.length >= PAGE_SIZE);
      
      // Calculate category counts using ref to avoid stale closure
      const counts: Record<string, number> = {};
      const allSkills = resetPage ? newSkills : [...hotSkillsRef.current, ...newSkills];
      allSkills.forEach((skill) => {
        const cat = (skill as any).category || 'all';
        counts[cat] = (counts[cat] || 0) + 1;
      });
      setCategoryCounts(counts);
    } catch {
      if (resetPage) {
        setHotSkills([]);
        setCategoryCounts({});
      }
    } finally {
      setLoading(false);
      setLoadingMore(false);
      isLoadingRef.current = false;
    }
  }, [activeSource]);

  const loadSearchResults = useCallback(async (resetPage = true) => {
    if (isLoadingRef.current) return;
    isLoadingRef.current = true;
    
    if (resetPage) {
      setLoading(true);
      setSearchResults([]);
      setPage(1);
    } else {
      setLoadingMore(true);
    }
    
    try {
      const currentPage = resetPage ? 1 : pageRef.current;
      const offset = (currentPage - 1) * PAGE_SIZE;
      
      const response = await chatService.searchMarketSkills(activeSource, search.trim());
      const newSkills = response.skills || [];
      
      if (resetPage) {
        setSearchResults(newSkills);
      } else {
        setSearchResults((prev) => [...prev, ...newSkills]);
      }
      
      // 搜索结果超过20条才显示加载更多
      setHasMore(newSkills.length >= PAGE_SIZE);
    } catch {
      if (resetPage) {
        setSearchResults([]);
      }
    } finally {
      setLoading(false);
      setLoadingMore(false);
      isLoadingRef.current = false;
    }
  }, [activeSource, search]);

  const loadMore = useCallback(() => {
    if (loadingMore || loading || !hasMore) return;
    setPage((prev) => prev + 1);
  }, [loadingMore, loading, hasMore]);

  // Trigger loadHot when page changes (for "Load More" button)
  useEffect(() => {
    if (page > 1) {
      if (search.trim()) {
        // 搜索状态：加载更多搜索结果
        loadSearchResults(false);
      } else {
        // 初始列表：加载更多
        loadHot(false);
      }
    }
  }, [page, loadHot, loadSearchResults, search]);

  // Search with debounce - reset pagination on search
  useEffect(() => {
    const timer = setTimeout(async () => {
      if (!search.trim()) {
        setSearchResults([]);
        setIsSearchMode(false);
        setPage(1);
        // Directly call the API instead of loadHot to avoid dependency cycle
        try {
          const response = await chatService.listHotSkills(activeSource, PAGE_SIZE, 0);
          setHotSkills(response.skills || []);
          setHasMore((response.skills || []).length >= PAGE_SIZE);
        } catch {
          setHotSkills([]);
        }
        return;
      }
      setIsSearchMode(true);
      try {
        const response = await chatService.searchMarketSkills(activeSource, search.trim());
        setSearchResults(response.skills || []);
        // 搜索返回结果超过20条才显示加载更多
        setHasMore((response.skills || []).length >= PAGE_SIZE);
      } catch {
        setSearchResults([]);
        setHasMore(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, activeSource]);

  const displayedSkills = useMemo(() => {
    let skills = search.trim() ? searchResults : hotSkills;
    
    // Filter by category
    if (activeCategory !== 'all') {
      skills = skills.filter((skill) => (skill as any).category === activeCategory);
    }
    
    return skills;
  }, [search, searchResults, hotSkills, activeCategory]);

  const handleInstall = useCallback(async (skill: MarketSkill) => {
    if (installedSlugs.includes(skill.slug)) return;
    setInstalling((prev) => new Set(prev).add(skill.slug));
    try {
      const response = await chatService.installMarketSkill(skill.source, skill.slug);
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
        next.delete(skill.slug);
        return next;
      });
    }
  }, [installedSlugs, onSkillsChange]);

  const isInstalled = useCallback((slug: string) => installedSlugs.includes(slug), [installedSlugs]);

  // Build category tabs with counts
  const categoryTabs = useMemo<CategoryTabItem[]>(() => {
    return CATEGORY_ITEMS.map((cat) => ({
      id: cat.id,
      label: cat.label,
      count: cat.id === 'all' ? displayedSkills.length : (categoryCounts[cat.id] || 0),
    }));
  }, [displayedSkills, categoryCounts]);

  // Source dropdown items
  const sourceItems = useMemo<CategoryTabItem[]>(() => {
    return sources.map((s) => ({ id: s.id, label: sourceLabel(s.id), count: 0 }));
  }, [sources]);

  return (
    <div className="skills-market-tab">
      {installMessage && (
        <div className={`skill-message ${installMessage.type === 'error' ? 'skill-message--error' : ''}`}>
          {installMessage.text}
        </div>
      )}

      {/* TagBar with custom content */}
      <TagBar
        tagSlot={
          <div className="skill-market-toolbar">
            {/* Source dropdown */}
            <div className="source-dropdown" ref={dropdownRef} style={{ position: 'relative' }}>
              <Button variant="secondary"onClick={() => setShowSourceDropdown(!showSourceDropdown)}className="source-dropdown__btn">
                {sourceLabel(activeSource)}
                <ChevronDown size={14} />
              </Button>
              {showSourceDropdown && (
                <div className="source-dropdown__menu">
                  {sourceItems.map((source) => (
                    <button key={source.id}className={`source-dropdown__item ${activeSource === source.id ? 'active' : ''}`}onClick={() => {setActiveSource(source.id);setShowSourceDropdown(false);setSearch('');setPage(1);}}>
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
        refreshLoading={loading}
        onRefresh={loadHot}
        refreshAriaLabel={t('skills.refresh')}
        className="skill-market-tagbar"
      />

      {/* Skills grid */}
      {loading ? (
        <div className="skill-market-loading-overlay">
          <Loader2 size={32} className="animate-spin" />
          <p style={{ marginTop: '16px', color: '#666' }}>{t('skills.loading')}</p>
        </div>
      ) : displayedSkills.length === 0 ? (
        <div className="skill-empty">
          <p>{search ? t('skills.no_match') : t('skills.empty')}</p>
        </div>
      ) : (
        <>
          <div className="skills-grid">
            {displayedSkills.map((skill) => {
              const installed = isInstalled(skill.slug);
              const isInstalling = installing.has(skill.slug);
              return (
                <GridCard
                  key={skill.slug}
                  icon={<span className="skill-emoji">{marketEmoji(skill.slug)}</span>}
                  title={skill.name}
                  subtitle={sourceLabel(skill.source)}
                  description={skill.description}
                  added={installed}
                  disabled={installed || isInstalling}
                  trailing={
                    installed ? (
                      <Check size={14} />
                    ) : isInstalling ? (
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
                  onClick={installed ? undefined : (() => { handleInstall(skill); })}
                />
              );
            })}
          </div>
          
          {/* Load more button */}
          {hasMore && (
            <div style={{ textAlign: 'center', padding: '24px 0' }}>
              <Button variant="secondary" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? (
                  <>
                    <Loader2 size={14} className="animate-spin" />
                    {t('skills.loading_more')}
                  </>
                ) : (
                  t('skills.load_more')
                )}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
