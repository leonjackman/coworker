import { useState } from 'react';
import { getLanguage, setLanguage, t, type Language } from '../../lib/i18n';
import { THEME_PRESETS, type ThemeMode, type ThemeSettings } from '../../lib/theme';
import type { AccessMode, WorkMode } from '../../types';
import { PageHeading } from '../ui/page-heading';
import { SettingsList } from './SettingsList';
import { ThemeCustomizer } from './ThemeCustomizer';
import { ToolAuditPanel } from './ToolAuditPanel';
import { accessModeOptions, languageOptions, themeOptions, workModeOptions } from './preference-options';

interface SettingsViewProps {
  themeSettings: ThemeSettings;
  workMode: WorkMode;
  accessMode: AccessMode;
  onThemeSettingsChange: (settings: ThemeSettings) => void;
  onWorkModeChange: (mode: WorkMode) => void;
  onAccessModeChange: (mode: AccessMode) => void;
  onLanguageChange: () => void;
}

export function SettingsView({
  themeSettings,
  workMode,
  accessMode,
  onThemeSettingsChange,
  onWorkModeChange,
  onAccessModeChange,
  onLanguageChange,
}: SettingsViewProps) {
  const [settingsPage, setSettingsPage] = useState<'main' | 'theme'>('main');

  async function selectLanguage(language: string) {
    if (language !== 'zh' && language !== 'en') return;
    if (language === getLanguage()) return;
    await setLanguage(language as Language);
    onLanguageChange();
  }

  const currentPreset = THEME_PRESETS.find((preset) => preset.id === themeSettings.presetId);

  if (settingsPage === 'theme') {
    return (
      <section className="settings-panel">
        <ThemeCustomizer settings={themeSettings} onChange={onThemeSettingsChange} onBack={() => setSettingsPage('main')} />
      </section>
    );
  }

  return (
    <section className="settings-panel">
      <PageHeading eyebrow={t('settings.title')} title={t('settings.heading')} description={t('settings.description')} />

      <SettingsList
        groups={[
          {
            id: 'interface',
            title: t('settings.interface_group'),
            description: t('settings.interface_group_desc'),
            items: [
              {
                id: 'language',
                type: 'toggle',
                label: t('settings.language'),
                description: t('settings.language_desc'),
                value: getLanguage(),
                options: languageOptions(),
                onChange: selectLanguage,
              },
              {
                id: 'theme',
                type: 'toggle',
                label: t('settings.appearance'),
                description: t('settings.appearance_desc'),
                value: themeSettings.mode,
                options: themeOptions(),
                onChange: (value) => onThemeSettingsChange({ ...themeSettings, mode: value as ThemeMode }),
              },
              {
                id: 'theme-palette',
                type: 'action',
                label: t('settings.palette_group'),
                description: t('settings.palette_entry_desc'),
                actionLabel: t('settings.configure'),
                meta: <span className="settings-chip">{themeSettings.presetId === 'custom' ? t('theme.preset_custom') : t(currentPreset?.labelKey ?? 'theme.preset_mineral')}</span>,
                onAction: () => setSettingsPage('theme'),
              },
            ],
          },
          {
            id: 'composer',
            title: t('settings.composer_group'),
            description: t('settings.composer_group_desc'),
            items: [
              {
                id: 'work-mode',
                type: 'toggle',
                label: t('settings.work_mode'),
                description: t('settings.work_mode_desc'),
                value: workMode,
                options: workModeOptions(),
                onChange: (value) => onWorkModeChange(value as WorkMode),
              },
              {
                id: 'access-mode',
                type: 'toggle',
                label: t('settings.access_mode'),
                description: t('settings.access_mode_desc'),
                value: accessMode,
                options: accessModeOptions(),
                onChange: (value) => onAccessModeChange(value as AccessMode),
              },
            ],
          },
        ]}
      />
      <ToolAuditPanel />
    </section>
  );
}
