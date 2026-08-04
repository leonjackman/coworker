import type { AccessMode } from '../types';
import { t } from '../lib/i18n';

interface WorkspaceInspectorProps {
  sessionTitle: string;
  projectName: string;
  modelName: string;
  providerName: string;
  accessMode: AccessMode;
  attachmentCount: number;
  messageCount: number;
}

export function WorkspaceInspector({
  sessionTitle,
  projectName,
  modelName,
  providerName,
  accessMode,
  attachmentCount,
  messageCount,
}: WorkspaceInspectorProps) {
  return (
    <aside className="inspector-panel">
      <div className="inspector-panel__header">
        <span>{t('inspector.title')}</span>
      </div>
      <div className="inspector-panel__body">
        <InfoRow label={t('inspector.session')} value={sessionTitle} />
        <InfoRow label={t('inspector.project')} value={projectName} />
        <InfoRow label={t('inspector.model')} value={modelName} />
        <InfoRow label={t('inspector.provider')} value={providerName} />
        <InfoRow label={t('inspector.access_mode')} value={accessMode === 'full' ? t('chat.access_full') : t('chat.access_default')} />
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
