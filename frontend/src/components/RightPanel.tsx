import { Globe, Plus, X } from 'lucide-react';
import type { ComposerAttachment, RightPanelTab } from '../types';
import { t } from '../lib/i18n';
import { usePanelResize } from '../lib/usePanelResize';
import { BrowserView, type BrowserViewHandle } from './BrowserView';

interface RightPanelProps {
  tabs: RightPanelTab[];
  activeTabId: string;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onAdd: () => void;
  onBrowserHandle: (tabId: string, handle: BrowserViewHandle | null) => void;
  onBrowserTitle: (tabId: string, title: string) => void;
  onOpenNewTab: (url: string) => void;
  onAddCapture: (attachments: ComposerAttachment[]) => void;
  onResizeStart: () => void;
  onResizeEnd: () => void;
  onResizeWidth: (width: number) => void;
  maxWidth?: number;
}

function tabTitle(tab: RightPanelTab): string {
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

export function RightPanel({
  tabs,
  activeTabId,
  onSelect,
  onClose,
  onAdd,
  onBrowserHandle,
  onBrowserTitle,
  onOpenNewTab,
  onAddCapture,
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
              <span className="right-panel__tab-icon">
                <Globe size={12} />
              </span>
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
        <button
          type="button"
          className="right-panel__add"
          onClick={onAdd}
          aria-label={t('right_panel.add_tab')}
          title={t('right_panel.add_tab')}
        >
          <Plus size={14} />
        </button>
      </div>
      <div className="right-panel__content">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
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
                onOpenNewTab={onOpenNewTab}
                onAddCapture={onAddCapture}
              />
            </div>
          );
        })}
      </div>
    </aside>
  );
}
