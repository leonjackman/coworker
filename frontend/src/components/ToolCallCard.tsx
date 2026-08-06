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

export function ToolCallCard({ tool }: { tool: PartTool }) {
  const status = mapStatus(tool);
  const done = tool.status === 'success';
  const argsText = formatTryJson(tool.input);

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
