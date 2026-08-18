import type { ReactNode, Ref, UIEventHandler } from 'react';
import { cn } from '../../lib/utils';
import './sidebar-scrollbar.css';

interface SidebarScrollbarProps {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  onScroll?: UIEventHandler<HTMLDivElement>;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Scrollable wrapper with thin scrollbar.
 * Applies overflow-y: auto so content can scroll.
 */
export function SidebarScrollbar({ children, className, style, onScroll, ref }: SidebarScrollbarProps) {
  return (
    <div
      className={cn('sidebar-scrollbar', className)}
      style={{ ...style, overflowY: 'auto' }}
      onScroll={onScroll}
      ref={ref}
    >
      {children}
    </div>
  );
}
