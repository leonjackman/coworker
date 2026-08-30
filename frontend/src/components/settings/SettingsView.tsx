import { ArrowLeft, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { getLanguage, setLanguage, t, type Language } from '../../lib/i18n';
import { THEME_PRESETS, type ThemeMode, type ThemeSettings } from '../../lib/theme';
import type { Autonomy, MemorySettings, MemorySettingsPatch, WebSettings } from '../../types';
import type { UpdateCenter } from '../../lib/useUpdateCenter';
import { useSound } from '../sound-provider';
import { chatService } from '../../services/chatService';
import { SHORTCUT_REGISTRY } from '../../keys';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';
import { SettingsList } from './SettingsList';
import { ThemeCustomizer } from './ThemeCustomizer';
import { ToolAuditPanel } from './ToolAuditPanel';
import { UpdatePanel } from './UpdatePanel';
import { WebSettingsPage } from './WebSettingsPage';
import { ShortcutsPage } from './ShortcutsPage';
import { autonomyOptions, languageOptions, themeOptions } from './preference-options';

interface SettingsViewProps {
  themeSettings: ThemeSettings;
  autonomy: Autonomy;
  maxAttachmentMb: number;
  onMaxAttachmentMbChange: (value: number) => void;
  revertCode: boolean;
  onRevertCodeChange: (value: boolean) => void;
  goalEnabled: boolean;
  onGoalEnabledChange: (value: boolean) => void;
  onThemeSettingsChange: (settings: ThemeSettings) => void;
  onAutonomyChange: (mode: Autonomy) => void;
  memorySettings: MemorySettings | null;
  onMemorySettingsChange: (patch: MemorySettingsPatch) => void;
  modelOptions: { id: string; label: string; provider: string }[];
  updateCenter: UpdateCenter;
  onLanguageChange?: () => void;
  onClose: () => void;
  settingsPage: SettingsPage;
  onSettingsPageChange: (page: SettingsPage) => void;
  onOpenMemory?: () => void;
}

export type SettingsPage = 'main' | 'theme' | 'audit' | 'web' | 'shortcuts';

export function SettingsView({
  themeSettings,
  autonomy,
  maxAttachmentMb,
  onMaxAttachmentMbChange,
  revertCode,
  onRevertCodeChange,
  goalEnabled,
  onGoalEnabledChange,
  onThemeSettingsChange,
  onAutonomyChange,
  memorySettings,
  onMemorySettingsChange,
  modelOptions,
  updateCenter,
  onLanguageChange,
  onClose,
  settingsPage,
  onSettingsPageChange,
  onOpenMemory,
}: SettingsViewProps) {
  const { enabled: soundEnabled, toggleEnabled: toggleSound } = useSound();
  const [webSettings, setWebSettings] = useState<WebSettings | null>(null);

  useEffect(() => {
    let mounted = true;
    chatService
      .getWebSettings()
      .then((settings) => {
        if (mounted) setWebSettings(settings);
      })
      .catch(() => {
        if (mounted) setWebSettings(null);
      });
    return () => {
      mounted = false;
    };
  }, []);

  async function selectLanguage(language: string) {
    const allowed = ['zh', 'en', 'zh-TW', 'zh-HK', 'ja', 'ko', 'fr', 'de', 'es', 'pt-BR', 'ru'];
    if (!allowed.includes(language)) return;
    if (language === getLanguage()) return;
    await setLanguage(language as Language);
    // No need to trigger re-render — useLanguage() hook handles it automatically
  }

  const currentPreset = THEME_PRESETS.find((preset) => preset.id === themeSettings.presetId);

  if (settingsPage === 'theme') {
    return <ThemeCustomizer settings={themeSettings} onChange={onThemeSettingsChange} onBack={() => onSettingsPageChange('main')} />;
  }

  if (settingsPage === 'audit') {
    return (
      <WorkspacePage
        eyebrow={t('settings.title')}
        title={t('settings.audit_group')}
        description={t('settings.audit_group_desc')}
        action={(
          <Button variant="ghost" onClick={() => onSettingsPageChange('main')}>
            <ArrowLeft size={15} />
            {t('settings.back')}
          </Button>
        )}
      >
        <ToolAuditPanel embedded />
      </WorkspacePage>
    );
  }

  if (settingsPage === 'web') {
    const current: WebSettings = webSettings ?? {
      enabled: false,
      provider: 'tavily',
      max_results: 8,
      search_depth: 'basic',
      fetch_enabled: true,
      api_key_configured: false,
    };
    return <WebSettingsPage settings={current} onChange={setWebSettings} onBack={() => onSettingsPageChange('main')} />;
  }

  if (settingsPage === 'shortcuts') {
    return <ShortcutsPage onBack={() => onSettingsPageChange('main')} />;
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
                meta: <span className="settings-chip">{t(currentPreset?.labelKey ?? 'theme.preset_mineral')}</span>,
                onAction: () => onSettingsPageChange('theme'),
              },
              {
                id: 'shortcuts',
                type: 'action',
                label: t('settings.shortcuts_entry'),
                description: t('settings.shortcuts_entry_desc'),
                actionLabel: t('settings.configure'),
                meta: <span className="settings-chip">{t('settings.shortcuts_count', { count: SHORTCUT_REGISTRY.length })}</span>,
                onAction: () => onSettingsPageChange('shortcuts'),
              },
            ],
          },
          {
            id: 'agent',
            title: t('settings.agent_group'),
            description: t('settings.agent_group_desc'),
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
              {
                id: 'revert_code',
                type: 'toggle',
                label: t('settings.revert_code'),
                description: t('settings.revert_code_desc'),
                value: revertCode ? 'true' : 'false',
                options: [
                  { value: 'true', label: t('memory.enabled') },
                  { value: 'false', label: t('memory.disabled') },
                ],
                onChange: (value) => onRevertCodeChange(value === 'true'),
              },
              {
                id: 'goal_enabled',
                type: 'toggle',
                label: t('settings.goal_enabled'),
                description: t('settings.goal_enabled_desc'),
                value: goalEnabled ? 'true' : 'false',
                options: [
                  { value: 'true', label: t('memory.enabled') },
                  { value: 'false', label: t('memory.disabled') },
                ],
                onChange: (value) => onGoalEnabledChange(value === 'true'),
              },
              {
                id: 'audit',
                type: 'action',
                label: t('settings.audit_entry'),
                description: t('settings.audit_entry_desc'),
                actionLabel: t('settings.audit_open'),
                onAction: () => onSettingsPageChange('audit'),
              },
            ],
          },
          {
            id: 'web',
            title: t('settings.web_group'),
            description: t('settings.web_group_desc'),
            items: [
              {
                id: 'web_tavily',
                type: 'action',
                label: t('settings.web_entry'),
                description: t('settings.web_entry_desc'),
                actionLabel: t('settings.web_open'),
                meta: (
                  <span className={`settings-chip${webSettings?.api_key_configured ? ' settings-chip--ok' : ''}`}>
                    {webSettings === null
                      ? ''
                      : webSettings.enabled
                        ? webSettings.api_key_configured
                          ? t('settings.web_configured')
                          : t('settings.web_not_configured')
                        : t('settings.web_disabled')}
                  </span>
                ),
                onAction: () => onSettingsPageChange('web'),
              },
            ],
          },
          {
            id: 'runtime',
            title: t('settings.runtime_group'),
            description: t('settings.runtime_group_desc'),
            items: [
              {
                id: 'open_memory',
                type: 'action',
                label: t('settings.memory_open'),
                description: t('settings.memory_open_desc'),
                actionLabel: t('settings.web_open'),
                onAction: () => onOpenMemory?.(),
              },
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
              {
                id: 'sound_enabled',
                type: 'toggle',
                label: t('settings.sound_enabled'),
                description: t('settings.sound_enabled_desc'),
                value: soundEnabled ? 'true' : 'false',
                options: [
                  { value: 'true', label: t('memory.enabled') },
                  { value: 'false', label: t('memory.disabled') },
                ],
                onChange: (value) => toggleSound(),
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
      <footer className="settings-brand-mark">
        <a href="https://coworker.lazzzyboy.com" target="_blank" rel="noreferrer">
          coworker.lazzzyboy.com
        </a>
      </footer>
    </WorkspacePage>
  );
}
