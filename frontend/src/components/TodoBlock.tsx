import { useRef, useState, type CSSProperties } from 'react';
import { Check, ListChecks, ChevronDown, GripVertical, Pencil, Send, X, Target, Pause, Play, Trash2 } from 'lucide-react';
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { GoalState, GoalStatus, Todo } from '../types';
import { t, tOrDefault } from '../lib/i18n';

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
  /** Save an edited queued message text. */
  onEditQueued?: (id: string, message: string) => void;
  /** Reorder the queue (drag-to-reorder); receives the new id order. */
  onReorderQueued?: (orderedIds: string[]) => void;
  /** Interject (插話) a queued message into the currently-running task to guide
   *  the LLM WITHOUT pausing/stopping the stream. */
  onInterjectQueued?: (id: string) => void;
  onClose?: () => void;
  /** Session-scoped goal (严格会话隔离：只渲染当前会话的目标）。 */
  goal?: GoalState | null;
  /** 暂停目标作用（停目标，不停任务）。 */
  onGoalPause?: (goal: GoalState) => void;
  /** 恢复目标作用。 */
  onGoalResume?: (goal: GoalState) => void;
  /** 清除目标（终止一切续跑）。 */
  onGoalClear?: (goal: GoalState) => void;
}

function goalStatusLabel(status: GoalStatus): string {
  switch (status) {
    case 'active':
      return tOrDefault('goal.status_active', '进行中');
    case 'paused':
      return tOrDefault('goal.status_paused', '已暂停');
    case 'blocked':
      return tOrDefault('goal.status_blocked', '受阻');
    case 'complete':
      return tOrDefault('goal.status_complete', '已完成');
    case 'budget_limited':
      return tOrDefault('goal.status_budget_limited', '预算受限');
    case 'usage_limited':
      return tOrDefault('goal.status_usage_limited', '用量受限');
    default:
      return status;
  }
}

function formatGoalElapsed(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

interface SortableQueueItemProps {
  queued: QueuedMessageItem;
  editing: boolean;
  editingText: string;
  onEditingTextChange: (text: string) => void;
  onStartEdit: (queued: QueuedMessageItem) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onRemove: (id: string) => void;
  onInterject: (id: string) => void;
}

/** One queued message row. Uses @dnd-kit sortable (same as the sidebar) so
 *  dragging animates smoothly: the lifted row casts a shadow and the other
 *  rows shift with a CSS transition instead of jumping. */
function SortableQueueItem({
  queued,
  editing,
  editingText,
  onEditingTextChange,
  onStartEdit,
  onCommitEdit,
  onCancelEdit,
  onRemove,
  onInterject,
}: SortableQueueItemProps) {
  const editingInputRef = useRef<HTMLInputElement>(null);
  const cancelledRef = useRef(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: queued.id });
  const dragStyle: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  const startEdit = () => {
    onStartEdit(queued);
    requestAnimationFrame(() => editingInputRef.current?.focus());
  };
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (editingText.trim()) onCommitEdit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelledRef.current = true;
      onCancelEdit();
    }
  };
  const handleBlur = () => {
    // Escape already asked to cancel (the input unmounts right after): don't
    // let the browser's blur fire a save on top of it.
    if (cancelledRef.current) {
      cancelledRef.current = false;
      return;
    }
    if (!editingText.trim()) return;
    onCommitEdit();
  };

  return (
    <li
      ref={setNodeRef}
      style={dragStyle}
      className={`todo-block__queue-item${editing ? ' todo-block__queue-item--editing' : ''}`}
      data-dragging={isDragging}
    >
      <span
        className="todo-block__queue-drag"
        {...attributes}
        {...listeners}
        aria-label={t('chat.reorder_queued')}
        title={t('chat.reorder_queued')}
      >
        <GripVertical size={13} />
      </span>
      <span className="todo-block__queue-dot" />
      {editing ? (
        <input
          ref={editingInputRef}
          className="todo-block__queue-input"
          value={editingText}
          onChange={(e) => onEditingTextChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          aria-label={t('chat.edit_queued')}
        />
      ) : (
        <span className="todo-block__queue-text" title={queued.message}>{queued.message}</span>
      )}
      <button
        type="button"
        className="todo-block__queue-interject"
        onClick={() => onInterject(queued.id)}
        aria-label={t('chat.interject_queued')}
        title={t('chat.interject_queued')}
      >
        <Send size={12} />
      </button>
      <button
        type="button"
        className="todo-block__queue-edit"
        onClick={startEdit}
        aria-label={t('chat.edit_queued')}
      >
        <Pencil size={12} />
      </button>
      <button
        type="button"
        className="todo-block__queue-remove"
        onClick={() => onRemove(queued.id)}
        aria-label={t('chat.remove_queued')}
      >
        <X size={12} />
      </button>
    </li>
  );
}

/**
 * The agent's self-decomposed task checklist (write_todos) plus the user's
 * queued messages, shown as a card in the composer slot. Each write_todos
 * update from the agent re-renders the card live, so the user sees each step
 * get checked off as the agent works; queued messages are listed one per row
 * with drag-to-reorder, inline edit and a remove action.
 */
export function TodoBlock({ todos, onToggleTodo, queuedMessages = [], onRemoveQueued, onEditQueued, onReorderQueued, onInterjectQueued, onClose, goal = null, onGoalPause, onGoalResume, onGoalClear }: TodoBlockProps) {
  const [expanded, setExpanded] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState('');
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const hasTodos = todos.length > 0;
  const hasQueue = queuedMessages.length > 0;
  if (!hasTodos && !hasQueue && !goal) return null;
  const doneCount = todos.filter((todo) => todo.status === 'completed').length;

  const startEdit = (queued: QueuedMessageItem) => {
    setEditingId(queued.id);
    setEditingText(queued.message);
  };
  const commitEdit = () => {
    const id = editingId;
    const text = editingText.trim();
    setEditingId(null);
    if (id && text) onEditQueued?.(id, text);
  };
  const cancelEdit = () => {
    setEditingId(null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = queuedMessages.findIndex((q) => q.id === active.id);
    const newIndex = queuedMessages.findIndex((q) => q.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    onReorderQueued?.(arrayMove(queuedMessages.map((q) => q.id), oldIndex, newIndex));
  };

  const goalTokenRatio = goal && goal.token_budget != null && goal.token_budget > 0 ? Math.min(1, goal.tokens_used / goal.token_budget) : 0;

  return (
    <div className="todo-block">
      {goal && (
        <div className={`todo-block__goal todo-block__goal--${goal.status}`}>
          <div className="todo-block__goal-head">
            <span className="todo-block__goal-title">
              <Target size={13} className="todo-block__sign" />
              <span className="todo-block__goal-objective" title={goal.objective}>{goal.objective}</span>
            </span>
            <span className="todo-block__goal-status">{goalStatusLabel(goal.status)}</span>
          </div>
          <div className="todo-block__goal-meta">
            <span>{tOrDefault('goal.elapsed', '已用时间')} {formatGoalElapsed(goal.time_used_seconds)}</span>
            <span>
              {goal.token_budget != null
                ? `${goal.tokens_used}/${goal.token_budget} tokens`
                : `${goal.tokens_used} tokens`}
            </span>
          </div>
          {goal.token_budget != null && goal.token_budget > 0 && (
            <div className="todo-block__goal-budget">
              <div className="todo-block__goal-budget-track">
                <div className="todo-block__goal-budget-fill" style={{ width: `${Math.round(goalTokenRatio * 100)}%` }} />
              </div>
            </div>
          )}
          <div className="todo-block__goal-actions">
            {(goal.status === 'active' || goal.status === 'budget_limited') && (
              <button type="button" className="todo-block__goal-btn" onClick={() => onGoalPause?.(goal)} title={tOrDefault('goal.pause', '暂停目标')}>
                <Pause size={12} />
                {tOrDefault('goal.pause', '暂停')}
              </button>
            )}
            {goal.status === 'paused' && (
              <button type="button" className="todo-block__goal-btn" onClick={() => onGoalResume?.(goal)} title={tOrDefault('goal.resume', '恢复目标')}>
                <Play size={12} />
                {tOrDefault('goal.resume', '恢复')}
              </button>
            )}
            <button type="button" className="todo-block__goal-btn" onClick={() => onGoalClear?.(goal)} title={tOrDefault('goal.clear', '清除目标')}>
              <Trash2 size={12} />
              {tOrDefault('goal.clear', '清除')}
            </button>
          </div>
        </div>
      )}
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
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext items={queuedMessages.map((q) => q.id)} strategy={verticalListSortingStrategy}>
                  <ul className="todo-block__queue-list">
                    {queuedMessages.map((queued) => (
                      <SortableQueueItem
                        key={queued.id}
                        queued={queued}
                        editing={editingId === queued.id}
                        editingText={editingText}
                        onEditingTextChange={setEditingText}
                        onStartEdit={startEdit}
                        onCommitEdit={commitEdit}
                        onCancelEdit={cancelEdit}
                        onRemove={(id) => onRemoveQueued?.(id)}
                        onInterject={(id) => onInterjectQueued?.(id)}
                      />
                    ))}
                  </ul>
                </SortableContext>
              </DndContext>
            </div>
          )}
        </>
      )}
    </div>
  );
}
