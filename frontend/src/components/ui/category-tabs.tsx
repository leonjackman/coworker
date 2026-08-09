import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface CategoryTabItem {
  id: string;
  label: ReactNode;
  count?: number;
}

interface CategoryTabsProps {
  categories: CategoryTabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
  'aria-label'?: string;
}

/**
 * Generic horizontal filter-tab bar.
 * Reusable across any catalog/list page (skills, MCP tools, extensions…).
 */
export function CategoryTabs({
  categories,
  value,
  onChange,
  className,
  'aria-label': ariaLabel = '分类',
}: CategoryTabsProps) {
  return (
    <div className={cn('category-tabs', className)} role="tablist" aria-label={ariaLabel}>
      {categories.map((cat) => {
        const active = cat.id === value;
        return (
          <button
            key={cat.id}
            type="button"
            role="tab"
            aria-selected={active}
            className={cn('category-tab', active && 'category-tab--active')}
            onClick={() => onChange(cat.id)}
          >
            <span className="category-tab__label">{cat.label}</span>
            {typeof cat.count === 'number' && (
              <span className="category-tab__count">{cat.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
