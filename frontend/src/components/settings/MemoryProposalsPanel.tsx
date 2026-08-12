import { Check, Loader2, RefreshCw, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t, translateError } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { MemoryProposalRecord } from '../../types';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';

type Flash = { kind: 'ok' | 'error'; text: string } | null;

export function MemoryProposalsPanel({ embedded = false }: { embedded?: boolean }) {
  const [proposals, setProposals] = useState<MemoryProposalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [flash, setFlash] = useState<Flash>(null);

  const notify = (kind: 'ok' | 'error', text: string) => {
    setFlash({ kind, text });
    window.setTimeout(() => setFlash(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await chatService.listMemoryProposals();
      setProposals(res.proposals);
    } catch (error) {
      notify('error', translateError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (id: string, decision: 'approved' | 'rejected') => {
    try {
      await chatService.resolveMemoryProposal({ proposal_id: id, status: decision });
      notify('ok', decision === 'approved' ? t('memory.proposal.approved') : t('memory.proposal.rejected'));
      await load();
    } catch (error) {
      notify('error', translateError(error));
    }
  };

  return (
    <div className={embedded ? 'memory-proposals-panel' : ''}>
      {flash && (
        <div className={`memory-flash memory-flash--${flash.kind}`} role="status">
          {flash.text}
        </div>
      )}
      <div className="memory-proposals-panel__toolbar">
        <Button variant="outline" size="sm" onClick={() => void load()}>
          {loading ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />}
          {t('settings.audit_refresh')}
        </Button>
      </div>
      {loading ? (
        <div className="memory-loading">
          <Loader2 className="animate-spin" size={18} />
          <span>{t('common.loading')}</span>
        </div>
      ) : proposals.length === 0 ? (
        <div className="memory-proposals__empty">{t('memory.proposals_empty')}</div>
      ) : (
        <ul className="memory-proposal-list">
          {proposals.map((p) => (
            <li key={p.id} className="memory-proposal">
              <div className="memory-proposal__body">
                <span className="memory-proposal__text">{p.text}</span>
                <Badge variant="secondary">{p.provider || t('memory.proposal.provider_unknown')}</Badge>
              </div>
              <div className="memory-proposal__actions">
                <Button variant="outline" size="sm" onClick={() => void decide(p.id, 'approved')}>
                  <Check size={13} />
                  {t('memory.proposal.approve')}
                </Button>
                <Button variant="destructive" size="sm" onClick={() => void decide(p.id, 'rejected')}>
                  <X size={13} />
                  {t('memory.proposal.reject')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
