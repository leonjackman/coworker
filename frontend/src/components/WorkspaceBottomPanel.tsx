import { useState } from 'react';
import type { RuntimeConfig } from '../types';
import { t } from '../lib/i18n';
import { chatService } from '../services/chatService';

export type BottomPanelView = 'terminal' | 'logs';

interface TerminalEntry {
  id: string;
  command: string;
  cwd: string;
  returnCode?: number | null;
  timedOut?: boolean;
  stdout?: string;
  stderr?: string;
  error?: string;
}

interface WorkspaceBottomPanelProps {
  view: BottomPanelView;
  runtimeStatus: 'connecting' | 'ready' | 'error';
  runtimeConfig?: RuntimeConfig | null;
  sessionCount: number;
  projectCount: number;
  messageCount: number;
  projectId?: string;
  workspaceLabel?: string;
  onViewChange: (view: BottomPanelView) => void;
}

export function WorkspaceBottomPanel({
  view,
  runtimeStatus,
  runtimeConfig,
  sessionCount,
  projectCount,
  messageCount,
  projectId,
  workspaceLabel,
  onViewChange,
}: WorkspaceBottomPanelProps) {
  const [command, setCommand] = useState('pwd');
  const [entries, setEntries] = useState<TerminalEntry[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  const runCommand = async () => {
    const nextCommand = command.trim();
    if (!nextCommand || isRunning) return;

    const entryId = `terminal-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setIsRunning(true);
    setEntries((current) => [
      ...current,
      {
        id: entryId,
        command: nextCommand,
        cwd: '.',
      },
    ]);

    try {
      const response = await chatService.runWorkspaceCommand({
        command: nextCommand,
        timeout_seconds: 20,
        ...(projectId ? { project_id: projectId } : {}),
      });
      setEntries((current) =>
        current.map((entry) =>
          entry.id === entryId
            ? {
                ...entry,
                cwd: response.result.cwd || '.',
                returnCode: response.result.return_code,
                timedOut: response.result.timed_out,
                stdout: response.result.stdout,
                stderr: response.result.stderr,
              }
            : entry,
        ),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setEntries((current) => current.map((entry) => (entry.id === entryId ? { ...entry, error: message } : entry)));
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <section className="bottom-panel">
      <div className="bottom-panel__tabs">
        <button
          type="button"
          className={view === 'terminal' ? 'bottom-panel__tab bottom-panel__tab--active' : 'bottom-panel__tab'}
          onClick={() => onViewChange('terminal')}
        >
          {t('bottom_panel.terminal')}
        </button>
        <button
          type="button"
          className={view === 'logs' ? 'bottom-panel__tab bottom-panel__tab--active' : 'bottom-panel__tab'}
          onClick={() => onViewChange('logs')}
        >
          {t('bottom_panel.logs')}
        </button>
      </div>
      <div className="bottom-panel__content">
        {view === 'terminal' ? (
          <div className="terminal-panel">
            <div className="terminal-panel__history" aria-live="polite">
              {entries.length === 0 && (
                <pre>{`$ coworker runtime\nstatus=${runtimeStatus}\nworkspace=${workspaceLabel || runtimeConfig?.workspace || 'unavailable'}\n\nAllowed examples: pwd, ls, rg \"TODO\", npm run build`}</pre>
              )}
              {entries.map((entry) => (
                <div className="terminal-entry" key={entry.id}>
                  <pre className="terminal-entry__command">{`$ ${entry.command}`}</pre>
                  {entry.error ? (
                    <pre className="terminal-entry__error">{entry.error}</pre>
                  ) : entry.returnCode === undefined ? (
                    <pre className="terminal-entry__muted">{t('bottom_panel.running')}</pre>
                  ) : (
                    <>
                      {(entry.stdout || entry.stderr) && (
                        <pre className={entry.returnCode === 0 ? '' : 'terminal-entry__error'}>{[entry.stdout, entry.stderr].filter(Boolean).join('\n')}</pre>
                      )}
                      {!entry.stdout && !entry.stderr && <pre className="terminal-entry__muted">{t('bottom_panel.no_output')}</pre>}
                      <pre className="terminal-entry__muted">
                        {`exit=${entry.returnCode ?? 'timeout'} cwd=${entry.cwd}${entry.timedOut ? ' timed_out=true' : ''}`}
                      </pre>
                    </>
                  )}
                </div>
              ))}
            </div>
            <form
              className="terminal-panel__prompt"
              onSubmit={(event) => {
                event.preventDefault();
                void runCommand();
              }}
            >
              <span>$</span>
              <input
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                placeholder={t('bottom_panel.command_placeholder')}
                disabled={isRunning}
              />
              <button type="submit" disabled={isRunning || !command.trim()}>
                {isRunning ? t('bottom_panel.running_short') : t('bottom_panel.run')}
              </button>
            </form>
          </div>
        ) : (
          <pre>{`runtime: ${runtimeStatus}\nsessions: ${sessionCount}\nprojects: ${projectCount}\nmessages: ${messageCount}`}</pre>
        )}
      </div>
    </section>
  );
}
