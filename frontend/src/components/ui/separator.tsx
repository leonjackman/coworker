import * as SeparatorPrimitive from '@radix-ui/react-separator';
import { cn } from '../../lib/utils';

interface SeparatorProps {
  className?: string;
}

export function Separator({ className }: SeparatorProps) {
  return <SeparatorPrimitive.Root className={cn('h-px w-full bg-[var(--material-border)]', className)} />;
}
