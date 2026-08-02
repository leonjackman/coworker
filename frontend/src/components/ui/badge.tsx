import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'secondary' | 'outline' | 'success' | 'warning' | 'destructive';
}

const variants: Record<NonNullable<BadgeProps['variant']>, string> = {
  default: 'bg-[var(--accent)] text-[var(--accent-foreground)]',
  secondary: 'bg-[var(--material-control)] text-[var(--foreground)]',
  outline: 'border border-[var(--material-border)] text-[var(--foreground)]',
  success: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-100',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100',
  destructive: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-100',
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
