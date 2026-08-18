import type { ReactNode } from 'react';
import './sidebar-scrollbar.css';

interface SidebarScrollbarProps {
  children: ReactNode;
}

/**
 * Zero-DOM wrapper. Imports sidebar-scrollbar.css so that any ancestor
 * using .sidebar__scroll gets the styled scrollbar styles.
 * The component itself renders nothing — children are used as React fragment children.
 */
export function SidebarScrollbar({ children }: SidebarScrollbarProps) {
  return <>{children}</>;
}
