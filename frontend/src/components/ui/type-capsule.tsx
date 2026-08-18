import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';
import './type-capsule.css';

export type SlashCommandType = 'sys' | 'skill' | 'mcp';

export const TYPE_CAPSULE_LABELS: Record<SlashCommandType, string> = {
  sys: 'sys',
  skill: 'skill',
  mcp: 'mcp',
};

interface TypeCapsuleProps extends HTMLAttributes<HTMLSpanElement> {
  /** Command source type; drives the capsule color (defined in type-capsule.css). */
  type: SlashCommandType;
  /** Slot text shown inside the capsule. */
  children?: ReactNode;
}

export function TypeCapsule({ type, className, children, ...rest }: TypeCapsuleProps) {
  return (
    <span className={cn('type-capsule', `type-capsule--${type}`, className)} {...rest}>
      {children}
    </span>
  );
}
