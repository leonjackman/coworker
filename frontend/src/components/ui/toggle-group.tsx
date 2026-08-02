import * as ToggleGroupPrimitive from '@radix-ui/react-toggle-group';
import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

interface ToggleGroupProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  items: Array<{ value: T; label: ReactNode; title?: string }>;
  className?: string;
}

export function ToggleGroup<T extends string>({ value, onValueChange, items, className }: ToggleGroupProps<T>) {
  return (
    <ToggleGroupPrimitive.Root
      type="single"
      value={value}
      onValueChange={(nextValue) => {
        if (nextValue) onValueChange(nextValue as T);
      }}
      className={cn('cw-toggle inline-flex', className)}
    >
      {items.map((item) => (
        <ToggleGroupPrimitive.Item
          key={item.value}
          value={item.value}
          title={item.title}
          className="cw-toggle__item"
        >
          {item.label}
        </ToggleGroupPrimitive.Item>
      ))}
    </ToggleGroupPrimitive.Root>
  );
}
