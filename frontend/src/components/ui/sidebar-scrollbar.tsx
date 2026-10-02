import type { ReactNode, Ref, UIEventHandler } from 'react';
import { cn } from '../../lib/utils';
import './sidebar-scrollbar.css';

interface SidebarScrollbarProps {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  onScroll?: UIEventHandler<HTMLDivElement>;
  ref?: Ref<HTMLDivElement>;
  id?: string;
  role?: string;
  'aria-label'?: string;
}

/**
 * Scrollable wrapper with thin scrollbar.
 * Applies overflow-y: auto so content can scroll.
 */
export function SidebarScrollbar({
  children,
  className,
  style,
  onScroll,
  ref,
  id,
  role,
  'aria-label': ariaLabel,
}: SidebarScrollbarProps) {
  return (
    <div
      className={cn('sidebar-scrollbar', className)}
      style={{ ...style, overflowY: 'auto' }}
      onScroll={onScroll}
      ref={ref}
      id={id}
      role={role}
      aria-label={ariaLabel}
    >
      {children}
    </div>
  );
}
