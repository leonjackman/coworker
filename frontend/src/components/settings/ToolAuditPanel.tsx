import { Download, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { AgentTraceEvent, CommandApproval, ToolAuditEvent } from '../../types';
import { Button } from '../ui/button';

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

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

export function ToolAuditPanel({ embedded = false }: { embedded?: boolean }) {
  const [events, setEvents] = useState<ToolAuditEvent[]>([]);
  const [traces, setTraces] = useState<AgentTraceEvent[]>([]);
  const [approvals, setApprovals] = useState<CommandApproval[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [resumeMessage, setResumeMessage] = useState('');
  const [traceLines, setTraceLines] = useState(100);
  const [auditLines, setAuditLines] = useState(100);

  async function loadRetention() {
    try {
      const r = await chatService.getRetentionSettings();
      setTraceLines(r.trace_lines);
      setAuditLines(r.audit_lines);
    } catch {
      // default 100/100
    }
  }

  async function exportTrace() {
    const text = await chatService.exportAgentTraces();
    downloadText('agent_trace.jsonl', text);
  }

  async function exportAudit() {
    const text = await chatService.exportToolAudit();
    downloadText('tool_audit.jsonl', text);
  }

  async function clearTrace() {
    await chatService.clearAgentTraces();
    await refreshAudit();
  }

  async function clearAudit() {
    await chatService.clearToolAudit();
    await refreshAudit();
  }

  async function saveRetention() {
    try {
      await chatService.saveRetentionSettings({ trace_lines: traceLines, audit_lines: auditLines });
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.audit_load_failed'));
    }
  }

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

  async function refreshApprovalsOnly() {
    try {
      const approvalsResponse = await chatService.listCommandApprovals();
      setApprovals(approvalsResponse.approvals);
    } catch {
      // ignore background poll failures
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
    void loadRetention();
    const timer = window.setInterval(() => {
      void refreshApprovalsOnly();
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="settings-group settings-audit" aria-labelledby={embedded ? undefined : 'settings-group-audit'}>
      {!embedded && (
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
      )}

      {embedded && (
        <div className="settings-audit__toolbar">
          <Button variant="secondary" onClick={refreshAudit} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'settings-audit__spin' : ''} />
            {t('settings.audit_refresh')}
          </Button>
        </div>
      )}

      <div className="settings-card settings-audit__card">
        <div className="settings-audit__retention">
          <label>
            {t('settings.retention_trace')}
            <input
              type="number"
              min={1}
              max={10000}
              value={traceLines}
              onChange={(e) => setTraceLines(Number(e.target.value))}
            />
          </label>
          <label>
            {t('settings.retention_audit')}
            <input
              type="number"
              min={1}
              max={10000}
              value={auditLines}
              onChange={(e) => setAuditLines(Number(e.target.value))}
            />
          </label>
          <Button variant="secondary" onClick={saveRetention}>{t('settings.retention_save')}</Button>
          <span className="settings-audit__retention-actions">
            <Button variant="secondary" onClick={exportTrace} title={t('settings.export_trace')}>
              <Download size={14} /> {t('settings.export_trace')}
            </Button>
            <Button variant="secondary" onClick={exportAudit} title={t('settings.export_audit')}>
              <Download size={14} /> {t('settings.export_audit')}
            </Button>
            <Button variant="ghost" onClick={clearTrace} title={t('settings.clear_trace')}>
              <Trash2 size={14} /> {t('settings.clear_trace')}
            </Button>
            <Button variant="ghost" onClick={clearAudit} title={t('settings.clear_audit')}>
              <Trash2 size={14} /> {t('settings.clear_audit')}
            </Button>
          </span>
        </div>
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
