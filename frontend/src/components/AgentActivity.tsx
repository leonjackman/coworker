import { CheckIcon, Loader2 } from 'lucide-react';
import { t } from '../lib/i18n';

interface AgentActivityProps {
  working: boolean;
}

export function AgentActivity({ working }: AgentActivityProps) {
  if (!working) return null;
  return (
    <div className="agent-activity">
      <Loader2 className="agent-activity__spinner" size={13} />
      <span>
        {t('agent.thinking')}
      </span>
    </div>
  );
}
