import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Loader2, Users, XCircle } from 'lucide-react';
import { t } from '../lib/i18n';
import type { PartAgent } from '../types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
import { OrderedParts } from './part-renderers';

interface AgentBlockProps {
  part: PartAgent;
  messageId?: string;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
}

function AgentBlockStatus({ part }: { part: PartAgent }) {
  const { status, chars, error, workerRunId, transcriptLoaded, done, parts } = part;
  if (status === 'running') {
    return (
      <>
        <Loader2 size={14} className="agent-block__spinner" />
        <span className="agent-block__status">{t('chat.delegate_running')}</span>
      </>
    );
  }
  if (status === 'error') {
    return (
      <>
        <XCircle size={14} />
        <span className="agent-block__status">{error || t('chat.delegate_failed')}</span>
      </>
    );
  }
  return (
    <>
      <Check size={14} />
      <span className="agent-block__status">
        {chars !== undefined ? `· ${chars} chars` : ''}
        {workerRunId && !done && transcriptLoaded && parts && parts.length > 0 ? ' · …' : ''}
      </span>
    </>
  );
}

/** Worker 内部转录用与主流相同的 OrderedParts 渲染（工具/推理/文本/计划块）。 */
function WorkerTranscript({ part }: { part: PartAgent }) {
  const { parts, transcriptLoaded, done, status } = part;
  if (transcriptLoaded && parts && parts.length > 0) {
    return (
      <OrderedParts
        parts={parts}
        running={!done && status === 'running'}
        isError={status === 'error'}
      />
    );
  }
  if (!transcriptLoaded) {
    return <div className="agent-block__placeholder">{t('chat.worker_loading')}</div>;
  }
  return null;
}

export function AgentBlock({ part, messageId, onSubscribeWorker }: AgentBlockProps) {
  const [open, setOpen] = useState(false);
  const { status, to, task, parallel, workerRunId, transcriptLoaded } = part;
  const targets = Array.isArray(to) ? to : [to];
  const label = parallel ? targets.join(', ') : targets[0] || 'Worker';

  useEffect(() => {
    if (open && workerRunId && !transcriptLoaded && onSubscribeWorker) {
      onSubscribeWorker(messageId ?? '', part);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, workerRunId, transcriptLoaded, onSubscribeWorker, messageId]);

  return (
    <div className={`agent-block agent-block--${status}`}>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="agent-block__trigger">
          <span className="agent-block__chevron">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <span className="agent-block__icon">
            <Users size={14} />
          </span>
          <span className="agent-block__title">{label}</span>
          <AgentBlockStatus part={part} />
        </CollapsibleTrigger>
        <CollapsibleContent>
          {task && <div className="agent-block__task">{task}</div>}
          <div className="agent-block__body">
            <WorkerTranscript part={part} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
