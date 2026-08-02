const STORAGE_KEY = 'coworker-theme-settings';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ThemePresetId = 'mineral' | 'hermes' | 'ember' | 'sage' | 'graphite' | 'custom';

export interface ThemePalette {
  background: string;
  foreground: string;
  mutedForeground: string;
  panel: string;
  panelSolid: string;
  panelHover: string;
  input: string;
  control: string;
  controlActive: string;
  border: string;
  ring: string;
  accent: string;
  accentHover: string;
  accentForeground: string;
  sidebar: string;
  messageUser: string;
  messageUserForeground: string;
  shadow: string;
  tooltip: string;
  tooltipForeground: string;
  scrollThumb: string;
  backgroundGrid: string;
}

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
  customPalette: Partial<ThemePalette>;
  translucent: boolean;
}

export type ThemeColorKey = keyof Pick<ThemePalette, 'background' | 'foreground' | 'accent' | 'panelSolid' | 'messageUser'>;

export const CUSTOM_COLOR_KEYS: ThemeColorKey[] = ['background', 'foreground', 'accent', 'panelSolid', 'messageUser'];

const mineralLight: ThemePalette = {
  background: '#ffffff',
  backgroundGrid: 'transparent',
  foreground: '#0a0a0b',
  mutedForeground: '#71717a',
  panel: '#ffffff',
  panelSolid: '#ffffff',
  panelHover: '#f4f4f5',
  input: '#e4e4e7',
  control: '#f4f4f5',
  controlActive: '#e4e4e7',
  border: '#e4e4e7',
  ring: '#2563eb',
  accent: '#2563eb',
  accentHover: '#1d4ed8',
  accentForeground: '#ffffff',
  sidebar: '#fafafa',
  messageUser: '#2563eb',
  messageUserForeground: '#ffffff',
  shadow: 'rgba(0, 0, 0, 0.06)',
  tooltip: '#18181b',
  tooltipForeground: '#ffffff',
  scrollThumb: 'rgba(113, 113, 122, 0.28)',
};

const mineralDark: ThemePalette = {
  background: '#0a0a0b',
  backgroundGrid: 'transparent',
  foreground: '#fafafa',
  mutedForeground: '#a1a1aa',
  panel: '#18181b',
  panelSolid: '#18181b',
  panelHover: '#27272a',
  input: '#27272a',
  control: '#27272a',
  controlActive: '#1a1a1e',
  border: '#27272a',
  ring: '#3b82f6',
  accent: '#3b82f6',
  accentHover: '#2563eb',
  accentForeground: '#ffffff',
  sidebar: '#0d0d0f',
  messageUser: '#3b82f6',
  messageUserForeground: '#ffffff',
  shadow: 'rgba(0, 0, 0, 0.08)',
  tooltip: '#fafafa',
  tooltipForeground: '#0a0a0b',
  scrollThumb: 'rgba(161, 161, 170, 0.32)',
};

export const THEME_PRESETS: ThemePreset[] = [
  {
    id: 'mineral',
    labelKey: 'theme.preset_mineral',
    descriptionKey: 'theme.preset_mineral_desc',
    preview: ['#e9ecef', '#2b6f8f', '#242c34'],
    light: mineralLight,
    dark: mineralDark,
  },
  {
    id: 'hermes',
    labelKey: 'theme.preset_hermes',
    descriptionKey: 'theme.preset_hermes_desc',
    preview: ['#efe7d4', '#b56a2c', '#2a2118'],
    light: {
      ...mineralLight,
      background: '#efe7d4',
      backgroundGrid: 'rgba(88, 58, 30, 0.055)',
      foreground: '#2a2118',
      mutedForeground: '#7d6a58',
      panelSolid: '#fbf4e7',
      panel: 'rgba(255, 248, 233, 0.74)',
      panelHover: 'rgba(243, 226, 196, 0.82)',
      accent: '#b56a2c',
      accentHover: '#985420',
      ring: '#b56a2c',
      sidebar: 'rgba(242, 229, 204, 0.82)',
      messageUser: '#3a2a1d',
      shadow: 'rgba(83, 55, 28, 0.16)',
    },
    dark: {
      ...mineralDark,
      background: '#18130f',
      backgroundGrid: 'rgba(236, 204, 160, 0.055)',
      foreground: '#f4ead9',
      mutedForeground: '#b9a890',
      panelSolid: '#221a13',
      panel: 'rgba(34, 26, 19, 0.82)',
      panelHover: 'rgba(64, 48, 33, 0.72)',
      accent: '#e0a15b',
      accentHover: '#efb36c',
      ring: '#e0a15b',
      sidebar: 'rgba(24, 18, 13, 0.9)',
      messageUser: '#f0d7b2',
      messageUserForeground: '#1b130d',
    },
  },
  {
    id: 'ember',
    labelKey: 'theme.preset_ember',
    descriptionKey: 'theme.preset_ember_desc',
    preview: ['#241719', '#e05d3d', '#ffd7c9'],
    light: {
      ...mineralLight,
      background: '#f3ebe7',
      accent: '#c94f3d',
      accentHover: '#ab3f31',
      ring: '#c94f3d',
      messageUser: '#3a2020',
      panelSolid: '#fff8f4',
      sidebar: 'rgba(247, 236, 231, 0.82)',
    },
    dark: {
      ...mineralDark,
      background: '#130f10',
      foreground: '#fff1eb',
      panelSolid: '#201617',
      accent: '#f07855',
      accentHover: '#ff8a67',
      ring: '#f07855',
      messageUser: '#ffd7c9',
      messageUserForeground: '#201111',
    },
  },
  {
    id: 'sage',
    labelKey: 'theme.preset_sage',
    descriptionKey: 'theme.preset_sage_desc',
    preview: ['#e8eee6', '#5e7f55', '#243327'],
    light: {
      ...mineralLight,
      background: '#e8eee6',
      accent: '#5e7f55',
      accentHover: '#4d6b46',
      ring: '#5e7f55',
      panelSolid: '#f6faf3',
      sidebar: 'rgba(235, 242, 230, 0.82)',
      messageUser: '#243327',
    },
    dark: {
      ...mineralDark,
      background: '#101610',
      foreground: '#edf4e9',
      panelSolid: '#182018',
      accent: '#93b985',
      accentHover: '#a3ca96',
      ring: '#93b985',
      messageUser: '#dcebd3',
      messageUserForeground: '#101610',
    },
  },
  {
    id: 'graphite',
    labelKey: 'theme.preset_graphite',
    descriptionKey: 'theme.preset_graphite_desc',
    preview: ['#121212', '#f0f0f0', '#7c8590'],
    light: {
      ...mineralLight,
      background: '#ededeb',
      foreground: '#161616',
      mutedForeground: '#666666',
      accent: '#2f3337',
      accentHover: '#1f2327',
      ring: '#2f3337',
      messageUser: '#1e1f21',
      panelSolid: '#f8f8f6',
    },
    dark: {
      ...mineralDark,
      background: '#0f0f10',
      foreground: '#f1f1ee',
      mutedForeground: '#a8a8a2',
      panelSolid: '#181819',
      accent: '#d7d7d2',
      accentHover: '#f0f0ea',
      ring: '#d7d7d2',
      messageUser: '#efefea',
      messageUserForeground: '#111112',
    },
  },
];

const DEFAULT_SETTINGS: ThemeSettings = {
  mode: 'system',
  presetId: 'mineral',
  customPalette: {},
  translucent: false,
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
  return {
    mode,
    presetId,
    customPalette: settings.customPalette ?? {},
    translucent: settings.translucent === true,
  };
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
  const palette = preset[effective];
  return settings.presetId === 'custom' ? { ...palette, ...settings.customPalette } : palette;
}

export function customBasePalette(settings: ThemeSettings): ThemePalette {
  const baseSettings: ThemeSettings = { ...settings, presetId: 'mineral' };
  return { ...resolveThemePalette(baseSettings), ...settings.customPalette };
}

export function applyTheme(settings: ThemeSettings): void {
  const effective = effectiveThemeMode(settings.mode);
  const palette = resolveThemePalette(settings);
  const root = document.documentElement;
  root.dataset.theme = effective;
  root.dataset.themePreset = settings.presetId;
  root.dataset.translucent = settings.translucent ? 'true' : 'false';
  root.style.colorScheme = effective;
  for (const [key, value] of Object.entries(palette)) {
    root.style.setProperty(`--${kebabCase(key)}`, value);
  }
  if (settings.translucent) {
    root.style.setProperty('--panel-hover', `color-mix(in srgb, ${palette.panelHover} 72%, transparent)`);
  }
  root.classList.toggle('dark', effective === 'dark');
  root.style.setProperty('--card', 'var(--material-panel-solid)');
  root.style.setProperty('--card-foreground', palette.foreground);
  root.style.setProperty('--popover', 'var(--material-panel-solid)');
  root.style.setProperty('--popover-foreground', palette.foreground);
  root.style.setProperty('--primary', palette.accent);
  root.style.setProperty('--primary-foreground', palette.accentForeground);
  root.style.setProperty('--secondary', 'var(--material-control)');
  root.style.setProperty('--secondary-foreground', palette.foreground);
  root.style.setProperty('--muted', 'var(--material-control)');
  root.style.setProperty('--muted-foreground', palette.mutedForeground);
  root.style.setProperty('--input', 'var(--material-control)');
  root.style.setProperty('--accent-foreground', palette.accentForeground);
  root.style.setProperty('--destructive', '#ef4444');
  root.style.setProperty('--destructive-foreground', '#ffffff');
  root.style.setProperty('--sidebar-foreground', palette.foreground);
  root.style.setProperty('--sidebar-accent', palette.controlActive);
  root.style.setProperty('--sidebar-accent-foreground', palette.foreground);
  root.style.setProperty('--sidebar-border', palette.border);
  if (typeof (window as any).electronAPI?.updateTranslucent === 'function') {
    (window as any).electronAPI.updateTranslucent(settings.translucent);
  }
}

function kebabCase(value: string): string {
  return value.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
}

function isThemePresetId(value: unknown): value is ThemePresetId {
  return value === 'custom' || THEME_PRESETS.some((preset) => preset.id === value);
}
