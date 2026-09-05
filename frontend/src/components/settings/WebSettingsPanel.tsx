import { Check, Loader2, Trash2, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { t } from '../../lib/i18n';
import { chatService } from '../../services/chatService';
import type { WebConfigPatch, WebSettings, WebTestResult } from '../../types';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';

interface WebSettingsPanelProps {
  settings: WebSettings;
  onChange: (next: WebSettings) => void;
  /** Open/close a temporary embedded-browser tab around a browser search test. */
  onSearchBrowserOpen?: (() => void) | undefined;
  onSearchBrowserClose?: (() => void) | undefined;
}

type TestState = 'idle' | 'testing' | WebTestResult;

const PROVIDER_OPTIONS: Array<{ value: string; labelKey: string }> = [
  { value: 'duckduckgo', labelKey: 'settings.web_provider_duckduckgo' },
  { value: 'browser', labelKey: 'settings.web_provider_browser' },
  { value: 'tavily', labelKey: 'settings.web_provider_tavily' },
];

const BROWSER_ENGINE_OPTIONS: Array<{ value: string; labelKey: string }> = [
  { value: 'bing', labelKey: 'settings.web_browser_engine_bing' },
  { value: 'google', labelKey: 'settings.web_browser_engine_google' },
  { value: 'duckduckgo', labelKey: 'settings.web_browser_engine_duckduckgo' },
  { value: 'baidu', labelKey: 'settings.web_browser_engine_baidu' },
  { value: 'sogou', labelKey: 'settings.web_browser_engine_sogou' },
];

function WebRow({
  label,
  description,
  control,
  meta,
}: {
  label: string;
  description?: string;
  control: React.ReactNode;
  meta?: React.ReactNode;
}) {
  return (
    <div className="settings-row">
      <div className="settings-row__copy">
        <label>{label}</label>
        {description && <p>{description}</p>}
      </div>
      {meta && <div className="settings-row__meta">{meta}</div>}
      <div className="settings-row__control">{control}</div>
    </div>
  );
}

function KeyStatusChip({ configured }: { configured: boolean }) {
  return (
    <span className={`settings-chip${configured ? ' settings-chip--ok' : ''}`}>
      {configured ? t('settings.web_key_configured') : t('settings.web_key_not_configured')}
    </span>
  );
}

export function WebSettingsPanel({ settings, onChange, onSearchBrowserOpen, onSearchBrowserClose }: WebSettingsPanelProps) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [keyInput, setKeyInput] = useState('');
  const [keyFlash, setKeyFlash] = useState(false);
  const [testState, setTestState] = useState<TestState>('idle');

  useEffect(() => {
    if (!keyFlash) return;
    const timer = setTimeout(() => setKeyFlash(false), 1600);
    return () => clearTimeout(timer);
  }, [keyFlash]);

  const persist = useCallback(
    async (patch: WebConfigPatch) => {
      setSaving(true);
      setError('');
      try {
        const next = await chatService.saveWebSettings(patch);
        onChange(next);
      } catch (exc) {
        setError(t('settings.web_save_failed') + `：${exc instanceof Error ? exc.message : String(exc)}`);
      } finally {
        setSaving(false);
      }
    },
    [onChange],
  );

  async function saveKey() {
    const value = keyInput.trim();
    if (!value) return;
    setError('');
    try {
      const result = await chatService.setWebTavilyKey(value);
      if (result.status === 'error') {
        setError(`${t('settings.web_save_failed')}：${result.detail || ''}`);
        return;
      }
      onChange({ ...settings, api_key_configured: true });
      setKeyInput('');
      setKeyFlash(true);
      setTestState('idle');
    } catch (exc) {
      setError(`${t('settings.web_save_failed')}：${exc instanceof Error ? exc.message : String(exc)}`);
    }
  }

  async function clearKey() {
    setError('');
    try {
      await chatService.clearWebTavilyKey();
      onChange({ ...settings, api_key_configured: false });
      setKeyInput('');
      setTestState('idle');
    } catch (exc) {
      setError(`${t('settings.web_save_failed')}：${exc instanceof Error ? exc.message : String(exc)}`);
    }
  }

  async function testConnection() {
    setError('');
    setTestState('testing');
    const key = settings.provider === 'tavily' ? keyInput.trim() || undefined : undefined;
    // Browser-backed test: ask the app to open a temporary browser tab so the
    // bridge has a live webview to drive; it is closed again afterwards.
    const needsProbe = settings.provider === 'browser';
    if (needsProbe && onSearchBrowserOpen) onSearchBrowserOpen();
    try {
      if (needsProbe) await new Promise((resolve) => setTimeout(resolve, 400));
      const result = await chatService.testWebSearch('daily news', key, settings.provider);
      setTestState(result);
    } catch (exc) {
      setTestState({ ok: false, message: exc instanceof Error ? exc.message : String(exc), results_count: 0 });
    } finally {
      if (needsProbe && onSearchBrowserClose) onSearchBrowserClose();
    }
  }

  const clampResults = (raw: number) => {
    if (!Number.isFinite(raw)) return 1;
    return Math.max(1, Math.min(20, Math.round(raw)));
  };

  const isTavily = settings.provider === 'tavily';
  const isBrowser = settings.provider === 'browser';

  return (
    <div className="settings-card">
      {error && <p className="settings-error">{error}</p>}
      <WebRow
        label={t('settings.web_enabled')}
        description={t('settings.web_enabled_desc')}
        control={
          <Switch
            id="setting-web-enabled"
            checked={settings.enabled}
            onChange={(e) => persist({ enabled: e.target.checked })}
          />
        }
      />
      <WebRow
        label={t('settings.web_provider')}
        description={isTavily ? t('settings.web_tavily_note') : t('settings.web_free_note')}
        control={
          <select
            className="settings-select"
            value={settings.provider}
            onChange={(e) => persist({ provider: e.target.value })}
            aria-label={t('settings.web_provider')}
          >
            {PROVIDER_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>
        }
      />
      {isBrowser && (
        <WebRow
          label={t('settings.web_browser_engine')}
          description={t('settings.web_browser_engine_desc')}
          control={
            <select
              className="settings-select"
              value={settings.browser_engine}
              onChange={(e) => persist({ browser_engine: e.target.value })}
              aria-label={t('settings.web_browser_engine')}
            >
              {BROWSER_ENGINE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.labelKey)}
                </option>
              ))}
            </select>
          }
        />
      )}
      {isTavily && (
        <WebRow
          label={t('settings.web_api_key')}
          description={t('settings.web_api_key_desc')}
          meta={<KeyStatusChip configured={settings.api_key_configured} />}
          control={
            <div className="settings-key-row">
              <input
                type="password"
                className="settings-number-input__field settings-key-row__input"
                value={keyInput}
                placeholder="tvly-••••••••••••"
                aria-label={t('settings.web_api_key')}
                onChange={(e) => setKeyInput(e.target.value)}
                onBlur={saveKey}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveKey();
                }}
              />
              {keyFlash ? (
                <span className="settings-key-row__flash">
                  <Check size={13} /> {t('settings.web_key_saved')}
                </span>
              ) : null}
              {settings.api_key_configured && (
                <Button variant="ghost" size="sm" onClick={clearKey} disabled={saving}>
                  <Trash2 size={13} />
                  {t('settings.web_clear_key')}
                </Button>
              )}
            </div>
          }
        />
      )}
      <WebRow
        label={t('settings.web_test_row')}
        description={isBrowser ? t('settings.web_test_browser_note') : ''}
        control={
          <div className="settings-key-row">
            <Button variant="secondary" size="sm" onClick={testConnection} disabled={testState === 'testing' || saving}>
              {testState === 'testing' ? <Loader2 size={13} className="spin" /> : <Wand2 size={13} />}
              {t('settings.web_test')}
            </Button>
          </div>
        }
      />
      {testState !== 'idle' && testState !== 'testing' && (
        <p className={`settings-row__hint ${testState.ok ? 'settings-row__hint--ok' : 'settings-row__hint--err'}`}>
          {testState.ok
            ? t('settings.web_test_ok', { count: testState.results_count })
            : `${t('settings.web_test_fail')}：${testState.message}`}
        </p>
      )}
      <WebRow
        label={t('settings.web_max_results')}
        description={t('settings.web_max_results_desc')}
        control={
          <div className="settings-number-input">
            <input
              type="number"
              className="settings-number-input__field"
              value={settings.max_results}
              min={1}
              max={20}
              onChange={(e) => {
                const next = clampResults(parseInt(e.target.value, 10));
                if (next !== settings.max_results) void persist({ max_results: next });
              }}
              onBlur={(e) => {
                const next = clampResults(parseInt(e.target.value, 10));
                if (next !== settings.max_results) void persist({ max_results: next });
              }}
              aria-label={t('settings.web_max_results')}
            />
            <span className="settings-number-input__unit">{t('settings.web_max_results_unit')}</span>
          </div>
        }
      />
      {isTavily && (
        <WebRow
          label={t('settings.web_depth')}
          control={
            <select
              className="settings-select"
              value={settings.search_depth}
              onChange={(e) => persist({ search_depth: e.target.value as 'basic' | 'advanced' })}
              aria-label={t('settings.web_depth')}
            >
              <option value="basic">{t('settings.web_depth_basic')}</option>
              <option value="advanced">{t('settings.web_depth_advanced')}</option>
            </select>
          }
        />
      )}
      <WebRow
        label={t('settings.web_fetch_enabled')}
        description={t('settings.web_fetch_enabled_desc')}
        control={
          <Switch
            id="setting-web-fetch"
            checked={settings.fetch_enabled}
            onChange={(e) => persist({ fetch_enabled: e.target.checked })}
          />
        }
      />
    </div>
  );
}
