import { RefreshCw, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { AgentTraceEvent, CommandApproval, ToolAuditEvent } from '../../types';
import { Button } from '../ui/button';

function formatAuditTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function summarizeDetails(details?: Record<string, unknown>): string {
  if (!details) return '';
  const parts = [
    typeof details.path === 'string' ? details.path : '',
    Array.isArray(details.command) ? details.command.join(' ') : '',
    typeof details.return_code === 'number' ? `exit ${details.return_code}` : '',
    details.timed_out === true ? 'timeout' : '',
    typeof details.error === 'string' ? details.error : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

function contextLabel(context?: Record<string, unknown>): string {
  if (!context) return '';
  const provider = typeof context.provider === 'string' ? context.provider : '';
  const model = typeof context.model === 'string' ? context.model : '';
  return [provider, model].filter(Boolean).join(' / ');
}

function summarizeTrace(event: AgentTraceEvent): string {
  const details = event.details || {};
  const parts = [
    typeof details.stage === 'string' ? details.stage : '',
    Array.isArray(details.approval_ids) ? `approvals ${details.approval_ids.join(', ')}` : '',
    typeof details.approval_id === 'string' ? `approval ${details.approval_id}` : '',
    typeof details.content_chars === 'number' ? `${details.content_chars} chars` : '',
    typeof details.error === 'string' ? details.error : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

export function ToolAuditPanel() {
  const [events, setEvents] = useState<ToolAuditEvent[]>([]);
  const [traces, setTraces] = useState<AgentTraceEvent[]>([]);
  const [approvals, setApprovals] = useState<CommandApproval[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [resumeMessage, setResumeMessage] = useState('');

  async function refreshAudit() {
    setLoading(true);
    setError('');
    try {
      const [auditResponse, traceResponse, approvalsResponse] = await Promise.all([
        chatService.listToolAudit(80),
        chatService.listAgentTraces(80),
        chatService.listCommandApprovals(),
      ]);
      setEvents(auditResponse.events);
      setTraces(traceResponse.events);
      setApprovals(approvalsResponse.approvals);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.audit_load_failed'));
    } finally {
      setLoading(false);
    }
  }

  async function updateApproval(approvalId: string, action: 'approve' | 'deny') {
    setLoading(true);
    setError('');
    try {
      setResumeMessage('');
      const response = await chatService.resolveCommandApproval(approvalId, { type: action === 'approve' ? 'approve' : 'reject' });
      const done = response.events?.findLast((event) => event.type === 'done');
      setResumeMessage(done?.content || '');
      await refreshAudit();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.command_approval_failed'));
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshAudit();
  }, []);

  return (
    <section className="settings-group settings-audit" aria-labelledby="settings-group-audit">
      <div className="settings-group__heading settings-audit__heading">
        <div>
          <h2 id="settings-group-audit">{t('settings.audit_group')}</h2>
          <p>{t('settings.audit_group_desc')}</p>
        </div>
        <Button variant="secondary" onClick={refreshAudit} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'settings-audit__spin' : ''} />
          {t('settings.audit_refresh')}
        </Button>
      </div>

      <div className="settings-card settings-audit__card">
        {error && <div className="settings-audit__empty">{error}</div>}
        {!error && resumeMessage && (
          <article className="settings-audit__event settings-audit__event--approval">
            <div className="settings-audit__event-top">
              <strong>{t('settings.command_resume_complete')}</strong>
              <span className="settings-audit__status settings-audit__status--success">done</span>
            </div>
            <p>{resumeMessage}</p>
          </article>
        )}
        {!error && approvals.filter((approval) => approval.status === 'pending').map((approval) => (
          <article className="settings-audit__event settings-audit__event--approval" key={approval.id}>
            <div className="settings-audit__event-top">
              <strong>{t('settings.command_approval_required')}</strong>
              <span className="settings-audit__status settings-audit__status--pending">{approval.status}</span>
            </div>
            <div className="settings-audit__event-meta">
              <span>{formatAuditTime(approval.updated_at)}</span>
              <span>{approval.cwd || '.'}</span>
            </div>
            <p>{approval.command.join(' ')}</p>
            <div className="settings-audit__actions">
              <Button variant="secondary" onClick={() => updateApproval(approval.id, 'deny')} disabled={loading}>
                {t('settings.command_deny')}
              </Button>
              <Button onClick={() => updateApproval(approval.id, 'approve')} disabled={loading}>
                {t('settings.command_approve_once')}
              </Button>
            </div>
          </article>
        ))}
        {!error && events.length === 0 && approvals.filter((approval) => approval.status === 'pending').length === 0 && (
          traces.length === 0 && (
          <div className="settings-audit__empty">
            <ShieldCheck size={18} />
            <span>{loading ? t('settings.audit_loading') : t('settings.audit_empty')}</span>
          </div>
          )
        )}
        {!error && traces.length > 0 && (
          <div className="settings-audit__section">
            <h3>{t('settings.trace_group')}</h3>
            {traces.map((trace, index) => {
              const context = contextLabel(trace.context);
              const detail = summarizeTrace(trace);
              return (
                <article className="settings-audit__event" key={`${trace.timestamp}-${trace.event}-${index}`}>
                  <div className="settings-audit__event-top">
                    <strong>{trace.event}</strong>
                    <span className={`settings-audit__status settings-audit__status--${trace.status}`}>{trace.status}</span>
                  </div>
                  <div className="settings-audit__event-meta">
                    <span>{formatAuditTime(trace.timestamp)}</span>
                    {context && <span>{context}</span>}
                  </div>
                  {detail && <p>{detail}</p>}
                </article>
              );
            })}
          </div>
        )}
        {!error && events.length > 0 && (
          <div className="settings-audit__section">
            <h3>{t('settings.tool_audit_group')}</h3>
        {!error && events.map((event, index) => {
          const detail = summarizeDetails(event.details);
          const context = contextLabel(event.context);
          return (
            <article className="settings-audit__event" key={`${event.timestamp}-${event.operation}-${index}`}>
              <div className="settings-audit__event-top">
                <strong>{event.operation}</strong>
                <span className={`settings-audit__status settings-audit__status--${event.status}`}>{event.status}</span>
              </div>
              <div className="settings-audit__event-meta">
                <span>{formatAuditTime(event.timestamp)}</span>
                {context && <span>{context}</span>}
              </div>
              {detail && <p>{detail}</p>}
            </article>
          );
        })}
          </div>
        )}
      </div>
    </section>
  );
}
