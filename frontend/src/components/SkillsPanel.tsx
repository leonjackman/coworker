import { Check, ExternalLink, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from './ui/button';
import { Switch } from './ui/switch';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import { CategoryTabs, type CategoryTabItem } from './ui/category-tabs';
import { SearchInput } from './ui/search-input';
import { GridCard } from './ui/grid-card';
import { DetailModal } from './ui/detail-modal';
import { SideDrawer } from './ui/side-drawer';
import type { SkillDiagnostic, SkillEntry } from '../types';

interface SkillsPanelProps {
  skills: SkillEntry[];
  diagnostics: SkillDiagnostic[];
  setSkills: React.Dispatch<React.SetStateAction<SkillEntry[]>>;
  setDiagnostics?: React.Dispatch<React.SetStateAction<SkillDiagnostic[]>>;
  onSkillsChange?: () => void;
}

// ── Deterministic emoji per skill name (stable across renders) ───────────────
const SKILL_EMOJIS = ['🔧', '📊', '📝', '💬', '🎨', '🔍', '📅', '🧪', '🚀', '🛡️', '⚙️', '📦', '🌐', '🤖', '📁', '🔐'];

function skillEmoji(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return SKILL_EMOJIS[h % SKILL_EMOJIS.length] ?? '🔧';
}

const SOURCE_LABELS: Record<string, string> = {
  user: 'skills.source.user',
  project: 'skills.source.project',
  'coworker-user': 'skills.source.coworker-user',
  'coworker-project': 'skills.source.coworker-project',
  validate: 'skills.source.validate',
};

function sourceLabel(source: string): string {
  return t(SOURCE_LABELS[source] ?? source);
}

export function SkillsPanel({ skills, diagnostics, setSkills, setDiagnostics, onSkillsChange }: SkillsPanelProps) {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<'ok' | 'error'>('ok');

  // Detail modal
  const [detail, setDetail] = useState<SkillEntry | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // View mode: 'list' | 'add' (add skill secondary page)
  type ViewMode = 'list' | 'add';
  const [viewMode, setViewMode] = useState<ViewMode>('list');

  // Installed drawer
  const [installedDrawerOpen, setInstalledDrawerOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await chatService.listSkills();
      setSkills(response.skills);
      setDiagnostics?.(response.diagnostics);
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error) || t('skills.failed_to_load'));
    } finally {
      setLoading(false);
    }
  }, [setSkills, setDiagnostics]);

  const rescan = useCallback(async () => {
    setLoading(true);
    try {
      const response = await chatService.scanSkills();
      setSkills(response.skills);
      setDiagnostics?.(response.diagnostics);
      setMessageType('ok');
      setMessage(t('skills.updated'));
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error) || t('skills.failed_to_load'));
    } finally {
      setLoading(false);
    }
  }, [setSkills, setDiagnostics]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const installedCount = useMemo(() => skills.filter((s) => s.enabled).length, [skills]);

  const categories = useMemo<CategoryTabItem[]>(() => {
    const sources = ['user', 'project', 'coworker-user', 'coworker-project'];
    const items: CategoryTabItem[] = [{ id: 'all', label: t('skills.cat_all'), count: skills.length }];
    for (const src of sources) {
      const count = skills.filter((s) => s.source === src).length;
      if (count > 0) items.push({ id: src, label: sourceLabel(src), count });
    }
    return items;
  }, [skills]);

  const visibleSkills = useMemo(() => {
    let list = skills;
    if (category !== 'all') list = list.filter((s) => s.source === category);
    const needle = search.trim().toLowerCase();
    if (needle) {
      list = list.filter(
        (s) => s.name.toLowerCase().includes(needle) || s.description.toLowerCase().includes(needle),
      );
    }
    return list;
  }, [skills, category, search]);

  const installedSkills = useMemo(() => skills.filter((s) => s.enabled), [skills]);

  const handleToggle = useCallback(
    async (skill: SkillEntry) => {
      try {
        const response = await chatService.updateSkill(skill.name, { enabled: !skill.enabled });
        const updated = response.skill;
        setSkills((current) => current.map((item) => (item.name === skill.name ? updated : item)));
        onSkillsChange?.();
      } catch (error) {
        setMessageType('error');
        setMessage(translateError(error) || t('skills.failed_to_load'));
      }
    },
    [setSkills, onSkillsChange],
  );

  const openDetail = useCallback(async (skill: SkillEntry) => {
    setDetail(skill);
    setDetailLoading(true);
    try {
      const response = await chatService.getSkill(skill.name);
      setDetail(response.skill);
    } catch {
      /* keep the list-level entry if detail fetch fails */
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const closeDetail = useCallback(() => setDetail(null), []);

  return (
    <WorkspacePage
      eyebrow={t('settings.eyebrow')}
      title={t('skills.title')}
      description={t('skills.subtitle')}
      action={
        <div className="skills-header__actions">
        {viewMode === 'list' ? (
          <>
            <button
              type="button"
              className="installed-badge"
              onClick={() => setInstalledDrawerOpen(true)}
              aria-label={t('skills.installed_aria')}
            >
              <span>{t('skills.installed')}</span>
              <span className="installed-badge__count">{installedCount}</span>
              <span className={`installed-badge__status ${installedCount > 0 ? 'is-on' : ''}`} />
            </button>
            <Button variant="primary" onClick={() => setViewMode('add')} disabled={loading}>
              <Plus size={14} />
              {t('skills.add_skill')}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={() => setViewMode('list')}>
            {t('mcp.back')}
          </Button>
        )}
        </div>
      }
    >
      <div className="workspace-page__content">
        {message && (
          <div className={`skill-message ${messageType === 'error' ? 'skill-message--error' : ''}`}>{message}</div>
        )}

        {/* ── List view ── */}
        {viewMode === 'list' && (
          <>
            {diagnostics.length > 0 && (
              <div className="skill-diagnostics">
                <div className="skill-diagnostics__title">
                  ⚠ {t('skills.diagnostics')} ({diagnostics.length})
                </div>
                {diagnostics.map((diagnostic, index) => (
                  <div key={index} className={`skill-diagnostics__item skill-diagnostics__item--${diagnostic.type}`}>
                    <strong>{diagnostic.type}</strong> {diagnostic.name}
                    <span className="skill-diagnostics__message">— {diagnostic.message}</span>
                  </div>
                ))}
              </div>
            )}

            {/* ── Market section ── */}

            <div className="skill-toolbar">
              <CategoryTabs categories={categories} value={category} onChange={setCategory} className="skill-toolbar__cats" />
              <div className="skill-toolbar__right">
                <SearchInput
                  value={search}
                  onChange={setSearch}
                  placeholder={t('skills.search_placeholder')}
                  className="skill-toolbar__search"
                />
                <Button variant="ghost" size="icon" onClick={() => void rescan()} disabled={loading} aria-label={t('skills.refresh')}>
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                </Button>
              </div>
            </div>

            {/* ── Market grid ── */}
            {visibleSkills.length === 0 ? (
              <div className="skill-empty">
                <p>{skills.length === 0 ? t('skills.empty') : t('skills.no_match')}</p>
              </div>
            ) : (
              <div className="skills-grid">
                {visibleSkills.map((skill) => (
                  <GridCard
                    key={skill.name}
                    icon={<span className="skill-emoji">{skillEmoji(skill.name)}</span>}
                    title={skill.name}
                    subtitle={`v${skill.version || '1.0.0'} · ${sourceLabel(skill.source)}`}
                    description={skill.description}
                    added={skill.enabled}
                    onClick={() => void openDetail(skill)}
                    trailing={
                      <Button
                        variant={skill.enabled ? 'secondary' : 'primary'}
                        size="icon-xs"
                        onClick={() => void handleToggle(skill)}
                        aria-label={skill.enabled ? t('skills.disable') : t('skills.enable')}
                        title={skill.enabled ? t('skills.added') : t('skills.add')}
                      >
                        {skill.enabled ? <Check size={14} /> : <Plus size={14} />}
                      </Button>
                    }
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Add skill view (secondary page) ── */}
        {viewMode === 'add' && (
          <div className="add-skill-page">
            <div className="add-skill-page__custom">
              <div className="add-skill-page__grid">
                <GridCard
                  icon={<span className="skill-custom__icon">📄</span>}
                  title={t('skills.custom_create')}
                  description={t('skills.custom_create_desc')}
                  onClick={() => {/* TODO: open create-skill flow */}}
                />
                <GridCard
                  icon={<span className="skill-custom__icon">📁</span>}
                  title={t('skills.custom_import')}
                  description={t('skills.custom_import_desc')}
                  onClick={() => {/* TODO: open import-skill (file picker) flow */}}
                />
              </div>
            </div>
          </div>
        )}

        {/* ── Detail modal ── */}
        <DetailModal
          open={detail !== null}
          onClose={closeDetail}
          icon={detail ? <span className="skill-emoji skill-emoji--lg">{skillEmoji(detail.name)}</span> : undefined}
          title={detail?.name}
          subtitle={
            detail ? `v${detail.version || '1.0.0'} · ${sourceLabel(detail.source)}` : undefined
          }
          footer={
            detail && (
              <>
                <Button variant="secondary" onClick={() => void rescan()}>
                  {t('skills.update')}
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (!detail.enabled) {
                      void closeDetail();
                      return;
                    }
                    void handleToggle(detail).then(() => closeDetail());
                  }}
                >
                  <Trash2 size={14} />
                  {t('skills.remove')}
                </Button>
              </>
            )
          }
        >
          {detailLoading ? (
            <div className="skill-detail-loading">
              <Loader2 size={14} className="animate-spin" />
            </div>
          ) : detail ? (
            <div className="skill-detail">
              <div className="skill-detail__section">
                <div className="skill-detail__label">{t('skills.description')}</div>
                <div className="skill-detail__value">{detail.description}</div>
              </div>
              <div className="skill-detail__grid">
                <div className="skill-detail__row">
                  <span className="skill-detail__label">{t('skills.version')}</span>
                  <span className="skill-detail__value">{detail.version || '-'}</span>
                </div>
                <div className="skill-detail__row">
                  <span className="skill-detail__label">{t('skills.source_label')}</span>
                  <span className="skill-detail__value">{sourceLabel(detail.source)}</span>
                </div>
                <div className="skill-detail__row">
                  <span className="skill-detail__label">{t('skills.auto_invoke')}</span>
                  <span className="skill-detail__value">
                    {detail.disable_model_invocation ? t('skills.disabled_status') : t('skills.enabled_status')}
                  </span>
                </div>
              </div>
              <div className="skill-detail__section">
                <div className="skill-detail__label">{t('skills.location_label')}</div>
                <code className="skill-detail__code">{detail.file_path}</code>
              </div>
              {detail.body && (
                <div className="skill-detail__section">
                  <div className="skill-detail__label">{t('skills.body')}</div>
                  <pre className="skill-detail__pre">{detail.body.slice(0, 1200)}{detail.body.length > 1200 ? '\n…' : ''}</pre>
                </div>
              )}
            </div>
          ) : null}
        </DetailModal>

        {/* ── Installed drawer ── */}
        <SideDrawer
          open={installedDrawerOpen}
          onClose={() => setInstalledDrawerOpen(false)}
          title={
            <span>{t('skills.installed_title')} <span className="side-drawer__count">{installedSkills.length}</span></span>
          }
        >
          {installedSkills.length === 0 ? (
            <div className="skill-empty">
              <p>{t('skills.installed_empty')}</p>
            </div>
          ) : (
            <div className="installed-list">
              {installedSkills.map((skill) => (
                <div className="installed-row" key={skill.name}>
                  <div className="installed-row__icon">{skillEmoji(skill.name)}</div>
                  <div className="installed-row__meta">
                    <div className="installed-row__name">{skill.name}</div>
                    <div className="installed-row__sub">
                      v{skill.version || '1.0.0'} · {sourceLabel(skill.source)}
                    </div>
                  </div>
                  <Switch
                    id={`installed-switch-${skill.name}`}
                    checked={skill.enabled}
                    onChange={() => void handleToggle(skill)}
                    aria-label={t('skills.toggle')}
                  />
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => void handleToggle(skill)}
                    aria-label={t('skills.remove')}
                    title={t('skills.remove')}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              ))}
            </div>
          )}
          {installedSkills.length > 0 && (
            <div className="side-drawer__footer-note">
              <ExternalLink size={12} /> {t('skills.open_file_hint')}
            </div>
          )}
        </SideDrawer>
      </div>
    </WorkspacePage>
  );
}
