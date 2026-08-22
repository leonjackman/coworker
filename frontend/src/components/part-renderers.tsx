import { lazy, Suspense, type ReactNode } from 'react';
import type { MessagePart, PartAgent } from '../types';
import { ThinkingBlock } from './ThinkingBlock';
import { PlanBlock } from './PlanBlock';
import { ToolCallCard } from './ToolCallCard';
import { ToolGroup, ToolGroupTrigger, ToolGroupContent } from './assistant-ui/tool-group';
import { toolLabel, toolPreview } from './toolMeta';

const MarkdownContent = lazy(() => import('./MarkdownContent').then((module) => ({ default: module.MarkdownContent })));

/** Tools rendered as tool cards; anything listed here is shown elsewhere. */
export const IGNORED_TOOLS = new Set(['ask_user']);

/**
 * Tools that never render as cards at all — their activity is surfaced through a
 * dedicated block instead (e.g. worker delegation → AgentBlock).
 */
export const HIDDEN_TOOLS = new Set(['use_worker', 'delegate_task', 'delegate_parallel']);

export interface AgentBlockRenderProps {
  part: PartAgent;
  messageId?: string;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
}

export type AgentBlockRenderer = (props: AgentBlockRenderProps) => ReactNode;

export function ToolChain({ toolParts }: { toolParts: Extract<MessagePart, { type: 'tool' }>[] }) {
  const visibleTools = toolParts.filter((t) => !HIDDEN_TOOLS.has(t.name) && !IGNORED_TOOLS.has(t.name));
  // 运行/折叠态：工具全部折进单个 ToolGroup，仅在 trigger 上暴露当前正在跑的
  // tool（名 + 命令/路径预览），与成功态同构；展开后仍能看到每张卡的实时进度。
  // 转圈图标只跟 tool 自身状态绑定：工具跑完即停，不再受整个消息 running 态拖累。
  const runningTool = toolParts.find((part) => part.status === 'running');
  const active = Boolean(runningTool);
  const current = runningTool ? toolLabel(runningTool.name) : undefined;
  const preview = runningTool ? toolPreview(runningTool.name, runningTool.input) : undefined;
  return (
    <div className="tool-chain">
      {toolParts.filter((part) => IGNORED_TOOLS.has(part.name)).map((part) => (
        <ToolCallCard key={part.id} tool={part} />
      ))}
      {visibleTools.length > 0 && (
        <ToolGroup.Root variant="ghost">
          <ToolGroupTrigger
            count={visibleTools.length}
            active={active}
            {...(current !== undefined ? { current } : {})}
            {...(preview !== undefined ? { preview } : {})}
          />
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

/** 把有序的 MessagePart[] 渲染成一组 JSX 节点，text/tool/reasoning/plan 按数组顺序交错。 */
export function OrderedParts({
  parts,
  running,
  isError,
  messageId,
  onSubscribeWorker,
  renderAgentBlock,
}: {
  parts: MessagePart[];
  running: boolean;
  isError: boolean;
  messageId?: string;
  onSubscribeWorker?: (messageId: string, part: PartAgent) => void;
  renderAgentBlock?: AgentBlockRenderer;
}) {
  const nodes: ReactNode[] = [];
  let toolRun: Extract<MessagePart, { type: 'tool' }>[] = [];
  const flushTools = (key: string) => {
    if (toolRun.length > 0) {
      nodes.push(<ToolChain key={key} toolParts={toolRun} />);
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
      if (isError) return;
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
