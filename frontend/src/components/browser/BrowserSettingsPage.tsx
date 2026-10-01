import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Eye, FolderOpen, Trash2 } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { BrowserCredential, BrowserSettings, BrowserSettingsPatch } from '../../types';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { SettingsList, type SettingsGroup } from '../settings/SettingsList';

const api = () => window.electronAPI;

export function BrowserSettingsPage({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<BrowserSettings | null>(null);
  const [credentials, setCredentials] = useState<BrowserCredential[]>([]);
  const [credentialsAvailable, setCredentialsAvailable] = useState(true);
  const [permissions, setPermissions] = useState<Record<string, Record<string, boolean>>>({});
  const [origins, setOrigins] = useState<{ domain: string; count: number }[]>([]);
  const [clearOptions, setClearOptions] = useState({ history: true, cookies: true, cache: true, passwords: false });
  const [busy, setBusy] = useState(false);

  const loadCredentials = useCallback(() => {
    void api()?.browserCredentialsList().then((r) => {
      if (!r) return;
      setCredentialsAvailable(r.available);
      setCredentials(r.items ?? []);
    });
  }, []);

  const loadPermissions = useCallback(() => {
    void api()?.browserPermissionList().then((r) => setPermissions(r ?? {}));
  }, []);

  const loadOrigins = useCallback(() => {
    void api()?.browserSiteOrigins().then((r) => setOrigins(r?.origins ?? []));
  }, []);

  useEffect(() => {
    void api()?.browserSettingsGet().then((s) => setSettings(s));
    loadCredentials();
    loadPermissions();
    loadOrigins();
  }, [loadCredentials, loadPermissions, loadOrigins]);

  const patch = useCallback((next: BrowserSettingsPatch) => {
    setSettings((prev) => (prev ? { ...prev, ...next } : prev));
    void api()?.browserSettingsSave(next).then((s) => {
      setSettings(s);
      // Let the app react live (e.g. start/stop persisting browser tabs).
      window.dispatchEvent(new CustomEvent('coworker-browser-settings-changed', { detail: s }));
    });
  }, []);

  if (!settings) {
    return (
      <WorkspacePage eyebrow={t('settings.title')} title={t('browser.settings_title')} description={t('browser.settings_desc')}>
        <p className="browser-overlay__empty">{t('common.loading')}</p>
      </WorkspacePage>
    );
  }

  const groups: SettingsGroup[] = [
    {
      id: 'browser-general',
      title: t('browser.settings_general'),
      items: [
        {
          id: 'restore_tabs',
          type: 'toggle',
          label: t('browser.setting_restore_tabs'),
          description: t('browser.setting_restore_tabs_desc'),
          value: settings.restore_tabs ? 'true' : 'false',
          options: [
            { value: 'true', label: t('memory.enabled') },
            { value: 'false', label: t('memory.disabled') },
          ],
          onChange: (value) => patch({ restore_tabs: value === 'true' }),
        },
        {
          id: 'ask_where_to_save',
          type: 'toggle',
          label: t('browser.setting_ask_save'),
          description: t('browser.setting_ask_save_desc'),
          value: settings.ask_where_to_save ? 'true' : 'false',
          options: [
            { value: 'true', label: t('memory.enabled') },
            { value: 'false', label: t('memory.disabled') },
          ],
          onChange: (value) => patch({ ask_where_to_save: value === 'true' }),
        },
        {
          id: 'download_dir',
          type: 'text_input',
          label: t('browser.setting_download_dir'),
          description: t('browser.setting_download_dir_desc'),
          value: settings.download_dir,
          placeholder: t('browser.setting_download_dir_placeholder'),
          onChange: (value) => patch({ download_dir: value }),
        },
      ],
      footer: (
        <div className="browser-settings__foot">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              const empty = undefined;
              void api()?.openDirectoryPicker(empty).then((dir) => {
                if (dir) patch({ download_dir: dir });
              });
            }}
          >
            <FolderOpen size={13} />
            {t('browser.choose_folder')}
          </Button>
        </div>
      ),
    },
    {
      id: 'browser-security',
      title: t('browser.settings_security'),
      items: [
        {
          id: 'permissions_prompt',
          type: 'toggle',
          label: t('browser.setting_permissions_prompt'),
          description: t('browser.setting_permissions_prompt_desc'),
          value: settings.permissions_prompt ? 'true' : 'false',
          options: [
            { value: 'true', label: t('memory.enabled') },
            { value: 'false', label: t('memory.disabled') },
          ],
          onChange: (value) => patch({ permissions_prompt: value === 'true' }),
        },
        {
          id: 'password_manager_enabled',
          type: 'toggle',
          label: t('browser.setting_password_manager'),
          description: t('browser.setting_password_manager_desc'),
          value: settings.password_manager_enabled ? 'true' : 'false',
          options: [
            { value: 'true', label: t('memory.enabled') },
            { value: 'false', label: t('memory.disabled') },
          ],
          onChange: (value) => patch({ password_manager_enabled: value === 'true' }),
        },
        {
          id: 'password_autofill',
          type: 'toggle',
          label: t('browser.setting_password_autofill'),
          description: t('browser.setting_password_autofill_desc'),
          value: settings.password_autofill ? 'true' : 'false',
          options: [
            { value: 'true', label: t('memory.enabled') },
            { value: 'false', label: t('memory.disabled') },
          ],
          onChange: (value) => patch({ password_autofill: value === 'true' }),
        },
      ],
    },
  ];

  return (
    <WorkspacePage
      eyebrow={t('settings.title')}
      title={t('browser.settings_title')}
      description={t('browser.settings_desc')}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <SettingsList groups={groups} />

      <section className="settings-group">
        <div className="settings-group__heading">
          <h2>{t('browser.passwords_title')}</h2>
          <p>{t('browser.passwords_desc')}</p>
        </div>
        <div className="settings-card">
          {!credentialsAvailable && <p className="browser-overlay__empty">{t('browser.passwords_unavailable')}</p>}
          {credentialsAvailable && credentials.length === 0 && <p className="browser-overlay__empty">{t('browser.passwords_empty')}</p>}
          {credentials.map((cred) => (
            <div key={cred.id} className="settings-row">
              <div className="settings-row__copy">
                <label>{cred.origin}</label>
                <p>{cred.username || t('browser.credential_unknown_user')}</p>
              </div>
              <div className="settings-row__control browser-password__actions">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void api()?.browserCredentialReveal(cred.id).then((r) => {
                      if (r?.password) window.alert(t('browser.password_reveal_value', { password: r.password }));
                      else if (r?.error === 'cancelled') window.alert(t('browser.password_reveal_cancelled'));
                    });
                  }}
                >
                  <Eye size={13} />
                  {t('browser.password_reveal')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void api()?.browserCredentialRemove(cred.id).then(loadCredentials)}>
                  <Trash2 size={13} />
                  {t('browser.remove')}
                </Button>
              </div>
            </div>
          ))}
          {credentialsAvailable && credentials.length > 0 && (
            <div className="browser-settings__foot">
              <Button size="sm" variant="ghost" onClick={() => void api()?.browserCredentialsClear().then(loadCredentials)}>
                {t('browser.clear_all')}
              </Button>
            </div>
          )}
        </div>
      </section>

      <section className="settings-group">
        <div className="settings-group__heading">
          <h2>{t('browser.permissions_title')}</h2>
          <p>{t('browser.permissions_desc')}</p>
        </div>
        <div className="settings-card">
          {Object.keys(permissions).length === 0 && <p className="browser-overlay__empty">{t('browser.permissions_empty')}</p>}
          {Object.entries(permissions).map(([origin, perms]) => (
            <div key={origin} className="settings-row">
              <div className="settings-row__copy">
                <label>{origin}</label>
                <p>{Object.entries(perms).map(([key, allowed]) => `${key}: ${allowed ? '✓' : '✕'}`).join('  ')}</p>
              </div>
              <div className="settings-row__control">
                <Button size="sm" variant="ghost" onClick={() => void api()?.browserPermissionReset(origin).then(loadPermissions)}>
                  {t('browser.permission_reset')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-group">
        <div className="settings-group__heading">
          <h2>{t('browser.site_data_title')}</h2>
          <p>{t('browser.site_data_desc')}</p>
        </div>
        <div className="settings-card">
          {origins.length === 0 && <p className="browser-overlay__empty">{t('browser.site_data_empty')}</p>}
          {origins.map((origin) => (
            <div key={origin.domain} className="settings-row">
              <div className="settings-row__copy">
                <label>{origin.domain}</label>
                <p>{t('browser.site_data_cookie_count', { count: origin.count })}</p>
              </div>
              <div className="settings-row__control">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void api()?.browserClearData({ origin: origin.domain, cookies: true, site_data: true }).then(loadOrigins)}
                >
                  <Trash2 size={13} />
                  {t('browser.site_data_clear')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-group">
        <div className="settings-group__heading">
          <h2>{t('browser.clear_data_title')}</h2>
          <p>{t('browser.clear_data_desc')}</p>
        </div>
        <div className="settings-card">
          <div className="browser-clear__options">
            {(['history', 'cookies', 'cache', 'passwords'] as const).map((key) => (
              <label key={key} className="browser-clear__option">
                <input
                  type="checkbox"
                  checked={clearOptions[key]}
                  onChange={(e) => setClearOptions((prev) => ({ ...prev, [key]: e.target.checked }))}
                />
                {t(`browser.clear_${key}`)}
              </label>
            ))}
          </div>
          <div className="browser-settings__foot">
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void api()?.browserClearData({
                  history: clearOptions.history,
                  cookies: clearOptions.cookies,
                  cache: clearOptions.cache,
                  site_data: clearOptions.cookies,
                  passwords: clearOptions.passwords,
                }).finally(() => {
                  setBusy(false);
                  loadCredentials();
                  loadPermissions();
                  loadOrigins();
                });
              }}
            >
              {t('browser.clear_data_action')}
            </Button>
          </div>
        </div>
      </section>
    </WorkspacePage>
  );
}
