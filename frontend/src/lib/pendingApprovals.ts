import { chatService } from '../services/chatService';
import type { ApprovalOption, PendingRequest } from '../types';

type SetPendingRequests = (updater: (current: PendingRequest[]) => PendingRequest[]) => void;

/**
 * Re-materialize the backend's pending command approvals/questions/plans for one
 * session into local `PendingRequest` cards (e.g. after a refresh / on session
 * open). Pure data reconciliation — the caller owns the pending-requests state.
 */
export async function restorePendingApprovalsForSession(
  targetSessionId: string,
  setPendingRequests: SetPendingRequests,
): Promise<void> {
  try {
    const response = await chatService.listCommandApprovals();
    const restored: PendingRequest[] = [];
    for (const approval of response.approvals) {
      if (approval.status !== 'pending') continue;
      const context = approval.context;
      if (!context || context.session_id !== targetSessionId) continue;
      const kind = context.kind === 'question' ? 'question' : context.kind === 'plan' ? 'plan' : 'command';
      const base: PendingRequest = {
        approval_id: approval.id,
        kind,
        session_id: targetSessionId,
        approval_status: approval.status,
        messageId: '',
      };
      if (kind === 'question') {
        const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
        restored.push({
          ...base,
          ...(typeof args.question === 'string' ? { question: args.question } : {}),
          ...(typeof args.header === 'string' ? { header: args.header } : {}),
          ...(Array.isArray(args.options) ? { options: args.options as ApprovalOption[] } : {}),
          ...(typeof args.multiple === 'boolean' ? { multiple: args.multiple } : {}),
        });
      } else if (kind === 'plan') {
        const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
        restored.push({
          ...base,
          ...(typeof args.plan_text === 'string' ? { plan: args.plan_text } : {}),
        });
      } else {
        const args = typeof context.action_args === 'object' && context.action_args ? (context.action_args as Record<string, unknown>) : {};
        restored.push({
          ...base,
          command: Array.isArray(approval.command) ? approval.command : [],
          ...(approval.cwd ? { cwd: approval.cwd } : {}),
          ...(typeof context.tool_name === 'string' && context.tool_name ? { tool_name: context.tool_name } : {}),
          ...(Object.keys(args).length > 0 ? { tool_args: args } : {}),
        });
      }
    }
    if (restored.length === 0) return;
    setPendingRequests((current) => {
      const existing = current.filter((item) => item.session_id === targetSessionId);
      const existingIds = new Set(existing.map((item) => item.approval_id));
      const additions = restored.filter((item) => !existingIds.has(item.approval_id));
      if (additions.length === 0) return current;
      return [...current.filter((item) => item.session_id !== targetSessionId), ...existing, ...additions];
    });
  } catch (error) {
    console.error('Failed to restore pending approvals:', error);
  }
}
