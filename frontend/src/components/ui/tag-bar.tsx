import { RefreshCw } from 'lucide-react';
import { SearchInput } from './search-input';
import { CategoryTabs, type CategoryTabItem } from './category-tabs';
import { Button } from './button';

export interface TagBarProps {
  /** 分类标签 */
  categories?: CategoryTabItem[];
  /** 当前选中的分类 */
  category?: string;
  /** 分类切换回调 */
  onCategoryChange?: (id: string) => void;
  /** 搜索占位文本 */
  searchPlaceholder?: string;
  /** 搜索值 */
  searchValue?: string;
  /** 搜索变化回调 */
  onSearchChange?: (value: string) => void;
  /** 刷新按钮 loading 状态 */
  refreshLoading?: boolean;
  /** 刷新回调 */
  onRefresh?: () => void;
  /** 刷新按钮 aria-label */
  refreshAriaLabel?: string;
  /** 左侧 slot - 插入在分类标签之前 */
  leftSlot?: React.ReactNode;
  /** 标签 slot - 插入在分类标签之后、搜索框之前 */
  tagSlot?: React.ReactNode;
  /** 右侧 slot - 插入在搜索框之后、刷新按钮之前 */
  rightSlot?: React.ReactNode;
  /** 自定义类名 */
  className?: string;
}

/**
 * 通用工具栏组件 - 包含分类标签、搜索框、刷新按钮和多个 slot
 * 
 * 布局结构:
 * [leftSlot] [categories] [tagSlot] [rightSlot] [searchInput] [refreshButton]
 * 
 * 示例用法:
 * ```tsx
 * <TagBar
 *   categories={categories}
 *   category={category}
 *   onCategoryChange={setCategory}
 *   searchValue={search}
 *   onSearchChange={setSearch}
 *   searchPlaceholder="搜索..."
 *   refreshLoading={loading}
 *   onRefresh={handleRefresh}
 *   leftSlot={<span>左侧内容</span>}
 *   tagSlot={<Button>自定义标签</Button>}
 *   rightSlot={<Button>右侧按钮</Button>}
 * />
 * ```
 */
export function TagBar({
  categories,
  category,
  onCategoryChange,
  searchPlaceholder,
  searchValue,
  onSearchChange,
  refreshLoading,
  onRefresh,
  refreshAriaLabel,
  leftSlot,
  tagSlot,
  rightSlot,
  className,
}: TagBarProps) {
  return (
    <div className={`skill-toolbar ${className || ''}`}>
      <div className="skill-toolbar__left">
        {leftSlot ? (
          <>
            {leftSlot}
            {categories && (
              <CategoryTabs
                categories={categories}
                value={category ?? ''}
                onChange={(id: string) => onCategoryChange?.(id)}
                className="skill-toolbar__cats"
              />
            )}
          </>
        ) : (
          categories && (
            <CategoryTabs
              categories={categories}
              value={category ?? ''}
              onChange={(id: string) => onCategoryChange?.(id)}
              className="skill-toolbar__cats"
            />
          )
        )}
      </div>
      <div className="skill-toolbar__right">
        {tagSlot}
        {rightSlot}
        {searchValue !== undefined && onSearchChange && (
          <SearchInput
            value={searchValue}
            onChange={onSearchChange}
            placeholder={searchPlaceholder ?? ''}
            className="skill-toolbar__search"
          />
        )}
        {onRefresh && (
          <Button variant="ghost" size="icon" onClick={onRefresh} disabled={refreshLoading} aria-label={refreshAriaLabel}>
            {refreshLoading ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
