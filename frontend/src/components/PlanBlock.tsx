import { useState } from 'react';
import { ChevronDown, ChevronRight, Map } from 'lucide-react';
import { lazy, Suspense } from 'react';
import type { MessagePart } from '../types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

interface PlanBlockProps {
  planParts: MessagePart[];
  working: boolean;
}

export function PlanBlock({ planParts, working }: PlanBlockProps) {
  const [open, setOpen] = useState(true);
  const content = planParts.filter((p) => p.type === 'plan').map((p) => p.content).join('\n');

  if (!content && !working) return null;

  return (
    <div className="plan-block">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="plan-block__trigger">
          <span className="plan-block__icon">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
          <Map size={14} className="plan-block__sign" />
          <span className="plan-block__label">{working && !content ? 'Planning…' : 'Plan'}</span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="plan-block__body">
            {content ? (
              <Suspense fallback={<pre className="plan-block__text">{content}</pre>}>
                <MarkdownContent content={content} />
              </Suspense>
            ) : (
              <pre className="plan-block__text">...</pre>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
