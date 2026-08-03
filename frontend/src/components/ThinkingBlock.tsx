import { useMemo } from 'react';
import type { MessagePart } from '../types';
import { Reasoning } from './assistant-ui/reasoning';

interface ThinkingBlockProps {
  reasoningParts: MessagePart[];
  working: boolean;
}

export function ThinkingBlock({ reasoningParts, working }: ThinkingBlockProps) {
  const content = useMemo(
    () => reasoningParts.filter((p) => p.type === 'reasoning').map((p) => p.content).join('\n'),
    [reasoningParts],
  );
  const heading = useMemo(() => {
    const part = reasoningParts.find((p) => p.type === 'reasoning' && p.heading);
    return part && part.type === 'reasoning' ? part.heading : undefined;
  }, [reasoningParts]);

  if (!content && !working) return null;

  return (
    <Reasoning.Root variant="muted" streaming={working} defaultOpen={false}>
      <Reasoning.Trigger active={working} {...(heading ? { summary: heading } : {})} />
      <Reasoning.Content>
        <Reasoning.Text>
          <pre className="reasoning-content">{content || '...'}</pre>
        </Reasoning.Text>
      </Reasoning.Content>
    </Reasoning.Root>
  );
}
