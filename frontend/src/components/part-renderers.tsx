import { lazy, Suspense, type ReactNode } from 'react';
import type { MessagePart, PartAgent } from '../types';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanBlock } from './PlanBlock';
import { ToolCallCard } from './ToolCallCard';
import { ToolGroup, ToolGroupTrigger, ToolGroupContent } from './assistant-ui/tool-group';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

/** Tools rendered as tool cards; anything listed here is shown elsewhere. */
export const IGNORED_TOOLS = new Set(['ask_user']);

/**
 * Tools that never render as cards at all — their activity is surfaced through a
 * dedicated block instead (e.g. worker delegation → AgentBlock).
 */
export const HIDDEN_TOOLS = new Set(['use_worker', 'delegate_task', 'delegate_parallel']);

const CONTEXT_TOOLS = new Set(['read_file', 'search_files']);

export interface AgentBlockRenderProps {
  part: PartAgent;
  messageId?: string;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
}

export type AgentBlockRenderer = (props: AgentBlockRenderProps) => ReactNode;

interface ToolGroupNode {
  key: string;
  tools: Extract<MessagePart, { type: 'tool' }>[];
}

function buildToolGroups(toolParts: Extract<MessagePart, { type: 'tool' }>[]): { groups: ToolGroupNode[]; ignored: Extract<MessagePart, { type: 'tool' }>[] } {
  const groups: ToolGroupNode[] = [];
  const ignored: Extract<MessagePart, { type: 'tool' }>[] = [];
  let current: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flush = () => {
    if (current.length > 0) {
      groups.push({ key: current[0]!.id, tools: current });
      current = [];
    }
  };
  for (const part of toolParts) {
    if (HIDDEN_TOOLS.has(part.name)) {
      continue;
    }
    if (IGNORED_TOOLS.has(part.name)) {
      ignored.push(part);
    } else if (CONTEXT_TOOLS.has(part.name)) {
      current.push(part);
    } else {
      flush();
      groups.push({ key: part.id, tools: [part] });
    }
  }
  flush();
  return { groups, ignored };
}

export function ToolChain({ toolParts, running }: { toolParts: Extract<MessagePart, { type: 'tool' }>[]; running?: boolean }) {
  if (!running) {
    const visibleTools = toolParts.filter((t) => !HIDDEN_TOOLS.has(t.name) && !IGNORED_TOOLS.has(t.name));
    const active = toolParts.some((part) => part.status === 'running');
    return (
      <div className="tool-chain">
        {toolParts.filter((part) => IGNORED_TOOLS.has(part.name)).map((part) => (
          <ToolCallCard key={part.id} tool={part} />
        ))}
        {visibleTools.length > 0 && (
          <ToolGroup.Root variant="ghost">
            <ToolGroupTrigger count={visibleTools.length} active={active} />
            <ToolGroupContent>
              {visibleTools.map((part) => (
                <ToolCallCard key={part.id} tool={part} />
              ))}
            </ToolGroupContent>
          </ToolGroup.Root>
        )}
      </div>
    );
  }
  const { groups, ignored } = buildToolGroups(toolParts);
  return (
    <div className="tool-chain">
      {ignored.map((part) => (
        <ToolCallCard key={part.id} tool={part} />
      ))}
      {groups.map((group) => {
        if (group.tools.length === 1) {
          const single = group.tools[0]!;
          return <ToolCallCard key={single.id} tool={single} />;
        }
        const active = group.tools.some((part) => part.status === 'running');
        return (
          <ToolGroup.Root key={group.key} variant="ghost">
            <ToolGroupTrigger count={group.tools.length} active={active} />
            <ToolGroupContent>
              {group.tools.map((part) => (
                <ToolCallCard key={part.id} tool={part} />
              ))}
            </ToolGroupContent>
          </ToolGroup.Root>
        );
      })}
    </div>
  );
}

/** 把有序的 MessagePart[] 渲染成一组 JSX 节点，text/tool/reasoning/plan 按数组顺序交错。 */
export function OrderedParts({
  parts,
  running,
  isError,
  isStopped,
  messageId,
  onSubscribeWorker,
  renderAgentBlock,
}: {
  parts: MessagePart[];
  running: boolean;
  isError: boolean;
  isStopped: boolean;
  messageId?: string;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
  renderAgentBlock?: AgentBlockRenderer;
}) {
  const nodes: ReactNode[] = [];
  let toolRun: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flushTools = (key: string) => {
    if (toolRun.length > 0) {
      nodes.push(<ToolChain key={key} toolParts={toolRun} running={running} />);
      toolRun = [];
    }
  };
  parts.forEach((part, index) => {
    if (part.type === 'tool') {
      toolRun.push(part);
      return;
    }
    flushTools(`tools-${index}`);
    if (part.type === 'text') {
      if (isError || isStopped) return;
      nodes.push(
        <Suspense key={`text-${index}`} fallback={<div className="markdown-body">{part.content}</div>}>
          <MarkdownContent content={part.content} />
        </Suspense>,
      );
    } else if (part.type === 'reasoning') {
      nodes.push(<ThinkingBlock key={`reasoning-${index}`} reasoningParts={[part]} working={running} />);
    } else if (part.type === 'plan') {
      nodes.push(<PlanBlock key={`plan-${index}`} planParts={[part]} working={running} />);
    } else if (part.type === 'agent') {
      if (renderAgentBlock) {
        const RenderAgentBlock = renderAgentBlock;
        nodes.push(
          <RenderAgentBlock
            key={`agent-${part.workerRunId ?? index}`}
            part={part}
            {...(messageId ? { messageId } : {})}
            {...(onSubscribeWorker ? { onSubscribeWorker } : {})}
          />,
        );
      }
    }
  });
  flushTools(`tools-end`);
  if (nodes.length === 0) return null;
  return <>{nodes}</>;
}
