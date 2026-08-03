import { Loader2 } from 'lucide-react';
import type { MessagePart } from '../types';
import { t } from '../lib/i18n';

interface AgentActivityProps {
  parts: MessagePart[];
  working: boolean;
}

export function AgentActivity({ parts, working }: AgentActivityProps) {
  if (!working) return null;
  const running = parts.filter((p) => p.type === 'tool' && p.status === 'running').length;
  return (
    <div className="agent-activity">
      <Loader2 className="agent-activity__spinner" size={13} />
      <span>
        {running > 0
          ? t('agent.running_tools', { count: running })
          : t('agent.thinking')}
      </span>
    </div>
  );
}
