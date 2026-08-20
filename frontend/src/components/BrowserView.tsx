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
import { ArrowLeft, ArrowRight, Loader2, RotateCw } from 'lucide-react';
import { t } from '../lib/i18n';

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
}

function normalizeUrl(input: string): string {
  const trimmed = (input || '').trim();
  if (!trimmed) return 'about:blank';
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

export const BrowserView = forwardRef<BrowserViewHandle, BrowserViewProps>(function BrowserView(
  { initialUrl, active = true, onTitleChange, onUrlChange, onHandle },
  ref,
) {
  const webviewRef = useRef<ElectronWebview | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const [address, setAddress] = useState(initialUrl || '');
  const [loading, setLoading] = useState(false);

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

  // Keep the main process targeting the visible tab for agent control.
  useEffect(() => {
    if (!active) return;
    const wv = webviewRef.current;
    if (!wv) return;
    try {
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
          allowpopups: false,
        })}
      </div>
    </div>
  );
});
