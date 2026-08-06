import { t } from '../lib/i18n';
import { usePanelResize } from '../lib/usePanelResize';
import type { Autonomy } from '../types';

interface WorkspaceInspectorProps {
  sessionTitle: string;
  projectName: string;
  modelName: string;
  providerName: string;
  autonomy: Autonomy;
  attachmentCount: number;
  messageCount: number;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
}

export function WorkspaceInspector({
  sessionTitle,
  projectName,
  modelName,
  providerName,
  autonomy,
  attachmentCount,
  messageCount,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
}: WorkspaceInspectorProps) {
  const handleResizePointerDown = usePanelResize({
    bodyClassName: 'inspector-resizing',
    min: 220,
    max: 480,
    direction: -1,
    onResizeStart,
    onResizeEnd,
    onResizeWidth,
  });

  return (
    <aside className="inspector-panel">
      <div
        className="inspector-panel__resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('inspector.resize')}
        onPointerDown={handleResizePointerDown}
      />
      <div className="inspector-panel__header">
        <span>{t('inspector.title')}</span>
      </div>
      <div className="inspector-panel__body">
        <InfoRow label={t('inspector.session')} value={sessionTitle} />
        <InfoRow label={t('inspector.project')} value={projectName} />
        <InfoRow label={t('inspector.model')} value={modelName} />
        <InfoRow label={t('inspector.provider')} value={providerName} />
        <InfoRow label={t('inspector.autonomy')} value={t(`chat.autonomy_${autonomy}`)} />
        <InfoRow label={t('inspector.attachments')} value={String(attachmentCount)} />
        <InfoRow label={t('inspector.messages')} value={String(messageCount)} />
      </div>
    </aside>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="inspector-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
