import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface DetailModalProps {
  open: boolean;
  onClose: () => void;
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}

/**
 * Generic centered modal rendered in a portal.
 * Closes on ESC and on overlay click. Reusable for any item-detail popup.
 */
export function DetailModal({ open, onClose, icon, title, subtitle, children, footer, className }: DetailModalProps) {
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
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className={cn('modal', className)} onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <div className="modal__heading">
            {icon != null && <div className="modal__icon">{icon}</div>}
            <div className="modal__titles">
              <div className="modal__title">{title}</div>
              {subtitle != null && <div className="modal__subtitle">{subtitle}</div>}
            </div>
          </div>
          <button type="button" className="modal__close" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </button>
        </div>
        <div className="modal__body">{children}</div>
        {footer != null && <div className="modal__footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
