import type { PartTool } from '../types';
import type { ToolCallMessagePartStatus } from '@assistant-ui/react';
import { ToolFallback } from './assistant-ui/tool-fallback';
import { FileChangesInline } from './FileChangesCard';
import { toolLabel, toolPreview } from './toolMeta';

function mapStatus(tool: PartTool): ToolCallMessagePartStatus {
  if (tool.status === 'running') return { type: 'running' };
  if (tool.status === 'pending') return { type: 'requires-action', reason: 'interrupt' };
  if (tool.status === 'error') return { type: 'incomplete', reason: 'error', error: tool.output };
  return { type: 'complete' };
}

function tryParse(input: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(input || '{}');
    return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function renderQuestionAnswer(tool: PartTool) {
  const args = tryParse(tool.input);
  if (!args) return null;
  const question = typeof args.question === 'string' ? args.question : '';
  if (!question) return null;

  return (
    <div className="ask-user-card">
      <div className="ask-user-item">
        <div className="ask-user-label">Question</div>
        <div className="ask-user-text">{question}</div>
      </div>
      {tool.output && (
        <div className="ask-user-item">
          <div className="ask-user-label">Answer</div>
          <div className="ask-user-text">{tool.output.slice(0, 2000)}</div>
        </div>
      )}
    </div>
  );
}

export function ToolCallCard({ tool }: { tool: PartTool }) {
  const status = mapStatus(tool);
  const done = tool.status === 'success';
  const argsText = formatTryJson(tool.input);

  if (tool.name === 'ask_user') {
    return (
      <div className="tool-call-card">
        <div className="tool-call-card__header">
          <div className="tool-call-card__title">
            <span className="tool-call-card__icon">
              {tool.status === 'running' ? '🔄' : tool.status === 'pending' ? '⏳' : tool.status === 'error' ? '✕' : '✓'}
            </span>
            <span className="tool-call-card__label">{toolLabel(tool.name)}</span>
          </div>
        </div>
        <div className="tool-call-card__body">
          {renderQuestionAnswer(tool)}
          {tool.files && tool.files.length > 0 && <FileChangesInline files={tool.files} />}
        </div>
      </div>
    );
  }

  return (
    <ToolFallback.Root>
      <ToolFallback.Trigger
        toolName={toolLabel(tool.name)}
        status={status}
        {...(tool.duration_ms !== undefined ? { durationMs: tool.duration_ms } : {})}
        preview={toolPreview(tool.name, tool.input)}
      />
      <ToolFallback.Content>
        <ToolFallback.Args argsText={argsText} />
        {tool.files && tool.files.length > 0 && <FileChangesInline files={tool.files} />}
        {done && tool.output ? <ToolFallback.Result result={tool.output} /> : null}
      </ToolFallback.Content>
    </ToolFallback.Root>
  );
}

function formatTryJson(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
  }
}
