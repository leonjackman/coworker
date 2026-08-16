import { ArrowLeft, X } from 'lucide-react';
import { useState } from 'react';
import { getLanguage, setLanguage, t, type Language } from '../../lib/i18n';
import { THEME_PRESETS, type ThemeMode, type ThemeSettings } from '../../lib/theme';
import type { Autonomy, MemorySettings, MemorySettingsPatch } from '../../types';
import type { UpdateCenter } from '../../lib/useUpdateCenter';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { SettingsList } from './SettingsList';
import { ThemeCustomizer } from './ThemeCustomizer';
import { ToolAuditPanel } from './ToolAuditPanel';
import { UpdatePanel } from './UpdatePanel';
import { autonomyOptions, languageOptions, themeOptions } from './preference-options';

interface SettingsViewProps {
  themeSettings: ThemeSettings;
  autonomy: Autonomy;
  goalMaxRounds: number;
  onGoalMaxRoundsChange: (value: number) => void;
  maxAttachmentMb: number;
  onMaxAttachmentMbChange: (value: number) => void;
  onThemeSettingsChange: (settings: ThemeSettings) => void;
  onAutonomyChange: (mode: Autonomy) => void;
  memorySettings: MemorySettings | null;
  onMemorySettingsChange: (patch: MemorySettingsPatch) => void;
  modelOptions: { id: string; label: string; provider: string }[];
  updateCenter: UpdateCenter;
  onLanguageChange?: () => void;
  onClose: () => void;
}

export function SettingsView({
  themeSettings,
  autonomy,
  goalMaxRounds,
  onGoalMaxRoundsChange,
  maxAttachmentMb,
  onMaxAttachmentMbChange,
  onThemeSettingsChange,
  onAutonomyChange,
  memorySettings,
  onMemorySettingsChange,
  modelOptions,
  updateCenter,
  onLanguageChange,
  onClose,
}: SettingsViewProps) {
  const [settingsPage, setSettingsPage] = useState<'main' | 'theme' | 'audit'>('main');

  async function selectLanguage(language: string) {
    const allowed = ['zh', 'en', 'zh-TW', 'zh-HK', 'ja', 'ko', 'fr', 'de', 'es', 'pt-BR', 'ru'];
    if (!allowed.includes(language)) return;
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
                type: 'select',
                label: t('settings.language'),
                description: t('settings.language_desc'),
                value: getLanguage(),
                options: languageOptions().map((opt) => ({ value: opt.value, label: opt.title })),
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
            id: 'attachments',
            title: t('settings.attachment_group'),
            description: t('settings.attachment_group_desc'),
            items: [
              {
                id: 'max_attachment_mb',
                type: 'number_input',
                label: t('settings.max_attachment_mb'),
                description: t('settings.max_attachment_mb_desc'),
                value: maxAttachmentMb,
                min: 1,
                max: 1024,
                unit: t('settings.max_attachment_mb_unit'),
                onChange: onMaxAttachmentMbChange,
              },
            ],
          },
          {
            id: 'memory',
            title: t('settings.memory_group'),
            description: t('settings.memory_group_desc'),
            items: [
              {
                id: 'memory_enabled',
                type: 'toggle',
                label: t('settings.memory_enabled'),
                description: t('settings.memory_enabled_desc'),
                value: memorySettings?.enabled ? 'true' : 'false',
                options: [
                  { value: 'true', label: t('memory.enabled') },
                  { value: 'false', label: t('memory.disabled') },
                ],
                onChange: (value) => onMemorySettingsChange({ enabled: value === 'true' }),
              },
              {
                id: 'memory_auto_extract',
                type: 'toggle',
                label: t('settings.memory_auto_extract'),
                description: t('settings.memory_auto_extract_desc'),
                value: memorySettings?.auto_extract ? 'true' : 'false',
                options: [
                  { value: 'true', label: t('memory.enabled') },
                  { value: 'false', label: t('memory.disabled') },
                ],
                onChange: (value) => onMemorySettingsChange({ auto_extract: value === 'true' }),
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
          {
            id: 'about',
            title: t('settings.about_group'),
            description: t('settings.about_group_desc'),
            items: [
              {
                id: 'version',
                type: 'info',
                label: t('settings.version'),
                description: t('settings.version_desc'),
                meta: (
                  <span className="settings-chip">
                    {updateCenter.state.currentVersion
                      ? `v${updateCenter.state.currentVersion}`
                      : t('update.version_unknown')}
                  </span>
                ),
              },
              {
                id: 'auto_update',
                type: 'switch',
                label: t('update.auto_update'),
                description: t('update.auto_update_desc'),
                checked: updateCenter.state.enabled,
                onChange: (checked) => void updateCenter.setAutoUpdate(checked),
              },
              {
                id: 'check_updates',
                type: 'action',
                label: t('update.check_now'),
                description: t('update.check_now_desc'),
                actionLabel: t('update.check_now_action'),
                disabled: updateCenter.state.state === 'checking',
                onAction: () => void updateCenter.check(),
              },
            ],
            footer: <UpdatePanel center={updateCenter} />,
          },
        ]}
      />
    </WorkspacePage>
  );
}
