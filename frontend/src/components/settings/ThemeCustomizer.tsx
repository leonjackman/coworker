import { ArrowLeft } from 'lucide-react';
import {
  THEME_PRESETS,
  type ThemePresetId,
  type ThemeSettings,
} from '../../lib/theme';
import { t } from '../../lib/i18n';
import { Button } from '../ui/button';
import { WorkspacePage } from '../ui/workspace-page';

interface ThemeCustomizerProps {
  settings: ThemeSettings;
  onChange: (settings: ThemeSettings) => void;
  onBack: () => void;
}

export function ThemeCustomizer({ settings, onChange, onBack }: ThemeCustomizerProps) {
  function selectPreset(presetId: ThemePresetId) {
    onChange({ ...settings, presetId });
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
      </div>
    </WorkspacePage>
  );
}
