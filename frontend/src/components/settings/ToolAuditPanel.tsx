import { Download, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { ApprovalDecisionPayload, ApprovalOption, CommandApproval, PendingRequest } from '../../types';
import { Button } from '../ui/button';
import { PendingDocks } from '../PendingDocks';

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  // Defer revocation so the download actually starts before the blob is released.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Backend `CommandApproval` → UI `PendingRequest`, mirroring the mapping the
 * main chat does in `restorePendingForSession` (and the backend's own
 * `stream_event_from_interrupt`), so the settings page renders the exact same
 * cards as the chat area — for command / question / plan / mcp alike.
 */
function approvalToPending(approval: CommandApproval): PendingRequest {
  const context = isRecord(approval.context) ? approval.context : {};
  const kind: PendingRequest['kind'] =
    context.kind === 'question' || context.kind === 'mcp' || context.kind === 'plan' ? context.kind : 'command';
  const base: PendingRequest = {
    approval_id: approval.id,
    kind,
    session_id: typeof context.session_id === 'string' ? context.session_id : '',
    approval_status: approval.status,
    messageId: '',
  };
  if (kind === 'question') {
    const args = isRecord(context.action_args) ? context.action_args : {};
    return {
      ...base,
      ...(typeof args.question === 'string' ? { question: args.question } : {}),
      ...(typeof args.header === 'string' ? { header: args.header } : {}),
      ...(Array.isArray(args.options) ? { options: args.options as ApprovalOption[] } : {}),
      ...(typeof args.multiple === 'boolean' ? { multiple: args.multiple } : {}),
    };
  }
  if (kind === 'plan') {
    const args = isRecord(context.action_args) ? context.action_args : {};
    return {
      ...base,
      ...(typeof args.plan_text === 'string' ? { plan: args.plan_text } : {}),
    };
  }
  if (kind === 'mcp') {
    const args = isRecord(context.action_args) ? context.action_args : {};
    const mcp = isRecord(context.mcp) ? context.mcp : {};
    const annotations = isRecord(mcp.annotations) ? mcp.annotations : {};
    return {
      ...base,
      ...(typeof context.tool_name === 'string' ? { tool_name: context.tool_name } : {}),
      ...(isRecord(args) && Object.keys(args).length > 0 ? { tool_args: args } : {}),
      ...(typeof mcp.server_name === 'string' ? { server_name: mcp.server_name } : {}),
      ...(typeof mcp.server_id === 'string' ? { server_id: mcp.server_id } : {}),
      ...(typeof mcp.remote_name === 'string' ? { remote_name: mcp.remote_name } : {}),
      ...(mcp.read_only === true ? { read_only: true } : {}),
      ...(annotations.destructive === true ? { destructive: true } : {}),
    };
  }
  return {
    ...base,
    command: Array.isArray(approval.command) ? approval.command : [],
    ...(approval.cwd ? { cwd: approval.cwd } : {}),
    ...(typeof context.tool_name === 'string' && context.tool_name ? { tool_name: context.tool_name } : {}),
    ...(isRecord(context.action_args) && Object.keys(context.action_args).length > 0 ? { tool_args: context.action_args } : {}),
  };
}

type PanelTab = 'pending' | 'logs';

export function ToolAuditPanel() {
  const [tab, setTab] = useState<PanelTab>('pending');
  const [approvals, setApprovals] = useState<CommandApproval[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [flash, setFlash] = useState('');
  // Logs
  const [logLevel, setLogLevel] = useState('INFO');
  const [jsonLog, setJsonLog] = useState(true);
  const [logFilePath, setLogFilePath] = useState('');
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logTotalLines, setLogTotalLines] = useState(0);
  const [logLoading, setLogLoading] = useState(false);
  const logPreRef = useRef<HTMLPreElement>(null);
  // Audit retention
  const [auditLines, setAuditLines] = useState(100);

  const pending = useMemo(
    () => approvals.filter((approval) => approval.status === 'pending').map(approvalToPending),
    [approvals],
  );

  async function refreshApprovals() {
    setLoading(true);
    try {
      const response = await chatService.listCommandApprovals();
      setApprovals(response.approvals);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.audit_load_failed'));
    } finally {
      setLoading(false);
    }
  }

  async function loadRetention() {
    try {
      const r = await chatService.getRetentionSettings();
      setAuditLines(r.audit_lines);
    } catch {
      // default 100
    }
  }

  async function saveRetention() {
    const audit = Number.isFinite(auditLines) && auditLines >= 1 ? Math.floor(auditLines) : 100;
    setAuditLines(audit);
    try {
      await chatService.saveRetentionSettings({ audit_lines: audit });
      setFlash(t('settings.retention_saved'));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.audit_load_failed'));
    }
  }

  async function refreshLogConfig() {
    setLogLoading(true);
    try {
      const settings = await chatService.getLogSettings();
      setLogLevel(settings.log_level);
      setJsonLog(settings.json_log);
      setLogFilePath(settings.log_file);
      setLogLines([]);
      setError('');
    } catch {
      // ignore
    } finally {
      setLogLoading(false);
    }
  }

  async function fetchLogLines() {
    try {
      const res = await chatService.readLogFile(0, 200);
      setLogLines(res.lines);
      setLogTotalLines(res.total_lines);
    } catch {
      // ignore
    }
  }

  async function handleLogLevelChange(newLevel: string) {
    try {
      await chatService.setLogLevel(newLevel);
      setLogLevel(newLevel);
      setFlash(newLevel === 'DEBUG' ? t('settings.log_level_debug_hint') : t('settings.log_level_saved'));
    } catch {
      // ignore
    }
  }

  async function clearLog() {
    if (!window.confirm(t('settings.log_clear_confirm'))) return;
    try {
      // maxBytes=0 tells the backend to clear the log file completely.
      await chatService.truncateLog(0);
      setLogLines([]);
      setLogTotalLines(0);
      setFlash(t('settings.log_cleared'));
    } catch {
      // ignore
    }
  }

  async function exportAudit() {
    const text = await chatService.exportToolAudit();
    downloadText('tool_audit.jsonl', text);
  }

  async function clearAudit() {
    if (!window.confirm(t('settings.audit_clear_confirm'))) return;
    await chatService.clearToolAudit();
    setFlash(t('settings.audit_action_done'));
  }

  async function handleResolve(request: PendingRequest, decision: ApprovalDecisionPayload) {
    try {
      await chatService.resolveCommandApproval(request.approval_id, decision);
      setFlash(t('settings.audit_action_done'));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.audit_load_failed'));
    } finally {
      await refreshApprovals();
    }
  }

  // Auto-expire the transient "done" feedback.
  useEffect(() => {
    if (!flash) return;
    const timer = window.setTimeout(() => setFlash(''), 3000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  useEffect(() => {
    void refreshApprovals();
    void loadRetention();
    void refreshLogConfig();
  }, []);

  // Global to-do center: keep the pending list fresh while this tab is visible.
  useEffect(() => {
    if (tab !== 'pending') return;
    const timer = window.setInterval(() => void refreshApprovals(), 5000);
    return () => window.clearInterval(timer);
  }, [tab]);

  // Entering the logs tab auto-loads the latest lines — the first thing a user
  // opening "logs" wants to see is the log, not a button.
  useEffect(() => {
    if (tab !== 'logs') return;
    void fetchLogLines();
  }, [tab]);

  // Keep the newest line visible after load/refresh.
  useEffect(() => {
    if (tab !== 'logs' || logLines.length === 0) return;
    const el = logPreRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [tab, logLines]);

  return (
    <section className="settings-group settings-audit" aria-label={t('settings.audit_group')}>
      <div className="settings-audit__tabs">
        <button
          type="button"
          className={tab === 'pending' ? 'settings-audit__tab settings-audit__tab--active' : 'settings-audit__tab'}
          onClick={() => setTab('pending')}
        >
          {t('settings.audit_tabs_pending')}
          {pending.length > 0 ? ` · ${pending.length}` : ''}
        </button>
        <button
          type="button"
          className={tab === 'logs' ? 'settings-audit__tab settings-audit__tab--active' : 'settings-audit__tab'}
          onClick={() => setTab('logs')}
        >
          {t('settings.audit_tabs_logs')}
        </button>
      </div>

      {error && <div className="settings-audit__banner settings-audit__banner--error">{error}</div>}
      {!error && flash && <div className="settings-audit__banner">{flash}</div>}

      {tab === 'pending' ? (
        <div className="settings-audit__card">
          {pending.length === 0 ? (
            <div className="settings-audit__empty">
              <ShieldCheck size={18} />
              <span>{loading ? t('settings.audit_loading') : t('settings.audit_pending_none')}</span>
            </div>
          ) : (
            <>
              <p className="settings-audit__pending-hint">{t('settings.audit_pending_desc')}</p>
              <PendingDocks
                requests={pending}
                onResolve={handleResolve}
                onDismiss={(request) => void handleResolve(request, { type: 'reject' })}
              />
            </>
          )}
        </div>
      ) : (
        <div className="settings-audit__card">
          <div className="settings-audit__group">
            <div className="settings-audit__group-head">
              <h3>{t('settings.logging')}</h3>
            </div>
            <div className="settings-audit__row">
              <label htmlFor="log-level-select">{t('settings.log_level')}</label>
              <select
                id="log-level-select"
                className="settings-input"
                value={logLevel}
                onChange={(e) => handleLogLevelChange(e.target.value)}
                disabled={logLoading}
              >
                {['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((level) => (
                  <option key={level} value={level}>{level}</option>
                ))}
              </select>
              <span className="settings-audit__hint">{t('settings.log_level_hint')}</span>
            </div>
            {logFilePath && (
              <div className="settings-audit__meta">
                {t('settings.log_file')}: {logFilePath}
                {jsonLog && <span> (JSON)</span>}
                {logTotalLines > 0 && logLines.length > 0 && <span> · {t('settings.log_range', { total: logTotalLines, count: logLines.length })}</span>}
              </div>
            )}
            <div className="settings-audit__row">
              <Button variant="secondary" size="sm" onClick={fetchLogLines} disabled={logLoading}>
                <RefreshCw size={14} />
                {t('settings.load_log')}
              </Button>
              <Button variant="ghost" size="sm" onClick={clearLog}>
                <Trash2 size={14} />
                {t('settings.clear_log')}
              </Button>
            </div>
            {logLines.length > 0 && (
              <pre ref={logPreRef} className="settings-audit__log">{logLines.join('\n')}</pre>
            )}
          </div>

          <div className="settings-audit__group">
            <div className="settings-audit__group-head">
              <h3>{t('settings.audit_records')}</h3>
            </div>
            <div className="settings-audit__row">
              <label>{t('settings.retention_audit')}</label>
              <input
                type="number"
                min={1}
                max={10000}
                value={auditLines}
                onChange={(e) => setAuditLines(Number(e.target.value))}
                className="settings-audit__number"
              />
              <Button variant="secondary" size="sm" onClick={saveRetention}>
                {t('settings.retention_save')}
              </Button>
            </div>
            <p className="settings-audit__meta">{t('settings.retention_audit_desc')}</p>
            <div className="settings-audit__row">
              <Button variant="secondary" size="sm" onClick={exportAudit} title={t('settings.export_audit')}>
                <Download size={14} />
                {t('settings.export_audit')}
              </Button>
              <Button variant="ghost" size="sm" onClick={clearAudit} title={t('settings.clear_audit')}>
                <Trash2 size={14} />
                {t('settings.clear_audit')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
