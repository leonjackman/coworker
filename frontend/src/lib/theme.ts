import {
  buildPresetPalette,
  type PresetModeSeed,
  type Palette,
} from './color';

const STORAGE_KEY = 'coworker-theme-settings';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ThemePresetId = 'mineral' | 'hermes' | 'ember' | 'sage' | 'graphite';

export interface ThemePalette extends Palette {}

export interface ThemePreset {
  id: ThemePresetId;
  labelKey: string;
  descriptionKey: string;
  preview: string[];
  light: ThemePalette;
  dark: ThemePalette;
}

export interface ThemeSettings {
  mode: ThemeMode;
  presetId: ThemePresetId;
}

/* ------------------------------------------------------------------ */
/* Preset seeds — only the *intent* is specified; the rest is derived. */
/* Each seed approximates the original hand-tuned palette so the look is   */
/* preserved, but every derived tone (hover, soft, fg) is now coordinated. */
/* ------------------------------------------------------------------ */

interface PresetSeed {
  id: ThemePresetId;
  labelKey: string;
  descriptionKey: string;
  preview: string[];
  light: PresetModeSeed;
  dark: PresetModeSeed;
}

const PRESET_SEEDS: PresetSeed[] = [
  {
    id: 'mineral',
    labelKey: 'theme.preset_mineral',
    descriptionKey: 'theme.preset_mineral_desc',
    preview: ['#e9ecef', '#2b6f8f', '#242c34'],
    light: { accent: { l: 0.546, c: 0.215, h: 256 }, bg: { l: 1.0, c: 0, h: 0 }, neutralH: 250 },
    dark: { accent: { l: 0.67, c: 0.17, h: 256 }, bg: { l: 0.145, c: 0, h: 0 }, neutralH: 250 },
  },
  {
    id: 'hermes',
    labelKey: 'theme.preset_hermes',
    descriptionKey: 'theme.preset_hermes_desc',
    preview: ['#efe7d4', '#b56a2c', '#2a2118'],
    light: {
      accent: { l: 0.6, c: 0.13, h: 54 },
      bg: { l: 0.91, c: 0.03, h: 85 },
      neutralH: 85,
      panelLift: 0.06,
    },
    dark: {
      accent: { l: 0.74, c: 0.12, h: 62 },
      bg: { l: 0.12, c: 0.02, h: 60 },
      neutralH: 60,
      panelLift: 0.05,
    },
  },
  {
    id: 'ember',
    labelKey: 'theme.preset_ember',
    descriptionKey: 'theme.preset_ember_desc',
    preview: ['#241719', '#e05d3d', '#ffd7c9'],
    light: {
      accent: { l: 0.57, c: 0.17, h: 27 },
      bg: { l: 0.92, c: 0.015, h: 30 },
      neutralH: 250,
    },
    dark: {
      accent: { l: 0.68, c: 0.16, h: 32 },
      bg: { l: 0.11, c: 0.01, h: 20 },
      neutralH: 250,
    },
  },
  {
    id: 'sage',
    labelKey: 'theme.preset_sage',
    descriptionKey: 'theme.preset_sage_desc',
    preview: ['#e8eee6', '#5e7f55', '#243327'],
    light: {
      accent: { l: 0.56, c: 0.09, h: 142 },
      bg: { l: 0.92, c: 0.025, h: 145 },
      neutralH: 250,
    },
    dark: {
      accent: { l: 0.74, c: 0.11, h: 140 },
      bg: { l: 0.1, c: 0.015, h: 150 },
      neutralH: 250,
    },
  },
  {
    id: 'graphite',
    labelKey: 'theme.preset_graphite',
    descriptionKey: 'theme.preset_graphite_desc',
    preview: ['#121212', '#f0f0f0', '#7c8590'],
    light: {
      accent: { l: 0.21, c: 0.006, h: 250 },
      bg: { l: 0.92, c: 0, h: 0 },
      neutralH: 250,
    },
    dark: {
      accent: { l: 0.86, c: 0.004, h: 100 },
      bg: { l: 0.11, c: 0, h: 0 },
      neutralH: 250,
    },
  },
];

function buildPreset(seed: PresetSeed): ThemePreset {
  return {
    id: seed.id,
    labelKey: seed.labelKey,
    descriptionKey: seed.descriptionKey,
    preview: seed.preview,
    light: buildPresetPalette('light', seed.light),
    dark: buildPresetPalette('dark', seed.dark),
  };
}

export const THEME_PRESETS: ThemePreset[] = PRESET_SEEDS.map(buildPreset);

const DEFAULT_SETTINGS: ThemeSettings = {
  mode: 'system',
  presetId: 'mineral',
};
const DEFAULT_PRESET = THEME_PRESETS[0] as ThemePreset;

function systemPrefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

function readStoredSettings(): ThemeSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(stored) as Partial<ThemeSettings>;
    return normalizeSettings(parsed);
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function normalizeSettings(settings: Partial<ThemeSettings>): ThemeSettings {
  const mode = settings.mode === 'light' || settings.mode === 'dark' || settings.mode === 'system' ? settings.mode : DEFAULT_SETTINGS.mode;
  const presetId = isThemePresetId(settings.presetId) ? settings.presetId : DEFAULT_SETTINGS.presetId;
  return { mode, presetId };
}

export function getThemeSettings(): ThemeSettings {
  return readStoredSettings();
}

export function setThemeSettings(settings: ThemeSettings): void {
  const normalized = normalizeSettings(settings);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Theme still applies for the current session.
  }
  applyTheme(normalized);
}

export function effectiveThemeMode(mode: ThemeMode): 'light' | 'dark' {
  return mode === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode;
}

export function resolveThemePalette(settings: ThemeSettings): ThemePalette {
  const effective = effectiveThemeMode(settings.mode);
  const preset = THEME_PRESETS.find((candidate) => candidate.id === settings.presetId) ?? DEFAULT_PRESET;
  return preset[effective];
}

export function applyTheme(settings: ThemeSettings): void {
  const effective = effectiveThemeMode(settings.mode);
  const palette = resolveThemePalette(settings);
  const root = document.documentElement;
  root.dataset.theme = effective;
  root.dataset.themePreset = settings.presetId;
  root.style.colorScheme = effective;
  for (const [key, value] of Object.entries(palette)) {
    root.style.setProperty(`--${kebabCase(key)}`, value);
  }
  root.classList.toggle('dark', effective === 'dark');
  // shadcn/Radix token aliases — keep these in sync with the generated palette.
  root.style.setProperty('--card', 'var(--panel-solid)');
  root.style.setProperty('--card-foreground', palette.foreground);
  root.style.setProperty('--popover', 'var(--panel-solid)');
  root.style.setProperty('--popover-foreground', palette.foreground);
  root.style.setProperty('--primary', palette.accent);
  root.style.setProperty('--primary-foreground', palette.accentForeground);
  root.style.setProperty('--secondary', 'var(--control)');
  root.style.setProperty('--secondary-foreground', palette.foreground);
  root.style.setProperty('--muted', 'var(--control)');
  root.style.setProperty('--muted-foreground', palette.mutedForeground);
  root.style.setProperty('--input', 'var(--control)');
  root.style.setProperty('--accent-foreground', palette.accentForeground);
  root.style.setProperty('--destructive', 'var(--danger)');
  root.style.setProperty('--destructive-foreground', 'var(--danger-foreground)');
  root.style.setProperty('--sidebar-foreground', palette.foreground);
  root.style.setProperty('--sidebar-accent', 'var(--control-active)');
  root.style.setProperty('--sidebar-accent-foreground', palette.foreground);
  root.style.setProperty('--sidebar-border', palette.border);
}

function kebabCase(value: string): string {
  return value.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
}

function isThemePresetId(value: unknown): value is ThemePresetId {
  return THEME_PRESETS.some((preset) => preset.id === value);
}
