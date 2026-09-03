import { Check, Download, Loader2, X } from 'lucide-react';
import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { Button } from './button';
import { t } from '../../lib/i18n';
import type { MarketSkill } from '../../types';

interface MarketSkillDetailModalProps {
  open: boolean;
  skill: MarketSkill | null;
  onClose: () => void;
  onInstall?: (skill: MarketSkill) => void;
  installed?: boolean | undefined;
  installing?: boolean;
}

/**
 * Detail modal for a skill from the market.
 * Shows full info (description, category, source, version, etc.) and an
 * Install button so the user can confirm before installing.
 */
export function MarketSkillDetailModal({
  open,
  skill,
  onClose,
  onInstall,
  installed,
  installing,
}: MarketSkillDetailModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open || !skill) return null;

  const busy = installing ?? false;
  const isInstalled = installed ?? false;

  return createPortal(
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <div className="modal__heading">
            <div className="modal__icon">
              {skill.icon_url ? (
                <img
                  className="skill-market-icon"
                  src={skill.icon_url}
                  alt=""
                  style={{ width: 32, height: 32, objectFit: 'contain' }}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                    (e.target as HTMLImageElement).parentElement!.textContent = '🔧';
                  }}
                />
              ) : (
                <span className="skill-emoji skill-emoji--lg">🔧</span>
              )}
            </div>
            <div className="modal__titles">
              <div className="modal__title">{skill.name}</div>
              <div className="modal__subtitle">
                {skill.source}
                {skill.owner && ` · @${skill.owner}`}
                {skill.category && ` · ${skill.category}`}
              </div>
            </div>
          </div>
          <button type="button" className="modal__close" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </button>
        </div>

        <div className="modal__body">
          <div className="skill-detail__section">
            <div className="skill-detail__label">{t('skills.description')}</div>
            <div className="skill-detail__value">{skill.description}</div>
          </div>

          <div className="skill-detail__grid">
            {skill.version && (
              <div className="skill-detail__row">
                <span className="skill-detail__label">{t('skills.version')}</span>
                <span className="skill-detail__value">{skill.version}</span>
              </div>
            )}
              <div className="skill-detail__row">
                <span className="skill-detail__label">Slug</span>
                <span className="skill-detail__value">{skill.slug}</span>
              </div>
            {skill.verified && (
              <div className="skill-detail__row">
                <span className="skill-detail__label">Verified</span>
                <span className="skill-detail__value">
                  <span className="verified-badge">
                    <Check size={12} />
                    Verified
                  </span>
                </span>
              </div>
            )}
          </div>
        </div>

        <div className="modal__footer">
          {isInstalled ? (
            <span className="installed-badge">
              <Check size={12} />
              {t('skills.installed_badge')}
            </span>
          ) : (
            <Button
              variant="primary"
              onClick={() => onInstall?.(skill)}
              disabled={busy}
            >
              {busy ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  {t('skills.market_installing')}
                </>
              ) : (
                <>
                  <Download size={14} />
                  {t('skills.market_install')}
                </>
              )}
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
