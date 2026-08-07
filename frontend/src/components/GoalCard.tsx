import { Check, ChevronDown, Pause, Pencil, Play, Target, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { t } from '../lib/i18n';
import type { GoalState } from '../types';
import { Button } from './ui/button';

interface GoalCardProps {
  goal: GoalState;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
  onDraftEdit: () => void;
  onToggleTodo?: (index: number) => void;
  onGoalStop?: () => void;
}

export function GoalCard({ goal, onPause, onResume, onDelete, onDraftEdit, onToggleTodo, onGoalStop }: GoalCardProps) {
  const [expanded, setExpanded] = useState(true);
  const hasTodos = goal.todos.length > 0;
  const doneCount = goal.todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className="goal-card" data-state={goal.done ? 'done' : goal.paused ? 'paused' : goal.stalled ? 'stalled' : 'active'}>
      <div className="goal-card__header">
        <div className="goal-card__header-left">
          <span className="goal-card__status">
            <Target size={15} />
            {goal.done ? t('chat.goal_status_done') : goal.paused ? t('chat.goal_status_paused') : goal.stalled ? 'Stalled' : t('chat.goal_status_active')}
          </span>
          <span className="goal-card__round">Round {goal.round}</span>
        </div>
        <div className="goal-card__actions">
          {!goal.done && (
            <Button type="button" variant="secondary" size="sm" onClick={goal.paused ? onResume : onPause} aria-label={goal.paused ? t('chat.goal_resume') : t('chat.goal_pause')}>
              {goal.paused ? <Play size={13} /> : <Pause size={13} />}
              {goal.paused ? t('chat.goal_resume') : t('chat.goal_pause')}
            </Button>
          )}
          {!goal.done && !goal.paused && !goal.stalled && onGoalStop && (
            <Button type="button" variant="icon" size="icon-sm" onClick={onGoalStop} aria-label="停止目标">
              <X size={14} />
            </Button>
          )}
          {!goal.done && (
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
            <span className="goal-card__verification-label">Verification</span>
            <span>{goal.verification}</span>
          </div>
        )}
        {goal.stalled && (
          <p className="goal-card__stalled">
            Agent stalled after multiple attempts. Consider pausing or editing the goal.
          </p>
        )}
        {hasTodos && (
          <>
            <div className="goal-card__todos-head">
              <span className="goal-card__todos-count">{doneCount}/{goal.todos.length}</span>
              <button type="button" className="goal-card__toggle" onClick={() => setExpanded((value) => !value)} aria-label={t('chat.goal_toggle_todos')}>
                <ChevronDown size={13} className={expanded ? 'goal-card__toggle-open' : ''} />
              </button>
            </div>
            {expanded && (
              <ul className="goal-card__todos">
                {goal.todos.map((todo, index) => (
                  <li
                    key={`${todo.content}-${index}`}
                    className={`goal-card__todo goal-card__todo--${todo.status}`}
                    onClick={() => onToggleTodo?.(index)}
                    role="checkbox"
                    aria-checked={todo.status === 'completed'}
                    tabIndex={0}
                  >
                    {todo.status === 'completed' ? <Check size={13} /> : <span className="goal-card__todo-dot" />}
                    <span>{todo.content}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {!goal.done && !goal.stalled && goal.progress && <p className="goal-card__progress">{goal.progress}</p>}
      </div>
    </div>
  );
}
