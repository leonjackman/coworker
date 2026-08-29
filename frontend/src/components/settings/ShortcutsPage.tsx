import { ArrowLeft, RotateCcw } from 'lucide-react';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { t } from '../../lib/i18n';
import { SHORTCUT_REGISTRY } from '../../keys';
import {
  bindingFromKeyEvent,
  bindingsEqual,
  findConflicts,
  formatBinding,
  getEffectiveShortcut,
  getShortcutsSnapshot,
  hasCustomBinding,
  setShortcutBinding,
  setShortcutEnabled,
  setShortcutRecording,
  subscribeShortcutsChange,
} from '../../keys';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';
import { WorkspacePage } from '../ui/workspace-page';

interface ShortcutsPageProps {
  onBack: () => void;
}

export function ShortcutsPage({ onBack }: ShortcutsPageProps) {
  useSyncExternalStore(subscribeShortcutsChange, getShortcutsSnapshot);
  const [recordingId, setRecordingId] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);

  useEffect(() => {
    if (!recordingId) {
      setShortcutRecording(null);
      return;
    }
    setShortcutRecording(recordingId); // pause all global shortcuts while recording
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setRecordingId(null);
        setConflictError(null);
        return;
      }
      const binding = bindingFromKeyEvent(event);
      if (!binding) return; // keep waiting for the real key
      event.preventDefault();
      event.stopPropagation();

      const current = getEffectiveShortcut(recordingId).binding;
      if (bindingsEqual(binding, current)) {
        setRecordingId(null);
        setConflictError(null);
        return;
      }
      const conflicts = findConflicts(binding, recordingId);
      const conflict = conflicts[0];
      if (conflict) {
        setConflictError(t('shortcuts.conflict', { name: t(conflict.labelKey) }));
        return; // stay in recording mode so the user can pick another combo
      }
      setShortcutBinding(recordingId, binding);
      setRecordingId(null);
      setConflictError(null);
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      setShortcutRecording(null);
    };
  }, [recordingId]);

  return (
    <WorkspacePage
      className="shortcuts-page"
      eyebrow={t('settings.title')}
      title={t('shortcuts.page_title')}
      description={t('shortcuts.page_desc')}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <div className="settings-list">
        <section className="settings-group">
          <div className="settings-card">
            {SHORTCUT_REGISTRY.map((definition) => {
              const effective = getEffectiveShortcut(definition.id);
              const isRecording = recordingId === definition.id;
              const hasOverride = hasCustomBinding(definition.id);
              return (
                <div className={`settings-row${isRecording ? ' settings-row--recording' : ''}`} key={definition.id}>
                  <div className="settings-row__copy">
                    <label>{t(definition.labelKey)}</label>
                    <p>{t(definition.descriptionKey)}</p>
                  </div>
                  <div className="settings-row__control">
                    <div className="settings-shortcut-row__control">
                      {isRecording ? (
                        <>
                          <span className="settings-chip settings-chip--recording">{t('shortcuts.recording')}</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setRecordingId(null);
                              setConflictError(null);
                            }}
                          >
                            {t('shortcuts.cancel')}
                          </Button>
                        </>
                      ) : (
                        <>
                          <span className={`settings-chip${effective.enabled ? '' : ' settings-chip--dim'}`}>
                            {effective.enabled
                              ? definition.doublePress
                                ? t('shortcuts.press_twice', { binding: formatBinding(effective.binding) })
                                : formatBinding(effective.binding)
                              : t('shortcuts.disabled')}
                          </span>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => {
                              setRecordingId(definition.id);
                              setConflictError(null);
                            }}
                          >
                            {t('shortcuts.change')}
                          </Button>
                          {hasOverride && (
                            <Button variant="ghost" size="sm" onClick={() => setShortcutBinding(definition.id, null)} title={t('shortcuts.reset')}>
                              <RotateCcw size={13} />
                              {t('shortcuts.reset')}
                            </Button>
                          )}
                          <Switch
                            id={`shortcut-${definition.id}`}
                            checked={effective.enabled}
                            onChange={() => setShortcutEnabled(definition.id, !effective.enabled)}
                            aria-label={t('shortcuts.enabled')}
                          />
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          {conflictError && <p className="settings-row__hint settings-row__hint--err">{conflictError}</p>}
        </section>
      </div>
    </WorkspacePage>
  );
}
