import { ChevronDown, ChevronRight, Map } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import type { MessagePart } from '../types';

export function PlanBlock({ planParts, working }: { planParts: MessagePart[]; working: boolean }) {
  const [open, setOpen] = useState(true);
  const content = planParts.map((p) => (p.type === 'plan' ? p.content : '')).join('\n');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (working && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [content, working]);

  if (!content && !working) return null;

  return (
    <div className="agent-block agent-block--plan">
      <button
        type="button"
        className="agent-block__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="agent-block__toggle-icon">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <Map size={14} className="agent-block__toggle-sign" />
        <span className="agent-block__toggle-label">
          {working && !content ? 'Planning…' : 'Plan'}
        </span>
      </button>
      {open && (
        <div className="agent-block__body" ref={containerRef}>
          <pre className="agent-block__text">{content || '...'}</pre>
        </div>
      )}
    </div>
  );
}
