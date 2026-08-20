import {
  forwardRef,
  createElement,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ClipboardPaste,
  Copy,
  Database,
  ExternalLink,
  FileText,
  Globe,
  Link,
  Loader2,
  PenLine,
  RotateCw,
  Scissors,
  Sparkles,
  Square,
} from 'lucide-react';
import { t } from '../lib/i18n';
import { ContextMenu, type ContextMenuItem } from './ui/context-menu';
import type { BrowserCaptureResult, BrowserContextMenuPayload, ComposerAttachment } from '../types';

// Minimal typing for Electron's <webview> custom element (not part of DOM lib).
export type ElectronWebview = HTMLElement & {
  loadURL: (url: string) => Promise<void>;
  getURL: () => string;
  getTitle: () => string;
  getWebContentsId: () => number;
  canGoBack: () => boolean;
  canGoForward: () => boolean;
  reload: () => void;
  goBack: () => void;
  goForward: () => void;
  focus: () => void;
};

export interface BrowserViewHandle {
  navigate: (url: string) => void;
  getUrl: () => string;
}

interface BrowserViewProps {
  initialUrl?: string | undefined;
  active?: boolean;
  onTitleChange?: (title: string) => void;
  onUrlChange?: (url: string) => void;
  onHandle?: (handle: BrowserViewHandle) => void;
  onOpenNewTab?: (url: string) => void;
  onAddCapture?: (attachments: ComposerAttachment[]) => void;
  agentActive?: boolean | undefined;
  agentClick?: { x: number; y: number; key: number } | null | undefined;
}

function normalizeUrl(input: string): string {
  const trimmed = (input || '').trim();
  if (!trimmed) return 'about:blank';
  // Pass through navigable schemes: http(s), file:// (local HTML), data:
  // (inline HTML/preview) and about:blank. Anything else defaults to https.
  if (/^(https?|file|data|about):/i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

function buildCaptureAttachments(capture: BrowserCaptureResult, intent: string): ComposerAttachment[] {
  const idBase = `browser-capture-${Date.now()}`;
  const payload: Record<string, unknown> = {
    url: capture.url,
    title: capture.title,
    task: intent,
    captured_at: new Date().toISOString(),
  };
  if (capture.element) payload.element = capture.element;
  if (capture.pageText) payload.page_text = capture.pageText;

  const attachments: ComposerAttachment[] = [];
  attachments.push({
    id: `${idBase}-json`,
    name: capture.element ? `browser-element-${capture.element.tag}.json` : 'browser-page.json',
    size: JSON.stringify(payload).length,
    type: 'application/json',
    content: JSON.stringify(payload, null, 2),
  });
  if (capture.screenshot) {
    attachments.push({
      id: `${idBase}-img`,
      name: capture.element ? 'browser-element.png' : 'browser-page.png',
      size: Math.round(capture.screenshot.length * 0.75),
      type: 'image/png',
      binary: true,
      content: capture.screenshot,
    });
  }
  return attachments;
}

export const BrowserView = forwardRef<BrowserViewHandle, BrowserViewProps>(function BrowserView(
  { initialUrl, active = true, onTitleChange, onUrlChange, onHandle, onOpenNewTab, onAddCapture, agentActive, agentClick },
  ref,
) {
  const webviewRef = useRef<ElectronWebview | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const [address, setAddress] = useState(initialUrl || '');
  const [loading, setLoading] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; params: BrowserContextMenuPayload } | null>(null);
  const [clickShown, setClickShown] = useState<{ x: number; y: number } | null>(null);

  // Briefly show a target ring at the agent's click coordinate.
  useEffect(() => {
    if (!agentClick) return;
    setClickShown({ x: agentClick.x, y: agentClick.y });
    const timer = setTimeout(() => setClickShown(null), 900);
    return () => clearTimeout(timer);
  }, [agentClick]);

  const navigate = useCallback((url: string) => {
    const wv = webviewRef.current;
    if (!wv) return;
    const target = normalizeUrl(url);
    setAddress(target);
    wv.loadURL(target).catch(() => {});
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      navigate,
      getUrl: () => webviewRef.current?.getURL() || '',
    }),
    [navigate],
  );

  // Register the imperative handle with the parent (RightPanel) once mounted.
  useEffect(() => {
    const handle: BrowserViewHandle = { navigate, getUrl: () => webviewRef.current?.getURL() || '' };
    onHandle?.(handle);
    return () => onHandle?.(undefined as unknown as BrowserViewHandle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Attach <webview> DOM listeners (React synthetic events do not fire on the
  // custom webview element, so we must use addEventListener).
  useEffect(() => {
    const wv = webviewRef.current;
    if (!wv) return;

    const onDidNavigate = (event: Event) => {
      const { url } = event as unknown as { url?: string };
      if (url) {
        setAddress(url);
        onUrlChange?.(url);
      }
    };
    const onTitleUpdated = (event: Event) => {
      const { title } = event as unknown as { title?: string };
      if (title) onTitleChange?.(title);
    };
    const onStartLoading = () => setLoading(true);
    const onStopLoading = () => setLoading(false);
    const onNewWindow = (event: Event) => {
      const { url } = event as unknown as { url?: string };
      if (url) wv.loadURL(url).catch(() => {});
    };
    const onDomReady = () => {
      if (!activeRef.current) return;
      try {
        if (wv.getBoundingClientRect().width > 0) wv.focus();
        const id = wv.getWebContentsId();
        window.electronAPI?.browserSetActiveTab(id);
      } catch {
        // ignore
      }
    };

    wv.addEventListener('did-navigate', onDidNavigate);
    wv.addEventListener('page-title-updated', onTitleUpdated);
    wv.addEventListener('did-start-loading', onStartLoading);
    wv.addEventListener('did-stop-loading', onStopLoading);
    wv.addEventListener('new-window', onNewWindow);
    wv.addEventListener('dom-ready', onDomReady);

    return () => {
      wv.removeEventListener('did-navigate', onDidNavigate);
      wv.removeEventListener('page-title-updated', onTitleUpdated);
      wv.removeEventListener('did-start-loading', onStartLoading);
      wv.removeEventListener('did-stop-loading', onStopLoading);
      wv.removeEventListener('new-window', onNewWindow);
      wv.removeEventListener('dom-ready', onDomReady);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Right-click context menu: the main process forwards the guest's
  // context-menu request (with the authoritative cursor position). Only the
  // tab owning that webContents shows the menu.
  useEffect(() => {
    if (!window.electronAPI?.onBrowserContextMenu) return;
    const unsubscribe = window.electronAPI.onBrowserContextMenu((payload) => {
      const wv = webviewRef.current;
      if (!wv) return;
      if (activeRef.current) {
        try {
          if (wv.getWebContentsId() !== payload.webContentsId) return;
        } catch {
          return;
        }
      }
      setContextMenu({ x: payload.x, y: payload.y, params: payload });
    });
    return unsubscribe;
  }, []);

  // Keep the main process targeting the visible tab for agent control.
  useEffect(() => {
    if (!active) return;
    const wv = webviewRef.current;
    if (!wv) return;
    try {
      // Only focus when the view is actually visible (the panel can stay
      // mounted-but-hidden so the browser keeps running in the background;
      // focusing a hidden webview would steal the user's keyboard input).
      if (wv.getBoundingClientRect().width > 0) wv.focus();
      const id = wv.getWebContentsId();
      window.electronAPI?.browserSetActiveTab(id);
    } catch {
      // webview not attached yet — the next active/mount cycle will retry.
    }
  }, [active]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    navigate(address);
  };

  const runMenuAction = (action: string) => {
    window.electronAPI?.browserMenuAction(action).catch(() => {});
  };

  const openLinkNewTab = (url: string) => {
    onOpenNewTab?.(url);
  };

  const copyLink = (url: string) => {
    void window.electronAPI?.clipboardWriteText(url);
  };

  const captureForAgent = (scope: 'element' | 'page', intent: string) => {
    if (!contextMenu) return;
    const { params } = contextMenu;
    // params.x/y are client (window) coordinates from the main process; the
    // capture IPC expects guest-viewport coordinates (relative to the webview).
    const wv = webviewRef.current;
    let x = params.x;
    let y = params.y;
    if (wv) {
      const rect = wv.getBoundingClientRect();
      x = params.x - rect.left;
      y = params.y - rect.top;
    }
    void window.electronAPI
      ?.browserCaptureElement({ x, y, scope })
      .then((capture) => {
        if (!capture || capture.error) {
          console.warn('[browser] capture failed:', capture?.error);
          return;
        }
        const attachments = buildCaptureAttachments(capture, intent);
        if (attachments.length) onAddCapture?.(attachments);
      })
      .catch((err) => console.warn('[browser] capture error:', err));
  };

  const buildMenuItems = (): ContextMenuItem[] => {
    const params = contextMenu?.params;
    if (!params) return [];
    const ef = params.editFlags;
    const items: ContextMenuItem[] = [];
    let clipboardShown = false;
    let linkShown = false;

    if (ef?.canCut) {
      clipboardShown = true;
      items.push({ id: 'cut', label: t('browser.menu.cut'), icon: <Scissors size={13} />, onSelect: () => runMenuAction('cut') });
    }
    if (ef?.canCopy) {
      clipboardShown = true;
      items.push({ id: 'copy', label: t('browser.menu.copy'), icon: <Copy size={13} />, onSelect: () => runMenuAction('copy') });
    }
    if (ef?.canPaste) {
      clipboardShown = true;
      items.push({ id: 'paste', label: t('browser.menu.paste'), icon: <ClipboardPaste size={13} />, onSelect: () => runMenuAction('paste') });
    }
    if (ef?.canSelectAll) {
      clipboardShown = true;
      items.push({ id: 'selectAll', label: t('browser.menu.select_all'), icon: <Square size={13} />, onSelect: () => runMenuAction('selectAll') });
    }

    if (params.linkURL) {
      linkShown = true;
      items.push({
        id: 'openLink',
        label: t('browser.menu.open_link_new_tab'),
        icon: <ExternalLink size={13} />,
        dividerBefore: clipboardShown,
        onSelect: () => openLinkNewTab(params.linkURL as string),
      });
      items.push({ id: 'copyLink', label: t('browser.menu.copy_link'), icon: <Link size={13} />, onSelect: () => copyLink(params.linkURL as string) });
    }

    items.push({
      id: 'explainElement',
      label: t('browser.menu.agent_explain_element'),
      icon: <Sparkles size={13} />,
      dividerBefore: clipboardShown || linkShown,
      onSelect: () => captureForAgent('element', t('browser.menu.agent_explain_element')),
    });
    items.push({ id: 'scrapeElement', label: t('browser.menu.agent_scrape_element'), icon: <Database size={13} />, onSelect: () => captureForAgent('element', t('browser.menu.agent_scrape_element')) });
    items.push({ id: 'annotateElement', label: t('browser.menu.agent_annotate_element'), icon: <PenLine size={13} />, onSelect: () => captureForAgent('element', t('browser.menu.agent_annotate_element')) });
    items.push({ id: 'explainPage', label: t('browser.menu.agent_explain_page'), icon: <Globe size={13} />, onSelect: () => captureForAgent('page', t('browser.menu.agent_explain_page')) });
    items.push({ id: 'scrapePage', label: t('browser.menu.agent_scrape_page'), icon: <FileText size={13} />, onSelect: () => captureForAgent('page', t('browser.menu.agent_scrape_page')) });

    return items;
  };

  return (
    <div className="browser-view">
      <form className="browser-view__toolbar" onSubmit={handleSubmit}>
        <button
          type="button"
          className="browser-view__nav"
          onClick={() => webviewRef.current?.goBack()}
          aria-label={t('browser.back')}
          title={t('browser.back')}
        >
          <ArrowLeft size={14} />
        </button>
        <button
          type="button"
          className="browser-view__nav"
          onClick={() => webviewRef.current?.goForward()}
          aria-label={t('browser.forward')}
          title={t('browser.forward')}
        >
          <ArrowRight size={14} />
        </button>
        <button
          type="button"
          className="browser-view__nav"
          onClick={() => webviewRef.current?.reload()}
          aria-label={t('browser.reload')}
          title={t('browser.reload')}
        >
          <RotateCw size={13} />
        </button>
        <div className="browser-view__address-wrap">
          {loading && <Loader2 size={12} className="browser-view__spinner" />}
          <input
            className="browser-view__address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder={t('browser.placeholder_url')}
            spellCheck={false}
          />
        </div>
      </form>
      <div className="browser-view__stage">
        {createElement('webview', {
          ref: webviewRef,
          src: initialUrl || 'about:blank',
          partition: 'persist:cw-browser',
          className: 'browser-view__webview',
          // allowpopups=true lets target=_blank / window.open emit new-window;
          // the main process's setWindowOpenHandler then denies the popup and
          // loads the URL in the same view. With allowpopups=false those links
          // were silently blocked on plain click (Cmd+click still worked via
          // the new-window path) — the "some buttons need Cmd" symptom.
          allowpopups: 'true',
        })}
        {active && agentActive && (
          <>
            <div className="browser-agent-ring" aria-hidden="true" />
            <span className="browser-agent-badge">{t('browser.agent_control_badge')}</span>
            {clickShown && <span className="browser-agent-click" style={{ left: clickShown.x, top: clickShown.y }} aria-hidden="true" />}
          </>
        )}
      </div>
      {contextMenu && (
        <>
          <div
            className="browser-view__scrim"
            onClick={() => setContextMenu(null)}
            onContextMenu={(e) => e.preventDefault()}
          />
          <ContextMenu
            open
            x={contextMenu.x}
            y={contextMenu.y}
            onClose={() => setContextMenu(null)}
            items={buildMenuItems()}
          />
        </>
      )}
    </div>
  );
});
