import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area';
import type { ReactNode, Ref } from 'react';
import { cn } from '../../lib/utils';

interface ScrollAreaProps {
  children: ReactNode;
  className?: string;
  viewportRef?: Ref<HTMLDivElement>;
  onViewportScroll?: () => void;
}

export function ScrollArea({ children, className, viewportRef, onViewportScroll }: ScrollAreaProps) {
  return (
    <ScrollAreaPrimitive.Root className={cn('overflow-hidden', className)}>
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        className="h-full w-full"
        onScroll={onViewportScroll}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar className="flex touch-none select-none bg-transparent p-0.5" orientation="vertical">
        <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-[var(--scroll-thumb)]" />
      </ScrollAreaPrimitive.Scrollbar>
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}
