import { Bot, Check, Eye, Loader2, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { formatTimeAgo } from '../../lib/utils';
import { t, translateError } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { PendingSkill } from '../../types';
import { Button } from '../ui/button';
import { DetailModal } from '../ui/detail-modal';

interface SkillPendingPanelProps {
  onChanged?: () => void;
}

/**
 * Self-calibration review queue: draft skills the agent staged for approval.
 * Supports preview/edit-before-approve, approve, and reject.
 */
export function SkillPendingPanel({ onChanged }: SkillPendingPanelProps) {
  const [pending, setPending] = useState<PendingSkill[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openName, setOpenName] = useState<string | null>(null);
  const [draftContent, setDraftContent] = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [busyName, setBusyName] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await chatService.listPendingSkills();
      setPending(response.pending);
      setError(null);
    } catch (err) {
      setError(translateError(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openDraft = useCallback(async (name: string) => {
    setOpenName(name);
    setDraftLoading(true);
    setDraftContent('');
    try {
      const response = await chatService.getPendingSkill(name);
      setDraftContent(response.content);
    } catch (err) {
      setError(translateError(err));
    } finally {
      setDraftLoading(false);
    }
  }, []);

  const saveDraft = useCallback(async () => {
    if (!openName) return;
    setBusyName(openName);
    try {
      await chatService.updatePendingSkill(openName, draftContent);
      setOpenName(null);
      await load();
    } catch (err) {
      setError(translateError(err));
    } finally {
      setBusyName(null);
    }
  }, [openName, draftContent, load]);

  const approve = useCallback(
    async (name: string) => {
      setBusyName(name);
      try {
        await chatService.approvePendingSkill(name);
        setOpenName(null);
        await load();
        onChanged?.();
      } catch (err) {
        setError(translateError(err));
      } finally {
        setBusyName(null);
      }
    },
    [load, onChanged],
  );

  const reject = useCallback(
    async (name: string) => {
      setBusyName(name);
      try {
        await chatService.rejectPendingSkill(name);
        setOpenName(null);
        await load();
      } catch (err) {
        setError(translateError(err));
      } finally {
        setBusyName(null);
      }
    },
    [load],
  );

  if (pending === null) {
    return (
      <div className="skill-empty">
        <Loader2 size={16} className="animate-spin" />
      </div>
    );
  }
  if (pending.length === 0) return null;

  return (
    <>
      <div className="skills-pending">
        <div className="skills-pending__title">
          <Bot size={14} />
          {t('skills.pending')}
          <span className="skills-pending__count">{pending.length}</span>
        </div>
        {error && <div className="skill-diagnostics__item skill-diagnostics__item--invalid">{error}</div>}
        <div className="skills-pending__list">
          {pending.map((draft) => (
            <div key={draft.name} className="skills-pending__card">
              <div className="skills-pending__head">
                <span className="skills-pending__name">{draft.name}</span>
                <span className="settings-chip">{t('skills.agent_generated')}</span>
                {draft.created_at && (
                  <span className="skills-pending__meta">{formatTimeAgo(draft.created_at)}</span>
                )}
              </div>
              <p className="skills-pending__desc">{draft.description}</p>
              {draft.sources && draft.sources.length > 0 && (
                <div className="skills-pending__sources">
                  {draft.sources.map((source, i) => (
                    <span key={i} className="skills-pending__source">{source}</span>
                  ))}
                </div>
              )}
              <div className="skills-pending__actions">
                <Button variant="outline" size="sm" onClick={() => void openDraft(draft.name)}>
                  <Eye size={14} />
                  {t('skills.preview')}
                </Button>
                <Button variant="primary" size="sm" disabled={busyName === draft.name} onClick={() => void approve(draft.name)}>
                  <Check size={14} />
                  {t('skills.approve')}
                </Button>
                <Button variant="ghost" size="sm" disabled={busyName === draft.name} onClick={() => void reject(draft.name)}>
                  <Trash2 size={14} />
                  {t('skills.reject')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <DetailModal
        open={openName !== null}
        onClose={() => setOpenName(null)}
        title={t('skills.pending_review')}
        footer={openName ? (
          <div className="skills-pending__modal-actions">
            <Button variant="ghost" disabled={busyName !== null} onClick={() => setOpenName(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="secondary" disabled={busyName !== null} onClick={() => void saveDraft()}>
              {t('skills.save_draft')}
            </Button>
            <Button variant="primary" disabled={busyName !== null} onClick={() => void approve(openName)}>
              <Check size={14} />
              {t('skills.approve')}
            </Button>
          </div>
        ) : undefined}
      >
        {draftLoading ? (
          <div className="skill-empty">
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : (
          <textarea
            className="skills-pending__editor"
            value={draftContent}
            onChange={(e) => setDraftContent(e.target.value)}
            spellCheck={false}
            aria-label={t('skills.pending_review')}
          />
        )}
      </DetailModal>
    </>
  );
}
