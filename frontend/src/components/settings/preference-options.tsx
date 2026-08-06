import { Languages, Moon, Monitor, ShieldCheck, Sun, UserCheck, Zap } from 'lucide-react';
import { t } from '../../lib/i18n';
import type { Autonomy, Language } from '../../types';
import type { ThemeMode } from '../../lib/theme';
import type { SettingsToggleOption } from './SettingsList';

export function autonomyOptions(): SettingsToggleOption<Autonomy>[] {
  return [
    { value: 'supervised', label: <><UserCheck size={14} />{t('chat.autonomy_supervised')}</>, title: t('chat.autonomy_supervised_tip') },
    { value: 'guarded', label: <><ShieldCheck size={14} />{t('chat.autonomy_guarded')}</>, title: t('chat.autonomy_guarded_tip') },
    { value: 'autonomous', label: <><Zap size={14} />{t('chat.autonomy_autonomous')}</>, title: t('chat.autonomy_autonomous_tip') },
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
