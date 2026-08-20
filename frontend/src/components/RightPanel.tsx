import { type ReactNode } from 'react';
import { Globe, Info, List, Plus, TerminalSquare, X } from 'lucide-react';
import type { RightPanelTab, RightPanelTabKind } from '../types';
import { t } from '../lib/i18n';
import { usePanelResize } from '../lib/usePanelResize';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './ui/dropdown-menu';
import { BrowserView, type BrowserViewHandle } from './BrowserView';
import { TerminalView } from './TerminalView';

interface RightPanelProps {
  tabs: RightPanelTab[];
  activeTabId: string;
  inspector: {
    sessionTitle: string;
    projectName: string;
    modelName: string;
    providerName: string;
    autonomy: string;
    attachmentCount: number;
    messageCount: number;
  };
  terminalProjectId?: string;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onAdd: (kind: RightPanelTabKind) => void;
  onBrowserHandle: (tabId: string, handle: BrowserViewHandle | null) => void;
  onBrowserTitle: (tabId: string, title: string) => void;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
  maxWidth?: number;
}

const KIND_LABEL: Record<RightPanelTabKind, () => string> = {
  browser: () => t('right_panel.browser'),
  inspector: () => t('right_panel.inspector'),
  terminal: () => t('right_panel.terminal'),
  logs: () => t('right_panel.logs'),
};

const KIND_ICON: Record<RightPanelTabKind, ReactNode> = {
  browser: <Globe size={12} />,
  inspector: <Info size={12} />,
  terminal: <TerminalSquare size={12} />,
  logs: <List size={12} />,
};

function tabTitle(tab: RightPanelTab): string {
  if (tab.kind === 'browser') {
    if (tab.data?.title) return tab.data.title;
    if (tab.data?.url) {
      try {
        const host = new URL(tab.data.url.startsWith('http') ? tab.data.url : `https://${tab.data.url}`).hostname;
        return host || t('right_panel.browser');
      } catch {
        return tab.data.url;
      }
    }
    return t('right_panel.browser');
  }
  return KIND_LABEL[tab.kind]();
}

export function RightPanel({
  tabs,
  activeTabId,
  inspector,
  terminalProjectId,
  onSelect,
  onClose,
  onAdd,
  onBrowserHandle,
  onBrowserTitle,
  onResizeStart,
  onResizeEnd,
  onResizeWidth,
  maxWidth,
}: RightPanelProps) {
  const handleResizePointerDown = usePanelResize({
    bodyClassName: 'inspector-resizing',
    min: 240,
    max: maxWidth ?? 640,
    direction: -1,
    onResizeStart,
    onResizeEnd,
    onResizeWidth,
  });

  const addItems: { kind: RightPanelTabKind }[] = [
    { kind: 'browser' },
    { kind: 'inspector' },
    { kind: 'terminal' },
    { kind: 'logs' },
  ];

  return (
    <aside className="right-panel">
      <div
        className="right-panel__resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('right_panel.resize')}
        onPointerDown={handleResizePointerDown}
      />
      <div className="right-panel__tabs" role="tablist">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            role="tab"
            aria-selected={tab.id === activeTabId}
            className={`right-panel__tab ${tab.id === activeTabId ? 'right-panel__tab--active' : ''}`}
          >
            <button
              type="button"
              className="right-panel__tab-main"
              onClick={() => onSelect(tab.id)}
              title={tabTitle(tab)}
            >
              <span className="right-panel__tab-icon">{KIND_ICON[tab.kind]}</span>
              <span className="right-panel__tab-label">{tabTitle(tab)}</span>
            </button>
            <button
              type="button"
              className="right-panel__tab-close"
              onClick={() => onClose(tab.id)}
              aria-label={t('right_panel.close_tab')}
              title={t('right_panel.close_tab')}
            >
              <X size={11} />
            </button>
          </div>
        ))}
        <DropdownMenu>
          <DropdownMenuTrigger asChild className="right-panel__add-trigger">
            <button type="button" className="right-panel__add" aria-label={t('right_panel.add_tab')} title={t('right_panel.add_tab')}>
              <Plus size={14} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="right-panel__add-menu">
            {addItems.map((item) => (
              <DropdownMenuItem key={item.kind} onSelect={() => onAdd(item.kind)}>
                <span className="right-panel__add-item">
                  {KIND_ICON[item.kind]}
                  <span>{KIND_LABEL[item.kind]()}</span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="right-panel__content">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          if (tab.kind === 'browser') {
            // Browser tabs stay mounted so the page survives tab switches;
            // inactive ones are hidden with CSS (not unmounted).
            return (
              <div
                key={tab.id}
                className={`right-panel__tabpanel ${isActive ? 'right-panel__tabpanel--active' : ''}`}
              >
                <BrowserView
                  initialUrl={tab.data?.url}
                  active={isActive}
                  onHandle={(handle) => onBrowserHandle(tab.id, handle)}
                  onTitleChange={(title) => onBrowserTitle(tab.id, title)}
                  onUrlChange={(url) => onBrowserTitle(tab.id, '')}
                />
              </div>
            );
          }
          if (!isActive) return null;
          return (
            <div key={tab.id} className="right-panel__tabpanel right-panel__tabpanel--active">
              {tab.kind === 'inspector' ? (
                <InspectorBody {...inspector} />
              ) : tab.kind === 'terminal' ? (
                <TerminalView {...(terminalProjectId ? { projectId: terminalProjectId } : {})} />
              ) : (
                <LogsBody />
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function InspectorBody({
  sessionTitle,
  projectName,
  modelName,
  providerName,
  autonomy,
  attachmentCount,
  messageCount,
}: RightPanelProps['inspector']) {
  return (
    <div className="inspector-panel__body">
      <InfoRow label={t('inspector.session')} value={sessionTitle} />
      <InfoRow label={t('inspector.project')} value={projectName} />
      <InfoRow label={t('inspector.model')} value={modelName} />
      <InfoRow label={t('inspector.provider')} value={providerName} />
      <InfoRow label={t('inspector.autonomy')} value={t(`chat.autonomy_${autonomy}`)} />
      <InfoRow label={t('inspector.attachments')} value={String(attachmentCount)} />
      <InfoRow label={t('inspector.messages')} value={String(messageCount)} />
    </div>
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

function LogsBody() {
  return <p className="right-panel__logs-placeholder">{t('right_panel.logs_body')}</p>;
}
