import { Pause, Play, Pencil, Target, Trash2 } from 'lucide-react';
import { t } from '../lib/i18n';
import type { GoalState } from '../types';
import { Button } from './ui/button';

interface GoalCardProps {
  goal: GoalState;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
  onDraftEdit: () => void;
  recentToolNames?: string[] | undefined;
}

export function GoalCard({ goal, onPause, onResume, onDelete, onDraftEdit, recentToolNames }: GoalCardProps) {

  return (
    <div className="goal-card" data-state={goal.done ? 'done' : goal.paused ? 'paused' : goal.stalled ? 'stalled' : 'active'}>
      <div className="goal-card__header">
        <div className="goal-card__header-left">
          <span className="goal-card__status">
            <Target size={15} />
            {goal.done ? t('chat.goal_status_done') : goal.paused ? t('chat.goal_status_paused') : goal.stalled ? t('chat.goal_status_stalled') : t('chat.goal_status_active')}
          </span>
          <span className="goal-card__round">{t('chat.goal_round', { round: goal.round })}</span>
        </div>
        <div className="goal-card__actions">
          {!goal.done && !goal.stalled && !goal.paused && (
            <Button type="button" variant="secondary" size="sm" onClick={onPause} aria-label={t('chat.goal_pause')}>
              <Pause size={13} />
              {t('chat.goal_pause')}
            </Button>
          )}
          {!goal.done && !goal.stalled && goal.paused && (
            <Button type="button" variant="secondary" size="sm" onClick={onResume} aria-label={t('chat.goal_resume')}>
              <Play size={13} />
              {t('chat.goal_resume')}
            </Button>
          )}
          {/* Editing the goal only works while paused (running the composer is
              locked by isThinking, so the edit would be a dead end). */}
          {!goal.done && !goal.stalled && goal.paused && (
            <Button type="button" variant="icon" size="icon-sm" onClick={onDraftEdit} aria-label={t('chat.goal_edit')}>
              <Pencil size={14} />
            </Button>
          )}
          <Button type="button" variant="icon" size="icon-sm" onClick={onDelete} aria-label={t('chat.goal_delete')}>
            <Trash2 size={14} />
          </Button>
        </div>
      </div>

      <div className="goal-card__body">
        <p className="goal-card__text">{goal.goalText}</p>
            {goal.verification && (
          <div className="goal-card__verification">
            <span className="goal-card__verification-label">{t('chat.goal_verification')}</span>
            <span>{goal.verification}</span>
          </div>
        )}
        {goal.stalled && (
          <p className="goal-card__stalled">
            {t('chat.goal_stalled_hint')}
          </p>
        )}
        {goal.done && goal.reason && (
          <p className="goal-card__reason">{goal.reason}</p>
        )}
        {recentToolNames && recentToolNames.length > 0 && (
          <div className="goal-card__tools">
            <span className="goal-card__tools-label">{t('chat.goal_tools')}:</span>
            <ul className="goal-card__tools-list">
              {recentToolNames.map((name, i) => (
                <li key={i} className="goal-card__tool-item"><span className="goal-card__tool-name">{name}</span></li>
              ))}
            </ul>
          </div>
        )}
        {!goal.done && !goal.stalled && goal.progress && <p className="goal-card__progress">{goal.progress}</p>}
      </div>
    </div>
  );
}
