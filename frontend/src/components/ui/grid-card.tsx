import type { KeyboardEvent, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface GridCardProps {
  icon?: ReactNode;
  title: string;
  subtitle?: ReactNode;
  description?: ReactNode;
  trailing?: ReactNode;
  footer?: ReactNode;
  onClick?: () => void;
  added?: boolean;
  disabled?: boolean;
  className?: string;
}

/**
 * Generic catalog card used in marketplace / gallery style grids.
 * Slot-based: `icon`, `trailing` (action) and `footer` (tags/status) accept
 * any content, so it is reusable beyond skills (extensions, templates…).
 */
export function GridCard({
  icon,
  title,
  subtitle,
  description,
  trailing,
  footer,
  onClick,
  added,
  disabled,
  className,
}: GridCardProps) {
  const clickable = Boolean(onClick) && !disabled;

  const handleKey = (e: KeyboardEvent<HTMLElement>) => {
    if (!clickable) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick?.();
    }
  };

  return (
    <article
      className={cn(
        'grid-card',
        added && 'grid-card--added',
        disabled && 'grid-card--disabled',
        clickable && 'grid-card--clickable',
        className,
      )}
      onClick={clickable ? onClick : undefined}
      onKeyDown={clickable ? handleKey : undefined}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-disabled={disabled || undefined}
    >
      <div className="grid-card__header">
        {icon != null && <div className="grid-card__icon">{icon}</div>}
        <div className="grid-card__meta">
          <div className="grid-card__title">{title}</div>
          {subtitle != null && <div className="grid-card__subtitle">{subtitle}</div>}
        </div>
        {trailing != null && (
          <div className="grid-card__trailing" onClick={(e) => e.stopPropagation()}>
            {trailing}
          </div>
        )}
      </div>
      {description != null && <p className="grid-card__desc">{description}</p>}
      {footer != null && <div className="grid-card__footer">{footer}</div>}
    </article>
  );
}
