import { ArrowLeft, X } from 'lucide-react';
import { useState } from 'react';
import { getLanguage, setLanguage, t, type Language } from '../../lib/i18n';
import { THEME_PRESETS, type ThemeMode, type ThemeSettings } from '../../lib/theme';
import type { Autonomy } from '../../types';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { SettingsList } from './SettingsList';
import { ThemeCustomizer } from './ThemeCustomizer';
import { ToolAuditPanel } from './ToolAuditPanel';
import { autonomyOptions, languageOptions, themeOptions } from './preference-options';

interface SettingsViewProps {
  themeSettings: ThemeSettings;
  autonomy: Autonomy;
  goalMaxRounds: number;
  onGoalMaxRoundsChange: (value: number) => void;
  onThemeSettingsChange: (settings: ThemeSettings) => void;
  onAutonomyChange: (mode: Autonomy) => void;
  onLanguageChange?: () => void;
  onClose: () => void;
}

export function SettingsView({
  themeSettings,
  autonomy,
  goalMaxRounds,
  onGoalMaxRoundsChange,
  onThemeSettingsChange,
  onAutonomyChange,
  onLanguageChange,
  onClose,
}: SettingsViewProps) {
  const [settingsPage, setSettingsPage] = useState<'main' | 'theme' | 'audit'>('main');

  async function selectLanguage(language: string) {
    if (language !== 'zh' && language !== 'en') return;
    if (language === getLanguage()) return;
    await setLanguage(language as Language);
    // No need to trigger re-render — useLanguage() hook handles it automatically
  }

  const currentPreset = THEME_PRESETS.find((preset) => preset.id === themeSettings.presetId);

  if (settingsPage === 'theme') {
    return <ThemeCustomizer settings={themeSettings} onChange={onThemeSettingsChange} onBack={() => setSettingsPage('main')} />;
  }

  if (settingsPage === 'audit') {
    return (
      <WorkspacePage
        eyebrow={t('settings.title')}
        title={t('settings.audit_group')}
        description={t('settings.audit_group_desc')}
        action={(
          <Button variant="ghost" onClick={() => setSettingsPage('main')}>
            <ArrowLeft size={15} />
            {t('settings.back')}
          </Button>
        )}
      >
        <ToolAuditPanel embedded />
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage
      eyebrow={t('settings.title')}
      title={t('settings.heading')}
      description={t('settings.description')}
      action={(
        <Button variant="ghost" onClick={onClose}>
          <X size={15} />
          {t('settings.close')}
        </Button>
      )}
    >
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
                id: 'autonomy',
                type: 'toggle',
                label: t('settings.autonomy'),
                description: t('settings.autonomy_desc'),
                value: autonomy,
                options: autonomyOptions(),
                onChange: (value) => onAutonomyChange(value as Autonomy),
              },
            ],
          },
          {
            id: 'observation',
            title: t('settings.audit_group'),
            description: t('settings.audit_group_desc'),
            items: [
              {
                id: 'audit',
                type: 'action',
                label: t('settings.audit_entry'),
                description: t('settings.audit_entry_desc'),
                actionLabel: t('settings.audit_open'),
                onAction: () => setSettingsPage('audit'),
              },
            ],
          },
          {
            id: 'goal',
            title: t('settings.goal_group'),
            description: t('settings.goal_group_desc'),
            items: [
              {
                id: 'goal_rounds',
                type: 'goal_rounds',
                label: t('settings.goal_rounds'),
                description: t('settings.goal_rounds_desc'),
                value: goalMaxRounds,
                onChange: onGoalMaxRoundsChange,
              },
            ],
          },
        ]}
      />
    </WorkspacePage>
  );
}
