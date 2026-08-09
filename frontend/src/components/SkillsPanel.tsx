import { Loader2, RefreshCw, Search, Shield, ShieldCheck, ShieldX, FileText, AlertTriangle, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Switch } from './ui/switch';
import { t, translateError } from '../lib/i18n';
import { chatService } from '../services/chatService';
import { WorkspacePage } from './ui/workspace-page';
import type { SkillDiagnostic, SkillEntry } from '../types';

interface SkillsPanelProps {
  skills: SkillEntry[];
  diagnostics: SkillDiagnostic[];
  setSkills: React.Dispatch<React.SetStateAction<SkillEntry[]>>;
  setDiagnostics?: React.Dispatch<React.SetStateAction<SkillDiagnostic[]>>;
  onSkillsChange?: () => void;
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

function PermissionIcon({ permission }: { permission: string | null }) {
  if (permission === 'deny') return <ShieldX size={14} />;
  if (permission === 'ask') return <Shield size={14} />;
  return <ShieldCheck size={14} />;
}

// ── Form state for skill detail/edit ──────────────────────────────────────────

type FormState = {
  name: string;
  file_path: string;
  body: string;
};

// ── Component ────────────────────────────────────────────────────────────────

export function SkillsPanel({ skills, diagnostics, setSkills, setDiagnostics, onSkillsChange }: SkillsPanelProps) {
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [messageType, setMessageType] = useState<'ok' | 'error'>('ok');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillEntry | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [validatePath, setValidatePath] = useState('');
  const [validateResult, setValidateResult] = useState<{ valid: boolean; diagnostics: SkillDiagnostic[] } | null>(null);

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

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter(
      (skill) => skill.name.toLowerCase().includes(needle) || skill.description.toLowerCase().includes(needle),
    );
  }, [skills, search]);

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

  const handleExpand = useCallback(
    async (skill: SkillEntry) => {
      if (expanded === skill.name && detail) {
        setExpanded(null);
        setDetail(null);
        return;
      }
      setExpanded(skill.name);
      setDetailLoading(true);
      try {
        const response = await chatService.getSkill(skill.name);
        setDetail(response.skill);
      } catch (error) {
        setDetail(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [expanded, detail],
  );

  const handleValidate = useCallback(async () => {
    if (!validatePath.trim()) return;
    setValidateResult(null);
    try {
      const response = await chatService.validateSkill({ path: validatePath.trim() });
      setValidateResult({ valid: response.valid, diagnostics: response.diagnostics });
    } catch (error) {
      setMessageType('error');
      setMessage(translateError(error) || t('skills.failed_to_load'));
    }
  }, [validatePath]);

  const statusDot = (enabled: boolean): string => {
    return enabled ? 'mcp-status-connected' : 'mcp-status-disabled';
  };

  return (
    <WorkspacePage
      eyebrow={t('skills.title')}
      title={t('skills.title')}
      description={t('skills.subtitle')}
    >
      <div className="workspace-page__content">
        {/* ── Quick-add / usage card ── */}
        <div className="skill-quick-card">
          <FileText size={18} className="skill-form-card__icon" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong>{t('skills.slash_usage')}</strong>
            <p className="skill-quick-card-desc">{t('skills.subtitle')}</p>
          </div>
        </div>

        {/* ── List heading ── */}
        <div className="skill-list-heading">
          <h2>{t('skills.title')} ({skills.length})</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="skill-search">
              <Search size={14} className="skill-search__icon" />
              <input
                className="skill-search__input"
                placeholder={t('skills.search_placeholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Button variant="ghost" onClick={rescan} disabled={loading} className="skill-refresh-btn">
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              {t('skills.refresh')}
            </Button>
            <Button variant="primary" onClick={() => {/* TODO: add skill form */}}>
              <Plus size={14} />
              {t('providers.add_provider')}
            </Button>
          </div>
        </div>

        {/* ── Message ── */}
        {message && (
          <div className={`skill-message ${messageType === 'error' ? 'skill-message--error' : ''}`}>
            {message}
          </div>
        )}

        {/* ── Diagnostics ── */}
        {diagnostics.length > 0 && (
          <div className="skill-diagnostics">
            <div className="skill-diagnostics__title">
              <AlertTriangle size={14} /> {t('skills.diagnostics')} ({diagnostics.length})
            </div>
            {diagnostics.map((diagnostic, index) => (
              <div key={index} className={`skill-diagnostics__item skill-diagnostics__item--${diagnostic.type}`}>
                <strong>{diagnostic.type}</strong> {diagnostic.name}
                <span className="skill-diagnostics__message">— {diagnostic.message}</span>
              </div>
            ))}
          </div>
        )}

        {/* ── Skill list ── */}
        {skills.length === 0 ? (
          <div className="skill-empty">
            <p>{t('skills.empty')}</p>
            <span>{t('mcp.empty_hint')}</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="skill-empty">
            <p>{t('mcp.no_match')}</p>
          </div>
        ) : (
          <div className="skill-list">
            {filtered.map((skill) => {
              const isExpanded = expanded === skill.name;
              return (
                <article
                  className={`skill-card skill-card--mir ${!skill.enabled ? 'skill-card--disabled' : ''}`}
                  key={skill.name}
                  onClick={() => handleExpand(skill)}
                >
                  {/* Row 1: status dot + name + badge / switch */}
                  <div className="skill-card__top">
                    <div className="skill-card__identity">
                      <span className={`skill-status-dot ${statusDot(skill.enabled)}`} />
                      <strong>{skill.name}</strong>
                      <Badge>{sourceLabel(skill.source)}</Badge>
                      {skill.disable_model_invocation && <Badge>{t('skills.auto_invoke_disabled')}</Badge>}
                      {!skill.enabled && <Badge>{t('skills.disabled')}</Badge>}
                    </div>
                    <div className="skill-card__controls" onClick={(e) => e.stopPropagation()}>
                      <Switch
                        id={`skill-switch-${skill.name}`}
                        checked={skill.enabled}
                        onChange={() => handleToggle(skill)}
                      />
                    </div>
                  </div>

                  {/* Row 2: subtitle / meta */}
                  <div className="skill-card__meta">
                    <span className="skill-card__subtitle">{skill.description}</span>
                  </div>

                  {/* ── Expanded detail ── */}
                  {isExpanded && (
                    <div className="skill-card__detail" onClick={(e) => e.stopPropagation()}>
                      {detailLoading ? (
                        <div className="skill-detail-loading">
                          <Loader2 size={14} className="animate-spin" />
                        </div>
                      ) : detail ? (
                        <div className="skill-detail">
                          <div className="skill-detail__row">
                            <span className="skill-detail__label">{t('skills.version')}</span>
                            <code>{detail.version || '-'}</code>
                          </div>
                          <div className="skill-detail__row">
                            <span className="skill-detail__label">{t('skills.location')}</span>
                            <code className="skill-detail__path">{detail.file_path}</code>
                          </div>
                          {detail.body && (
                            <div className="skill-detail__body">
                              <div className="skill-detail__label">{t('skills.body')}</div>
                              <pre className="skill-detail__pre">{detail.body.slice(0, 1200)}{detail.body.length > 1200 ? '\n…' : ''}</pre>
                            </div>
                          )}
                        </div>
                      ) : null}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}

        {/* ── Validate tool ── */}
        <div className="skill-quick-card" style={{ marginTop: 16 }}>
          <FileText size={16} className="skill-form-card__icon" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="skill-template-row">
              <input
                className="skill-search__input"
                style={{ flex: 1 }}
                placeholder={t('skills.validate_placeholder')}
                value={validatePath}
                onChange={(e) => setValidatePath(e.target.value)}
              />
              <Button variant="secondary" onClick={handleValidate} disabled={!validatePath.trim()}>
                {t('skills.validate_btn')}
              </Button>
            </div>
            {validateResult && (
              <div className={`skill-validate-result ${validateResult.valid ? 'skill-validate-result--ok' : 'skill-validate-result--error'}`}>
                {validateResult.valid
                  ? t('skills.valid')
                  : validateResult.diagnostics.map((d) => d.message).join('; ') || t('skills.invalid')}
              </div>
            )}
          </div>
        </div>
      </div>
    </WorkspacePage>
  );
}
