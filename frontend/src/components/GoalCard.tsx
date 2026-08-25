import { Pause, Play, Target, Trash2 } from 'lucide-react';
import { t, tOrDefault } from '../lib/i18n';
import type { GoalState } from '../types';
import { Button } from './ui/button';

interface GoalCardProps {
  goal: GoalState;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}

function stateDataState(goal: GoalState): string {
  switch (goal.status) {
    case 'complete':
      return 'done';
    case 'paused':
      return 'paused';
    case 'blocked':
    case 'budget_limited':
    case 'usage_limited':
      return 'stalled';
    default:
      return 'active';
  }
}

function statusLabel(goal: GoalState): string {
  switch (goal.status) {
    case 'active':
      return t('chat.goal_status_active');
    case 'paused':
      return t('chat.goal_status_paused');
    case 'complete':
      return t('chat.goal_status_done');
    case 'blocked':
      return t('chat.goal_status_stalled');
    case 'budget_limited':
      return tOrDefault('chat.goal_status_budget_limited', '目标预算受限');
    case 'usage_limited':
      return tOrDefault('chat.goal_status_usage_limited', '目标用量受限');
    default:
      return goal.status;
  }
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function GoalCard({ goal, onPause, onResume, onDelete }: GoalCardProps) {
  const done = goal.status === 'complete';
  const paused = goal.status === 'paused';
  const stalled = goal.status === 'blocked' || goal.status === 'budget_limited' || goal.status === 'usage_limited';
  const active = !done && !paused && !stalled;
  const budget = goal.token_budget != null ? `${goal.tokens_used} / ${goal.token_budget}` : String(goal.tokens_used);

  return (
    <div className="goal-card" data-state={stateDataState(goal)}>
      <div className="goal-card__header">
        <div className="goal-card__header-left">
          <span className="goal-card__status">
            <Target size={15} />
            {statusLabel(goal)}
          </span>
          <span className="goal-card__round">{t('chat.goal_round', { round: goal.round + 1 })}</span>
        </div>
        <div className="goal-card__actions">
          {active && (
            <Button type="button" variant="secondary" size="sm" onClick={onPause} aria-label={t('chat.goal_pause')}>
              <Pause size={13} />
              {t('chat.goal_pause')}
            </Button>
          )}
          {paused && (
            <Button type="button" variant="secondary" size="sm" onClick={onResume} aria-label={t('chat.goal_resume')}>
              <Play size={13} />
              {t('chat.goal_resume')}
            </Button>
          )}
          <Button type="button" variant="icon" size="icon-sm" onClick={onDelete} aria-label={t('chat.goal_delete')}>
            <Trash2 size={14} />
          </Button>
        </div>
      </div>

      <div className="goal-card__body">
        <p className="goal-card__text">{goal.objective}</p>
        <p className="goal-card__usage">
          {budget} tokens · {tOrDefault('goal.elapsed', '已用')} {formatElapsed(goal.time_used_seconds)}
        </p>
        {stalled && <p className="goal-card__stalled">{t('chat.goal_stalled_hint')}</p>}
      </div>
    </div>
  );
}
