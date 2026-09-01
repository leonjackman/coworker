import { Loader2 } from 'lucide-react';
import type { SessionBadges } from '../types';
import { t } from '../lib/i18n';

interface SessionStatusBadgeProps {
  badges: SessionBadges | undefined;
  className?: string;
}

/**
 * 固定寬度的會話狀態槽：只渲染優先級最高的一種狀態
 * （待審批 > 錯誤 > 進行中）。
 *
 * 優先級按「需要使用者介入的程度」排：待審批是阻塞（agent 卡住等人），
 * 錯誤是求救，進行中只是資訊。
 *
 * 無狀態時仍渲染一個同寬的佔位元素——若直接回傳 null，指示器出現／消失
 * 時標題會左右跳動。
 *
 * 未讀不在此處競爭，由呼叫端獨立渲染於右側。
 */
export function SessionStatusBadge({ badges, className }: SessionStatusBadgeProps) {
  if (!badges) {
    return <span className={`session-status ${className ?? ''}`.trim()} aria-hidden="true" />;
  }

  if (badges.approvals > 0) {
    const label = t('session_status.pending_approval', { count: badges.approvals });
    return (
      <span
        className={`session-status session-status--approval ${className ?? ''}`.trim()}
        title={label}
        aria-label={label}
      >
        {badges.approvals > 9 ? '9+' : badges.approvals}
      </span>
    );
  }

  if (badges.error) {
    return (
      <span
        className={`session-status session-status--error ${className ?? ''}`.trim()}
        title={badges.error}
        aria-label={t('session_status.error')}
      >
        !
      </span>
    );
  }

  if (badges.running) {
    return (
      <span
        className={`session-status session-status--running ${className ?? ''}`.trim()}
        aria-label={t('session_status.running')}
      >
        <Loader2 size={13} className="session-status__spinner" />
      </span>
    );
  }

  return <span className={`session-status ${className ?? ''}`.trim()} aria-hidden="true" />;
}
