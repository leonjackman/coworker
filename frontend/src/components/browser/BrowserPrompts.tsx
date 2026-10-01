import { useEffect, useState } from 'react';
import { KeyRound, ShieldQuestion, X } from 'lucide-react';
import { t } from '../../lib/i18n';
import { permissionLabelKey } from '../../lib/browser';
import type { BrowserCredentialCaptured, BrowserCredentialUseRequest, BrowserPermissionRequest } from '../../types';

// Global prompts for the embedded browser: site-permission requests, "save this
// password?" capture offers, and the agent's "use a stored credential" consent.
// Mounted once (in App) because the main process addresses the window, not a
// specific browser tab.

export function BrowserPrompts() {
  const [permission, setPermission] = useState<BrowserPermissionRequest | null>(null);
  const [remember, setRemember] = useState(false);
  const [capture, setCapture] = useState<BrowserCredentialCaptured | null>(null);
  const [credentialUse, setCredentialUse] = useState<BrowserCredentialUseRequest | null>(null);

  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;
    const offPermission = api.onBrowserPermissionRequest?.((payload) => {
      setRemember(false);
      setPermission(payload);
    });
    const offCapture = api.onBrowserCredentialCaptured?.((payload) => setCapture(payload));
    const offUse = api.onBrowserCredentialUseRequest?.((payload) => setCredentialUse(payload));
    return () => {
      offPermission?.();
      offCapture?.();
      offUse?.();
    };
  }, []);

  const answerPermission = (allow: boolean) => {
    if (!permission) return;
    window.electronAPI?.respondBrowserPermission({ id: permission.id, allow, remember });
    setPermission(null);
  };

  const answerCapture = (save: boolean) => {
    if (!capture) return;
    void window.electronAPI?.browserCredentialSavePending({ token: capture.token, save });
    setCapture(null);
  };

  const answerCredentialUse = (allow: boolean) => {
    if (!credentialUse) return;
    window.electronAPI?.respondBrowserCredentialUse({ id: credentialUse.id, allow });
    setCredentialUse(null);
  };

  return (
    <>
      {permission && (
        <div className="browser-prompt" role="dialog" aria-modal="true">
          <div className="browser-prompt__card">
            <div className="browser-prompt__head">
              <ShieldQuestion size={16} />
              <span>{t('browser.permission_title')}</span>
            </div>
            <p className="browser-prompt__body">
              {t('browser.permission_body', {
                origin: permission.origin,
                permission: t(permissionLabelKey(permission.permission)),
              })}
            </p>
            <label className="browser-prompt__remember">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              {t('browser.permission_remember')}
            </label>
            <div className="browser-prompt__actions">
              <button type="button" className="browser-prompt__btn" onClick={() => answerPermission(false)}>{t('browser.permission_deny')}</button>
              <button type="button" className="browser-prompt__btn browser-prompt__btn--primary" onClick={() => answerPermission(true)}>{t('browser.permission_allow')}</button>
            </div>
          </div>
        </div>
      )}

      {credentialUse && (
        <div className="browser-prompt" role="dialog" aria-modal="true">
          <div className="browser-prompt__card">
            <div className="browser-prompt__head">
              <KeyRound size={16} />
              <span>{t('browser.credential_use_title')}</span>
            </div>
            <p className="browser-prompt__body">
              {t('browser.credential_use_body', { origin: credentialUse.origin, username: credentialUse.username || t('browser.credential_unknown_user') })}
            </p>
            <div className="browser-prompt__actions">
              <button type="button" className="browser-prompt__btn" onClick={() => answerCredentialUse(false)}>{t('browser.permission_deny')}</button>
              <button type="button" className="browser-prompt__btn browser-prompt__btn--primary" onClick={() => answerCredentialUse(true)}>{t('browser.credential_use_allow')}</button>
            </div>
          </div>
        </div>
      )}

      {capture && (
        <div className="browser-toast" role="dialog">
          <KeyRound size={15} />
          <div className="browser-toast__copy">
            <strong>{t('browser.credential_save_title')}</strong>
            <span>{t('browser.credential_save_body', { origin: capture.origin, username: capture.username || t('browser.credential_unknown_user') })}</span>
          </div>
          <div className="browser-toast__actions">
            <button type="button" className="browser-prompt__btn" onClick={() => answerCapture(false)}>{t('browser.credential_save_no')}</button>
            <button type="button" className="browser-prompt__btn browser-prompt__btn--primary" onClick={() => answerCapture(true)}>{t('browser.credential_save_yes')}</button>
          </div>
          <button type="button" className="browser-toast__close" aria-label={t('common.close')} onClick={() => setCapture(null)}>
            <X size={12} />
          </button>
        </div>
      )}
    </>
  );
}
