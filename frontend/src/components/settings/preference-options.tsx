import { Hammer, Languages, ListChecks, Moon, Monitor, Shield, ShieldCheck, Sun } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { AccessMode, Language, WorkMode } from '../../types';
import type { ThemeMode } from '../../lib/theme';
import type { SettingsToggleOption } from './SettingsList';

export function workModeOptions(): SettingsToggleOption<WorkMode>[] {
  return [
    { value: 'plan', label: <><ListChecks size={14} />{t('chat.mode_plan')}</>, title: t('chat.mode_plan_tip') },
    { value: 'build', label: <><Hammer size={14} />{t('chat.mode_build')}</>, title: t('chat.mode_build_tip') },
  ];
}

export function accessModeOptions(): SettingsToggleOption<AccessMode>[] {
  return [
    { value: 'default', label: <><Shield size={14} />{t('chat.access_default')}</>, title: t('chat.access_default_tip') },
    { value: 'full', label: <><ShieldCheck size={14} />{t('chat.access_full')}</>, title: t('chat.access_full_tip') },
  ];
}

export function themeOptions(): SettingsToggleOption<ThemeMode>[] {
  return [
    { value: 'light', label: <><Sun size={14} />{t('theme.light')}</>, title: t('theme.light') },
    { value: 'dark', label: <><Moon size={14} />{t('theme.dark')}</>, title: t('theme.dark') },
    { value: 'system', label: <><Monitor size={14} />{t('theme.system')}</>, title: t('theme.system') },
  ];
}

export function languageOptions(): SettingsToggleOption<Language>[] {
  return [
    { value: 'zh', label: <><Languages size={14} />{t('language.zh')}</>, title: t('language.zh') },
    { value: 'en', label: <><Languages size={14} />{t('language.en')}</>, title: t('language.en') },
  ];
}
