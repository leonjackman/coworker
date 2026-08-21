import { useState } from 'react';
import { Check, ListChecks, ChevronDown, X } from 'lucide-react';
import type { Todo } from '../types';
import { t } from '../lib/i18n';

interface TodoBlockProps {
  todos: Todo[];
  onToggleTodo?: (index: number) => void;
  onClose?: () => void;
}

/**
 * The agent's self-decomposed task checklist (write_todos), shown as a card in
 * the composer slot. Each write_todos update from the agent re-renders the card
 * live, so the user sees each step get checked off as the agent works.
 */
export function TodoBlock({ todos, onToggleTodo, onClose }: TodoBlockProps) {
  const [expanded, setExpanded] = useState(true);
  if (!todos || todos.length === 0) return null;
  const doneCount = todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className="todo-block">
      <div className="todo-block__head">
        <span className="todo-block__title">
          <ListChecks size={14} className="todo-block__sign" />
          {t('todo.task_list')}
        </span>
        <span className="todo-block__count">{doneCount}/{todos.length}</span>
        <button type="button" className="todo-block__toggle" onClick={() => setExpanded((value) => !value)} aria-label={t('todo.toggle')}>
          <ChevronDown size={13} className={expanded ? 'todo-block__toggle-open' : ''} />
        </button>
        {onClose && (
          <button type="button" className="todo-block__close" onClick={onClose} aria-label={t('todo.close')}>
            <X size={13} />
          </button>
        )}
      </div>
      {expanded && (
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
    </div>
  );
}
