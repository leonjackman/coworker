import { Check, Loader2, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { GridCard } from './ui/grid-card';
import { SearchInput } from './ui/search-input';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { SideDrawer } from './ui/side-drawer';
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

export function SkillsMarketTab({ onSkillsChange, installedSlugs = [] }: SkillsMarketTabProps) {
  const [sources, setSources] = useState<MarketSource[]>([]);
  const [activeSource, setActiveSource] = useState('skillhub');
  const [search, setSearch] = useState('');
  const [hotSkills, setHotSkills] = useState<MarketSkill[]>([]);
  const [searchResults, setSearchResults] = useState<MarketSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installMessage, setInstallMessage] = useState<{ text: string; type: 'ok' | 'error' } | null>(null);

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
    } catch {
      setHotSkills([]);
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
    return search.trim() ? searchResults : hotSkills;
  }, [search, searchResults, hotSkills]);

  const sourceTabs = useMemo<CategoryTabItem[]>(() => {
    return sources.map((s) => ({ id: s.id, label: sourceLabel(s.id), count: 0 }));
  }, [sources]);

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

  return (
    <div className="skills-market-tab">
      {installMessage && (
        <div className={`skill-message ${installMessage.type === 'error' ? 'skill-message--error' : ''}`}>
          {installMessage.text}
        </div>
      )}

      {/* Source tabs */}
      {sourceTabs.length > 0 && (
        <CategoryTabs
          categories={sourceTabs}
          value={activeSource}
          onChange={(id) => {
            setActiveSource(id);
            setSearch('');
            loadHot();
          }}
          className="skill-toolbar"
        />
      )}

      {/* Search bar */}
      <div className="skill-toolbar__right" style={{ marginBottom: '12px' }}>
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder={t('skills.market_search')}
          className="skill-toolbar__search"
        />
      </div>

      {/* Skills grid */}
      {loading ? (
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
