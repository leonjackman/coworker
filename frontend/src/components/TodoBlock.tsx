import { useState } from 'react';
import { Check, ListChecks, ChevronDown, X } from 'lucide-react';
import type { Todo } from '../types';
import { t } from '../lib/i18n';

export interface QueuedMessageItem {
  id: string;
  message: string;
}

interface TodoBlockProps {
  todos: Todo[];
  onToggleTodo?: (index: number) => void;
  /** Messages queued to auto-send after the current stream finishes. Listed in
   *  this card so the user sees each pending message instead of a hidden icon. */
  queuedMessages?: QueuedMessageItem[];
  /** Remove a queued message from the queue without sending it. */
  onRemoveQueued?: (id: string) => void;
  onClose?: () => void;
}

/**
 * The agent's self-decomposed task checklist (write_todos) plus the user's
 * queued messages, shown as a card in the composer slot. Each write_todos
 * update from the agent re-renders the card live, so the user sees each step
 * get checked off as the agent works; queued messages are listed one per row
 * with a remove action.
 */
export function TodoBlock({ todos, onToggleTodo, queuedMessages = [], onRemoveQueued, onClose }: TodoBlockProps) {
  const [expanded, setExpanded] = useState(true);
  const hasTodos = todos.length > 0;
  const hasQueue = queuedMessages.length > 0;
  if (!hasTodos && !hasQueue) return null;
  const doneCount = todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className="todo-block">
      <div className="todo-block__head">
        <span className="todo-block__title">
          <ListChecks size={14} className="todo-block__sign" />
          {hasTodos ? t('todo.task_list') : t('chat.queue_section')}
        </span>
        {hasTodos && <span className="todo-block__count">{doneCount}/{todos.length}</span>}
        <button type="button" className="todo-block__toggle" onClick={() => setExpanded((value) => !value)} aria-label={t('todo.toggle')}>
          <ChevronDown size={13} className={expanded ? 'todo-block__toggle-open' : ''} />
        </button>
        {onClose && hasTodos && (
          <button type="button" className="todo-block__close" onClick={onClose} aria-label={t('todo.close')}>
            <X size={13} />
          </button>
        )}
      </div>
      {expanded && (
        <>
          {hasTodos && (
            <ul className="todo-block__list">
              {todos.map((todo, index) => (
                <li
                  key={`${todo.content}-${index}`}
                  className={`todo-block__item todo-block__item--${todo.status}`}
                  onClick={() => onToggleTodo?.(index)}
                  role="checkbox"
                  aria-checked={todo.status === 'completed'}
                  tabIndex={0}
                >
                  {todo.status === 'completed' ? <Check size={13} /> : <span className="todo-block__dot" />}
                  <span>{todo.content}</span>
                </li>
              ))}
            </ul>
          )}
          {hasQueue && (
            <div className="todo-block__queue">
              <div className="todo-block__queue-head">
                <span className="todo-block__queue-title">{t('chat.queue_section')}</span>
                <span className="todo-block__queue-count">{queuedMessages.length}</span>
              </div>
              <ul className="todo-block__queue-list">
                {queuedMessages.map((queued) => (
                  <li key={queued.id} className="todo-block__queue-item">
                    <span className="todo-block__queue-dot" />
                    <span className="todo-block__queue-text" title={queued.message}>{queued.message}</span>
                    <button
                      type="button"
                      className="todo-block__queue-remove"
                      onClick={() => onRemoveQueued?.(queued.id)}
                      aria-label={t('chat.remove_queued')}
                    >
                      <X size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
