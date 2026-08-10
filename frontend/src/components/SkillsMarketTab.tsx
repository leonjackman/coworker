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
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installMessage, setInstallMessage] = useState<{ text: string; type: 'ok' | 'error' } | null>(null);

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
    void loadHot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const loadHot = useCallback(async () => {
    setLoading(true);
    try {
      const response = await chatService.listHotSkills(activeSource);
      setHotSkills(response.skills);
      // Calculate category counts
      const counts: Record<string, number> = {};
      response.skills.forEach((skill) => {
        const cat = (skill as any).category || 'all';
        counts[cat] = (counts[cat] || 0) + 1;
      });
      setCategoryCounts(counts);
    } catch {
      setHotSkills([]);
      setCategoryCounts({});
    } finally {
      setLoading(false);
    }
  }, [activeSource]);

  // Search with debounce
  useEffect(() => {
    const timer = setTimeout(async () => {
      if (!search.trim()) {
        setSearchResults([]);
        return;
      }
      try {
        const response = await chatService.searchMarketSkills(activeSource, search.trim());
        setSearchResults(response.skills);
      } catch {
        setSearchResults([]);
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
                    <button key={source.id}className={`source-dropdown__item ${activeSource === source.id ? 'active' : ''}`}onClick={() => {setActiveSource(source.id);setShowSourceDropdown(false);setSearch('');loadHot();}}>
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
      {loading && displayedSkills.length === 0 ? (
        <div className="skill-empty" style={{ textAlign: 'center', padding: '40px' }}>
          <Loader2 size={24} className="animate-spin" style={{ margin: '0 auto' }} />
        </div>
      ) : displayedSkills.length === 0 ? (
        <div className="skill-empty">
          <p>{search ? t('skills.no_match') : t('skills.empty')}</p>
        </div>
      ) : (
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
      )}
    </div>
  );
}
