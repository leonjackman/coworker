import { ChevronDown, ChevronRight, Check, Download, Loader2, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { Button } from './button';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { MarketSkill } from '../../types';

interface MarketSkillDetailModalProps {
  open: boolean;
  skill: MarketSkill | null;
  onClose: () => void;
  onInstall?: (skill: MarketSkill) => void;
  installed?: boolean | undefined;
  installing?: boolean;
}

interface ParsedCommand {
  name: string;
  description?: string;
  file?: string;
}

interface DetailData {
  body: string;
  commands: ParsedCommand[];
}

/**
 * Detail modal for a market skill.
 * Fetches the full SKILL.md body on open via GET /skills/market/detail,
 * so the user can see what the skill actually does before installing.
 */
export function MarketSkillDetailModal({
  open,
  skill,
  onClose,
  onInstall,
  installed,
  installing,
}: MarketSkillDetailModalProps) {
  const [detail, setDetail] = useState<DetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [commandsExpanded, setCommandsExpanded] = useState(false);
  const fetchDone = useRef(false);

  const fetchDetail = useCallback(async () => {
    if (!skill) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await chatService.getMarketSkillDetail(
        skill.source,
        skill.slug,
        skill.owner ?? null,
      );
      if (response.status === 'ok' && response.skill) {
        setDetail({
          body: response.skill.body || '',
          commands: (response.skill.commands ?? []) as ParsedCommand[],
        });
      } else {
        setDetailError(response.message || 'Failed to load skill details');
      }
    } catch {
      setDetailError('Failed to load skill details');
    } finally {
      setDetailLoading(false);
    }
  }, [skill]);

  useEffect(() => {
    if (!open) {
      setCommandsExpanded(false);
      setDetail(null);
      setDetailError(null);
      fetchDone.current = false;
      return;
    }
    if (!skill) return;
    if (fetchDone.current) return;
    fetchDone.current = true;
    void fetchDetail();
  }, [open, skill, fetchDetail]);

  const handleClose = useCallback(() => {
    fetchDone.current = false;
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, handleClose]);

  if (!open || !skill) return null;

  const busy = installing ?? false;
  const isInstalled = installed ?? false;
  const body = detail?.body ?? '';
  const commands = detail?.commands ?? [];

  return createPortal(
    <div className="modal-overlay" onClick={handleClose} role="dialog" aria-modal="true">
      <div className="modal modal--lg" onClick={(e) => e.stopPropagation()}>
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
          <button type="button" className="modal__close" onClick={handleClose} aria-label="关闭">
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

          {/* Commands section */}
          {commands.length > 0 && (
            <div className="skill-detail__section">
              <button
                type="button"
                className="skill-detail__collapsible-header"
                onClick={() => setCommandsExpanded((prev) => !prev)}
              >
                {commandsExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                <span className="skill-detail__label">{t('skills.commands')}</span>
                <span className="skill-detail__count">{commands.length}</span>
              </button>
              {commandsExpanded && (
                <div className="skill-detail__commands">
                  {commands.map((cmd) => (
                    <div key={cmd.name} className="skill-detail__command">
                      <code className="skill-detail__command-name">/{cmd.name}</code>
                      {cmd.description && (
                        <span className="skill-detail__command-desc">{cmd.description}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Body — always shown */}
          {body && (
            <div className="skill-detail__section">
              <span className="skill-detail__label">{t('skills.body')}</span>
              <pre className="skill-detail__pre-full">{body}</pre>
            </div>
          )}

          {detailLoading && (
            <div className="skill-detail-loading">
              <Loader2 size={14} className="animate-spin" />
              <span>{t('skills.loading_detail')}</span>
            </div>
          )}

          {detailError && (
            <div className="skill-message skill-message--error">{detailError}</div>
          )}
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
