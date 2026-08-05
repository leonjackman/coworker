import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/**
 * 底部卡片共用外壳（composer / 提问 / 回答 / 批准共用）。
 * 只负责统一外壳观感，不设固定高度：高度由内部容器撑起，内部容器顶满 slot 宽度。
 */
export function CardSlot({ className, ...props }: ComponentProps<'section'>) {
  return <section className={cn('card-slot', className)} {...props} />;
}
