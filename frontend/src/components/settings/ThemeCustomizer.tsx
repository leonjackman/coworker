import { ArrowLeft, Palette } from 'lucide-react';
import {
  CUSTOM_COLOR_KEYS,
  THEME_PRESETS,
  customBasePalette,
  resolveThemePalette,
  type ThemeColorKey,
  type ThemePresetId,
  type ThemeSettings,
} from '../../lib/theme';
import { t } from '../../lib/i18n';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';
import { WorkspacePage } from '../ui/workspace-page';

interface ThemeCustomizerProps {
  settings: ThemeSettings;
  onChange: (settings: ThemeSettings) => void;
  onBack: () => void;
}

function colorLabel(key: ThemeColorKey): string {
  return t(`theme.color_${key}`);
}

export function ThemeCustomizer({ settings, onChange, onBack }: ThemeCustomizerProps) {
  const palette = settings.presetId === 'custom' ? customBasePalette(settings) : resolveThemePalette(settings);

  function selectPreset(presetId: ThemePresetId) {
    onChange({ ...settings, presetId });
  }

  function changeColor(key: ThemeColorKey, value: string) {
    onChange({
      ...settings,
      presetId: 'custom',
      customPalette: {
        ...settings.customPalette,
        [key]: value,
      },
    });
  }

  function toggleTranslucent(checked: boolean) {
    onChange({ ...settings, translucent: checked });
  }

  return (
    <WorkspacePage
      className="theme-customizer"
      eyebrow={t('settings.title')}
      title={t('settings.palette_group')}
      description={t('settings.palette_group_desc')}
      action={(
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft size={15} />
          {t('settings.back')}
        </Button>
      )}
    >
      <div className="settings-card material-card">
        <div className="settings-row">
          <div className="settings-row__copy">
            <label htmlFor="setting-translucent-input">{t('settings.translucent_theme')}</label>
            <p>{t('settings.translucent_theme_desc')}</p>
          </div>
          <div className="settings-row__control">
            <Switch
              id="setting-translucent"
              checked={settings.translucent}
              onChange={(event) => toggleTranslucent(event.currentTarget.checked)}
            />
          </div>
        </div>
      </div>

      <div className="theme-preset-grid">
        {THEME_PRESETS.map((preset) => (
          <button
            className={`theme-preset-card ${settings.presetId === preset.id ? 'theme-preset-card--active' : ''}`}
            key={preset.id}
            type="button"
            onClick={() => selectPreset(preset.id)}
          >
            <span className="theme-preset-card__swatches">
              {preset.preview.map((color) => (
                <span key={color} style={{ background: color }} />
              ))}
            </span>
            <strong>{t(preset.labelKey)}</strong>
            <small>{t(preset.descriptionKey)}</small>
          </button>
        ))}
        <button
          className={`theme-preset-card theme-preset-card--custom ${settings.presetId === 'custom' ? 'theme-preset-card--active' : ''}`}
          type="button"
          onClick={() => selectPreset('custom')}
        >
          <span className="theme-preset-card__swatches">
            <span style={{ background: palette.background }} />
            <span style={{ background: palette.accent }} />
            <span style={{ background: palette.messageUser }} />
          </span>
          <strong>{t('theme.preset_custom')}</strong>
          <small>{t('theme.preset_custom_desc')}</small>
        </button>
      </div>

      <div className={`settings-card color-card ${settings.presetId !== 'custom' ? 'color-card--disabled' : ''}`}>
        <div className="settings-row">
          <div className="settings-row__copy">
            <label>{t('settings.custom_palette')}</label>
            <p>{t('settings.custom_palette_desc')}</p>
          </div>
          <div className="settings-row__control">
            <span className="palette-badge">
              <Palette size={14} />
              {settings.presetId === 'custom' ? t('theme.preset_custom') : t('settings.select_custom_first')}
            </span>
          </div>
        </div>
        <div className="color-grid">
          {CUSTOM_COLOR_KEYS.map((key) => (
            <label className="color-field" key={key}>
              <span>{colorLabel(key)}</span>
              <div>
                <input
                  type="color"
                  value={normalizeColor((settings.customPalette[key] as string | undefined) ?? palette[key])}
                  onChange={(event) => changeColor(key, event.target.value)}
                  disabled={settings.presetId !== 'custom'}
                />
                <code>{(settings.customPalette[key] as string | undefined) ?? palette[key]}</code>
              </div>
            </label>
          ))}
        </div>
      </div>
    </WorkspacePage>
  );
}

function normalizeColor(color: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(color)) return color;
  return '#2b6f8f';
}
