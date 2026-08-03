import { ChevronDown, ChevronRight, BrainCircuit } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';
import type { MessagePart } from '../types';

export function ThinkingBlock({ reasoningParts, working }: { reasoningParts: MessagePart[]; working: boolean }) {
  const [open, setOpen] = useState(false);
  const content = reasoningParts.map((p) => (p.type === 'reasoning' ? p.content : '')).join('\n');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (working && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [content, working]);

  if (!content && !working) return null;

  return (
    <div className="agent-block agent-block--thinking">
      <button
        type="button"
        className="agent-block__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="agent-block__toggle-icon">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <BrainCircuit size={14} className="agent-block__toggle-sign" />
        <span className="agent-block__toggle-label">
          {working && !content ? 'Thinking…' : 'Thinking'}
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
