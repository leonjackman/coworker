import type { TextareaHTMLAttributes } from 'react';
import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          'min-h-24 w-full resize-none rounded-lg border border-[var(--material-border)] bg-[var(--material-control)] px-4 py-3 text-sm leading-6 text-[var(--foreground)] outline-none transition-colors placeholder:text-[var(--muted-foreground)] focus:border-[var(--ring)]',
          className,
        )}
        {...props}
      />
    );
  },
);
