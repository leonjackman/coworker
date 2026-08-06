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
  onSaveEdit: (goalText: string) => void;
}

export function GoalCard({ goal, onPause, onResume, onDelete, onSaveEdit }: GoalCardProps) {
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(goal.goalText);
  const hasTodos = goal.todos.length > 0;
  const doneCount = goal.todos.filter((todo) => todo.status === 'completed').length;

  const startEdit = () => {
    setDraft(goal.goalText);
    setEditing(true);
  };

  const saveEdit = () => {
    const next = draft.trim();
    if (!next) return;
    setEditing(false);
    onSaveEdit(next);
  };

  return (
    <div className="goal-card" data-state={goal.done ? 'done' : goal.paused ? 'paused' : 'active'}>
      <div className="goal-card__header">
        <span className="goal-card__status">
          <Target size={15} />
          {goal.done ? t('chat.goal_status_done') : goal.paused ? t('chat.goal_status_paused') : t('chat.goal_status_active')}
        </span>
        <div className="goal-card__actions">
          {!goal.done &&
            (goal.running && !goal.paused ? (
              <Button type="button" variant="secondary" size="sm" onClick={onPause} aria-label={t('chat.goal_pause')}>
                <Pause size={13} />
                {t('chat.goal_pause')}
              </Button>
            ) : (
              <Button type="button" variant="secondary" size="sm" onClick={onResume} aria-label={t('chat.goal_resume')}>
                <Play size={13} />
                {t('chat.goal_resume')}
              </Button>
            ))}
          {!editing && (
            <Button type="button" variant="icon" size="icon-sm" onClick={startEdit} aria-label={t('chat.goal_edit')}>
              <Pencil size={14} />
            </Button>
          )}
          <Button type="button" variant="icon" size="icon-sm" onClick={onDelete} aria-label={t('chat.goal_delete')}>
            <Trash2 size={14} />
          </Button>
        </div>
      </div>

      <div className="goal-card__body">
        {editing ? (
          <div className="goal-card__edit">
            <textarea
              className="goal-card__edit-input"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={2}
              autoFocus
            />
            <div className="goal-card__edit-actions">
              <Button type="button" variant="icon" size="icon-sm" onClick={() => setEditing(false)} aria-label={t('chat.goal_edit_cancel')}>
                <X size={14} />
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={saveEdit} aria-label={t('chat.goal_edit_save')}>
                <Check size={13} />
                {t('chat.goal_edit_save')}
              </Button>
            </div>
          </div>
        ) : (
          <p className="goal-card__text">{goal.goalText}</p>
        )}
        {!editing && hasTodos && (
          <>
            <div className="goal-card__todos-head">
              <span className="goal-card__todos-count">
                {doneCount}/{goal.todos.length}
              </span>
              <button type="button" className="goal-card__toggle" onClick={() => setExpanded((value) => !value)} aria-label={t('chat.goal_toggle_todos')}>
                <ChevronDown size={13} className={expanded ? 'goal-card__toggle-open' : ''} />
              </button>
            </div>
            {expanded && (
              <ul className="goal-card__todos">
                {goal.todos.map((todo, index) => (
                  <li key={`${todo.content}-${index}`} className={`goal-card__todo goal-card__todo--${todo.status}`}>
                    {todo.status === 'completed' ? <Check size={13} /> : <span className="goal-card__todo-dot" />}
                    <span>{todo.content}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {!editing && goal.progress && !goal.done && <p className="goal-card__progress">{goal.progress}</p>}
      </div>
    </div>
  );
}
