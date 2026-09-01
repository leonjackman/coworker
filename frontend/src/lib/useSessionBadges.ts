import { useMemo } from 'react';
import type { ChatMessage, CommandApproval, SessionBadgeMap, SessionSummary } from '../types';

interface UseSessionBadgesArgs {
  sessions: SessionSummary[];
  messages: ChatMessage[];
  /** 後端 `/sessions/active` 輪詢結果（前端刷新後不知道哪些會話仍在跑的兜底）。 */
  backendActiveSessionIds: Set<string>;
  /** sessionId → 待審批完整記錄，來自 `/command-approvals` 全域掃描。 */
  pendingBySession: Map<string, CommandApproval[]>;
}

const MAX_ERROR_SUMMARY = 240;

/**
 * 把四種狀態來源彙整成單一 `Map<sessionId, SessionBadges>`。
 *
 * 三處會話列表（側欄 / 整頁 / Dashboard）共用這一份，取代過去各自判斷
 * `runningSessionIds.has(id)` 的重複邏輯。
 *
 * 資料來源分兩類：
 * - 後端持久化快照（`unread_count` / `last_error` / 待審批改數）：跨重啟存活
 * - 前端即時訊息流（`running` / `error`）：當下最即時，但重啟即丟
 *
 * 即時來源覆蓋快照，因為它反映的是「現在這一刻」。
 */
export function useSessionBadges({
  sessions,
  messages,
  backendActiveSessionIds,
  pendingBySession,
}: UseSessionBadgesArgs): SessionBadgeMap {
  return useMemo(() => {
    const map: SessionBadgeMap = new Map();

    for (const session of sessions) {
      const approvals = pendingBySession.get(session.id);
      map.set(session.id, {
        running: false,
        approvals: approvals?.length ?? 0,
        unread: session.unread_count ?? 0,
        error: session.last_error || null,
      });
    }

    // 前端即時來源：只對已知會話生效，避免為尚未載入的會話憑空生徽章。
    for (const message of messages) {
      if (!message.sessionId) continue;
      const badges = map.get(message.sessionId);
      if (!badges) continue;
      if (message.status === 'running' || message.status === 'waiting') {
        badges.running = true;
      }
      if (message.status === 'error' && !badges.error) {
        badges.error = (message.content || '').slice(0, MAX_ERROR_SUMMARY);
      }
    }

    for (const id of backendActiveSessionIds) {
      const badges = map.get(id);
      if (badges) badges.running = true;
    }

    return map;
  }, [sessions, messages, backendActiveSessionIds, pendingBySession]);
}
