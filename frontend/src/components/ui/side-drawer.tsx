import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SideDrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}

/**
 * Generic right side slide-in drawer rendered in a portal.
 * Closes on ESC and on overlay click. Reusable for "managed items" pages.
 */
export function SideDrawer({ open, onClose, title, children, footer, width = 380 }: SideDrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className={cn('side-drawer-overlay', open && 'side-drawer-overlay--open')} onClick={onClose}>
      <aside className="side-drawer" style={{ width }} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="side-drawer__header">
          <div className="side-drawer__title">{title}</div>
          <button type="button" className="side-drawer__close" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </button>
        </div>
        <div className="side-drawer__body">{children}</div>
        {footer != null && <div className="side-drawer__footer">{footer}</div>}
      </aside>
    </div>,
    document.body,
  );
}
