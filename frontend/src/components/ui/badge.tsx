import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'secondary' | 'outline' | 'success' | 'warning' | 'destructive';
}

const variants: Record<NonNullable<BadgeProps['variant']>, string> = {
  default: 'bg-[var(--accent)] text-[var(--accent-foreground)]',
  secondary: 'bg-[var(--material-control)] text-[var(--foreground)]',
  outline: 'border border-[var(--material-border)] text-[var(--foreground)]',
  success: 'badge--success',
  warning: 'badge--warning',
  destructive: 'badge--destructive',
};

export function Badge({ className, variant = 'secondary', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-sm px-2 py-0.5 text-xs font-medium transition-colors',
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
