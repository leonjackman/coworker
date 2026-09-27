/**
 * Persisted Workflow Studio preferences (electron pane/dock layout, canvas
 * edge style, minimap, etc.).
 *
 * Stored in localStorage so the editor reopens exactly how the user left it —
 * independently of any workflow document. Values are validated on load so a
 * stale/corrupt entry can never crash the editor; unknown fields fall back to
 * the defaults below.
 */

export type EdgeStyle = 'default' | 'smoothstep' | 'straight';

export interface StudioSettings {
  /** Canvas edge style. `default` = bezier curve (the default). */
  edgeType: EdgeStyle;
  showMinimap: boolean;
  interactive: boolean;
  leftOpen: boolean;
  leftWidth: number;
  leftTab: string;
  rightOpen: boolean;
  rightWidth: number;
  inspectorTab: string;
  bottomOpen: boolean;
  bottomTab: string;
}

export const DEFAULT_STUDIO_SETTINGS: StudioSettings = {
  edgeType: 'default',
  showMinimap: true,
  interactive: true,
  leftOpen: true,
  leftWidth: 240,
  leftTab: 'nodes',
  rightOpen: true,
  rightWidth: 320,
  inspectorTab: 'basic',
  bottomOpen: false,
  bottomTab: 'problems',
};

const STORAGE_KEY = 'cw-workflow-studio-settings';

const EDGE_STYLES: EdgeStyle[] = ['default', 'smoothstep', 'straight'];

function asBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function asWidth(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 160 && value <= 640 ? value : fallback;
}

function asOneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export function loadStudioSettings(): StudioSettings {
  const defaults = DEFAULT_STUDIO_SETTINGS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw) as Partial<Record<keyof StudioSettings, unknown>>;
    return {
      edgeType: asOneOf(parsed.edgeType, EDGE_STYLES, defaults.edgeType),
      showMinimap: asBool(parsed.showMinimap, defaults.showMinimap),
      interactive: asBool(parsed.interactive, defaults.interactive),
      leftOpen: asBool(parsed.leftOpen, defaults.leftOpen),
      leftWidth: asWidth(parsed.leftWidth, defaults.leftWidth),
      leftTab: typeof parsed.leftTab === 'string' ? parsed.leftTab : defaults.leftTab,
      rightOpen: asBool(parsed.rightOpen, defaults.rightOpen),
      rightWidth: asWidth(parsed.rightWidth, defaults.rightWidth),
      inspectorTab: typeof parsed.inspectorTab === 'string' ? parsed.inspectorTab : defaults.inspectorTab,
      bottomOpen: asBool(parsed.bottomOpen, defaults.bottomOpen),
      bottomTab: typeof parsed.bottomTab === 'string' ? parsed.bottomTab : defaults.bottomTab,
    };
  } catch {
    return defaults;
  }
}

export function saveStudioSettings(settings: StudioSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // localStorage may be unavailable in restricted renderer contexts.
  }
}
